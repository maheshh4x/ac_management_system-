-- 012_asset_report_indexes.sql — Phase 7 report date filters (Plan #17, phase-7 task 2).
-- Run AFTER 011_audit_readonly.sql. Additive and safe to re-run.
--
-- Every /reports/assets-* endpoint accepts `?from&to` filtered on
-- acs.installation_date (see app/api/v1/reports.py::_asset_report_where), and
-- /reports/due-for-maintenance filters next_service_date. `next_service_date`
-- has been indexed since 002 (idx_acs_next_service); installation_date had no
-- index at all, so a narrow installation window forced a full scan of the acs
-- table. One btree covers both directions of the range filter and keeps the
-- report latency flat as the fleet grows past 100k units.
--
-- Measured with `scripts/seed_perf.py` (10k assets) — see docs/perf.md.

CREATE INDEX IF NOT EXISTS idx_acs_installation_date
    ON acs (installation_date)
    WHERE installation_date IS NOT NULL;
