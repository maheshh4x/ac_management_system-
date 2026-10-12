--═══════════════════════════════════════════════════════════════════════════
-- Phase 5 — Movements & approvals
--   * movement_comments       discussion on a request (requester/approvers)
--   * cancelled_at            who/when cancelled is covered by audit_logs;
--                             the timestamp is needed for list display
--   * completed_by            who actually relocated the unit
--   * one OPEN movement per asset (partial unique index on PENDING/APPROVED)
--   * (status, requested_at) index for the approval queues
--   * asset_location_history  append-only room changes written in the same
--                             transaction as the completion (the asset's room
--                             and this history can never disagree)
--═══════════════════════════════════════════════════════════════════════════

ALTER TABLE asset_movements
    ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS completed_by UUID REFERENCES profiles(id);

CREATE INDEX IF NOT EXISTS idx_mov_status_requested
    ON asset_movements (status, requested_at);

-- ── at most one open (PENDING/APPROVED) movement per asset ──────────────────
-- Existing data may predate the rule: keep the newest open request per asset
-- and cancel the older duplicates so the invariant holds before the unique
-- index is created (they are superseded, not rejected).
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY ac_id ORDER BY requested_at DESC, id DESC
           ) AS rn
      FROM asset_movements
     WHERE status IN ('PENDING', 'APPROVED')
       AND ac_id IS NOT NULL
)
UPDATE asset_movements m
   SET status = 'CANCELLED',
       cancelled_at = NOW(),
       updated_at = NOW()
  FROM ranked r
 WHERE r.id = m.id
   AND r.rn > 1
   AND m.status IN ('PENDING', 'APPROVED');

CREATE UNIQUE INDEX IF NOT EXISTS uq_mov_one_open
    ON asset_movements (ac_id)
    WHERE status IN ('PENDING', 'APPROVED') AND ac_id IS NOT NULL;

-- ── request discussion ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS movement_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    movement_id UUID NOT NULL REFERENCES asset_movements(id) ON DELETE CASCADE,
    author_id UUID REFERENCES profiles(id),
    body TEXT NOT NULL CHECK (length(body) BETWEEN 1 AND 4000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_mov_comments_movement
    ON movement_comments (movement_id, created_at);

-- ── room-change ledger (written atomically with acs.room_id on complete) ────
CREATE TABLE IF NOT EXISTS asset_location_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ac_id TEXT REFERENCES acs(ac_id) ON DELETE SET NULL,
    from_room_id TEXT REFERENCES rooms(room_id),
    to_room_id TEXT REFERENCES rooms(room_id),
    movement_id UUID REFERENCES asset_movements(id) ON DELETE SET NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_loc_hist_ac
    ON asset_location_history (ac_id, at DESC);
