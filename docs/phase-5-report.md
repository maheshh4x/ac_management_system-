# Phase 5 Report — Movements & approvals

**Status: COMPLETE** (all offline-verifiable items green; live end-to-end pending credential rotation).
Date: 2026-10-07. Depends on Phase 1 (auth/permissions + API contract) and Phase 3 (asset hooks); ran parallel with Phase 6.

Goal met: relocating an AC requires a request that someone else must approve, and completion
updates the asset's room, writes `asset_location_history`, and flips the movement to
`COMPLETED` in **one row-locked transaction** — the approvals UI (admin/manager panel,
approvals page, technician flow) runs entirely on `/api/v1/movements`.

## Progress / what was built

### Backend (plan tasks 1–5)

1. **Migration `009_movements.sql`** — `movement_comments` (FK `ON DELETE CASCADE`,
   author → profiles), `asset_movements.cancelled_at` + `completed_by`, index
   `(status, requested_at)` (`idx_mov_status_requested`) and the **partial unique index
   `uq_mov_one_open`** — at most one open (`PENDING`/`APPROVED`) movement per asset, so
   `MOVEMENT_OPEN` conflicts are also enforced by the database. **`asset_location_history`**
   (`ac_id, from_room_id, to_room_id, movement_id, at`). Applied + verified on the
   localhost:5433 test DB; live Supabase application is blocked on credential rotation.
