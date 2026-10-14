# Phase 7 Report — Dashboards, reports, audit, notifications, assistant

**Status: COMPLETE** (all offline-verifiable items green; live end-to-end and the physical
QR/phone work remain blocked on the user, as in previous phases).
Date: 2026-10-07. Depends on Phases 3–5 (assets, maintenance, movements being real); ran
after Phase 6.

Goal met: every number on the dashboards now comes from SQL (role-scoped), the five
`/reports/*` endpoints serve the charts **and** CSV downloads behind `VIEW_REPORTS`, the
admin audit page answers "who changed this AC, when, from what" against a read-only,
redacted, cursor-paginated trail, the assistant widget talks to the authenticated
`POST /api/v1/chat`, and **no mock data remains in the app bundle**.

## Progress / what was built

### Backend (plan tasks 1–5)

1. **`/dashboard/summary` role-scoped (task 1)** — `app/api/v1/system.py::dashboard_summary`
   rewritten around a `scope` label: `all` (SUPER_ADMIN / unscoped manager), `blocks`
   (manager limited by `profile.assigned_blocks`), `mine` (technician: blocks where they
   hold work orders + a `my_jobs` section), `counts` (viewer: aggregates only). Added the
   plan's `overdue`, `due_7d`, `mttr_hours`, `by_status`, `by_type`, `by_block`,
   `capacity_by_block` (top 20, keys `block` / `capacity_ton` / `count`) and `my_jobs`.
   `?include_detail=0|false|no` downgrades *any* role to counts-only (suppresses the
   `overdue` rows and `my_jobs`). Distributions are always computed — they are aggregates,
   not row detail — so viewer charts keep working.
2. **`/reports/*` (task 2)** — `app/api/v1/reports.py` rewritten: the five contract
   endpoints (`assets-by-block`, `assets-by-type`, `capacity`, `due-for-maintenance`,
   `maintenance-trend`) share `_report_args()`, `_csv_response()`, `_run_report_query()`,
   `_asset_report_where()` and `_wo_scope()`, all gated on **`VIEW_REPORTS`** with no
   second export permission (a role that can see a chart can download it). Every endpoint
   takes `?from&to&block&format=csv`. Trend keeps `{monthlyTrend, priorityDistribution,
   stats}` and is capped at `MAX_TREND_MONTHS = 24` (else 400 `INVALID_RANGE`); the row
   reports return `{items, count}` (due adds `limit`; `DUE_JSON_LIMIT=500`,
   `DUE_CSV_LIMIT=5000`); unsupported `format` → 400 `UNSUPPORTED_FORMAT`. The
   `build_*_query` helpers are deliberately public so `scripts/seed_perf.py` benchmarks the
   exact production SQL. Rate prefixes `reports-*` are 120/min.
3. **`/audit` (task 3)** — `list_audit` rewritten: filters `entity_type`, `user` (a
   non-UUID → 400 `INVALID_FILTER`), `action` (case-insensitive contains), `q`
   (action / entity id / actor name), `from` / `to` (→ 400 `INVALID_DATE`); `LEFT JOIN
   profiles` for `actor_name` / `actor_role`; keyset cursor on `(created_at, id)` with a
   base64url `next_cursor` (bad cursor → 400 `INVALID_CURSOR`) **and** classic
   `page`/`page_size` when no cursor is passed; `old_values`/`new_values` are redacted at
   read time by the new `app/services/audit_service.py::redact_values` (`***REDACTED***`
   marker for password/secret/token/api_key-class keys) as defence in depth over the
   write-side redaction; `format=csv` is gated **in code** on `VIEW_REPORTS` (403
   otherwise, since `VIEW_AUDIT` alone must not imply export), capped at
   `AUDIT_CSV_LIMIT=5000`, UTF-8 **with BOM**, columns
   `created_at,action,entity_type,entity_id,actor_name,actor_role,user_id,ip_address,old_values,new_values`,
   `attachment; filename="audit_log.csv"`. No rate limit on the read (it is a paginated
   dashboard view, not a hammering surface).
