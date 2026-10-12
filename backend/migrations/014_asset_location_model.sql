-- 014_asset_location_model.sql — permanent location architecture.
--
-- SOURCE location (what the import supplied) and CONFIRMED location (the
-- canonical campus hierarchy the AC is actually assigned in) become two
-- permanently separate concepts, tracked PER HIERARCHY LEVEL so resolution
-- is no longer all-or-nothing (a known block/floor/department survives an
-- unresolvable room).
--
--   asset_locations  — one current resolution record per AC: canonical FKs,
--                      preserved source_* values, per-field status, provenance.
--   location_aliases — centrally managed alias registry (entity_type ->
--                      canonical id); the only place "NITTTRC -> NITTTR-CHENNAI"
--                      style mappings live. No special cases in Python.
--   acs.location_status — EXISTING overall column, extended with 'PARTIAL'
--                      (ASSIGNED = room confirmed, PARTIAL = some levels
--                      resolved, PENDING = nothing resolved). No second
--                      status system.
--   acs.room_id      — compatibility mirror of asset_locations.room_id.
--
-- Authority contract:
--   * app/services/location_resolver.py is the only semantic writer; it writes
--     asset_locations + acs (room_id, location_status) in one transaction.
--   * Trigger acs_location_mirror_upsert keeps asset_locations consistent for
--     LEGACY direct writers of acs.room_id (movements history, old SQL paths,
--     tests). It never touches source_* values.
--   * Never edit asset_locations by hand.
--   * Per-field status invariant: id set => RESOLVED; id NULL + source value
--     with no match => UNRESOLVED; >1 matches => AMBIGUOUS; no source => MISSING.
--
-- Integrity (composite FKs; MATCH SIMPLE keeps partial rows legal):
--   room -> floor -> block -> college chains that are fully populated must be
--   consistent with the canonical hierarchy. Department/room consistency is
--   resolver-enforced + test-pinned (rooms may carry no department, so a DB
--   FK there would wrongly reject a source-resolved department).
--
-- Additive and idempotent; forward-only runner (app/migrate.py). Manual
-- down-SQL, in reverse order, if ever required:
--   DROP FUNCTION IF EXISTS acs_location_mirror_upsert();
--   DROP TRIGGER IF EXISTS trg_acs_location_mirror ON acs;
--   ALTER TABLE acs DROP CONSTRAINT IF EXISTS acs_location_status_valid;
--   ALTER TABLE acs ADD CONSTRAINT acs_location_status_valid
--       CHECK (location_status IN ('ASSIGNED', 'PENDING'));
--   DROP TABLE IF EXISTS asset_locations;
--   DROP TABLE IF EXISTS location_aliases;
--   DROP CONSTRAINT IF EXISTS rooms_room_floor_key / floors_floor_block_key /
--       blocks_block_college_key on rooms/floors/blocks (data preserved).

