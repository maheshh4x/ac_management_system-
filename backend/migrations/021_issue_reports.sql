-- 021_issue_reports.sql — universal issue reporting with Super Admin approval.
--
-- Reports are stored PENDING_APPROVAL; approval/rejection flips status and,
-- on approval, links exactly one work order (UNIQUE work_order_id). The
-- report row is never deleted (rejected rows keep their reason + history).
--
-- Rollback: DROP TABLE maintenance_reports. (Work orders created from
-- reports are ordinary maintenance_work_orders rows and are kept.)

CREATE TABLE IF NOT EXISTS maintenance_reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_code TEXT NOT NULL UNIQUE,
    ac_id TEXT REFERENCES acs(ac_id) ON DELETE SET NULL,
    reporter_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
    category TEXT NOT NULL CHECK (category IN (
        'AC_NOT_COOLING', 'AC_NOT_STARTING', 'WATER_LEAKAGE',
        'NOISE_VIBRATION', 'ELECTRICAL_POWER', 'OUTDOOR_UNIT',
        'INDOOR_UNIT', 'PREVENTIVE_MAINTENANCE', 'OTHER'
    )),
    description TEXT NOT NULL CHECK (
        char_length(btrim(description)) BETWEEN 1 AND 2000
    ),
    priority TEXT NOT NULL DEFAULT 'MEDIUM'
        CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    location_text TEXT CHECK (
        location_text IS NULL OR char_length(location_text) <= 500
    ),
    asset_location_snapshot TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK (
        status IN ('PENDING_APPROVAL', 'APPROVED', 'REJECTED')
    ),
    decided_by UUID REFERENCES profiles(id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    rejection_reason TEXT CHECK (
        rejection_reason IS NULL OR char_length(btrim(rejection_reason)) BETWEEN 1 AND 1000
    ),
    work_order_id UUID UNIQUE REFERENCES maintenance_work_orders(id) ON DELETE SET NULL,
    idempotency_key TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reports_status ON maintenance_reports (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_reporter ON maintenance_reports (reporter_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_asset ON maintenance_reports (ac_id, created_at DESC);