4. **Migration `011_audit_readonly.sql`** — `idx_audit_actor`, `idx_audit_action`, an
   append-only `UPDATE` trigger, and `REVOKE UPDATE, DELETE` on `audit_logs` for the app
   role (superuser `DELETE` still works, which is how `seed_perf.py clean` empties its own
   rows). `idx_audit_created` already existed from `002_industrial_foundation.sql`.
5. **`/chat` (task 4)** — new `app/api/v1/chat.py` (`chat_v1_bp`), decorators ordered
   `@require_auth` → `@require_permission("VIEW_ASSET")` → `@rate_limit(30, 60, "chat")`.
   Deterministic intents only (rule parser in `app/services/question_parser.py` +
   `app/services/query_service.py`); `INTENT_PERMISSIONS` maps intent → required
   permission (default `VIEW_ASSET`) so an intent can be locked per role and the tests can
   monkeypatch it to prove 403; the new `MY_JOBS` intent is identity-scoped and runs
   *before* the generic branch, so a caller only ever sees their own work orders
   regardless of role; list-shaped `data` is capped at `DATA_CAP=50` while the answer text
   still reports the true total. Errors: `QUESTION_REQUIRED`, `QUESTION_TOO_LONG`
   (> 500 chars), 403 `FORBIDDEN` on an intent the caller's role lacks, 429 `RATE_LIMITED`
   from the app-wide handler. Envelope `{question, intent, answer, data, latency_ms}`.
   **The legacy anonymous `app/routes/chat.py` was deleted** and `chat_bp` unregistered.
