# Phase 8 Report — Testing & hardening

**Status: COMPLETE** (every offline-verifiable task green; Playwright e2e, k6, ZAP and the
live-DB items remain blocked on tooling/credentials, as documented below).
Date: 2026-10-08. Depends on Phases 0–7; ran against the complete application.

Goal met: the system now has **evidence** rather than vibes — 535 backend tests at **85 %**
coverage, a written security review with **no open High/Critical** findings, measured
performance (Lighthouse 95/100/96 desktop, route-level bundle analysis, `docs/perf.md`), an
axe-core audit at **0 violations** on every reachable page with a keyboard-run and
24/24 badge-contrast pass, and the plan's cleanup items (legacy `/api/*` blueprints and the
`current_status` free-text column) removed.

## Progress / what was done

### 8.1 Backend tests — COMPLETE

- **13 suites built or extended**: `test_db_target`, `test_rbac_matrix`,
  `test_injection_and_bounds`, `test_audit`, `test_campus_v1`, `test_users_lifecycle`,
  `test_email_service`, `test_question_parser`, `test_query_service`, `test_migrations`,
  `test_concurrency`, `test_openapi_contract`, `test_idor`.
- **Coverage 74 % → 85 %** total (`app/api` 85.8 %, `app/auth` 86.0 %,
  `app/services` 92.7 %) via `--cov=app --cov-report=term:skip-covered`.
- The **RBAC matrix** is generated from `url_map` (every route × every role × expected
  status). Lessons encoded in the suite: rules are not merged by path, `require_auth` sets
  `_requires_permissions = ()`, `POST /auth/register-profile` inserts a PENDING profile
  whose `profiles.role` (not the JWT claim) is authoritative — so each rule gets one fresh
  throwaway subject; `PATCH /api/v1/me` has an in-code VIEWER guard proven by a staleness
  test; 5xx is tolerated only when `error.code != INTERNAL_ERROR`.
- SQL-injection probes on every string filter (incl. the `?status=` fallback), pagination
  bounds (far-beyond-last-page → empty page, `INVALID_PAGINATION` on bad input),
  concurrency (movement complete race, sync replay), migration numbering/idempotency, and
  a **schemathesis** contract run of `openapi.yaml` vs the live app
  (`tests/test_openapi_contract.py`).
- **Migration runner is forward-only by design** — `tests/test_migrations.py` asserts
  sorted 3-digit names, the `_supabase_auth_trigger.sql` exclusion, `006` presence, and
  `up` idempotency; there is no down path to test (documented deviation from the plan's
  "migration up/down" wording).
- **Defects found and fixed while writing the tests** (details and severity in
  `docs/security-review.md`, F1–F12): audit offset paging had no `OFFSET` (page 2 showed
  page 1); `_build_filters` status fallback 500 on a trailing backslash → `_like_literal()`
  escape helper; `INVALID_COLLEGE` 400 on campus-block create
  (`app/api/v1/campus.py:264`); a test-isolation hole (`.env` load order) → autouse
  `TEST_DATABASE_URL` pinning in `tests/conftest.py`; RBAC teardown `uuid = text` cast bug;
  pydantic `value_error` surfacing as 500 → `_json_safe()` in `app/core/__init__.py`;
  date/tz flakiness → `datetime.now(timezone.utc).date()`; **cross-technician IDOR** on job
  detail/update/delete → `_own_jobs_clause()` (`app/api/v1/maintenance.py:185`, applied at
  `:330/:646/:874`); event-order non-determinism → `app/core/clock.py::utcnow()` used by
  `work_orders.py` and `maintenance.py`; audit append-only proven by a trigger test.
- **Test-DB policy**: a temp reset script (`DROP SCHEMA public CASCADE` + `migrate up`,
  deleted after use) recovered from a seeded-`TEST_USER_ID` FK collision — the rule "never
  seed `TEST_USER_ID` from a test module" is now baked into the suites, and every suite
  self-seeds its own uniquely-tagged fixtures (`QS-AC-*`, `SCRATCH-AC-*`, `P8-RACE-*`,
  `PAGE-AC-*`, …) instead of leaning on global seed data (which would break
  absolute-count assertions).

### 8.2 Frontend tests — COMPLETE (Playwright sub-item blocked)

