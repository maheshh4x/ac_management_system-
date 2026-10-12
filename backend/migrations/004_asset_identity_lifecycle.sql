-- 004: asset identity + lifecycle consistency (additive; idempotent).
-- Does NOT modify 001/002. Guards fail loudly instead of breaking data.

-- asset_uuid is immutable and must map 1:1 to an asset row.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM acs GROUP BY asset_uuid HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'duplicate asset_uuid values exist; resolve them before applying 004';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS acs_asset_uuid_unique ON acs (asset_uuid);

-- An asset must never be DECOMMISSIONED while still is_active = true.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM acs WHERE lifecycle_status = 'DECOMMISSIONED' AND is_active
    ) THEN
        RAISE EXCEPTION 'contradictory lifecycle state (DECOMMISSIONED + is_active) exists; fix data before applying 004';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'acs_lifecycle_consistency') THEN
        ALTER TABLE acs ADD CONSTRAINT acs_lifecycle_consistency
            CHECK (NOT (lifecycle_status = 'DECOMMISSIONED' AND is_active));
    END IF;
END $$;
