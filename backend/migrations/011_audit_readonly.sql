-- 011_audit_readonly.sql — Phase 7 audit hardening (Plan #17, phase-7 task 3).
-- Run AFTER 010_qr_labels.sql. Additive and safe to re-run.
--
-- 1. Filter indexes for GET /api/v1/audit (actor + action + date range).
-- 2. Read-only guarantee: audit rows may never be rewritten or removed by
--    the application (retain >= 1 year — the app has no DELETE path at all).
--    * BEFORE UPDATE trigger: role-independent, mirrors the maintenance_events
--      append-only guard from 008_work_orders.sql.
--    * REVOKE UPDATE/DELETE: recorded in the ACL so it takes effect for any
--      non-superuser app role (dev/tests connect as superuser postgres, which
--      bypasses ACLs — test cleanup DELETEs keep working; the UPDATE trigger
--      still blocks rewrites there).

-- ── indexes ─────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_audit_actor
    ON audit_logs (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_action
    ON audit_logs (action, created_at DESC);

-- ── append-only: no rewriting audit history ─────────────────────────────────
CREATE OR REPLACE FUNCTION audit_logs_block_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only (UPDATE forbidden)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_logs_block_update ON audit_logs;
CREATE TRIGGER trg_audit_logs_block_update
    BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_block_update();

-- ── revoke write access for the app role(s) ─────────────────────────────────
DO $$
BEGIN
    REVOKE UPDATE, DELETE ON public.audit_logs FROM PUBLIC;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'audit_logs PUBLIC revoke skipped: %', SQLERRM;
END $$;

DO $$
DECLARE
    r TEXT;
BEGIN
    FOREACH r IN ARRAY ARRAY['acms_app', 'authenticator', 'anon', 'authenticated', 'service_role']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('REVOKE UPDATE, DELETE ON public.audit_logs FROM %I', r);
        END IF;
    END LOOP;
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'audit_logs role revoke skipped: %', SQLERRM;
END $$;
