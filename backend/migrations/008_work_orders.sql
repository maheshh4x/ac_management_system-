-- 008_work_orders.sql — Phase 4: server-enforced work-order lifecycle.
--
-- 1. Lifecycle columns on maintenance_work_orders (assigned/accepted/escalated/
--    completed_by + sync idempotency + legacy row provenance).
-- 2. maintenance_events  — append-only timeline; also the idempotency ledger
--    for POST /maintenance/sync (unique client_mutation_id).
-- 3. maintenance_comments — threaded notes (optional photo URLs).
-- 4. Data migration: legacy `maintenance` rows copied into work orders as
--    COMPLETED, idempotent via legacy_maintenance_id. The legacy table is
--    NOT dropped (kept for rollback; retirement happens later).
--
-- Notes:
--   * `client_mutation_id` is unique but nullable: NULLs (online actions)
--     never conflict — Postgres treats NULLs as distinct in unique indexes.
--   * One job can receive many offline mutations over its lifetime, so the
--    replay ledger lives on maintenance_events (one row per applied action),
--     while maintenance_work_orders.client_mutation_id remembers the most
--     recent mutation applied to the row (plan-mandated column).

ALTER TABLE maintenance_work_orders
    ADD COLUMN IF NOT EXISTS assigned_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS escalated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS escalation_reason TEXT,
    ADD COLUMN IF NOT EXISTS completed_by UUID REFERENCES profiles(id),
    ADD COLUMN IF NOT EXISTS client_mutation_id TEXT,
    ADD COLUMN IF NOT EXISTS legacy_maintenance_id BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_wo_client_mutation
    ON maintenance_work_orders (client_mutation_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wo_legacy_maintenance
    ON maintenance_work_orders (legacy_maintenance_id);

-- assigned_to=me / technician list scans.
CREATE INDEX IF NOT EXISTS idx_wo_technician
    ON maintenance_work_orders (technician_id);

-- ── append-only event timeline + sync idempotency ledger ────────────────────
CREATE TABLE IF NOT EXISTS maintenance_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id UUID NOT NULL REFERENCES maintenance_work_orders(id) ON DELETE CASCADE,
    actor_id UUID REFERENCES profiles(id),
    event TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    note TEXT,
    client_mutation_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_me_client_mutation
    ON maintenance_events (client_mutation_id);

CREATE INDEX IF NOT EXISTS idx_me_work_order
    ON maintenance_events (work_order_id, created_at);

-- Enforce append-only: rows may never be rewritten (DELETE stays allowed so
-- FK CASCADE cleanup of an entire work order keeps working).
CREATE OR REPLACE FUNCTION maintenance_events_block_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'maintenance_events is append-only (UPDATE forbidden)';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_maintenance_events_block_update ON maintenance_events;
CREATE TRIGGER trg_maintenance_events_block_update
    BEFORE UPDATE ON maintenance_events
    FOR EACH ROW EXECUTE FUNCTION maintenance_events_block_update();

-- ── comments ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS maintenance_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    work_order_id UUID NOT NULL REFERENCES maintenance_work_orders(id) ON DELETE CASCADE,
    author_id UUID REFERENCES profiles(id),
    body TEXT NOT NULL,
    photos TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mc_work_order
    ON maintenance_comments (work_order_id, created_at);

-- ── data migration: legacy maintenance rows -> COMPLETED work orders ────────
-- Idempotent twice over: ON CONFLICT on legacy_maintenance_id guards replays,
-- and the migration runner itself refuses to re-run an applied file.
INSERT INTO maintenance_work_orders
    (ac_id, asset_uuid, maintenance_type, status, priority, scheduled_date,
     completed_at, remarks, legacy_maintenance_id, created_at)
SELECT m.ac_id, a.asset_uuid, m.maintenance_type, 'COMPLETED', 'MEDIUM',
       m.maintenance_date, m.created_at, m.remarks, m.maintenance_id, m.created_at
FROM maintenance m
LEFT JOIN acs a ON a.ac_id = m.ac_id
ON CONFLICT (legacy_maintenance_id) DO NOTHING;

-- Give every migrated row a visible origin event so the FE timeline is not
-- blank for historical jobs (idempotent via NOT EXISTS).
INSERT INTO maintenance_events (work_order_id, actor_id, event, from_status, to_status, note)
SELECT w.id, NULL, 'MIGRATED', NULL, 'COMPLETED',
       'Imported from legacy maintenance table'
FROM maintenance_work_orders w
WHERE w.legacy_maintenance_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM maintenance_events e
      WHERE e.work_order_id = w.id AND e.event = 'MIGRATED'
  );