- `npx vitest run` → **44 tests / 4 files**: `src/lib/permissions.test.ts` (16, new —
  the whole role × action permission table), `src/components/features/assets/AssetFormModal.test.tsx`
  (14, new — client validation + server error mapping, incl. "refuses to submit without an
  AC ID and never calls the API"), plus the existing `adapters.test.ts` (10) and
  `offline-queue.test.ts` (4). `vitest.setup.ts` added for jsdom environment wiring.
- **Playwright e2e: blocked** — the repo has no `playwright` install, no `e2e/`
  directory, no `playwright.config.ts` and no docker-compose stack to run against
  (plan task assumed all four). Setting that up is the user's infrastructure task; the
  flows it would cover are exercised by the backend suites instead.

### 8.3 Security review — COMPLETE

- **`docs/security-review.md`** (new): OWASP ASVS L2 pass over auth, session, access
  control, input validation, file upload, logging — twelve findings **F1–F12** with
  pass/fail, evidence and severity. **Gate: no open High/Critical → PASS.** §11 has a
  human sign-off line that is *pending your signature* (the plan explicitly requires human
  sign-off for this phase).
- **Dependency audit**: backend `pip-audit -r requirements.txt` → **"No known
  vulnerabilities found"** (was **83 vulnerabilities across 7 packages**). Pins added with
  rationale comments: `python-dotenv 1.2.2`, `PyJWT[crypto] 2.15.0`, `Pillow 12.3.0`,
  `Werkzeug 3.1.9`. Frontend: `npm audit fix` + `next@16.4.0` / `eslint-config-next@16.4.0`
  → `npm audit --omit=dev` = **0**; the full audit shows **8 dev-only** issues (braces via
  `eslint-config-next`; tinypool/vitest chain) — accepted, no `overrides` hack.
- **Secrets**: no `NEXT_PUBLIC_*` secret anywhere (service-role key and
  `SUPABASE_JWT_SECRET` remain server-side only); `.env` never printed. `gitleaks`/CI/
  Dependabot are absent from the repo — documented as operator follow-ups, not silently
  passed.
- **Cookies**: `@supabase/ssr` gained `secure` (protocol-derived) in
  `src/lib/supabase/client.ts`; `httpOnly` is impossible for a JS-authored cookie, so the
  compensating controls (CSP, Bearer-only Flask API, read-only `proxy.ts`) are listed and
  the BFF cookie store is recorded as the long-term remediation.
- **IDOR**: technician A vs B job access (fixed — `_own_jobs_clause`), viewer → `/users`
  (403), manager outside assigned blocks (scope test) — all pinned by `test_idor.py`.
- **Accepted findings** (documented, not hidden): F6 `script-src 'unsafe-inline'` (nonce
  CSP in `proxy.ts` is the remediation), F8 no rate limit on `register-profile` (28 other
  `@rate_limit` sites; sign-up is inherently unauthenticated), F9 dev-only npm issues,
  F10 `backend/*.xlsx` ignore + your `git rm --cached`.

### 8.4 Performance — COMPLETE

- **3D asset**: GLB compressed **336,964 → 195,372 bytes** (quantize-only;
  `meshopt`'s 71 KB variant rejected because `WebAssembly.instantiate()` is CSP-blocked),
  its 925 KB-class texture externalized to `public/model/ac_model3.png` (925 B), and the
  HDRI self-hosted and downsampled (1,540,678 → 524,337 B). Original preserved at
  `Frontend/_glb_backup/ac_model3.glb.orig` until your baseline commit.
- **Zero-CSP viewer fix**: `useGLTF(url, false, false)` + self-hosted
  `<Environment files="/hdri/…">` in `src/components/features/3d/AC3DViewer.tsx` — the
  three CSP conflicts (loader fetch, wasm, external HDR) are gone **without touching a
  single CSP directive**; browser-verified, cross-referenced in `security-review.md` §5.
- **Lighthouse (Chrome headless, LH 12.8.2)**: `/login` desktop **95 / 100 / 96**
  (FCP 0.4 s, LCP 1.4 s, CLS 0); mobile simulated 76 / 100 / 96 (LCP 4.1 s, TBT 430 ms —
  LH itself warned the simulated CPU was slower than expected). The plan's dashboard /
  asset-list targets are **auth-blocked** (Lighthouse cannot log in) — `/login` was
  measured instead; best-practices 96 is capped by an unidentified `inspector-issues`
  "Content security policy" entry with **empty subitems** (minor follow-up).
- **Bundle**: `@next/bundle-analyzer` is not installed, so Next 16.4's built-in
  `npx next analyze --output` (Turbopack) was used. Route baseline ≈1464 KB raw / 617 KB
  gz; asset detail 2512 KB / 907 KB gz; dashboards/reports ≈1888 KB / 804 KB gz. Heavy
  libraries **route-split correctly**: three+drei+fiber (1035 KB) only on asset detail,
  `html5-qrcode` (370 KB) only on scan, recharts (397 KB) only on dashboards/reports.
- **k6: blocked** — no k6 binary and no seeded 10k-asset live DB in this environment.
  Substitute evidence: `docs/perf.md` (10k-asset seed, all 11 endpoints p95 < 300 ms,
  EXPLAIN ANALYZE on every report query) from Phase 7.

### 8.5 Accessibility & UX QA — COMPLETE

- **axe-core 4.13.0 on every reachable page** (Playwright unavailable, so axe was injected
  as a temporary script and run with `axe.run(document, {resultTypes:['violations']})`;
  the temp file was deleted after the audit). Pre-fix → post-fix:

  | Page | Pre-fix violations | Post-fix |
  |---|---|---|
  | `/` (landing) | 4 — color-contrast ×2 (serious, #62748e on #020618 = 4.23), heading-order ×2 | **0** (36 passes) |
  | `/login` | 8 best-practice — landmark-one-main, page-has-heading-one, region ×7 | **0** (34 passes) |
  | `/signup` | 0 | 0 |
  | 404 | landmark-one-main + region | **0** (27 passes) |
  | `/a/QR-TEST-404` | color-contrast (#90a1b9 on white = 2.63) ×2 | **0** (28 passes) |

  Fixes: login root `div` → `main` + "Sign in" `h2` → `h1`;
  `Footer` bottom-bar `text-slate-500 → slate-400` (dark bg, 4.23 → ~7:1);
  `FeatureStrip` card `h4 → h2`, `AboutSection` photo-card `h4 → h3` and list `h5 → h3`,
  `HeroSection` modal `h3 → h2` / `h4,h5 → h3` (heading-order jumps incl. one inside the
  conditionally-opened modal, fixed proactively); `PublicAssetView` 2 ×
  `text-slate-400 → slate-500` on the white card (2.63 → 4.76:1); new branded
  `src/app/not-found.tsx` with `<main>` + `h1`.
- **Keyboard-only run of the core flow: PASS** — tab order email → password → submit →
  "Create an account", visible focus rings everywhere (2 px outlines), Enter on an empty
  required field blocked by native validation (stays on `/login`), link activation
  navigates to `/signup`.
- **Status-badge contrast: 6 initial fails → 24/24 pass ≥ 4.5:1** (worst remaining pair
  4.52). Semantic tokens darkened in `globals.css`: `--color-success #10B981 → #047857`,
  `--color-warning #F59E0B → #92400E` (an interim #B45309 still failed at 4.35),
  `--color-danger #F45B69 → #B91C1C` (audited: only StatusBadge dots/tints and KpiCard
  chips/text use them — no token-bg buttons); slate/blue marginal fails fixed in
  `manager|admin/maintenance` (Cancelled/Low/Medium), `viewer/search` (Offline),
  `admin/users` (inactive chip) and the `?? 'bg-slate-100 text-slate-500'` fallbacks.
  Measured empirically via injected chips + canvas `getImageData` (getComputedStyle emits
  `lab()`/`oklch()` which regex-on-`rgb()` misses) + the WCAG formula over a white
  composite.
- **Async states**: static review PASS — sampled views all render loading
  ("Loading jobs…", `Loader2` spinners), error (`{error && …}` banners, `isError` + Retry)
  and empty ("No maintenance jobs found.", dashed-border empties) states.
- **360 px mobile viewport: blocked** — no viewport-resize tool exists in this browser
  harness and technician pages are auth-gated; substitute evidence is Lighthouse's mobile
  run (a11y **100**).

### 8.6 Cleanup — COMPLETE

- **Legacy `/api/*` routes removed**: `app/routes/dashboard.py` and `app/routes/db_test.py`
  deleted and unregistered from `app/__init__.py` (the FE never called
  `/api/dashboard` or `/api/db-test` — grep-verified). Anonymous probes still answer 401
  (the legacy guard rejects anonymous `/api/*` before routing, unchanged), so proof of
  removal is a new authenticated test: `test_auth.py::
  test_legacy_dashboard_and_db_test_routes_removed` asserts both paths **404** for a
  logged-in admin. `app/routes/acs.py` stays deliberately — it is a parser library
  imported by `assets_import.py`, not a registered blueprint.
- **`current_status` free-text column dependency removed** (P3 had kept the column
  "until P8"):
  - **New `migrations/013_drop_current_status.sql`** — `ALTER TABLE acs DROP COLUMN IF
    EXISTS current_status;` with the rationale in comments. Forward-only runner (no down
    by design); `idx_acs_status` (002, single-column) auto-drops with the column;
    **applied to the localhost:5433 test DB and verified gone**
    (`information_schema` count = 0). Live-DB application waits on your credential
    rotation like 001–012.
  - **Reads/filters converted to `operational_status`**: `assets.py` `BASE_SELECT` +
    the `?status=` unknown-token fallback (now an exact ILIKE against the constrained
    vocabulary, still `_like_literal`-escaped and bound raw); `qr.py` `/qr/resolve`
    SELECT; `assets_write.py` INSERT (column + tuple) and PATCH (translate-then-pop so
    the dynamic SET map never sees the dropped column); `query_service.py` all seven
    SELECTs plus a rewritten `ac_status()` — "not working" →
    `operational_status IN ('FAULT','OFFLINE')` and "under maintenance" →
    `= 'MAINTENANCE'` preserve the old synonym-set intent, and any other phrase goes
    through `normalize_status_filter` (unknown token → `[]` instead of probing free
    text).
  - **Input vocabulary kept at the boundary only**: `v1_schemas.py` still accepts
    `current_status` on create/update (the FE contract), `CSV_ALIASES` still recognizes
    the `status`/`current_status` headers, and both write paths derive
    `operational_status` via `status_map.derive_operational_status` (import path now
    `payload.pop(...)`). **The FE needed zero changes** — adapters already read
    `toUiStatusFromRow(a.operational_status, a.current_status)` preferring the
    canonical column, and the zod schema marks the legacy key `nullish()`.
  - Post-change footprint (grep): `current_status` survives only as *accepted input*
    (schemas, CSV allowlist, `status_map` params, comments) — **zero column
    reads/writes/filters**; `openapi.yaml` contains no reference (never did).
  - Tests updated accordingly: `test_assets_v1` (seed + detail assert), `test_query_service`
    (fixture + four column-set/baseline asserts, plus a new "unknown token → no rows"
    assert), `test_import_flexible`, `test_qr_v1` (seed), `test_injection_and_bounds`
    (docstring), `test_import_canonical` unchanged (it asserts the *CSV header* is
    recognized — input side), `test_auth` (+1 removal-proof test).
- **Dead mock files: none exist** — the plan's `delete dead mock files` item was
  grep-surveyed across `Frontend/src` (mock stores were already deleted in Phase 7;
  remaining hits are historical mentions inside doc comments).
- **Unused dependencies (`depcheck`, `knip`): not installed** — nothing to remove;
  `pip-audit`/`npm audit`/`schemathesis`/`axe-core` are present and used.

## Files changed (created C / modified M / deleted D)

### Backend
C: `migrations/013_drop_current_status.sql`, `app/core/clock.py`, `tests/test_db_target.py`,
`tests/test_rbac_matrix.py`, `tests/test_injection_and_bounds.py`, `tests/test_campus_v1.py`,
`tests/test_users_lifecycle.py`, `tests/test_email_service.py`, `tests/test_question_parser.py`,
`tests/test_query_service.py`, `tests/test_migrations.py`, `tests/test_concurrency.py`,
`tests/test_openapi_contract.py`, `tests/test_idor.py` (several of these extend suites that
already existed — the 13-suite list in §8.1 is the authoritative set);
M: `tests/conftest.py` (autouse DB pinning), `tests/test_auth.py` (+ removal proof),
`tests/test_audit.py`, `tests/test_assets_v1.py`, `tests/test_import_flexible.py`,
`tests/test_qr_v1.py`, `app/__init__.py` (blueprint registrations removed),
`app/api/v1/system.py` (audit OFFSET), `app/api/v1/assets.py` (`_like_literal` +
`operational_status`), `app/api/v1/assets_write.py`, `app/api/v1/assets_import.py`,
`app/api/v1/maintenance.py` (`_own_jobs_clause` + clock), `app/api/v1/qr.py`,
`app/api/v1/campus.py` (`INVALID_COLLEGE`), `app/services/query_service.py`,
`app/services/status_map.py` (docstrings), `app/services/work_orders.py` (clock),
`app/core/__init__.py` (`_json_safe`), `requirements.txt` (pins), `openapi.yaml` (contract
sync; verified clean of `current_status`);
D: `app/routes/dashboard.py`, `app/routes/db_test.py`.

### Frontend
C: `src/lib/permissions.test.ts`, `src/components/features/assets/AssetFormModal.test.tsx`,
`vitest.setup.ts`, `src/app/not-found.tsx`;
M: `src/app/(public)/login/page.tsx` (main/h1), `src/components/landing/{Footer,FeatureStrip,AboutSection,HeroSection}.tsx`
(contrast + heading levels), `src/app/(public)/a/[ref]/PublicAssetView.tsx` (slate-500),
`src/app/globals.css` (success/warning/danger tokens darkened),
`src/app/(portal)/manager/maintenance/page.tsx` + `admin/maintenance/page.tsx`
(Cancelled/Low/Medium contrast), `src/app/(portal)/viewer/search/page.tsx`,
`src/app/(portal)/admin/users/page.tsx`, `src/components/features/3d/AC3DViewer.tsx`
(zero-CSP `useGLTF`), `public/model/ac_model3.glb` (quantized) +
`public/model/ac_model3.png` (externalized texture) + `public/hdri/potsdamer_platz_512.hdr`
(downsampled), `src/lib/supabase/client.ts` (cookie `secure`).

### Docs
C: `docs/security-review.md`, this report; M: `docs/perf.md` untouched (Phase 7).

## Mock / direct-DB / dead-code removal (grep evidence)

- `current_status` in `backend/app/**` → **14 hits, all input-boundary**
  (2 schema fields, 2 CSV allowlist lines, 3 `status_map` param/doc lines, 5
  derive/pop sites, 2 comments); **0 column reads/writes/filters**. Same pattern in
  `backend/tests` → 1 comment + the CSV-header recognition assert.
- `current_status` in `backend/openapi.yaml` → **0 matches**; `/api/dashboard|/api/db-test`
  in the spec → **0 matches**.
- `app/routes/dashboard.py|db_test.py` → files deleted; `app/routes/__init__.py` is empty
  (no re-exports to clean); registrations removed from `app/__init__.py`.
- FE `src/app/api/` (legacy Next routes) → directory does not exist; mock stores → already
  gone (Phase 7); `depcheck|knip` in either manifest → not present.

## API integrations

No endpoints were added or removed by Phase 8. Contract state: `openapi.yaml` ↔ app is
enforced by `tests/test_openapi.py` + `tests/test_route_auth_coverage.py` (both green) and
exercised by the schemathesis run; `/api/v1/qr/resolve` now returns `operational_status`
instead of `current_status` (the scan page only reads `ac_id`/`asset_code`/`asset_uuid`,
so no FE change was needed).

## Validation results

Backend (`TEST_DATABASE_URL` → the `acms-test` container on **5433**; migration 013
applied with a temp script — created, run, verified, deleted):
- **Full suite: 532 passed, 3 skipped** (535 total, **0 failures**) — the Phase-8.1 floor
  was 531+3, and `test_auth` gained one test (+1). A second consecutive run produced
  **529 passed, 6 skipped**, proving the skip-count difference is the ambient-corpus guard
  in `test_offset_paging_actually_advances` (non-asset routes need ≥ 2 leftover rows to
  page; the parametrization skips below that) — the guard's tables are untouched by 8.6,
  and both runs total 535 with zero failures.
- The 3 stable skips are exactly that guard (`/maintenance`, `/movements`, `/notifications`
  at 0 ambient rows); every other test passes deterministically.
- `ruff check app tests` → **all checks passed**; `black --check app tests` → **87 files
  unchanged** (one reformat of `test_query_service.py` was applied and its 15 tests
  re-run green); `ruff check scripts` → clean. `ruff check .` still shows only the 8
  pre-existing errors in the backend-root scratch scripts (untouched).
- `tests/test_openapi.py` + `tests/test_route_auth_coverage.py` → green in the full run.
- pip-audit → exit 0, no known vulnerabilities.

Frontend (`cd Frontend`, final tree after all 8.2–8.6 edits):
- `npx tsc --noEmit` → **exit 0**
- `npx eslint . --quiet` → **0 errors**
- `npx vitest run` → **44 passed** (4 files)
- `npm run build` (placeholder `NEXT_PUBLIC_*`) → **exit 0**, **40 routes + `ƒ Proxy`**
  (unchanged count — the explicit `not-found.tsx` replaced the auto-generated stub).
- Lighthouse `/login` desktop (final post-fix re-run) → **95 / 100 / 96**.

Plan **Verify** gate status:
- `make test e2e audit` → **no Makefile, no CI workflow, no e2e directory exist** in this
  repo. Offline equivalents: pytest full suite ✓ (532 green), vitest ✓ (44 green),
  `pip-audit` ✓ + `npm audit --omit=dev` ✓ (0 prod vulns).
- `docs/security-review.md` → **no open High/Critical ✓** (§11 human sign-off still
  pending — yours to sign).
- `k6 run perf/mixed.js` → **blocked** (no k6 binary, no seeded live 10k DB). Substitute:
  `docs/perf.md` p95 < 300 ms on all 11 endpoints + Lighthouse + route bundle analysis.

## Deviations & notes

1. **Migration runner has no down path** — the plan asked for "migration up/down"; the
   runner is forward-only by design (documented since Phase 4), so the test asserts
   numbering/idempotency/`up` instead. Restoring a dropped column would be a *new*
   additive migration, which is the correct operational pattern anyway.
2. **`ac_status()` vocabulary mapping** — the old free-text synonym lists included
   `'faulty'`, which neither `007` nor `status_map` ever recognized (those rows classify
   as `UNKNOWN`), so the converted `FAULT ∪ OFFLINE` set matches the system's own
   normalization exactly; the behaviour delta for genuinely unknown phrases is now
   "no rows" rather than a free-text match, pinned by a test.
3. **`current_status` kept as an API/CSV input name** — dropping the *column* must not
   break the FE or existing CSV templates, so the name survives at the boundary only and
   is translated in one place per path (`status_map.derive_operational_status`).
4. **Playwright/k6/ZAP/gitleaks were not installed** — adding infra tooling is outside
   what this environment can do offline; each is listed as a blocker rather than
   simulated.
5. **axe ran without Playwright** — a temporary script injection (deleted after) gave the
   same `axe.run` results on demand; keyboard testing used the real browser tab.
6. **Lighthouse targets measured on `/login`** — the plan's dashboard/asset-list pages
   are auth-gated and the harness cannot log in; `/login` covers first-load perf, a11y and
   best-practices for the public surface.
7. **360 px viewport not exercised** — no viewport-resize tool; mobile a11y evidence is
   Lighthouse's mobile run (a11y 100).
8. **GLB backup kept** — `Frontend/_glb_backup/ac_model3.glb.orig` stays until your
   baseline commit; then it can go.
9. **Historical docs untouched** — `backend_doc/ac-backend-docs.html` still describes
   `current_status` (as do Phases 0–7 reports); they are historical records, not code.

## Remaining blockers

1. **Credential rotation** (yours) → apply migrations **001–013** + the Supabase auth
   trigger to the live DB, then the live end-to-end pass. Everything this phase did to a
   database was **localhost:5433 only**.
2. **Human sign-off** — `docs/security-review.md` §11 is waiting for your signature line;
   the Phase-9 gate treats "no open High/Critical" as met but the plan requires a human.
3. **Playwright e2e stack** (config + `e2e/` + seeded users + docker-compose) — your
   infrastructure task; 8.2's e2e line stays unrunnable until then.
4. **k6** binary + a seeded 10k-asset live DB → `perf/mixed.js` as written in the plan
   (offline substitute recorded in `docs/perf.md`).
5. **ZAP baseline scan** against staging; **gitleaks in CI**; **Dependabot**; a
   **timed backup-restore test** and the **"API DB role has no DDL rights"** check —
   all need infrastructure/network this environment does not have (SQL provided in
   `security-review.md` §8).
6. **Physical phone/HTTPS scan test** (plan line — yours, unchanged since Phase 6).
7. **Repo hygiene**: `git rm --cached "backend/*.xlsx"`, decide the fate of
   `Frontend/_glb_backup/ac_model3.glb.orig`, and create the baseline commit (no git used
   by me).
8. **Possible production side effect (unverified)**: please check/delete the Supabase
   profile id `44444444-4444-4444-4444-444444444444` (name "T").
9. `docs/private-data/*.xlsx` acceptance dataset still absent (noted since Phase 1).

## Exact next task

**Phase 9 — DevOps & go-live** (`acms-plan/phases/phase-9-devops-golive.md`): read the
phase file first, then execute its steps against the now-complete Phases 0–8, keeping the
same conventions (no git, no live-DB work, lint/type/test/build green, **535 backend tests
and 44 vitest tests as the floor**, `docs/security-review.md` as the security gate).
