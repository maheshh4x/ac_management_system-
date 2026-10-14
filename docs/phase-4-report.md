# Phase 4 Report — Maintenance & technician jobs

**Status: COMPLETE** (all offline-verifiable items green; live end-to-end pending credential rotation).
Date: 2026-10-06. Depends on Phase 1 (API contract) and Phase 2 (frontend foundation); ran parallel with Phase 3.

Goal met: work orders have a server-enforced lifecycle (state machine + append-only event
timeline), the technician app (list, detail, offline queue) and the admin/manager maintenance
pages and reports run on it; browser storage holds job data only as the pending-sync mutation
queue (IndexedDB).

## Progress / what was built

### Backend (plan tasks 1–6)

1. **Migration `008_work_orders.sql`** — lifecycle columns on `maintenance_work_orders`
   (`assigned_at, accepted_at, escalated_at, escalation_reason, completed_by,
   client_mutation_id, legacy_maintenance_id`); **`maintenance_events`** (append-only,
   `uq_me_client_mutation` unique on `client_mutation_id`); **`maintenance_comments`**
   (FK `ON DELETE CASCADE` from work orders). **Data migration:** legacy `maintenance` rows
   copied into work orders as `COMPLETED`, idempotent via `legacy_maintenance_id`; legacy
   table kept (its history is excluded from `assets.maintenance_history` via
   `AND w.legacy_maintenance_id IS NULL` so nothing double-counts).
2. **State machine** — single table-driven module `app/services/work_orders.py`
   (`compute_changes` + `apply_transition`):
   `SCHEDULED →(assign) ASSIGNED →(accept) ASSIGNED* (accepted_at set) →(start) IN_PROGRESS
   →(complete) COMPLETED`; `cancel` from SCHEDULED/ASSIGNED/IN_PROGRESS (manager-only,
   reason); `escalate` from ASSIGNED/IN_PROGRESS (owner-or-manager, reason required,
   priority ladder Low→Medium→High→Critical). Rules enforced server-side: owner-or-manager
   for accept/start/complete/escalate, manager-only assign/cancel, terminal states
   immutable, `complete` requires `work_performed` (optional `parts_used/remarks`,
   `labor_hours` ≥ 0), and **in the same transaction** sets
   `acs.last_maintenance_date = today` / `next_service_date = today + interval`
   (SERVICE_INTERVALS: preventive/routine/service/emergency 90 d, gas refill 120 d,
   inspection/repair 180 d, installation 365 d, default 180 d). Every transition writes a
   `maintenance_events` row + `audit_logs` + a notification; `audit()`/notifications run
   post-commit so a side effect can never 500 a committed transition.
   Error codes: `TERMINAL_STATE`/`INVALID_TRANSITION`/`ALREADY_ACCEPTED`/`NOT_ACCEPTED` 409,
   `FORBIDDEN` 403, `TECHNICIAN_REQUIRED`/`WORK_PERFORMED_REQUIRED`/
   `INVALID_LABOR_HOURS`/`REASON_REQUIRED`/`INVALID_TECHNICIAN`/`UNSUPPORTED_ACTION` 400,
   `TECHNICIAN_NOT_FOUND`/`WORK_ORDER_NOT_FOUND` 404.
3. **Endpoints** (`app/api/v1/maintenance.py`, 13 routes, all `@require_auth` +
   `@require_permission`): list with `assigned_to=me`, `status`, `priority`, `from/to`
   scheduled date, `ac_id`, `q` (page_size ≤ 200) joined to technician name +
   make/model/room/floor/block; **TECHNICIAN scope** `technician_id = me OR created_by = me`;
   detail returning the full row + `events[]` (ASC) + `comments[]` + `created_by_name`;
   create (self-assign allowed, assigning someone else needs `ASSIGN_MAINTENANCE`, missing
   technician → SCHEDULED else ASSIGNED); six action routes assign/accept/start/complete/
   escalate/cancel each with its own rate-limit scope; comments (201, body ≤ 4000, photos ≤
   10); **PATCH rejects `status`** (`STATUS_ACTION_REQUIRED`) — transitions only via actions.
