-- 015_fix_asset_location_mirror.sql — fix 014's mirror trigger function.
--
-- 014 was already applied (immutable once recorded), but its
-- acs_location_mirror_upsert() body has three unqualified column references
-- inside the ON CONFLICT DO UPDATE SET clause: a bare name there is ambiguous
-- between the target table row and the EXCLUDED pseudo-row, so any legacy
-- write of a DIFFERENT acs.room_id onto an asset that already had a
-- resolution record raised AmbiguousColumn at runtime. Only the qualified
-- spellings were wrong; semantics are unchanged from 014's intent:
--   * source_* and manual_fields in the DO UPDATE branch must read the
--     EXISTING row (asset_locations.<col>), never EXCLUDED defaults.
-- The trigger already references the function by name, so replacing the
-- function body is sufficient (no trigger re-create needed).
--
-- Manual down-SQL: re-apply the function body from 014 (see that file's
-- section 6) — forward-only project convention otherwise.

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
                WHEN asset_locations.source_college_value IS NOT NULL THEN 'UNRESOLVED'
                ELSE 'MISSING' END,
            department_status = CASE
                WHEN EXCLUDED.department_id IS NOT NULL THEN EXCLUDED.department_status
                WHEN asset_locations.department_id IS NOT NULL
                    THEN asset_locations.department_status
                WHEN asset_locations.source_department_value IS NOT NULL THEN 'UNRESOLVED'
                ELSE 'MISSING' END,
            -- Overwritten levels can no longer claim to be manually pinned
            -- (department only when the room chain actually supplied one).
            manual_fields = CASE
                WHEN EXCLUDED.department_id IS NOT NULL THEN COALESCE(array_remove(
                    array_remove(
                        array_remove(
                            array_remove(
                                array_remove(COALESCE(asset_locations.manual_fields, '{}'), 'room'),
                                'floor'),
                            'block'),
                        'college'),
                    'department'),
                '{}'::text[])
                ELSE COALESCE(
                    array_remove(
                        array_remove(
                            array_remove(
                                array_remove(COALESCE(asset_locations.manual_fields, '{}'), 'room'),
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
