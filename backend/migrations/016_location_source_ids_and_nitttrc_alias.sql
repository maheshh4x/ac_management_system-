-- 016_location_source_ids_and_nitttrc_alias.sql — explicit source IDs + verified alias.
--
-- Two additive, idempotent changes; no existing object is altered:
--
-- 1. Explicit canonical source-ID preservation. The importer recognizes
--    block_id / floor_id / room_id columns, but 014 only stored source NAME
--    text — an explicit canonical id had nowhere to live, so the resolver
--    could never prefer it (priority: explicit id > alias > name). Three
--    nullable TEXT columns close that gap:
--      source_block_id_value, source_floor_id_value, source_room_id_value.
--    Blank handling follows the project convention: the resolver omits blank
--    source columns from its upsert, so a blank cell never erases preserved
--    provenance (see location_resolver.resolution_values).
--
-- 2. Verified college alias. A read-only scan of ACMS 301.xlsx on 2026-10-09
--    (300 data rows, sheet 'AC Import') found college_name = 'NITTTRC' on
--    ALL 300 rows — the verified alias NITTTRC -> NITTTR-CHENNAI. The same
--    scan found 9 distinct block names, 19 department names and 112 room
--    types with NO verifiable canonical target in the campus masters, so no
--    other alias is seeded here (aliases must never be invented; they are
--    added through POST /campus/location/aliases when verified).
--
-- Forward-only runner (app/migrate.py); safe to re-apply (IF NOT EXISTS +
-- ON CONFLICT DO NOTHING).

-- ─────────────────────────────────────────────────────────────────────────
-- 1. Explicit source-ID columns (additive, nullable, no backfill: only new
--    imports carry explicit ids; legacy rows keep NULL = "no id supplied").
-- ─────────────────────────────────────────────────────────────────────────

ALTER TABLE asset_locations ADD COLUMN IF NOT EXISTS source_block_id_value TEXT;
ALTER TABLE asset_locations ADD COLUMN IF NOT EXISTS source_floor_id_value TEXT;
ALTER TABLE asset_locations ADD COLUMN IF NOT EXISTS source_room_id_value TEXT;

-- ─────────────────────────────────────────────────────────────────────────
-- 2. Verified alias NITTTRC -> NITTTR-CHENNAI (normalized 'nitttrc' per
--    location_resolver.normalize: NFKD -> ascii -> lower -> strip non-alnum).
-- ─────────────────────────────────────────────────────────────────────────

INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)
VALUES ('college', 'NITTTR-CHENNAI', 'NITTTRC', 'nitttrc', 'migration-016')
ON CONFLICT (entity_type, normalized_alias) DO NOTHING;