6. **Notifications (task 5)** — `list_notifications` gained `?since=` (ISO date/datetime,
   space-tolerant `+` repair, → 400 `INVALID_DATE`) which filters both the items and the
   pagination `total`; `unread_count` deliberately stays global. The FE keeps its 60 s
   poll (the plan's "SSE **or** 60 s polling" — polling chosen, no new transport).
7. **Email (task 5)** — new `app/services/email.py`, wired **centrally** into
   `app/services/notify.py::notify()` so every future notification gets it for free.
   `EMAIL_TYPES` = `ACCOUNT_APPROVED`, `ACCOUNT_REJECTED`, `JOB_ASSIGNED`,
   `MOVEMENT_APPROVED`, `MOVEMENT_REJECTED`; `EMAIL_ENABLED` defaults **off**, SMTP_* is
   env-driven, and it is best-effort — a send failure is logged and **never** raised into
   the request path.
8. **Migrations 011 + 012 applied to the localhost:5433 test DB only** (011 was written
   before being applied; 012 is additive and was added after a perf run showed the
   `?from&to` installation-date filter had no index at all). Live Supabase application is
   blocked on credential rotation.
9. **Contracts pinned** — `backend/openapi.yaml` synced (trend params, the four new report
   paths, `/chat`, the `/audit` params, `/notifications?since=`, dashboard role-scope);
   `tests/test_openapi.py` + `tests/test_route_auth_coverage.py` green.

### Backend performance (plan task 2's `< 300 ms on 10k assets`)

- **New `backend/scripts/seed_perf.py`** — subcommands `seed` / `bench` / `clean`.
  Deterministic (`random.Random(20261007)`), everything prefixed `PERF-`, actor profile
  `90000000-…-000000000001` (SUPER_ADMIN). `bench` measures HTTP p50/p95/max through
  `app.test_client()` (full auth → permission → SQL → envelope path) **and** runs
  `EXPLAIN (ANALYZE, BUFFERS)` on the real `build_*_query` builders, then writes
  `docs/perf.md`. `seed` always `clean`s first, so runs are idempotent; `bench` refuses if
  the table has fewer than 5000 rows; `clean` runs after `bench` to keep the test DB lean.
- **`docs/perf.md` recorded** — dataset 10,000 ACs / 8,000 work orders / 40,000 audit rows
  over 8 blocks × 6 floors × 10 rooms. **All 11 HTTP endpoints are p95 < 300 ms** (worst
  is `maintenance-trend` at p50 118 / p95 181 ms; `dashboard/summary` p95 82 ms; audit
  ≤ 45 ms). EXPLAIN ANALYZE on all eight report queries, raw plans included.
- **New migration `012_asset_report_indexes.sql`** — `idx_acs_installation_date`
  (`WHERE installation_date IS NOT NULL`). All three asset reports take `?from&to` on
  `installation_date` and that range had **no index at all before Phase 7**, so any window
  had to read the whole table. With `012` a narrow window plans a
  `Bitmap Index Scan on idx_acs_installation_date` — **1.0 ms execution, no seq scan**
  (full plan in `docs/perf.md`), versus the 11.0 ms full-table `Seq Scan on acs` that the
  unfiltered query still uses (which the doc flags ⚠️ and explains as the correct plan for
  10k rows).

### Frontend (plan tasks 1–6)

1. **Mock-fed charts replaced (task 1)** —
   - `MaintenanceTrendChart` had a hardcoded `DEFAULT_TREND_DATA` array (Apr–Sep fake job
     counts) that rendered whenever the caller passed no data — which the admin dashboard
     did. The constant is **deleted** (empty array default → the existing empty state) and
     `admin/dashboard/page.tsx` now feeds it from `useMaintenanceTrend()`. The card
     subtitle was corrected to describe what the chart actually shows.
   - `ACStatusChart`, `ACTypeChart`, `ACCapacityChart`, `BuildingDistributionChart` were
     already on `useDashboardSummary` (Phase 3/5) and are unchanged.
   - `useMaintenanceTrend(filters?)` now threads `?from&to&block` and keys the query on the
     filter object, so the window actually re-queries.
   - `EnergyPlaceholderChart` was **deleted** (it rendered "no data" copy for a schema that
     does not exist; task 5). `SmartCampusCard` is orphaned — it is not imported anywhere,
     so it ships no code and no numbers (left untouched per the pre-phase survey).
   - `/admin/*` is SUPER_ADMIN-only and both report-capable roles hold `VIEW_REPORTS`, so
     no extra permission gate was needed on the trend query.
2. **Reports module (task 2)** — `ReportsAnalyticsModule` gained a **reporting window**
   (`from`/`to` date inputs) and a report picker + **server-side CSV**:
   - `src/lib/api/reports.ts` (new) — `ReportFilters`, `reportQueryString()`,
     `CSV_REPORTS`, `fetchReportCsv(name, filters)` via `apiBlob` (the API is
     Bearer-authenticated, so a plain `<a href>` would arrive unauthenticated), and
     `reportFilename()`.
   - Header controls are now *report select* + **Download CSV**, hitting
     `?format=csv` on the same window/block the charts are showing; the raw asset table
     keeps its own row-level CSV (those are the exact rows on screen, so it is a client
     export by design).
   - The date inputs **constrain each other to 24 months** (`shiftMonths` + `min`/`max`)
     so the pickers cannot build a window `_trend_window` rejects with 400
     `INVALID_RANGE`.
   - **Print stylesheet** (plan: "print stylesheet") — `globals.css` gained an
     `@media print` block that un-clips the `h-screen overflow-hidden` shell (otherwise
     paper gets one viewport), drops `[data-print="hide"]` / `.no-print` chrome, forces
     full-width tables with `thead` repeated across pages, and avoids breaking rows.
     Markers were added to the sidebar, top bar, assistant widget, technician tab bar, the
     reports header/filter bar and the table controls.
   - **Bug fixed on the way:** `PRIORITY_COLORS` was keyed `Low/Medium/High/Critical` but
     the API emits `PRIORITY_ORDER` verbatim (`LOW/MEDIUM/HIGH/CRITICAL`), so every
     priority slice silently fell through to the grey default. Keys corrected.
3. **Audit page (task 3)** — `admin/audit/page.tsx` was 100% `mockAuditLogs` and is now a
   full server view: new `src/lib/api/audit.ts` (`useAuditLogs` with `keepPreviousData`,
   `auditQueryString`, `fetchAuditCsv`) plus debounced `q`, entity chips matching the eight
   `entity_type`s the writer actually produces, an action `<select>` grouped by domain, an
   actor `<select>` fed by `GET /users`, and `from`/`to` date inputs — all pushed to the
   server (nothing is filtered in the browser). Summary tiles are now honest
   (server `pagination.total`, rows on this page, distinct actors/actions *on this page*)
   instead of counts over a mock array. Loading skeleton, error-with-Retry, empty state,
   `page X of Y` prev/next, and a real CSV download. The `Details` column renders a compact
   `field: before → after` diff of the (redacted) `old_values`/`new_values`, and styling is
   keyed on `entity_type` so new actions stay legible without a CSS change. The page reset
   happens by adjusting state during render (react.dev "You Might Not Need an Effect") —
   the React-Compiler rule rejects `setState` inside an effect.
4. **Assistant (task 4)** — `AssistantWidget` no longer echoes canned answers. New
   `src/lib/api/chat.ts` (`useAskAssistant()` → `POST /api/v1/chat`, `MAX_QUESTION_LENGTH`
   500) and the widget now shows the server's `answer`, renders `data` as a small table
   when it is row-shaped (array, or `{summary, items}` from `MY_JOBS`; capped at 6 columns
   × 8 rows), shows a typing indicator while pending, disables submit while in flight, and
   maps failures to human sentences — **429 gets its own polite copy** ("I am getting a lot
   of questions right now…"), plus distinct text for `QUESTION_TOO_LONG`, `FORBIDDEN`,
   `NETWORK_ERROR` and `TIMEOUT`. It is mounted only in `DashboardLayout`, so the caller is
   always authenticated.
5. **Placeholders removed (task 5)** — `EnergyPlaceholderChart` deleted (see above); the
   reports *Energy* tab already states the missing backend fields rather than inventing
   numbers, and no fake energy figure ships.
6. **Landing (task 6)** — the four metric cards in `DashboardPreview` are live
   (`getCampusStats` → `/dashboard/summary`) and show `—` for anonymous visitors; the
   stylised dashboard mockup **below** them carries a caption: *"Illustrative preview —
   the mockup's counts and rows are sample content, not live figures."*
   `SystemOverview` has no statistics (heading + copy + CTA only), so nothing to label
   there.
7. **Mocks retired (plan "Done when")** —
   - **`src/lib/user-service.ts` deleted** (the last localStorage business-data store).
     `ManagedUser` / `ApprovalStatus` moved to `src/lib/adapters/user.ts` — they are real
     types produced by `mapProfileToManagedUser` and consumed by `ApprovalQueue`, only the
     store was mock. `ApprovalQueue` and `adapters/user.ts` import them from there.
   - **`src/lib/mock-data.ts` deleted.**
   - The Sidebar's "Pending Approvals" badge, which read `getUsers()` out of localStorage,
     now uses `useApprovals('PENDING')` → `GET /approvals?status=PENDING`
     (`pagination.total`), gated `enabled: hasPermission(…, 'MANAGE_USERS')` so roles
     without it don't collect a 403. `useApprovals` gained an optional `enabled` option
     (backwards compatible) and its existing `["approvals"]` invalidation keeps the badge
     fresh after approve/reject.
   - `downloadBlob` moved from `qr-service.ts` to `src/lib/utils.ts` (report/audit CSVs
     need it too); `qr-service` re-exports it so existing importers are unchanged.
   - **`eslint.config.mjs` shrank**: the `src/lib/mock-*` ignore, the `user-service.ts`
     localStorage exemption, and three entries from the lint-debt downgrade list
     (`admin/dashboard`, `admin/maintenance`, `manager/maintenance` — all now clean) are
     gone; comments record the Phase 7 removals alongside the previous phases'.

## Files changed (created C / modified M / deleted D)

### Backend
C: `migrations/011_audit_readonly.sql`, `migrations/012_asset_report_indexes.sql`,
`app/api/v1/chat.py`, `app/services/email.py`, `scripts/seed_perf.py`,
`tests/test_reports.py` (21), `tests/test_chat.py` (9), `tests/test_notifications_email.py` (16);
M: `app/api/v1/system.py` (dashboard scope + audit rewrite + audit CSV),
`app/api/v1/reports.py` (full rewrite), `app/api/v1/users.py` (`?since=`),
`app/services/audit_service.py` (redaction), `app/services/notify.py` (email wiring),
`app/services/question_parser.py` + `app/services/query_service.py` (`MY_JOBS`),
`app/core/__init__.py` (`paginated(next_cursor=…)`), `openapi.yaml`,
`tests/test_audit.py` (13), `tests/test_dashboard_summary.py` (7);
D: `app/routes/chat.py` (legacy anonymous chat — replaced by `/api/v1/chat`);
Docs: `docs/perf.md` (generated), this report.
`tests/test_openapi.py` + `tests/test_route_auth_coverage.py` untouched and green.

### Frontend
C: `src/lib/api/chat.ts`, `src/lib/api/audit.ts`, `src/lib/api/reports.ts`;
M: `src/lib/api/maintenance.ts` (trend filters), `src/lib/api/users.ts`
(`useApprovals({enabled})`), `src/lib/adapters/user.ts` (`ManagedUser`/`ApprovalStatus`),
`src/lib/utils.ts` + `src/lib/qr-service.ts` (`downloadBlob` move),
`src/components/layout/AssistantWidget.tsx` (rewritten on `/chat`),
`src/components/layout/Sidebar.tsx` (real badge), `src/components/layout/TopNavbar.tsx`
+ `DashboardLayout.tsx` (print markers),
`src/app/(portal)/admin/audit/page.tsx` (full rewrite),
`src/app/(portal)/admin/dashboard/page.tsx` (real trend),
`src/components/features/charts/MaintenanceTrendChart.tsx` (fake default removed),
`src/components/features/reports/ReportsAnalyticsModule.tsx` (window + server CSV +
print), `src/components/features/ApprovalQueue.tsx` (type import),
`src/components/landing/DashboardPreview.tsx` (illustrative caption),
`src/app/globals.css` (print stylesheet), `eslint.config.mjs`;
D: `src/lib/user-service.ts`, `src/lib/mock-data.ts`,
`src/components/features/charts/EnergyPlaceholderChart.tsx`.

## Mock / direct-DB removal (grep evidence)

- `mock-data|mockUsers|mockMaintenance` across `src/**/*.{ts,tsx}` → **2 hits, both
  historical mentions inside doc comments** (`types/maintenance.ts:7`,
  `types/movement.ts:3`). Widening to `user-service|DEFAULT_TREND_DATA` adds 3 more
  comment-only hits (`adapters/user.ts:13`, `MaintenanceTrendChart.tsx:9`,
  `admin/dashboard/page.tsx:48`). **Zero imports; no test imports them either.**
- `src/lib/mock-*` → **no files**.
- `localStorage.` (actual call sites) → exactly **one file**, `src/lib/storage/index.ts`
  (the designated module). `user-service.ts`, the last grandfathered exemption, is gone —
  that entry is removed from `eslint.config.mjs`.
- Direct-DB (`from 'pg'`, `src/lib/server`, `/app/api/` route handlers) across `src` →
  **0 matches** (gone since Phase 2; nothing reintroduced).

## API integrations (endpoint → screen)

| Endpoint | Consumers |
|---|---|
| `GET /dashboard/summary` (+`?include_detail=0`) | admin/manager dashboards, stat cards, landing metric cards |
| `GET /reports/maintenance-trend?from&to&block` | `ReportsAnalyticsModule` trend + priority + KPIs; `admin/dashboard` Maintenance Jobs Trend |
| `GET /reports/{assets-by-block,assets-by-type,capacity,due-for-maintenance,maintenance-trend}?format=csv` | reports **Download CSV** (same window/block as the charts) |
| `GET /audit?entity_type&user&action&q&from&to&page&page_size` | `admin/audit` table, tiles, pagination |
| `GET /audit?…&format=csv` | `admin/audit` **Export CSV** |
| `POST /chat` | `AssistantWidget` |
| `GET /approvals?status=PENDING` | `ApprovalQueue` **and** the Sidebar badge |
| `GET /users` | `admin/audit` actor dropdown |
| `GET /notifications?since=` (60 s poll) | notification bell (unchanged FE behaviour) |

## Validation results

Backend (`TEST_DATABASE_URL` → the `acms-test` Postgres container on **5433**; migrations
011 and 012 applied with the temp-script pattern, then removed):
- **Full suite: 278 collected, 0 failures / 0 errors / 0 skipped** (216 Phase-6 baseline →
  **+62**; measured via `--junitxml` because `-q` prints no summary line). Breakdown of the
  touched files: `test_reports.py` 21, `test_notifications_email.py` 16, `test_audit.py` 13,
  `test_chat.py` 9, `test_dashboard_summary.py` 7.
- Plan **Verify** subsets: `pytest tests/test_reports.py tests/test_audit.py -q` → **34
  passed** (21 + 13); role-scope coverage lives in `test_dashboard_summary.py` as
  `test_summary_technician_scopes_to_own_jobs`, `test_summary_manager_scopes_to_assigned_blocks`
  and `test_summary_viewer_gets_counts_only` (viewer → `scope: "counts"`, `overdue: []`,
  `my_jobs: None`, so no row-level technician detail reaches them) plus
  `test_summary_include_detail_zero_downgrades_viewer`.
- `ruff check app tests` → all checks passed; `black --check app tests` → **76 files
  unchanged**; `ruff check scripts` + `black --check scripts` → clean.
- `ruff check .` → **8 errors, all pre-existing** in the two backend-root scratch scripts
  (`diagnose_db.py` 6, `test_endpoints.py` 2) — untouched on purpose (user tooling).
- `tests/test_openapi.py` + `tests/test_route_auth_coverage.py` → green.
- **Perf** (`scripts/seed_perf.py seed --assets 10000 --work-orders 8000 --audit 40000`
  → `bench --runs 40 --out ..\docs\perf.md` → `clean`): all 11 endpoints **p95 < 300 ms**;
  test DB restored afterwards (20 ACs / 2 audit rows / 0 work orders at rest).

Frontend (`cd Frontend`, final tree):
- `npx tsc --noEmit` → **exit 0**
- `npx eslint . --quiet` → **0 errors**; `npx eslint .` → **41 warnings** (Phase 6: 46) —
  five config entries went away with the retired files (the `mock-*` ignore, the
  `user-service.ts` exemption and three lint-debt downgrade entries), and **none of the
  files touched this phase produce a warning**.
- `npx vitest run` → **14 passed** (10 adapters, 4 offline-queue)
- `npm run build` (placeholder `NEXT_PUBLIC_*`) → **exit 0**, **40 routes + `ƒ Proxy`**.
  Earlier phase reports logged 36 because they counted only the truncated tail of the build
  output; this run counted the whole table (39 `page.tsx` + the auto-generated
  `/_not-found`). **No route file was created or deleted this phase** — the only `page.tsx`
  writes are the rewrites of `admin/audit` and `admin/dashboard`.
- `grep -R "mock-data\|mockUsers\|mockMaintenance" src` → nothing outside doc comments.

Plan **Verify** section status:
- `pytest tests/test_reports.py tests/test_audit.py -q` → ✓ 34 passed.
- "seed 10k assets → p95 of each report < 300 ms" → ✓ recorded in `docs/perf.md`.
- `grep -R "mock-data…" frontend/src` → ✓ nothing but two historical doc comments (no
  code, no test references).

**Done-when walk-through:** no mock data is imported anywhere in the bundle (the last
store, `user-service.ts`, and `mock-data.ts` are deleted; the sidebar badge, audit page and
assistant all hit real endpoints). Dashboards are role-scoped aggregates computed in SQL —
a viewer receives `scope: "counts"` with no `overdue` rows, a technician gets only their
own jobs, a manager only their assigned blocks (pinned by the role-scope tests). The audit
page answers "who changed this AC, when, from what": filters on entity/actor/action/date,
actor name + role joined from `profiles`, and the payload column shows
`status: PENDING → APPROVED` style diffs with password-like values redacted — on a table
the app role can no longer `UPDATE` or `DELETE`.

## Deviations & notes

1. **Audit UI uses offset paging, not the cursor** — the backend supports both (cursor
   keyset when `cursor` is passed, `page`/`page_size` otherwise). The UI shows
   "page X of Y", which keyset pagination cannot provide, so it uses offsets; the cursor
   path is implemented and covered server-side, and `next_cursor` is typed on the envelope
   for future use. CSV export ignores paging entirely (one capped download of the filter).
2. **Audit CSV filename is built client-side** — `apiBlob()` returns the body only, so the
   download is named `audit_log_YYYY-MM-DD.csv` rather than reading the server's
   `Content-Disposition`. Same bytes, same headers on the wire.
3. **Reporting-window semantics differ per report** — `?from&to` filters
   `installation_date` on the asset reports, `next_service_date` on due-for-maintenance,
   and bounds the month buckets on the trend (max 24 months). The filter bar says so
   ("Date range scopes the trend chart and CSV exports; block/type scope everything")
   rather than pretending one date column drives the whole page.
4. **Date pickers constrain each other to 24 months** — a UX guard for the backend's
   `MAX_TREND_MONTHS`; native `min`/`max`, so the error surface is avoided rather than
   merely explained.
5. **Trend chart only exists on `/admin/dashboard`** (SUPER_ADMIN-only route) and reports
   pages (SUPER_ADMIN + FACILITY_MANAGER) — both hold `VIEW_REPORTS`, so no `enabled` gate
   was needed. VIEWER's sidebar does link to `/manager/reports`, which lands on the
   existing 403 wall; that is pre-existing behaviour outside this phase's scope.
6. **`PRIORITY_COLORS` key case fixed** — a pre-existing defect (UI used
   `Low/Medium/High/Critical`, API sends `LOW/…`), so priority slices were all grey before.
7. **`SmartCampusCard` left untouched** — orphaned (not imported), so it contributes no
   code and no numbers to the bundle; the plan's task-1 list is satisfied without deleting
   a file the survey agreed to leave.
8. **Chat = new `/api/v1/chat`, legacy `/api/chat` removed**; the separate legacy
   `/api/dashboard*` routes stay because `tests/test_auth.py` asserts them (they are not
   part of the plan's "kill the mock data" list).
9. **Email is off by default** (`EMAIL_ENABLED`) and best-effort — no SMTP is configured
   in this environment, so nothing is sent during tests; the flag and env vars are ready
   for the operator.
10. **Notifications**: 60 s polling chosen over SSE (simpler, no new transport, already
    wired); `?since=` is the new server-side piece.
11. **`gen:api` not re-run** — the FE hooks for the new endpoints are hand-typed
    (`chat.ts`, `audit.ts`, `reports.ts`), the established pattern; `schema.d.ts` will be
    refreshed when the backend is running for a contract regen.
12. **Perf tooling** — `seed_perf.py` was extended mid-phase to add an installation-window
    query (to exercise index 012) and to count seq scans on *any* large table
    (`acs`/`audit_logs`/`maintenance_work_orders`), not just `acs`/`audit_logs`, so the
    doc's "seq scan on a large table?" column is honest. The doc's index attributions were
    corrected after verification: `idx_acs_next_service` and `idx_wo_scheduled` come from
    `002_industrial_foundation.sql`, `idx_audit_created` from `002`, only
    `idx_audit_actor`/`idx_audit_action` are from `011`, and `idx_acs_installation_date`
    is `012`.

## Remaining blockers

1. **Credential rotation** (yours) → then: apply migrations **001–012** + the Supabase auth
   trigger to the live DB, and run the live end-to-end (login per role → dashboard scope →
   reports with a date window → CSV download → audit filter by actor → assistant question →
   approval email once SMTP is configured). Migrations 011 and 012 were applied to the
   **localhost:5433 test DB only**.
2. **Physical phone/HTTPS scan test** (plan line — yours; unchanged since Phase 6).
3. **Playwright e2e** — no playwright config / `e2e/` directory exists in the repo, so the
   plan's e2e lines remain unrunnable (behaviour covered by the pytest suites).
4. `docs/private-data/*.xlsx` acceptance dataset still absent (noted since Phase 1).
5. Baseline git commit is your task (no git used by me).

## Exact next task

**Phase 8 — Testing & hardening** (`acms-plan/phases/phase-8-testing-hardening.md`): read
the phase file first, then run its backend/frontend/verify steps against the now-complete
Phases 0–7, keeping the same conventions (no git, no live-DB work, `ruff`/`black`/`tsc`/
`eslint --quiet`/`vitest`/`build` green, 278 backend tests as the floor).