4. **Offline sync** — `POST /api/v1/maintenance/sync`: `{mutations:[{client_mutation_id,
   action, job_id, payload}]}`, each applied in its own **SAVEPOINT**, deduped against the
   events ledger; response `{results:[{client_mutation_id, result: applied|duplicate|
   rejected, code, message}], applied, duplicate, rejected}` — server-wins with the
   rejection reason echoed back.
5. **Due-job scan** — `python -m app.jobs.notify_due` creates notifications for jobs due
   ≤ 2 days and overdue (replaces `notifyDueTechnicianJobs`); smoke run exit 0.
6. **Legacy retired** — `app/routes/maintenance.py` (old `/api/maintenance*`) deleted;
   `auth/legacy_guard.py` keeps its permission mapping so stale clients get 403/410.
7. **Reports** — `GET /api/v1/reports/maintenance-trend` (`VIEW_REPORTS`): 6 zero-filled
   months `{month, fullMonth, total, Preventive, Repair, Emergency, Inspection}` bucketed by
   `COALESCE(scheduled_date, completed_at::date)`, `priorityDistribution[{name,value}]`,
   `stats {total, scheduled, assigned, in_progress, completed, cancelled, mtdCount,
   mttrDays, overdueCount, upcomingCount}`.
8. **Contracts pinned** — all 13 maintenance paths + the trend path documented in
   `backend/openapi.yaml`; `tests/test_openapi.py` (every v1 route documented) and
   `tests/test_route_auth_coverage.py` (auth markers on every v1 route) green.

### Frontend (plan tasks 1–5)

1. **API layer** — new `src/lib/api/maintenance.ts` hooks: `useJobs(filters)` (server
   filters, `keepPreviousData`), `useJob(id)` (detail + events + comments),
   `useCreateJob`, `useJobAction` (6 actions + offline queueing; invalidates job list/
   detail and — for `complete` — the asset caches so `last_maintenance_date`/`next_service_date`
   refresh), `useSyncQueue`, `useSyncNow`, `useDismissQueued`, `useAddComment`,
   `useMaintenanceTrend`. UI vocabulary moved to `src/lib/types/maintenance.ts`;
   `src/lib/adapters/job.ts` extended (`assigned_at`, `accepted_at`, `escalated_at`,
   `escalation_reason`, `created_by_name`, `work_performed` → camelCase) plus reverse
   helpers `toApiJobStatus`/`toApiJobPriority` for filter chips and forms.
2. **Offline queue** — `src/lib/api/offline-queue.ts`: **IndexedDB via `idb-keyval`**
   (key `acms.pending-job-sync`, in-memory fallback), `client_mutation_id =
   crypto.randomUUID()`, pure `mergeSyncResults` (applied/duplicate removed, rejected kept
   with `CODE: message`, unanswered stay pending). Job actions queue only when
   `navigator.onLine === false` or the request fails with a network/timeout `ApiError`;
   server rejections surface immediately. **The old localStorage key
   `nitttr_technician_offline_queue` is gone.**
3. **Pages rewired**
   - `technician/jobs` — `useJobs({assignedTo:'me'})`; status/date chips stay client-side
     over the fetched set.
   - `technician/jobs/[id]` — `useJob(id)` timeline (events + comments rendered from
     `maintenance_events`), asset panel re-pointed to `useAsset(job.acId)`, state-machine
     buttons (Accept when ASSIGNED & !`acceptedAt`, Start when accepted, Complete when
     IN_PROGRESS, Escalate) with server-error banner, completion form →
     `{work_performed, labor_hours: minutes/60, parts_used}`, "Add a note" → comments
     endpoint.
   - `technician/dashboard` — `useJobs({assignedTo:'me'})`; the `notifyDueTechnicianJobs`
     localStorage side effect removed (server job replaces it).
   - `technician/assets/[id]` — self-report via `useCreateJob` (self-assign, Repair/
     MEDIUM/today → router.push detail); history via `useJobs({acId: business key})`.
   - `admin|manager/maintenance` — server-side `q` (300 ms debounced) + status chips mapped
     through `toApiJobStatus`; stats counted over the fetched (filtered) set to preserve the
     old `getStats` semantics; Schedule Job → `useCreateJob` with assignee picker from
     `useUsers({role:'TECHNICIAN'})`; contextual action buttons per state (Assign…
     modal, Accept, Start, Complete… modal with required `work_performed`); detail modal
     from `useJob(selectedId)` showing `created_by_name` + the events timeline; all server
     rejections shown in a dismissible banner / inline in modals.
   - `ReportsAnalyticsModule` — `useMaintenanceTrend()` for the monthly BarChart, priority
     donut (client colour map) and KPI stats (optional-chained while loading).
   - `ACDetailView` — history from `useJobs({acId: ac.acId})` (business key).
   - `TopNavbar` — bell → `GET /api/v1/notifications` (**60 s poll**), mark-read /
     mark-all, `entity_type='maintenance'` deep links (technician → job detail, else →
     maintenance page); the 3 s localStorage sync interval replaced by the
     `SyncQueueMenu` badge (queue count, **Sync now**, `online`-event auto-replay,
     per-item `applied/duplicate/rejected` summary, rejected reason + Dismiss).
