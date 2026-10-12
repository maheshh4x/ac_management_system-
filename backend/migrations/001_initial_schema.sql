-- Non-destructive project schema. Run this once against the Supabase database.
-- Existing tables are preserved; optional AC columns are added only when absent.

CREATE TABLE IF NOT EXISTS colleges (
    college_id TEXT PRIMARY KEY,
    college_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS blocks (
    block_id TEXT PRIMARY KEY,
    college_id TEXT REFERENCES colleges(college_id),
    block_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS floors (
    floor_id TEXT PRIMARY KEY,
    block_id TEXT NOT NULL REFERENCES blocks(block_id),
    floor_name TEXT NOT NULL,
    floor_number INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS departments (
    department_id TEXT PRIMARY KEY,
    department_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS rooms (
    room_id TEXT PRIMARY KEY,
    floor_id TEXT NOT NULL REFERENCES floors(floor_id),
    department_id TEXT REFERENCES departments(department_id),
    room_name TEXT NOT NULL,
    room_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS acs (
    ac_id TEXT PRIMARY KEY,
    room_id TEXT REFERENCES rooms(room_id),
    ac_make_company TEXT,
    ac_model_number TEXT,
    ac_serial_number TEXT,
    ac_capacity_ton NUMERIC,
    ac_type TEXT,
    manufacturing_year INTEGER,
    installation_year INTEGER,
    current_status TEXT,
    ac_star_rating INTEGER,
    power_rating_kw NUMERIC,
    power_factor NUMERIC,
    installation_date DATE,
    last_maintenance_date DATE,
    next_service_date DATE,
    technician TEXT,
    remarks TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS maintenance (
    maintenance_id BIGSERIAL PRIMARY KEY,
    ac_id TEXT NOT NULL REFERENCES acs(ac_id) ON DELETE CASCADE,
    maintenance_type TEXT NOT NULL,
    maintenance_date DATE NOT NULL,
    remarks TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE acs ADD COLUMN IF NOT EXISTS installation_date DATE;
ALTER TABLE acs ADD COLUMN IF NOT EXISTS last_maintenance_date DATE;
ALTER TABLE acs ADD COLUMN IF NOT EXISTS next_service_date DATE;
ALTER TABLE acs ADD COLUMN IF NOT EXISTS technician TEXT;
ALTER TABLE acs ADD COLUMN IF NOT EXISTS remarks TEXT;

CREATE INDEX IF NOT EXISTS idx_acs_room_id ON acs(room_id);
CREATE INDEX IF NOT EXISTS idx_maintenance_ac_id ON maintenance(ac_id);