-- ─────────────────────────────────────────────────────────────────────────
-- 1. Alias registry (centralized, app-validated canonical targets)
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS location_aliases (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL
        CONSTRAINT location_aliases_entity_type_valid
            CHECK (entity_type IN ('college', 'block', 'floor', 'department', 'room')),
    entity_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- No duplicate aliases: one (type, normalized alias) -> exactly one target,
    -- so an alias can never be ambiguous.
    CONSTRAINT location_aliases_unique_alias UNIQUE (entity_type, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_location_aliases_target
    ON location_aliases (entity_type, entity_id) WHERE is_active;

-- ─────────────────────────────────────────────────────────────────────────
-- 2. Referenced-side unique keys required by the composite FKs of
--    asset_locations (derived from existing PKs; no naming policy).
-- ─────────────────────────────────────────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'rooms_room_floor_key') THEN
        ALTER TABLE rooms ADD CONSTRAINT rooms_room_floor_key UNIQUE (room_id, floor_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'floors_floor_block_key') THEN
        ALTER TABLE floors ADD CONSTRAINT floors_floor_block_key UNIQUE (floor_id, block_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'blocks_block_college_key') THEN
        ALTER TABLE blocks ADD CONSTRAINT blocks_block_college_key UNIQUE (block_id, college_id);
    END IF;
END $$;

-- ─────────────────────────────────────────────────────────────────────────
-- 3. Per-asset location resolution record
-- ─────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS asset_locations (
    ac_id TEXT PRIMARY KEY REFERENCES acs(ac_id) ON DELETE CASCADE,

    -- Confirmed canonical ids (NULL = not resolved at that level).
    college_id TEXT REFERENCES colleges(college_id),
    block_id TEXT REFERENCES blocks(block_id),
    floor_id TEXT REFERENCES floors(floor_id),
    department_id TEXT REFERENCES departments(department_id),
    room_id TEXT REFERENCES rooms(room_id),

    -- Exactly what the import supplied — preserved even when unresolvable.
    source_college_value TEXT,
    source_block_value TEXT,
    source_floor_value TEXT,
    source_department_value TEXT,
    source_room_type_value TEXT,
    source_room_number_value TEXT,

    -- Per-field resolution state (never a single all-or-nothing flag).
    college_status TEXT NOT NULL DEFAULT 'MISSING'
        CONSTRAINT asset_locations_college_status_valid
            CHECK (college_status IN ('RESOLVED', 'UNRESOLVED', 'MISSING', 'AMBIGUOUS')),
    block_status TEXT NOT NULL DEFAULT 'MISSING'
        CONSTRAINT asset_locations_block_status_valid
            CHECK (block_status IN ('RESOLVED', 'UNRESOLVED', 'MISSING', 'AMBIGUOUS')),
    floor_status TEXT NOT NULL DEFAULT 'MISSING'
        CONSTRAINT asset_locations_floor_status_valid
            CHECK (floor_status IN ('RESOLVED', 'UNRESOLVED', 'MISSING', 'AMBIGUOUS')),
    department_status TEXT NOT NULL DEFAULT 'MISSING'
        CONSTRAINT asset_locations_department_status_valid
            CHECK (department_status IN ('RESOLVED', 'UNRESOLVED', 'MISSING', 'AMBIGUOUS')),
    room_status TEXT NOT NULL DEFAULT 'MISSING'
        CONSTRAINT asset_locations_room_status_valid
            CHECK (room_status IN ('RESOLVED', 'UNRESOLVED', 'MISSING', 'AMBIGUOUS')),

    -- Provenance (§ auditability): who/what resolved this, from which import,
    -- why a level is unresolved/ambiguous, which fields were set manually.
    source_ref TEXT,
    resolved_by TEXT NOT NULL DEFAULT 'backfill',
    manual_fields TEXT[]
        CONSTRAINT asset_locations_manual_fields_valid
            CHECK (manual_fields IS NULL OR manual_fields <@
                ARRAY['college', 'block', 'floor', 'department', 'room']::text[]),
    resolution_notes JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Hierarchy consistency: a fully populated chain must match the masters.
    CONSTRAINT asset_locations_room_floor_fk
        FOREIGN KEY (room_id, floor_id) REFERENCES rooms(room_id, floor_id),
    CONSTRAINT asset_locations_floor_block_fk
        FOREIGN KEY (floor_id, block_id) REFERENCES floors(floor_id, block_id),
    CONSTRAINT asset_locations_block_college_fk
        FOREIGN KEY (block_id, college_id) REFERENCES blocks(block_id, college_id)
);

CREATE INDEX IF NOT EXISTS idx_asset_locations_block_id ON asset_locations (block_id);
CREATE INDEX IF NOT EXISTS idx_asset_locations_floor_id ON asset_locations (floor_id);
CREATE INDEX IF NOT EXISTS idx_asset_locations_department_id ON asset_locations (department_id);
CREATE INDEX IF NOT EXISTS idx_asset_locations_room_id ON asset_locations (room_id);
CREATE INDEX IF NOT EXISTS idx_asset_locations_college_id ON asset_locations (college_id);

-- ─────────────────────────────────────────────────────────────────────────
-- 4. Overall status: extend the EXISTING acs vocabulary with PARTIAL.
--    Existing values remain valid (superset constraint => safe migration).
-- ─────────────────────────────────────────────────────────────────────────

ALTER TABLE acs DROP CONSTRAINT IF EXISTS acs_location_status_valid;
ALTER TABLE acs ADD CONSTRAINT acs_location_status_valid
    CHECK (location_status IN ('ASSIGNED', 'PARTIAL', 'PENDING'));

-- ─────────────────────────────────────────────────────────────────────────
-- 5. Backfill existing ACs from DB truth ONLY (no source values were retained
--    by the legacy importer, so none are invented): the confirmed chain of a
--    room-assigned AC derives from acs.room_id; everything else is MISSING.
--    Source metadata for legacy rows arrives via one idempotent re-import of
--    the source file (documented in the implementation report).
-- ─────────────────────────────────────────────────────────────────────────

INSERT INTO asset_locations (
    ac_id, room_id, floor_id, block_id, college_id, department_id,
    room_status, floor_status, block_status, college_status, department_status,
    source_ref, resolved_by, resolution_notes
)
SELECT
    a.ac_id,
    a.room_id,
    r.floor_id,
    f.block_id,
    b.college_id,
    r.department_id,
    CASE WHEN a.room_id IS NULL THEN 'MISSING' ELSE 'RESOLVED' END,
    CASE WHEN r.floor_id   IS NULL THEN 'MISSING' ELSE 'RESOLVED' END,
    CASE WHEN f.block_id   IS NULL THEN 'MISSING' ELSE 'RESOLVED' END,
    CASE WHEN b.college_id IS NULL THEN 'MISSING' ELSE 'RESOLVED' END,
    CASE WHEN r.department_id IS NULL THEN 'MISSING' ELSE 'RESOLVED' END,
    'migration-014',
    'backfill',
    '{"_origin": "legacy import: source location not retained by pre-014 importer"}'::jsonb
FROM acs a
LEFT JOIN rooms r ON r.room_id = a.room_id
LEFT JOIN floors f ON f.floor_id = r.floor_id
LEFT JOIN blocks b ON b.block_id = f.block_id
ON CONFLICT (ac_id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────────
-- 6. Legacy-writer mirror: any direct INSERT/UPDATE of acs.room_id keeps the
--    resolution record consistent (and overall status correct). Terminates:
--    it only re-enters when acs.room_id itself changes, and the record then
--    already matches. Never touches source_* or manual pins it does not
--    overwrite. Direct asset_locations edits are NOT mirrored back — the
--    resolver service is the single writer (see header contract).
-- ─────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION acs_location_mirror_upsert() RETURNS TRIGGER AS $$
DECLARE
    v_exists    BOOLEAN;
    v_room      TEXT;
    v_floor     TEXT;
    v_block     TEXT;
    v_college   TEXT;
    v_dept      TEXT;
    v_overall   TEXT;
    v_keep_fl   BOOLEAN;
    v_keep_bl   BOOLEAN;
    v_keep_co   BOOLEAN;
    v_keep_de   BOOLEAN;
BEGIN
    SELECT TRUE, al.room_id INTO v_exists, v_room
      FROM asset_locations al WHERE al.ac_id = NEW.ac_id;

    IF v_exists AND v_room IS NOT DISTINCT FROM NEW.room_id THEN
        -- Record already reflects this room: only the overall status may need
        -- syncing (e.g. legacy INSERT leaving the column default).
        NULL;
    ELSIF NEW.room_id IS NULL THEN
        IF v_exists THEN
            v_keep_fl := (SELECT source_floor_value IS NOT NULL
                               OR 'floor' = ANY(COALESCE(manual_fields, '{}'))
                          FROM asset_locations WHERE ac_id = NEW.ac_id);
            v_keep_bl := (SELECT source_block_value IS NOT NULL
                               OR 'block' = ANY(COALESCE(manual_fields, '{}'))
                          FROM asset_locations WHERE ac_id = NEW.ac_id);
            v_keep_co := (SELECT source_college_value IS NOT NULL
                               OR 'college' = ANY(COALESCE(manual_fields, '{}'))
                          FROM asset_locations WHERE ac_id = NEW.ac_id);
            v_keep_de := (SELECT source_department_value IS NOT NULL
                               OR 'department' = ANY(COALESCE(manual_fields, '{}'))
                          FROM asset_locations WHERE ac_id = NEW.ac_id);
            UPDATE asset_locations SET
                room_id = NULL,
                room_status = CASE
                    WHEN source_room_number_value IS NOT NULL
                      OR source_room_type_value IS NOT NULL THEN 'UNRESOLVED'
                    ELSE 'MISSING' END,
                -- Levels whose knowledge came from the cleared room go with it;
                -- source-backed or manually confirmed levels are preserved.
                floor_id = CASE WHEN v_keep_fl THEN floor_id ELSE NULL END,
                floor_status = CASE
                    WHEN v_keep_fl THEN floor_status
                    WHEN source_floor_value IS NOT NULL THEN 'UNRESOLVED'
                    ELSE 'MISSING' END,
                block_id = CASE WHEN v_keep_bl THEN block_id ELSE NULL END,
                block_status = CASE
                    WHEN v_keep_bl THEN block_status
                    WHEN source_block_value IS NOT NULL THEN 'UNRESOLVED'
                    ELSE 'MISSING' END,
                college_id = CASE WHEN v_keep_co THEN college_id ELSE NULL END,
                college_status = CASE
                    WHEN v_keep_co THEN college_status
                    WHEN source_college_value IS NOT NULL THEN 'UNRESOLVED'
                    ELSE 'MISSING' END,
                department_id = CASE WHEN v_keep_de THEN department_id ELSE NULL END,
                department_status = CASE
                    WHEN v_keep_de THEN department_status
                    WHEN source_department_value IS NOT NULL THEN 'UNRESOLVED'
                    ELSE 'MISSING' END,
                manual_fields = COALESCE(array_remove(manual_fields, 'room'), '{}'::text[]),
                resolution_notes = resolution_notes || jsonb_build_object(
                    'room', 'cleared by direct acs.room_id write'),
                updated_at = NOW()
            WHERE ac_id = NEW.ac_id;
        ELSE
            INSERT INTO asset_locations (ac_id, room_id, room_status, resolution_notes)
            VALUES (NEW.ac_id, NULL, 'MISSING',
                    '{"room": "cleared/absent on legacy acs insert"}'::jsonb)
            ON CONFLICT (ac_id) DO NOTHING;
        END IF;
    ELSE
        -- Room present and the record differs (or is missing): derive the
        -- confirmed chain from the canonical room. Source values untouched.
        SELECT r.floor_id, f.block_id, b.college_id, r.department_id
          INTO v_floor, v_block, v_college, v_dept
          FROM rooms r
          LEFT JOIN floors f ON f.floor_id = r.floor_id
          LEFT JOIN blocks b ON b.block_id = f.block_id
         WHERE r.room_id = NEW.room_id;

        INSERT INTO asset_locations (
            ac_id, room_id, floor_id, block_id, college_id, department_id,
            room_status, floor_status, block_status, college_status, department_status,
            resolution_notes
        ) VALUES (
            NEW.ac_id, NEW.room_id, v_floor, v_block, v_college, v_dept,
            'RESOLVED',
            CASE WHEN v_floor   IS NULL THEN 'MISSING' ELSE 'RESOLVED' END,
            CASE WHEN v_block   IS NULL THEN 'MISSING' ELSE 'RESOLVED' END,
            CASE WHEN v_college IS NULL THEN 'MISSING' ELSE 'RESOLVED' END,
            CASE WHEN v_dept    IS NULL THEN 'MISSING' ELSE 'RESOLVED' END,
            '{"_origin": "derived from confirmed room by acs mirror"}'::jsonb
        )
        ON CONFLICT (ac_id) DO UPDATE SET
            room_id = EXCLUDED.room_id,
            floor_id = EXCLUDED.floor_id,
            block_id = EXCLUDED.block_id,
            -- college: prefer the room chain's college; keep the prior one only
            -- when the block did not change (so the composite FK still holds).
            college_id = CASE
                WHEN EXCLUDED.college_id IS NOT NULL THEN EXCLUDED.college_id
                WHEN EXCLUDED.block_id = asset_locations.block_id
                     THEN asset_locations.college_id
                ELSE NULL END,
            -- department: a room carrying no department must not erase a
            -- source-resolved or previously confirmed department.
            department_id = COALESCE(EXCLUDED.department_id, asset_locations.department_id),
            room_status = 'RESOLVED',
            floor_status = EXCLUDED.floor_status,
            block_status = EXCLUDED.block_status,
            college_status = CASE
                WHEN EXCLUDED.college_id IS NOT NULL THEN EXCLUDED.college_status
                WHEN EXCLUDED.block_id = asset_locations.block_id
                     AND asset_locations.college_id IS NOT NULL
                    THEN asset_locations.college_status
                WHEN source_college_value IS NOT NULL THEN 'UNRESOLVED'
                ELSE 'MISSING' END,
            department_status = CASE
                WHEN EXCLUDED.department_id IS NOT NULL THEN EXCLUDED.department_status
                WHEN asset_locations.department_id IS NOT NULL
                    THEN asset_locations.department_status
                WHEN source_department_value IS NOT NULL THEN 'UNRESOLVED'
                ELSE 'MISSING' END,
            -- Overwritten levels can no longer claim to be manually pinned
            -- (department only when the room chain actually supplied one).
            manual_fields = CASE
                WHEN EXCLUDED.department_id IS NOT NULL THEN COALESCE(array_remove(
                    array_remove(
                        array_remove(
                            array_remove(
                                array_remove(COALESCE(manual_fields, '{}'), 'room'),
                                'floor'),
                            'block'),
                        'college'),
                    'department'),
                '{}'::text[])
                ELSE COALESCE(
                    array_remove(
                        array_remove(
                            array_remove(
                                array_remove(COALESCE(manual_fields, '{}'), 'room'),
                                'floor'),
                            'block'),
                        'college'),
                    '{}'::text[]) END,
            updated_at = NOW();
    END IF;

    SELECT CASE
        WHEN room_id IS NOT NULL THEN 'ASSIGNED'
        WHEN college_status = 'RESOLVED' OR block_status = 'RESOLVED'
          OR floor_status = 'RESOLVED' OR department_status = 'RESOLVED'
            THEN 'PARTIAL'
        ELSE 'PENDING'
    END INTO v_overall
    FROM asset_locations WHERE ac_id = NEW.ac_id;

    IF v_overall IS NOT NULL AND v_overall IS DISTINCT FROM NEW.location_status THEN
        UPDATE acs SET location_status = v_overall
         WHERE ac_id = NEW.ac_id
           AND location_status IS DISTINCT FROM v_overall;
    END IF;

    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_acs_location_mirror ON acs;
CREATE TRIGGER trg_acs_location_mirror
    AFTER INSERT OR UPDATE OF room_id ON acs
    FOR EACH ROW
    EXECUTE FUNCTION acs_location_mirror_upsert();