4. **Deletions** — `maintenance-data-service.ts`, `technician-data-service.ts`,
   `mockMaintenanceJobs` seed and the maintenance types (incl. now-dead
   `JobStatusHistory`/`JobCompletion`) from `mock-data.ts`; `eslint.config.mjs`
   grandfather entries for both services removed.

## Files changed (created C / modified M / deleted D)

### Backend
C: `migrations/008_work_orders.sql`, `app/services/work_orders.py`,
`app/api/v1/reports.py`, `app/jobs/notify_due.py`,
`tests/test_work_order_state_machine.py`, `tests/test_maintenance_v1.py`,
`tests/test_offline_sync.py`
M: `app/schemas/v1_schemas.py`, `app/api/v1/maintenance.py` (rewrite: list/detail/actions/
sync/comments), `app/api/v1/assets.py` (maintenance_history dedupe), `app/__init__.py`
(reports blueprint; legacy route removal), `backend/openapi.yaml` (13 maintenance paths +
trend);
 route/auth coverage pinned additionally by `tests/{test_openapi,test_route_auth_coverage}.py`
D: `app/routes/maintenance.py` (legacy `/api/maintenance*`); temp helpers
`_tmp_inspect.py`, `_tmp_probe_5433.py`, `_tmp_migrate_5433.py` (all removed)

### Frontend
C: `src/lib/types/maintenance.ts`, `src/lib/api/maintenance.ts`,
`src/lib/api/offline-queue.ts`, `src/lib/api/offline-queue.test.ts`,
`docs/phase-4-report.md`
M: `src/lib/adapters/job.ts` (mapping + reverse helpers), `src/lib/mock-data.ts`
(maintenance types/seed removed), `src/lib/technician-asset-service.ts` (optional `acId`),
`src/lib/api/schema.d.ts` (regenerated by `gen:api`),
`src/app/(portal)/technician/{jobs/page.tsx, jobs/[id]/page.tsx, dashboard/page.tsx,
assets/[id]/page.tsx}`, `src/app/(portal)/admin/maintenance/page.tsx`,
`src/app/(portal)/manager/maintenance/page.tsx`,
`src/components/features/reports/ReportsAnalyticsModule.tsx`,
`src/components/features/assets/ACDetailView.tsx`, `src/components/layout/TopNavbar.tsx`,
`eslint.config.mjs` (grandfather list shrunk), `package.json` (+`idb-keyval`)
D: `src/lib/maintenance-data-service.ts`, `src/lib/technician-data-service.ts`

## Mock / direct-DB removal (grep evidence)

- `maintenance-data-service|technician-data-service|useMaintenanceJobs|useTechnicianJobs|
  mockMaintenanceJobs|notifyDueTechnicianJobs|syncOfflineJobUpdates|getPendingSyncCount`
  across `src` → **0 matches** (plan verify: delete the two services + mock jobs ✓).
- `nitttr_technician_offline_queue` → **0 matches** (queue moved to IndexedDB
  `acms.pending-job-sync`).
