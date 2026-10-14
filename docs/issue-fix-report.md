# Final Report — Issue 1 (AC detail location fields) & Issue 2 (portal performance)

## 1. Issue 1 — Root cause (traced CSV → importer → DB → hierarchy → API → adapter → page)

The chain was verified end-to-end against `ACMS 301.xlsx` (read-only fixture) and the code:

| Stage | Finding |
|---|---|
| CSV | Fixture row with serial `50037154 2169011273` has **blank `room_number`** (168/300 rows blank; `room_type` filled on 299/300) |
| Importer | `_resolve_room_best_effort` early-returns `"room_number not provided"` — a **deliberate** design: blank room ⇒ asset is imported **unassigned**, pinned by `test_import_canonical` ("stored unassigned, not rejected") |
| DB | `acs.room_id = NULL`, `location_status = 'PENDING'` |
| Hierarchy | Detail query LEFT JOINs `rooms → floors → blocks → departments` — all NULL ⇒ every location field is `NULL` |
| API | `GET /api/v1/assets/:id` (`BASE_SELECT`) **does** return `building/floor/room/department/room_type` — when `room_id` is NULL they are legitimately `null` |
| FE | Two real defects: (a) `mapAsset` **dropped `room_type`** (zod parsed it, adapter discarded it); (b) Room card displayed only `roomName`/`roomId`, never `room_type` per the intended mapping |

The AC ID (`AC-LOC-ADMIN-BLOCK-ADMINISTRATION-S50037154-2169011273`) matches `_derive_ac_id()` exactly — the ID was **not** used to derive display values, and no value was fabricated anywhere. CSV location columns are lookup-only (`IMPORT_LOOKUP_FIELDS`: "never written as acs columns"), so persistence is room-mediated **by schema design** — not a mapping bug.

## 2. Issue 1 — The fix (FE adapter/display only; no API, no importer, no resolver changes)

- `Frontend/src/lib/types/asset.ts` — `ExtendedAC.roomType?: string`
- `Frontend/src/lib/adapters/asset.ts` — `mapAsset` now maps `roomType: a.room_type ?? undefined`
- `Frontend/src/components/features/assets/ACDetailView.tsx` (Room card) — `ac.roomType || ac.roomName || ac.location.roomId`: `room_type` primary per the intended mapping, fallbacks only when it's absent — never fabricates

Backend already returned everything (`room_type` is in `BASE_SELECT`), so **zero API-contract change** (openapi tests pass untouched).

## 3. Issue 1 — The genuinely-unresolved asset & required data

The tested asset `AC-LOC-ADMIN-BLOCK-ADMINISTRATION-S50037154-2169011273` **still shows "—" for all four fields — correctly**:

- Root cause: its CSV row has no `room_number`, so no room can be resolved, so no hierarchy exists. The "—" is truthful, not a display bug.
- **Required data to resolve it:** a `room_number` value in that CSV row that matches an existing `rooms.room_name` in your campus directory (or create that room in `Admin block → Administration` first). Then re-run the idempotent import — the row will link, `location_status` becomes resolved, and all four fields populate.
- I could not verify your live campus directory (credential rotation still pending), so I did **not** invent a room or change the resolver — blank-room ⇒ unassigned stays as pinned by the import tests.

## 4. Issue 1 — Regression tests (resolved AC returns **and** displays all four)

- **Backend** `backend/tests/test_assets_v1.py::test_detail_sections_location_chain_and_qr_version` — now also asserts `room_type == "Lab"` on a resolved AC (seeded chain: Main Academic Block → Floor 1 → ROOM-102 → type "Lab", dept Computer Science) plus building/floor/room/department.
- **Adapter** `Frontend/src/lib/adapters/adapters.test.ts` — +2 tests: `room_type` is mapped through `mapAsset`; `room_type: undefined` produces **no** fabricated value.
- **Display** `Frontend/src/components/features/assets/ACDetailView.test.tsx` (new) — full path `fetch → envelope → zod → mapAsset → InfoCard`: renders the resolved AC and asserts all four values appear in the DOM; second test proves `room_type` ("Lab") wins over `room_name` while falling back safely.

## 5. Issue 2 — Task A: `/api/v1/me` duplicates & 5+ s latency

**Callers found:** only `AuthContext.tsx` — bootstrap on mount, `SIGNED_IN`/`TOKEN_REFRESHED` Supabase events, explicit call in `signIn`/`signUp`, and the manual "refresh" button on `/pending`. No `middleware.ts`, no React Query refetch, no proxy lookup.

