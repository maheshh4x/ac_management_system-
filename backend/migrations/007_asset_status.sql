-- 007: normalised operational status + filter/search indexes (Phase 3.1).
-- Additive and idempotent. current_status (free text) is KEPT until P8;
-- operational_status is the constrained column dashboards/filters use.

ALTER TABLE acs ADD COLUMN IF NOT EXISTS operational_status TEXT;

-- Backfill from the legacy free-text vocabulary (01-assessment §4):
--   working -> WORKING; not working|damaged -> FAULT;
--   under maintenance -> MAINTENANCE; inactive -> OFFLINE; else UNKNOWN.
UPDATE acs
SET operational_status = CASE LOWER(TRIM(COALESCE(current_status, '')))
    WHEN 'working' THEN 'WORKING'
    WHEN 'not working' THEN 'FAULT'
    WHEN 'damaged' THEN 'FAULT'
    WHEN 'fault' THEN 'FAULT'
    WHEN 'under maintenance' THEN 'MAINTENANCE'
    WHEN 'maintenance' THEN 'MAINTENANCE'
    WHEN 'inactive' THEN 'OFFLINE'
    WHEN 'offline' THEN 'OFFLINE'
    ELSE 'UNKNOWN'
END
WHERE operational_status IS NULL;

ALTER TABLE acs ALTER COLUMN operational_status SET DEFAULT 'UNKNOWN';
ALTER TABLE acs ALTER COLUMN operational_status SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'acs_operational_status_valid'
    ) THEN
        ALTER TABLE acs ADD CONSTRAINT acs_operational_status_valid
            CHECK (operational_status IN ('WORKING', 'FAULT', 'MAINTENANCE', 'OFFLINE', 'UNKNOWN'));
    END IF;
END $$;

-- Indexes for the new Phase 3 list filters. next_service_date (due_before)
-- is already covered by idx_acs_next_service from 002.
CREATE INDEX IF NOT EXISTS idx_acs_operational_status ON acs (operational_status);
CREATE INDEX IF NOT EXISTS idx_acs_lifecycle_status ON acs (lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_acs_location_status ON acs (location_status);
CREATE INDEX IF NOT EXISTS idx_acs_room_id ON acs (room_id);

-- Trigram index for DB-side search. pg_trgm needs CREATE privilege; when it
-- is unavailable the plain ILIKE search still works, just without the index.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS pg_trgm;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'pg_trgm not available, skipping trigram search index: %', SQLERRM;
    END;
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
        EXECUTE $idx$
            CREATE INDEX IF NOT EXISTS idx_acs_search_trgm ON acs USING gin (
                (COALESCE(ac_id, '') || ' ' || COALESCE(ac_serial_number, '') || ' '
                 || COALESCE(ac_make_company, '') || ' ' || COALESCE(ac_model_number, '')) gin_trgm_ops
            )
        $idx$;
    END IF;
END $$;

-- ── Campus block layout + photo references (Phase 3 task 6) ──────────────
-- Photos live in a private Supabase Storage bucket; only paths/metadata are
-- stored here (never image bytes — no data_url blobs in the DB).
ALTER TABLE blocks ADD COLUMN IF NOT EXISTS layout JSONB;
ALTER TABLE blocks ADD COLUMN IF NOT EXISTS photos JSONB NOT NULL DEFAULT '[]';
CREATE INDEX IF NOT EXISTS idx_blocks_college ON blocks (college_id);
