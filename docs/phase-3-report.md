# Phase 3 Report — Assets, campus & import

**Status: COMPLETE** (all offline-verifiable items green; live end-to-end pending credential rotation).
Date: 2026-10-06. Depends on Phase 1 (API contract) and Phase 2 (frontend foundation).

Goal met: asset register, campus hierarchy/map and bulk import are served entirely by
Flask/Postgres; browser storage no longer participates in any asset/campus flow.

## Progress / what was built

### Backend (plan tasks 1–8)

1. **Status normalisation** — `migrations/007_asset_status.sql` adds
   `operational_status` CHECK (`WORKING|FAULT|MAINTENANCE|OFFLINE|UNKNOWN`) with backfill
   from `current_status`; old column kept until P8. `app/services/status_map.py` is the
   single derivation point (`derive_operational_status`, both columns kept in step on
   every write/import), so dashboards/filters read the new column.
2. **Widened `AssetCreate/AssetUpdate`** — all UI fields (star rating 1–5, power kW,
   PF 0–1, installation/manufacturing years, next service, remarks, technician,
   **`last_maintenance_date`**); range validation; duplicate non-empty
   `ac_serial_number` → 409 carrying the conflicting `asset_code`.
3. **List + facets** — `GET /api/v1/assets` gained `sort` (whitelisted keys),
   `floor`, `room`, `lifecycle`, `location_status`, `due_before`; `GET /api/v1/assets/facets`
   returns `{status, asset_type, manufacturer, block, department, lifecycle, location_status}`;
   new indexes + `pg_trgm` trigram search index.
4. **Enriched detail** — `GET /api/v1/assets/<ref>` (triple ref: uuid/code/ac_id) returns
   `maintenance_history` (work orders + migrated legacy), `movement_history`, `attributes`,
   full location chain and `qr_version` — one query per section, no N+1.
5. **Transactional import** — `POST /api/v1/assets/import` (multipart field `file`,
   10 MB cap, `.xlsx/.csv` sniffed, formulas not evaluated): `dry_run=true` → 200 +
   row-level report (create/update/duplicate/invalid/pending-location, unknown columns kept
   as `asset_attributes`, legacy report keys inside `data`); commit → 201 **one transaction
   with a per-row SAVEPOINT**, counts derived from `report_rows` via `_counts()`.
6. **Campus** — `GET /api/v1/campus/tree` (colleges → blocks → floors → rooms, orphan
   blocks, departments); CRUD for blocks/floors/rooms/departments behind `MANAGE_CAMPUS`
   with delete guards (`409 HAS_CHILDREN`, `DUPLICATE_ID`); `PATCH /campus/blocks/<id>/layout`
   (JSON-validated, 256 KB capped); photos via signed upload (≤5 MB png/jpg/webp) to the
   private `building-photos` bucket with signed read URLs — no `data_url` blobs stored.
   Legacy `POST /api/campus/import` **removed**; the legacy guard keeps its permission
   mapping so stale clients get 403/410 instead of falling through to auth.
7. **CSV export** — `GET /api/v1/assets/export?format=csv` streaming, `EXPORT_DATA`.
8. **Legacy route removal** — `/api/acs*` handlers and the hierarchy blueprints
   (`/api/blocks|floors|rooms|departments|colleges`) deleted after the FE grep showed no
   callers; `app/api/acs.py` retained purely as the import **parser library**;
   `/api/dashboard*` kept (db_test/maintenance/chat are separate concerns).
   `tests/test_route_auth_coverage.py` + `tests/test_openapi.py` pin the result.

**Extras delivered in phase:**
- `GET /api/v1/dashboard/summary` extended: accepts the **same filters as `/assets`**
  (reuses the `JOINS`/`_build_filters` machinery), `kpis {total, working, under_maint,
  faulty, offline, unknown, capacity, departments, rooms, due_soon, overdue,
  completeness_pct}`, plus `by_type`/`by_block`/`by_capacity`; numerics cast `::float`/`::int`.
- **PATCH null-clear semantics** — `PATCH /api/v1/assets/<ref>` honours explicit `null`
  (clears the column), leaves omitted keys untouched, re-derives `operational_status` when
  `current_status` is supplied (incl. null), `400 NO_FIELDS` when nothing recognised.
  Test: `test_patch_explicit_null_clears_omitted_keeps`.
- `tests/test_transactions.py` gained a full FK-chain fixture.

### Frontend (plan tasks 1–6)