**Duplicate sources (verified):**
1. React Strict Mode double-mount (dev) → two bootstraps racing.
2. `signIn`'s explicit `loadProfile()` racing Supabase's own `SIGNED_IN` event → two `/me` calls per login.

**Fix:** an in-flight shared promise in `loadProfile` (`AuthContext.tsx`). Concurrent callers now share one request; sequential calls still hit the server. **Security unchanged:** role/status always come from the server response; nothing was added to client-side authorization caching (`setAuthSnapshot` untouched); `require_auth`/profile-lookup on the backend untouched.

**Latency:** the endpoint itself is minimal — measured **1 statement** (JWT verify + one indexed profile SELECT). The 93 ms → 5,234 ms growth in your logs was **not** `/me`'s own query; it was queueing behind the dashboard's concurrent requests (statement sequences holding workers/connections) plus remote RTT — addressed in §6–§7 and the gunicorn fix (§9).

## 6. Issue 2 — Tasks B & C: dashboard summary + assets/facets SQL

Profiled by statement count (the round-trip multiplier; ~43 ms remote RTT per statement from the earlier timeout analysis):

| Endpoint | Before | After | Change |
|---|---:|---:|---|
| `GET /dashboard/summary` | 10 stmts (profile + KPIs + MTTR + **6 sequential dimension GROUP BYs** + overdue [+ my_jobs]) | **5 stmts** | 6 dimension queries → one `UNION ALL`; each leg keeps its exact original `GROUP BY/ORDER BY/LIMIT`; rows re-keyed + re-sorted in Python so response values/shape are **identical** |
| `GET /assets/facets` | 8 stmts (profile + **7 sequential facets**) | **2 stmts** | 7 facets → one `UNION ALL` (byte-identical legs, outer `ORDER BY` restores per-facet `count DESC, value`) |
| `GET /assets` | 3 stmts | 3 stmts | already minimal (profile + COUNT + page) |
| `GET /me`, `/campus/tree`, `/reports/maintenance-trend` | 1 / 6 / — | 1 / 6 / 5 | unchanged (tree/trend not in the slow list — left alone) |

