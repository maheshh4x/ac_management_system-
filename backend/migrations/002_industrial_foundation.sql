-- 002_industrial_foundation.sql — additive, non-destructive (Plan #4, #7, #8, #13, #15, #17, #24, #25, #34)
-- Run AFTER 001_initial_schema.sql. Uses IF NOT EXISTS / ADD COLUMN IF NOT EXISTS so it is safe to re-run.

-- ── profiles (server-side role source of truth) ─────────────────────────────
CREATE TABLE IF NOT EXISTS profiles (
    id UUID PRIMARY KEY,
    email TEXT,
    name TEXT,
    role TEXT NOT NULL DEFAULT 'VIEWER' CHECK (role IN ('SUPER_ADMIN','FACILITY_MANAGER','TECHNICIAN','VIEWER')),
    college_id TEXT REFERENCES colleges(college_id),
    department_id TEXT REFERENCES departments(department_id),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── asset identity: immutable UUID + human-readable code + lifecycle (Plan #6-#8)
ALTER TABLE acs ADD COLUMN IF NOT EXISTS asset_uuid UUID NOT NULL DEFAULT gen_random_uuid();
ALTER TABLE acs ADD COLUMN IF NOT EXISTS asset_code TEXT;
ALTER TABLE acs ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE';
ALTER TABLE acs ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE acs ADD COLUMN IF NOT EXISTS decommissioned_at TIMESTAMPTZ;

-- Backfill asset_code from ac_id where empty (keeps existing hierarchy-based IDs working)
UPDATE acs SET asset_code = ac_id WHERE asset_code IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'acs_asset_code_unique') THEN
        ALTER TABLE acs ADD CONSTRAINT acs_asset_code_unique UNIQUE (asset_code);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'acs_capacity_positive') THEN
        ALTER TABLE acs ADD CONSTRAINT acs_capacity_positive CHECK (ac_capacity_ton IS NULL OR ac_capacity_ton > 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'acs_star_rating_range') THEN
        ALTER TABLE acs ADD CONSTRAINT acs_star_rating_range CHECK (ac_star_rating IS NULL OR (ac_star_rating BETWEEN 1 AND 5));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'acs_power_factor_range') THEN
        ALTER TABLE acs ADD CONSTRAINT acs_power_factor_range CHECK (power_factor IS NULL OR (power_factor >= 0 AND power_factor <= 1));
    END IF;
END $$;

-- ── maintenance work orders (Plan #15) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS maintenance_work_orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ac_id TEXT REFERENCES acs(ac_id) ON DELETE SET NULL,
    asset_uuid UUID,
    maintenance_type TEXT,
    priority TEXT NOT NULL DEFAULT 'MEDIUM' CHECK (priority IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    status TEXT NOT NULL DEFAULT 'SCHEDULED' CHECK (status IN ('SCHEDULED','ASSIGNED','IN_PROGRESS','COMPLETED','CANCELLED')),
    scheduled_date DATE,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    technician_id UUID REFERENCES profiles(id),
    problem_description TEXT,
    work_performed TEXT,
    parts_used TEXT,
    labor_hours NUMERIC CHECK (labor_hours IS NULL OR labor_hours >= 0),
    remarks TEXT,
    created_by UUID REFERENCES profiles(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── asset movements (Plan #13) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS asset_movements (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ac_id TEXT REFERENCES acs(ac_id) ON DELETE SET NULL,
    asset_uuid UUID,
    from_room_id TEXT REFERENCES rooms(room_id),
    to_room_id TEXT NOT NULL REFERENCES rooms(room_id),
    requested_by UUID REFERENCES profiles(id),
    approved_by UUID REFERENCES profiles(id),
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    reason TEXT,
    rejection_reason TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPROVED','REJECTED','CANCELLED','COMPLETED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── audit logs, append-only (Plan #17) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES profiles(id),
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    old_values JSONB,
    new_values JSONB,
    ip_address TEXT,
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── asset documents metadata (Plan #34, files live in Supabase Storage) ─────
CREATE TABLE IF NOT EXISTS asset_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ac_id TEXT REFERENCES acs(ac_id) ON DELETE CASCADE,
    asset_uuid UUID,
    file_path TEXT NOT NULL,
    file_name TEXT,
    document_type TEXT,
    uploaded_by UUID REFERENCES profiles(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── indexes (Plan #25) ──────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_acs_asset_code ON acs(asset_code);
CREATE INDEX IF NOT EXISTS idx_acs_asset_uuid ON acs(asset_uuid);
CREATE INDEX IF NOT EXISTS idx_acs_status ON acs(current_status);
CREATE INDEX IF NOT EXISTS idx_acs_make ON acs(ac_make_company);
CREATE INDEX IF NOT EXISTS idx_acs_serial ON acs(ac_serial_number);
CREATE INDEX IF NOT EXISTS idx_acs_next_service ON acs(next_service_date);
CREATE INDEX IF NOT EXISTS idx_wo_ac_id ON maintenance_work_orders(ac_id);
CREATE INDEX IF NOT EXISTS idx_wo_status ON maintenance_work_orders(status);
CREATE INDEX IF NOT EXISTS idx_wo_scheduled ON maintenance_work_orders(scheduled_date);
CREATE INDEX IF NOT EXISTS idx_mov_ac_id ON asset_movements(ac_id);
CREATE INDEX IF NOT EXISTS idx_mov_status ON asset_movements(status);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rooms_floor ON rooms(floor_id);
CREATE INDEX IF NOT EXISTS idx_floors_block ON floors(block_id);
CREATE INDEX IF NOT EXISTS idx_blocks_college ON blocks(college_id);
