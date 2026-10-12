-- 006_profiles_approval.sql — Phase 1.3: signup approval workflow, extended
-- profile fields, notifications. Additive + idempotent; run after 005.
-- Rollback: DROP TABLE notifications; ALTER TABLE profiles DROP COLUMN ...
-- (columns are additive; dropping them is safe only before data is relied on).

-- ── profiles: approval + extended identity fields ───────────────────────────
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'PENDING';
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS requested_role TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS address TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS staff_id TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS designation TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS assigned_blocks TEXT[];
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS years_experience INT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS certification_no TEXT;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS reporting_manager_id UUID REFERENCES profiles(id);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS approved_by UUID REFERENCES profiles(id);
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ;
ALTER TABLE profiles ADD COLUMN IF NOT EXISTS rejection_reason TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_status_check') THEN
        ALTER TABLE profiles
            ADD CONSTRAINT profiles_status_check
            CHECK (status IN ('PENDING','APPROVED','REJECTED'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_staff_id_unique') THEN
        ALTER TABLE profiles ADD CONSTRAINT profiles_staff_id_unique UNIQUE (staff_id);
    END IF;
END $$;

-- Rows that existed before the approval workflow pre-date it: grandfather them
-- as APPROVED so existing deployments keep working.
UPDATE profiles SET status = 'APPROVED'
WHERE status = 'PENDING' AND created_at < NOW() - INTERVAL '1 hour';

CREATE INDEX IF NOT EXISTS idx_profiles_status ON profiles(status);
CREATE INDEX IF NOT EXISTS idx_profiles_role ON profiles(role);

-- ── notifications ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    entity_type TEXT,
    entity_id TEXT,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read, created_at DESC);