2. **`GET /movements`** (`app/api/v1/movements.py`, fully rewritten) — enriched projection:
   asset `asset_code`/make/model, from/to **room → floor → block** names plus a composed
   `from_location`/`to_location` ("Block / Floor / Room"), requester name/email/**role**,
   approver/completer names, `cancelled_at`, and the `movement_comments` thread aggregated
   as `comments[]` (`json_agg`, ASC). Filters: `mine=1`, `ac_id`, `status` (comma list),
   `from`/`to` (requested-at window), pagination (`page_size` ≤ 200). Degrades gracefully
   on pre-009 databases (`to_regclass` probe → projection without the comments subselect).
3. **Rules — 7 routes, all `@require_auth` + `@require_permission` + rate-limited:**
   - `POST /movements` (create): `CREATE_MOVEMENT`; `ac_id` must exist and be ACTIVE
     (`ASSET_DECOMMISSIONED` 409), `to_room_id` must exist and differ from the current
     room (`SAME_ROOM` 400), **one open movement per asset** (`MOVEMENT_OPEN` 409 —
     race-safe: unique-violation `23505` is also mapped to it).
   - `approve`/`reject`: `APPROVE_MOVEMENT`; **self-decision → 403 `SELF_APPROVE`**;
     a `FACILITY_MANAGER` with `assigned_blocks` may only decide movements whose *from*
     block is in scope (else 403 `FORBIDDEN`); reject requires `rejection_reason`
     (`REASON_REQUIRED` 400). Both set `approved_by`/`approved_at` as the decision actor.
   - `complete`: `COMPLETE_MOVEMENT` (requester technician or any manager/admin) —
     fast-fail status check → role check → `SELECT … FROM acs … FOR UPDATE` → re-read the
     movement (loser gets 409 `INVALID_STATE`) → **one transaction**: insert
     `asset_location_history` (from = the locked *actual* current room) + `UPDATE acs
     SET room_id` + `SET status='COMPLETED', completed_by`. Double-complete → 409;
     injected failure → full rollback (both sides intact).
   - `cancel` (new, see deviations): requester-only, `PENDING` only → `CANCELLED`.
   - `comments` (new): `require_auth` + `VIEW_ASSET`, `PENDING` only, body 1..4000 chars.
4. **Notifications + audits** — new request → every approved admin/manager (except the
   requester) gets `MOVEMENT_REQUESTED`; approve/reject/complete → requester gets
   `notify_movement_decision` (post-commit); every transition audited
   (`CREATE/APPROVED/REJECTED/COMPLETE/CANCELLED_MOVEMENT`).
5. **Location history surfaced** — `GET /assets/{ref}` returns `location_history[]`
   (from/to room→floor→block names, `at`, newest first, LIMIT 50; `[]` pre-009).
6. **Contracts pinned** — the movements section of `backend/openapi.yaml` rewritten
   (enriched GET params, all 5 transition responses incl. `SELF_APPROVE`/`REASON_REQUIRED`/
   `INVALID_STATE`, the two new paths); `tests/test_openapi.py` +
   `tests/test_route_auth_coverage.py` green.

### Frontend (plan tasks 1–3)

1. **API layer** — new `src/lib/api/movements.ts`: `useMovements({mine, acId, status,
   from, to, page, pageSize})` (server-filtered, `keepPreviousData`), `useCreateMovement`,
   `useMovementAction` (approve/reject/complete/cancel; invalidates the movement caches and
   — for `complete` — **the asset caches** so the room change shows everywhere),
   `useAddMovementComment`, plus `movementErrorMessage()` (per server error code) and
   `isMovementConflict()`. UI vocabulary moved to `src/lib/types/movement.ts`
   (`MovementRequest` + `MovementComment`, `Cancelled` status, required `comments[]`);
   `src/lib/adapters/movement.ts` extended (composed `from_location`/`to_location`,
   `requested_by_role`/email, `completed_by(_name)`, `cancelled_at`, asset make/model,
   comments mapping; `CANCELLED` now renders as **Cancelled** instead of collapsing to
   Rejected). `permissions.ts` gained `COMPLETE_MOVEMENT` (mirrors the backend matrix:
   SUPER_ADMIN / FACILITY_MANAGER / TECHNICIAN).
2. **Pages rewired** —
   - `technician/movement` — `useMovements({mine:true})`; create form keeps the asset
     search but the destination is now **Block → Floor → Room selects from
     `useCampusTree`** (free text and the preferred-date input — a column that never
     existed — are gone), reason required; Cancel (requester, Pending) and
     **“Mark as completed”** (own Approved) via `useMovementAction`; comments via
     `POST /movements/<id>/comments`.
   - `MovementPanel` (admin/manager `maintenance?tab=movement`) — React Query state
     replaces the localStorage seed; approve opens a **from → to confirmation dialog**,
     reject keeps the required-reason sheet, approved cards gain **Complete move**
     (confirm dialog explains the atomic room + history update), the previously dead
     **View** button now opens a detail sheet (metadata + comments thread), and the mock
     create modal became a real form: asset search (`useAssets`) + campus-tree room
     selects + reason. Buttons disable while mutating; every server rejection shows in a
     banner.
   - **New `MovementApprovals`** section on `admin/approvals` + `manager/approvals`
     (plan’s ApprovalQueue split: `ApprovalQueue` = user signups, `MovementApprovals` =
     PENDING movements with the same confirm/reject dialogs).
   - `technician/assets/[id]` — movement history from `useMovements({acId: business key})`
     (localStorage `getMovementsForAsset` gone).
   - `ACDetailView` — new **Location History** card on the Maintenance History tab
     rendering `asset_location_history` (from → to composed rooms + timestamp).
   - `TopNavbar` — `entity_type='asset_movement'` notifications deep-link (technician →
     `/technician/movement`, else → `maintenance?tab=movement`).
3. **Conflict handling** — every 409 (`INVALID_STATE`, `MOVEMENT_OPEN`) triggers
   `refetch()` + a code-specific explanation (“the list was refreshed; …”), per plan FE
   task 3.
4. **Deletions** — `movement-data-service.ts` (localStorage), the `MovementRequest` type +
   `mockMovementRequests` seed from `mock-data.ts`, and the `eslint.config.mjs`
   grandfather entries for `movement-data-service.ts` **and** `ApprovalQueue.tsx` (it no
   longer touches localStorage).

## Files changed (created C / modified M / deleted D)

### Backend
C: `migrations/009_movements.sql`, `tests/test_movements_v1.py` (13 tests)
M: `app/api/v1/movements.py` (full rewrite: 7 routes), `app/schemas/v1_schemas.py`
(`MovementCommentCreate`), `app/auth/jwt.py` (`_lookup_profile` now selects
`assigned_blocks` — see note 4), `app/api/v1/assets.py` (`location_history` section),
`app/services/notify.py` (standalone-commit fix — note 4), `backend/openapi.yaml`
(movements paths), `tests/test_transactions.py` (adapted: see note 5);
 `tests/test_openapi.py` + `tests/test_route_auth_coverage.py` unchanged and green
D: none (temp `_tmp_*.py` scripts + their `.pyc` created during the work and removed)

### Frontend
C: `src/lib/types/movement.ts`, `src/lib/api/movements.ts`,
`src/components/features/MovementApprovals.tsx`
M: `src/lib/adapters/movement.ts` (rewrite), `src/lib/adapters/adapters.test.ts`
(Cancelled + composed-location/comments cases), `src/lib/api/assets.ts`
(`locationHistory`), `src/lib/permissions.ts` (`COMPLETE_MOVEMENT`),
`src/components/features/MovementPanel.tsx` (API rewire), `src/app/(portal)/technician/movement/page.tsx`
(API rewire + campus selects), `src/app/(portal)/technician/assets/[id]/page.tsx`,
`src/app/(portal)/admin/approvals/page.tsx`, `src/app/(portal)/manager/approvals/page.tsx`,
`src/components/features/assets/ACDetailView.tsx` (Location History card),
`src/components/layout/TopNavbar.tsx` (`asset_movement` links), `src/lib/mock-data.ts`
(movement type + seed removed), `eslint.config.mjs` (2 grandfather entries removed)
D: `src/lib/movement-data-service.ts`

## Mock / direct-DB removal (grep evidence)

- `useTechnicianMovements|getAllMovementRequests|updateMovementStatus|getMovementsForAsset|
  mockMovementRequests|nitttr_movement_requests|MOVEMENTS_KEY|movement-data-service`
  across `src` → **0 matches in code** (2 residual matches are historical mentions inside
  doc comments of `types/movement.ts` and `MovementPanel.tsx`).
- `localStorage.` (actual calls) → only `src/lib/storage/**` (the designated module) —
  **no movement data (or any business data) in localStorage**; remaining mock services are
  `qr-service.ts` (P6) and `user-service.ts` (P7).
- Direct-DB (`pg`, `src/lib/server`, `src/app/api`): gone since P2; nothing reintroduced.

## API integrations (endpoint → screen)

| Endpoint | Consumers |
|---|---|
| `GET /movements` (`mine=1`, `ac_id`, `status`, `from`, `to`, pagination) | technician movement list (`mine`), MovementPanel (all), MovementApprovals (`status=PENDING`), technician asset detail (`ac_id`) |
| `POST /movements` | create modals: MovementPanel + technician movement page (asset search + campus-tree room + reason) |
| `POST /movements/<id>/approve` | confirm dialogs in MovementPanel + MovementApprovals |
| `POST /movements/<id>/reject` (`rejection_reason`) | reject-with-required-reason sheets in both surfaces |
| `POST /movements/<id>/complete` | MovementPanel “Complete move”, technician “Mark as completed” (also invalidates asset caches) |
| `POST /movements/<id>/cancel` | technician “Cancel request” (requester, Pending) |
| `POST /movements/<id>/comments` | technician “Add comment”; comments rendered from the list response in cards + View sheet |
| `GET /assets/<ref>` → `location_history[]` | `ACDetailView` Location History card |
| `GET /campus/tree` | Block → Floor → Room destination selects (both create forms) |
| `GET /assets?search=` | MovementPanel asset search |
| `GET /notifications` (`entity_type='asset_movement'`) | TopNavbar bell deep links |

API-only (no FE screen this phase): `from`/`to` date-window filters and multi-status comma
lists — the UI keeps its client-side filter chips over the fetched set to preserve the
existing interactions.

## Validation results

Backend (`TEST_DATABASE_URL` → postgres container on **5433**):
- **Full suite: 198 passed, exit 0** (185 Phase-4 baseline + 13 new movement tests).
- Plan **Verify** subset `pytest tests/test_movements_v1.py -q` → **13 passed, exit 0**,
  including the plan’s comment lines: **self-approve 403**, **double-complete 409**,
  **concurrent complete (threads) leaves exactly one history row**, **rollback on failure**
  (movement and room both intact afterwards, retry succeeds). `tests/test_transactions.py`
  → 6 passed (atomicity + concurrent-commit cases).
- `ruff check app tests` → all checks passed; `black --check app tests` → 71 files
  unchanged.
- `ruff check .` → 8 errors, **all in the two pre-existing backend-root scratch scripts**
  (`diagnose_db.py` 6, `test_endpoints.py` 2) — untouched on purpose (user tooling).
- `tests/test_openapi.py` + `tests/test_route_auth_coverage.py` → green with the
  movements routes documented and auth-marked.

Frontend (`cd Frontend`, final tree):
- `npx tsc --noEmit` → **exit 0**
- `npx eslint . --quiet` → **0 errors**; `npx eslint .` → **60 warnings** (exact
  grandfathered baseline; the list shrank by two entries this phase)
- `npx vitest run` → **14 passed** (10 adapter incl. the new composed-location/comments +
  Cancelled cases, 4 offline-queue)
- `npm run build` (placeholder `NEXT_PUBLIC_*` vars) → **exit 0**, 36 routes + `ƒ Proxy`

Plan **Verify** section status:
- `pytest tests/test_movements_v1.py -q` → ✓ 13 passed (all four comment lines covered).
- `npx playwright test e2e/movement-approval.spec.ts` → **BLOCKED**: no playwright
  config/`e2e/` directory exists in the repo (same as Phase 4).

**Done-when walk-through (offline path pending live creds):** a technician requests a move
(asset + destination room + reason); a **different** user must approve it — the server
returns 403 `SELF_APPROVE` otherwise (verified by test); a second open request for the same
asset is rejected by both service and DB (`MOVEMENT_OPEN`); completion row-locks the asset
and writes `asset_location_history` + `acs.room_id` + `COMPLETED` in one transaction, so
concurrent completers collapse to one history row and a failure rolls back both sides
(verified by tests); the asset detail then shows the new room and the Location History
card in agreement.

## Deviations & notes

1. **Two new endpoints beyond the plan** (required by the pre-existing UI, documented in
   `openapi.yaml`): `POST /movements/{id}/cancel` (plan task 3 covers cancel as a *rule*
   but named no route; the UI had a Cancel button) and `POST /movements/{id}/comments`
   (the UI threads comments; plan puts them on the model via migration 009 but names no
   route). Comments + full metadata ride the **list** response — there is deliberately no
   `GET /movements/{id}` detail route.
2. **Preferred date dropped** — no such column exists (plan never mentions one), so the
   date input was removed from the create form rather than silently ignored.
3. **Approvals-page interpretation** — plan FE task 2 lists `admin|manager/approvals` and
   “ApprovalQueue (split: user approvals → P1.3; movement approvals → here)”. Implemented
   as: `ApprovalQueue` keeps user signups; a new `MovementApprovals` section below it
   handles movements. The `admin|manager/movements` routes already redirect to
   `maintenance?tab=movement` (pre-existing) — `MovementPanel` remains that surface.
4. **Two backend bug fixes discovered while wiring (both required by the plan’s rules):**
   - `auth/jwt.py _lookup_profile` never selected `assigned_blocks`, so the plan’s manager
     block scoping (and the documented `/users` scoping) could never trigger; fixed and
     verified — no test regressions.
   - `services/notify.py` with `conn=None` (standalone call) never committed — the pool’s
     `close()` rolls back, silently dropping notifications such as
     `notify_movement_decision`; fixed (commit on success when `owns_conn`).
5. **`tests/test_transactions.py` adapted** — admin self-approve now correctly 403s, so the
   test seeds a second `TECHNICIAN` profile as requester; the profile fixture force-upserts
   role/status/is_active (a leftover stale row had once flipped a request into 403) and
   tears down `completed_by`/movement comments/location history with the profiles.
6. **Cancelled is now visible** — the P2 adapter collapsed `CANCELLED` → `Rejected` (“UI
   has no Cancelled badge”); `Cancelled` is a first-class status in the type, badge map and
   filter chips. The adapter test’s old title mentioned the collapse but asserted it
   nowhere — title/cases updated, one new assertion added.
7. **Complete needed a UI** — none existed; added “Complete move” (manager/admin,
   ProtectedAction `COMPLETE_MOVEMENT`) and the technician’s own “Mark as completed”, both
   behind the from → to confirmation. `COMPLETE_MOVEMENT` was added to the FE permission
   table to mirror the backend matrix exactly.
8. **Movements are online-only** — the plan has no movement sync endpoint, so unlike jobs
   there is **no offline queue**: errors surface immediately (409 → refresh-and-explain).
   The P4 IndexedDB queue stays jobs-only.
9. **Rate limits** — new scopes `mov-list`/`mov-decide`/`mov-complete` (120/min) and
   `mov-create`/`mov-comments` (60/min); the limiter’s storage is a session-wide singleton,
   so all test counts were kept under budget.
10. **psycopg2 `%` gotcha** — LIKE patterns must travel as parameter *values* (e.g.
    `"P5-AC-%"`); a literal `'%'` inside SQL breaks placeholder parsing.
11. **`openapi.yaml` / schema.d.ts** — the hand-maintained spec is updated and pinned by
    tests; `gen:api` was not re-run (the FE movement hooks are hand-typed like the P4
    maintenance hooks, which is the established pattern).
12. **`mock-data.ts`** now keeps only the P7 user/audit seeds (movement section moved out).

## Remaining blockers

1. **Credential rotation** (yours) → then: apply migrations 001–009 + the auth trigger to
   the Supabase/dev DB, create the `building-photos` bucket if missing, and run the live
   end-to-end (login → technician requests move → manager approves → technician completes →
   asset detail shows the new room **and** matching Location History row; notifications
   bell deep-links; `uq_mov_one_open` under real concurrency). Migration 009 was applied to
   the **localhost:5433 test DB only**.
2. **Playwright e2e** not present in the repo → the plan’s `e2e/movement-approval.spec.ts`
   line is skipped (behaviour is unit-covered by `test_movements_v1.py`, including the
   threaded concurrent-complete and rollback cases).
3. `docs/private-data/*.xlsx` acceptance dataset still absent (noted since Phase 1).
4. Baseline git commit is your task (no git used by me).

## Exact next task

**Phase 6 — QR & scanning** (`acms-plan/phases/phase-6-qr-scan.md`): retire `qr-service.ts`
(the last localStorage data service before P7’s `user-service`) onto the QR endpoints,
rewire scan/print flows, and shrink the `eslint.config.mjs` grandfather list accordingly.