1. **`ac-data-service.ts` (acStore + localStorage) retired** → `src/lib/api/assets.ts`
   hooks: `useAssets/useAllAssets/useAssetDetail/useAssetFacets/useDashboardSummary/
   useCreateAsset/useUpdateAsset/useDecommissionAsset`. `useACDataset` fans out to list +
   unpaginated rows + summary; stats come from `/dashboard/summary`, never client counting.
   Pure valuation helpers moved server-data-compatible into `api/assets.ts`.
2. **Server-driven tables** — `ACTable` controlled (server pagination/sort, inline edit
   save, decommission); `ACFilterBar` posts whitelisted sort keys only; **URL query state**
   (`useAssetQueryState`: q/block/type/status/sort/page/size, 300 ms debounced search,
   page reset, back/forward adoption); `admin|manager` assets/dashboard pages rewired;
   detail links use `asset_uuid`; `viewer/search` does **server-side** search + status filter.
3. **Forms + optimistic writes** — `AssetFormModal` (create/edit) with zod schemas mirroring
   backend rules, server field errors mapped inline (`VALIDATION_ERROR`, `ASSET_EXISTS`,
   `SERIAL_EXISTS`, `INVALID_ROOM`), room select from the campus tree; `useUpdateAsset`
   applies an optimistic overlay to list/all/detail caches with **rollback on error** and
   invalidation on settle; `ACDetailView` inline edit PATCHes through the same hook
   (`detailEditToPayload`); decommission flow confirms then calls the v1 endpoint.
4. **Import modal** — `ImportACModal` rewritten around the server dry-run: SELECT_FILE →
   REVIEW (counts cards, errors/warnings, unknown columns, paginated row table with
   filter/search) → PROCESSING → RESULT; downloadable 22-column template
   (`TEMPLATE_COLUMNS`, client-generated CSV). Client parsers `excel-parser.ts` and
   `csv-cleaner-validator.ts` **deleted**; `xlsx` + `papaparse` (+`@types/papaparse`)
   removed from `package.json`.
5. **Campus** — `campus-services.ts` is now a facade over `/campus/tree` (module cache +
   shared mappers) keeping the async signatures `CampusLayout/FloorView/CampusOverlay/
   GlobalSearch/DashboardPreview` call; `CampusManagementPage` runs on `useCampusTree` +
   CRUD endpoints (floor/room diff ordered around the server's `HAS_CHILDREN` guards,
   AC-linked pre-flight before deletes, block code immutable after create); photos upload
   signed (no `data_url` blobs); **map page** (`/campus`): host page forwards per-block AC
   stats to the static 3D map over `postMessage` (it cannot hold an API session) and a new
   **`LayoutEditorPanel`** (MANAGE_CAMPUS) saves block positions through
   `PATCH /api/v1/campus/blocks/<id>/layout`; `CampusOverlay` building panel now shows real
   floors/AC counts (was hardcoded 4/6/58) and enters the real first-floor id.
   Deleted: `useCampusManagement.ts`, `real-campus-service.ts`, `mock-campus-database.ts`.
   `CampusContext` kept (pure navigation state, no data).
6. **Demo concept removed** — `DataSourceIndicator.tsx` deleted (0 render sites left);
   `isDemo`/`isDemoData` gone from types; detail shows a lifecycle badge instead of
   "Demo Data"; `viewer/dashboard` + `viewer/search` off `mockACs` onto
   `useDashboardSummary`/`useAssets`; landing `DashboardPreview` renders `—` when
   unauthenticated (the summary endpoint is auth-guarded; no public stats endpoint invented).

## Files changed (created C / modified M / deleted D)

### Backend
C: `migrations/007_asset_status.sql`, `app/api/v1/assets_import.py`,
`app/api/v1/campus_import.py`, `app/services/status_map.py`,
`tests/test_dashboard_summary.py`, `tests/test_campus_import.py`
M: `app/schemas/v1_schemas.py`, `app/api/v1/{assets,assets_write,campus,system}.py`,
`app/auth/legacy_guard.py`, `app/api/acs.py` (now parser-only), `backend/openapi.yaml`,
`tests/test_assets_v1.py` (+null-clear test), `tests/test_transactions.py` (FK fixture);
 route/auth coverage pinned additionally by `tests/{test_openapi,test_route_auth_coverage}.py`
