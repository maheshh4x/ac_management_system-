--═══════════════════════════════════════════════════════════════════════════
-- Phase 6 — QR codes & scanning
--   * qr_version   per-asset label version. The payload itself is immutable
--                  ({FRONTEND_URL}/a/{asset_uuid}), so a "regenerate" only
--                  re-issues the image/label and stickers already in the
--                  field keep resolving — qr_version records how many times
--                  a label has been re-issued (default 1 = first issue).
--                  Bumped by POST /api/v1/assets/<ref>/qr/regenerate and
--                  returned by GET /api/v1/assets/<ref>.
--═══════════════════════════════════════════════════════════════════════════

ALTER TABLE acs
    ADD COLUMN IF NOT EXISTS qr_version INTEGER NOT NULL DEFAULT 1;