No indexes added (existing `idx_acs_*` from migrations 002/012 remain the right plan — `docs/perf.md` 10k-row analysis); no server-side summary cache (React Query's 30 s client cache already covers repeats; a server cache would need scope-aware keys to avoid cross-role staleness/leakage — rejected deliberately).

## 7. Issue 2 — Task D (waterfall) + Task E (dev compilation)

**Waterfall:** the dashboard's four queries (`/assets`, `/dashboard/summary`, `/assets/facets`, `/reports/maintenance-trend`) already fire **in parallel** via React Query. `/me` legitimately gates page mount (role routing is server-authoritative — kept). Findings: search input is debounced 300 ms; URL-state sync has a `lastPushed` guard (no echo refetch); pagination changes the list key only — summary/facets don't refetch; no `middleware.ts` auth lookup. No unnecessary sequential blocking found.

**Dev compilation:** `next.config.ts` is minimal (headers/CSP only) — nothing defeats the dev compile cache. First-visit route compilation (~11.7–18.9 s in your logs) is standard `next dev` JIT and was explicitly excluded from optimization; your own logs show subsequent navigations at 323/171/115 ms — **no recompilation on repeated navigation**. Production: full `next build` compiles every route in **2.4 s**, dashboard is a static shell (○) with client-side data fetching, detail pages ƒ.

## 8. Issue 2 — Task F (THREE.Clock / 3D) + Task G (cache invalidation)

**3D:** project source contains **zero** `THREE.Clock` usage. The deprecation warning comes from `@react-three/fiber` internals (`clock: new THREE.Clock()` in `events-156d8d12.esm.js:1016`) against three 0.186 — an upstream issue; patching `node_modules` was out of scope, so no change. The AC-detail viewer is already lazy (`next/dynamic`, `ssr:false`, separate chunk) — it does **not** block initial render; it is kept working. `CampusCanvas` is currently unreferenced by any page.

**Invalidation:** every asset mutation (create/update/decommission/import) invalidates the `assetKeys.all = ["assets"]` prefix → list + summary + facets + detail refresh — **targeted**, never global; campus mutations invalidate `campusKeys.all` plus `assets` (tree embeds the location chain); jobs/audit untouched. New caches respect this: `useAssetFacets` and `useCampusTree` got `staleTime: 300_000` (bounded 5-min windows; any write invalidates immediately; nothing cached indefinitely). Summary keeps the global 30 s ("Real-time" dashboard).

## 9. Issue 2 — Task H: before/after timings

**Local measurements** (test client against the test DB; no network RTT, so statement count is the durable metric):

| Endpoint | Before | After |
|---|---|---|
| `/api/v1/me` | 1 stmt, 36.1 ms | 1 stmt, 11.3 ms |
| `/api/v1/assets` | 3 stmts, 47.4 ms | 3 stmts, 15.7 ms |
| `/api/v1/assets/facets` | **8 stmts**, 16.7 ms | **2 stmts**, 12.7 ms |
| `/api/v1/dashboard/summary` | **10 stmts**, 28.2 ms | **5 stmts**, 17.4 ms |
| `/api/v1/campus/tree` | 6 stmts, 26.5 ms | 6 stmts, 16.3 ms |
| `/api/v1/reports/maintenance-trend` | not instrumented | 5 stmts, 11.4 ms |

**Projected on your production RTT (~43 ms/statement):** facets −6 round trips ≈ **−260 ms** off the observed ~891 ms; summary −5 ≈ **−215 ms** off the observed 671–1109 ms. Plus two structural fixes for the `/me` 5,234 ms spike: (a) `gunicorn.conf.py` now sets `worker_class = "gthread"`, `threads = 8` — the default **sync** worker served one request at a time per worker, so the dashboard's 5 parallel requests *queued* (`/me` paid the sum; matches your growing-latency pattern); (b) fewer `/me` calls and shorter statement sequences reduce queueing. A connection-keepalive change was **not** made — no direct evidence, so it stayed out per the no-speculative-changes rule.

## 10. Issue 2 — Before/after request counts

**Login → admin dashboard:**

| | Before | After |
|---|---:|---:|
| `GET /me` | **2** (Strict Mode double-mount in dev; SIGNED_IN + signIn race in prod) | **1** (shared in-flight promise) |
| `/assets` + `/summary` + `/facets` + `/trend` | 4 | 4 |
| **Total API requests** | **6** (7 under dev Strict Mode) | **5** (−17%) |

**Navigation:** dashboard → AC detail within staleness windows adds only the detail (+jobs) query — summary/facets/tree are **not** refetched (30 s/5 min windows). Filter change = +2 (`assets` + `summary`); facets/trend stay cached. Writes invalidate immediately via the `assets` prefix.

## 11. Validation results

| Check | Result |
|---|---|
| Backend pytest | **592 passed, 3 skipped** ✓ (was 586+3; +6 new perf-budget tests) |
| Backend ruff | All checks passed ✓ |
| Backend black | 91 files unchanged ✓ |
| Frontend vitest | **67 passed** (9 files) ✓ (was 61; +6 new) |
| Frontend tsc | `--noEmit` clean ✓ |
| Frontend eslint | clean (`--quiet`) ✓ |
| Production build | `next build` **EXIT=0**, all routes compiled in 2.4 s ✓ |
| OpenAPI contract | passes untouched (no API change) ✓ |

## 12. Not changed / limitations / remaining items

- **Not changed (deliberately):** importer & `_resolve_room_best_effort` (pinned semantics), live DB/Supabase credentials, auth/security anywhere, trend & campus-tree query counts (not in the slow list), `THREE.Clock` (upstream), connection keepalives (unproven), indexes (existing ones already right), server-side caching (client cache suffices), 3D viewer (kept, still lazy).
- **Limitations:** live end-to-end verification (Supabase auth, real browser walkthrough, your campus directory for the test asset) is blocked on your credential rotation — API verification used the Flask test client; `/me` 5 s spikes aren't reproducible locally (no network RTT), so statement counts + your logs are the evidence base.
- **Remaining for you/Phase 9:** deploy must run `gunicorn -c gunicorn.conf.py wsgi:app` for gthread to apply; keep `WEB_CONCURRENCY × DB_POOL_MAX` below the database's `max_connections`; playwright/k6/ZAP/CI/backup-restore remain infra tasks; `docs/security-review.md` §11 sign-off + baseline commit; then Phase 9 (`phase-9-devops-golive.md`).
- One flagged-but-unfixed observation: `campus/tree` signs photo URLs (one storage HTTP call per photo) while holding its DB connection — only relevant if blocks have photos; no verified slow case, so untouched.
