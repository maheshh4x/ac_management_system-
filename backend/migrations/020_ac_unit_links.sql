-- 020_ac_unit_links.sql — indoor/outdoor unit relationship (Feature A).
--
-- A split-system AC is modelled as two real `acs` rows: the existing indoor
-- row and an outdoor row with unit_role='OUTDOOR' linked to its indoor
-- parent via parent_ac_id. No existing row is touched: every pre-020 row
-- keeps unit_role='INDOOR' and parent_ac_id=NULL, so existing QR payloads,
-- asset URLs, visual IDs and energy/session references are unchanged.
--
-- Rollback: DROP INDEX uq_outdoor_per_parent; ALTER TABLE acs DROP COLUMN
-- parent_ac_id; ALTER TABLE acs DROP COLUMN unit_role. (Loses links; indoor
-- rows are unaffected.)

ALTER TABLE acs
    ADD COLUMN IF NOT EXISTS unit_role TEXT NOT NULL DEFAULT 'INDOOR';

ALTER TABLE acs
    ADD CONSTRAINT acs_unit_role_valid CHECK (unit_role IN ('INDOOR', 'OUTDOOR'));

ALTER TABLE acs
    ADD COLUMN IF NOT EXISTS parent_ac_id TEXT REFERENCES acs(ac_id) ON DELETE SET NULL;

-- An outdoor row links to exactly one indoor parent, and an indoor asset has
-- at most one linked outdoor row. Partial index: indoor rows (NULL parent)
-- never conflict.
CREATE UNIQUE INDEX IF NOT EXISTS uq_outdoor_per_parent
    ON acs (parent_ac_id)
    WHERE unit_role = 'OUTDOOR' AND parent_ac_id IS NOT NULL;

-- A row can never be its own parent.
ALTER TABLE acs
    ADD CONSTRAINT acs_parent_not_self CHECK (parent_ac_id IS NULL OR parent_ac_id <> ac_id);