- `localStorage` non-comment usage: only `src/lib/storage/**` (the designated module) and
  the grandfathered P5/P7 services (`movement-data-service`, `user-service`) — **no job
  data in localStorage**; the only job data in browser storage is the pending-sync queue.
- Direct-DB (`pg`, `src/lib/server`, `src/app/api`): gone since P2; nothing reintroduced.

## API integrations (endpoint → screen)

| Endpoint | Consumers |
|---|---|
| `GET /maintenance` (+`assigned_to=me`, status, priority, dates, `ac_id`, `q`) | technician jobs list, technician dashboard, technician self-report history, admin/manager maintenance lists |
| `GET /maintenance/<id>` (row + `events[]` + `comments[]`) | job detail page timeline, admin/manager detail modal |
| `POST /maintenance` | admin/manager “Schedule Job”, technician self-report (self-assign) |
| `POST /maintenance/<id>/assign\|accept\|start\|complete\|escalate\|cancel` | job detail buttons, admin/manager contextual actions + Assign/Complete modals (queued when offline) |
| `POST /maintenance/<id>/comments` | job detail “Add a note” |
| `POST /maintenance/sync` | `SyncQueueMenu` (manual button, `online` event, mount catch-up) |
| `GET /reports/maintenance-trend` | `ReportsAnalyticsModule` (monthly trend, priority donut, KPI stats) |
| `GET /notifications` + `POST /notifications/<id>/read`, `/read-all` | TopNavbar bell (60 s poll) |
| `GET /users?role=TECHNICIAN` | assignee selects (add-job + Assign… modal) |
| `GET /assets/<ref>` | job detail asset panel (`useAsset`) |

API-only (no FE screen this phase): `PATCH /maintenance/<id>` (content edit — no UI edits
job content).

## Validation results

Backend (`TEST_DATABASE_URL` → postgres container on **5433**):
- **Full suite: 185 passed, exit 0** (119 baseline + 48 state machine + 13 maintenance +
  5 sync).