D: legacy `/api/acs*` route handlers, hierarchy blueprints (`blocks/floors/rooms/
departments/colleges`), legacy `/api/campus/import`; temp helpers `_dbq.py`,
`_migrate_target.py`

### Frontend
C: `src/lib/types/asset.ts`, `src/lib/api/imports.ts`, `src/lib/api/campus.ts`,
`src/hooks/useAssetQueryState.ts`, `src/hooks/useACDataset.ts`,
`src/components/features/assets/AssetFormModal.tsx`,
`src/components/campus/ui/LayoutEditorPanel.tsx`, `docs/phase-3-report.md`
M: `src/lib/api/{client,assets}.ts`, `src/lib/adapters/{asset,adapters.test}.ts`,
`src/lib/campus-services.ts`, `src/components/features/csv-import/ImportACModal.tsx`,
`src/components/features/assets/{ACTable,ACDetailView}.tsx`,
`src/components/features/assets/{ACFilterBar,ACStatCards}.tsx`,
`src/components/features/reports/ReportsAnalyticsModule.tsx`,
`src/components/campus/{CampusManagementPage,ui/CampusOverlay}.tsx`,
`src/app/(portal)/campus/page.tsx`, `public/campus_map.html`,
`src/app/(portal)/admin/{assets,dashboard}/page.tsx`,
`src/app/(portal)/manager/{assets,dashboard}/page.tsx`,
`src/app/(portal)/viewer/{dashboard,search}/page.tsx`,
`src/components/landing/DashboardPreview.tsx`, `src/lib/technician-asset-service.ts`,
`eslint.config.mjs` (grandfather list shrunk), `package.json`
D: `src/lib/{ac-data-service,mock-campus-database,real-campus-service}.ts`,
`src/hooks/useCampusManagement.ts`, `src/components/ui/DataSourceIndicator.tsx`,
`src/components/features/csv-import/{excel-parser,csv-cleaner-validator}.ts`,
deps `xlsx`, `papaparse`, `@types/papaparse`

## Mock / direct-DB removal (grep evidence)

- `acStore|nitttr_ac_` (case-sensitive) across `src` → **0 matches** (plan verify ✓).
- `mockACs|mockBuildings|DataSourceIndicator|isDemoData` → **0 code matches**
  (only a case-insensitive false positive: the report filename `NITTTR_AC_Asset_Report_*.csv`).
- `localStorage` outside `src/lib/storage`: only comments in P3 files + the
  grandfathered P4/P5/P7 services (`maintenance/movement/technician/user-service`),
  documented in `eslint.config.mjs` (entries for `ac-data-service`/`useCampusManagement`
  removed from the list).
- Direct-DB (`pg`, `src/lib/server`, `src/app/api`): gone since P2; nothing reintroduced.

## API integrations (endpoint → screen)

| Endpoint | Consumers |
|---|---|
| `GET /assets` (+filters/sort/page) | ACTable pages, `viewer/search`, reports, map search |
| `GET /assets/facets` | filter dropdowns, stat facets, map overlay AC counts |
| `GET /assets/<ref>` | `ACDetailView` (histories/attributes/qrVersion) |
| `POST /assets`, `PATCH /assets/<ref>` | `AssetFormModal`, table/detail inline edit (optimistic) |
| `DELETE …/decommission` | table + detail decommission confirm |
| `POST /assets/import?dry_run=` | `ImportACModal` (preview → commit → counts) |
| `GET /assets/export?format=csv` | reports export |
| `GET /dashboard/summary` | stat cards, admin/manager/viewer dashboards, reports, landing preview |
| `GET /campus/tree` | `campus-services` facade, `CampusManagementPage`, overlay, layout editor, viewer locations |
| `POST/PATCH/DELETE /campus/{blocks,floors,rooms}` | `CampusManagementPage` save/delete cascade |
| `PATCH /campus/blocks/<id>/layout` | `LayoutEditorPanel` (map layout save) |
| `POST/DELETE /campus/blocks/<id>/photos…` | `CampusManagementPage` signed photo upload |
| `GET /campus/blocks/<id>` (tree) | static map stats via `postMessage` bridge |

Not wired to any UI (API-only, outside the P3 FE task list): `POST /api/v1/campus/import`.

## Validation results

Backend (`TEST_DATABASE_URL` → container `acms-test` on **5433**):
- **Full suite: 119 passed, exit 0.**
- Plan verify subset `tests/test_assets_v1.py + test_import_{canonical,flexible} +
  test_campus_import.py`: **41 passed, exit 0**.
