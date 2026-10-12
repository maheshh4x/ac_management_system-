-- 005: flexible AC import — location status + extensible asset attributes
-- (additive; idempotent). Does NOT modify 001/002/004.

-- "Location pending" is a first-class lifecycle-adjacent state: no existing
-- field represents it cleanly (current_status is operational, lifecycle_status
-- is decommission semantics, is_active is a boolean), so we add one typed
-- column on acs.
ALTER TABLE acs ADD COLUMN IF NOT EXISTS location_status TEXT NOT NULL DEFAULT 'ASSIGNED';

-- Existing rows: rows without a room are definitionally "location pending".
UPDATE acs SET location_status = 'PENDING' WHERE room_id IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'acs_location_status_valid') THEN
        ALTER TABLE acs ADD CONSTRAINT acs_location_status_valid
            CHECK (location_status IN ('ASSIGNED', 'PENDING'));
    END IF;
END $$;

-- Extensible attributes for genuinely variable/future AC fields (e.g. columns
-- present in a new spreadsheet format that the importer does not yet map to
-- typed core columns). Keeps core AC data typed and relational.
CREATE TABLE IF NOT EXISTS asset_attributes (
    id BIGSERIAL PRIMARY KEY,
    asset_uuid UUID NOT NULL REFERENCES acs(asset_uuid) ON DELETE CASCADE,
    attribute_name TEXT NOT NULL,
    attribute_value TEXT,
    attribute_type TEXT NOT NULL DEFAULT 'TEXT',
    source TEXT NOT NULL DEFAULT 'import',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (asset_uuid, attribute_name)
);

CREATE INDEX IF NOT EXISTS idx_asset_attributes_uuid ON asset_attributes (asset_uuid);
CREATE INDEX IF NOT EXISTS idx_asset_attributes_name ON asset_attributes (attribute_name);