- Plan **Verify** subset
  `pytest tests/test_work_order_state_machine.py tests/test_maintenance_v1.py tests/test_offline_sync.py -q`
  → **66 passed, exit 0**, including the plan's comment lines:
  property test `test_random_action_sequences_never_reach_illegal_state` (seeded random
  action sequences, every outcome a documented error or legal state),
  idempotency (sync replay → all `duplicate`, DB unchanged), permission
  (technician B cannot touch technician A's job → 403).
- `ruff check app tests` → all checks passed; `black --check app tests` → unchanged.
- `ruff check .` → 8 errors, **all in two pre-existing backend-root scratch scripts**
  (`diagnose_db.py` 6, `test_endpoints.py` 2) — untouched on purpose (user tooling).
- `tests/test_openapi.py` + `tests/test_route_auth_coverage.py` → **4 passed** after the
  new routes.
- `npm run gen:api` (ad-hoc server on :5000) → exit 0, `schema.d.ts` now carries
  `/maintenance*` + `/reports/maintenance-trend`.
- `python -m app.jobs.notify_due` smoke → exit 0.

Frontend (`cd Frontend`, final tree):
- `npx tsc --noEmit` → **exit 0**
- `npx eslint . --quiet` → **0 errors**; `npx eslint .` → **60 warnings**
  (baseline 70 — the retired services took 10 with them; remainder is the documented
  lint-debt block)
- `npx vitest run` → **13 passed** (9 adapter + 4 new `offline-queue` merge tests)
- `npm run build` (placeholder `NEXT_PUBLIC_*` vars) → **exit 0**, 36 routes + `ƒ Proxy`

Plan **Verify** section status:
- pytest three files → ✓ 66 passed.
- property/idempotency/permission comment lines → ✓ covered inside the suite (above).
- `npx playwright test e2e/technician-job.spec.ts` → **BLOCKED**: no playwright
  config/`e2e/` directory exists in the repo.

**Done-when walk-through (offline path pending live creds):** manager creates a job from
the admin/manager page (create → ASSIGNED with assignee, or SCHEDULED + Assign…), the
technician sees it under `assigned_to=me`, Accept → Start → Complete all record
`maintenance_events` (visible in the detail timeline and the manager modal), actions taken
with `navigator.onLine === false` land in the IndexedDB queue and replay through
`/maintenance/sync` on reconnect with per-item results, and no job data exists in browser
storage except the pending-sync queue (grep-verified above).

## Deviations & notes

1. **Complete has no photo/sign-off** — the backend `complete` payload is
   `work_performed|parts_used|remarks|labor_hours` only, so the completion sheet's
   PhotoPicker, sign-off name and confirmation checkbox were dropped (parts kept, minutes
   converted to hours). Job photos remain available through comment attachments if needed.
2. **No `/notifications` page/route** — plan FE task 5's “bell → `/notifications`
   (poll every 60 s)” is implemented as the bell dropdown wired to
   `GET /api/v1/notifications` with the 60 s `useNotifications` poll; no new page route was
   invented (the existing UI's notification surface is the dropdown).
3. **Accept/start carry no free-text note** — the state machine records
   who/when/from→to automatically and only assign/escalate/cancel take text
   (`reason`). The old “note for the audit trail” textarea became the **comments**
   box on the detail page (server-backed, shows in the timeline area) instead of a
   silently-ignored input.
4. **UI status conflation (by design)** — the P2 adapter contract maps backend `ASSIGNED`
   → UI `'Accepted'` (asserted by `adapters.test.ts`, not changed); the detail page
   additionally shows `'Assigned'` until `acceptedAt` is set, and buttons are gated on
   `acceptedAt`, so accept/start are never ambiguous. Chips on list pages remain bucket
   filters over backend statuses.
5. **`load_dotenv(override=True)` DB-target hijack (discovery)** —
   `app/config/database.py:24` loads `.env` with `override=True` at import time, so shell
   `DB_*` variables are overridden; earlier “migrate 5433” runs actually hit the Supabase
   pooler in `.env`. Migration 008 was applied to the **real localhost:5433** test DB via an
   import-then-`os.environ.update` wrapper (temp scripts deleted). The `.env`/credential
   files were never printed or modified — rotating them remains your step.
6. **`exc.errors(include_context=False)`** at the 3 error-serialization sites in
   `maintenance.py` (pydantic v2 `ctx` holds a raw `ValueError` → not JSON serializable);
   the other 22 pre-existing `exc.errors()` call sites were left untouched (out of scope).
7. **Asset business key vs route UUID** — work orders filter on `ac_id` (`AC-…`) but asset
   route params are `asset_uuid`; `ACDetailView`/technician asset pages therefore pass the
   mapped `ac.acId` (`TechnicianAsset` gained an optional `acId`) — no backend change.
   Create uses the same business key (FK to `acs(ac_id)`).
8. **Stats** — admin/manager maintenance stat cards count over the fetched (filtered) set
   to preserve the old `getStats` semantics; the reports module uses the trend endpoint's
   authoritative `stats` instead of client counting.
9. **PATCH/`useUpdateJob` dropped** — no screen edits job content, so the unused hook and
   its `JobPatch` type were removed; the endpoint stays documented API-only.
10. **`mock-data.ts`** keeps the P5 movement and P7 user/audit data (next phases' scope);
    only the maintenance section moved out.

## Remaining blockers

1. **Credential rotation** (yours) → then: apply migrations 001–008 + the auth trigger to
   the Supabase/dev DB, create the `building-photos` bucket if missing, and run the live
   end-to-end (login → manager creates job → technician accepts/starts → offline complete →
   reconnect → synced → timeline visible; notifications bell; `notify_due` cron).
2. **Playwright e2e** not present in the repo → the plan's offline e2e line is skipped
   (queue behaviour is unit-covered by the `mergeSyncResults` tests + backend sync tests).
3. Baseline git commit is your task (no git used by me).

## Exact next task

**Phase 5 — movements & approvals** (`acms-plan/phases/phase-5-movements-approvals.md`): retire
`movement-data-service.ts` (localStorage) onto the v1 movements endpoints, rewire the
movement screens + audit data off `mock-data.ts`, and shrink the `eslint.config.mjs`
grandfather list accordingly.