- `ruff check app tests` → **all checks passed**; `black --check app tests` → 64 files unchanged.
- `ruff check .` → 8 errors, **all in two pre-existing backend-root scratch scripts**
  (`diagnose_db.py` 6, `test_endpoints.py` 2) — untouched on purpose (user tooling).
- `npm run gen:api` (ad-hoc server on :5000) → exit 0, `schema.d.ts` regenerated.

Frontend (`cd Frontend`):
- `npx tsc --noEmit` → **exit 0**
- `npm run lint` / `npx eslint .` → **0 errors, 70 warnings** (documented pre-existing debt)
- `npx vitest run` → **9 passed**
- `npm run build` (placeholder `NEXT_PUBLIC_*` Supabase vars) → **exit 0**, all routes incl. `ƒ Proxy`

Plan **Verify** section status:
- `pytest …/test_assets_v1, test_import_*, test_campus_*` → ✓ 41 passed.
- Acceptance import `docs/private-data/*.xlsx` → **BLOCKED**: the dataset folder does not
  exist in this checkout (needs 370-row dry-run classification + commit/re-import check).
- `grep -R "acStore\|nitttr_ac_" frontend/src` → ✓ 0 matches.
- `npm run build` → ✓; `npx playwright test e2e/assets.spec.ts` → **BLOCKED**: no
  playwright config/`e2e/` directory exists in the repo.

## Deviations & notes

1. **Block editor fields dropped** — `blocks` has no `type/description/status` columns
   (contract: `block_id, college_id, block_name, layout, photos`); the block editor keeps
   name/code/floors/rooms/photos only. Block code is immutable after create (no
   `block_id` in `BlockPatch`).
2. **Departments tab**: floor column dropped — departments are college-wide on the server
   (rooms reference them); no floor linkage exists to display.
3. **"Set cover" removed** from photo UI — the API has no cover operation; first uploaded
   photo becomes the cover server-side.
4. **Map department breakdown** — the static map's per-building department bars cannot be
   sourced (no block×department aggregate exists; inventing an endpoint was out of scope),
   so the panel shows an honest "No department breakdown available" fallback.
5. **Static map geometry** — `public/campus_map.html` keeps its hardcoded building layout
   (it now ingests live per-block AC counts via `postMessage` instead of the dead
   `/api/campus/buildings` legacy call). The new `LayoutEditorPanel` persists
   `{x,y,w,h,color}` — the exact contract the React canvas (`BuildingBlock`) renders —
   but the static iframe does not reposition from server layout yet (follow-up item).
6. **OpenAPI** — `backend/openapi.yaml` (hand-maintained, validated against `url_map` by
   `tests/test_openapi.py`) has never declared asset write request bodies, so
   `schema.d.ts` has no `AssetCreate/AssetUpdate` types; FE payload types are hand-written
   in `src/lib/types` + zod adapters. `last_maintenance_date` therefore does not appear in
   the generated schema (nothing regressed).
7. **`gen:api` needs a running backend** (`python -c "from app import create_app;
   create_app().run(port=5000)"`); the script targets the fixed local URL from the P3 fix.
8. **Landing preview stats** are `—` for anonymous visitors (`/dashboard/summary` requires
   auth + `VIEW_ASSET`; no public stats endpoint was invented).
9. **Dev DB disclosure** — migrations 001–007 are applied to your **local PostgreSQL 17 on
   host 5432**; the test suite runs against the **Docker `acms-test` (postgres:16) on
   5433**. Supabase migrations remain pending rotation.

## Remaining blockers

1. **Credential rotation** (yours) → then: apply migrations 001–007 + auth trigger to the
   Supabase/dev DB, create the private `building-photos` bucket, run the live smoke test
   (signup → approve → login → asset CRUD → import → campus photos).
2. **Acceptance dataset** `docs/private-data/*.xlsx` missing from the repo → the plan's
   370-row import acceptance run cannot be executed.
3. **Playwright e2e** not present in the repo → plan verify's e2e line skipped.
4. Baseline git commit is your task (no git used by me).

## Exact next task

**Phase 4 — Maintenance & work orders** (`acms-plan/phases/phase-4-maintenance-jobs.md`):
replace `maintenance-data-service.ts`/`technician-data-service.ts` (localStorage + `mock-data`
jobs) with the v1 maintenance endpoints, wire admin/manager/technician job screens and the
reports module's job data, then remove the `mock-data.ts` job types if the phase retires them.
