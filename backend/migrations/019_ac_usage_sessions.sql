-- 019_ac_usage_sessions.sql — AC usage-session ledger (QR ON/OFF control).
--
-- A usage session records one continuous ON period of one AC asset: who
-- started it, when it started, and when (if ever) it stopped. Sessions are
-- purely record-keeping: starting or stopping one never requires an approval
-- step, and the audit trail (SESSION_STARTED / SESSION_STOPPED via
-- app/services/audit_service.py) is written best-effort AFTER the business
-- transaction commits, exactly like movements and maintenance.
--
-- Integrity rules enforced by the schema itself:
--   * ac_id references acs(ac_id) ON DELETE CASCADE — deleting an asset
--     removes its sessions; a session can never outlive its asset;
--   * uq_open_session_per_asset — at most ONE open (ended_at IS NULL)
--     session per asset. A racing second start loses with a unique
--     violation, which the endpoint translates to 409 SESSION_ALREADY_OPEN
--     instead of a duplicate open session.
--
-- Additive and safe to re-run. Rollback: DROP TABLE IF EXISTS
-- ac_usage_sessions (cascades nothing else; audit_logs rows that reference
-- finished sessions by entity id remain as the historical record).

CREATE TABLE IF NOT EXISTS ac_usage_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ac_id TEXT NOT NULL REFERENCES acs(ac_id) ON DELETE CASCADE,
    started_by UUID REFERENCES profiles(id),
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    start_note TEXT,
    end_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_open_session_per_asset
    ON ac_usage_sessions (ac_id) WHERE ended_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_usage_sessions_asset_started
    ON ac_usage_sessions (ac_id, started_at DESC);
