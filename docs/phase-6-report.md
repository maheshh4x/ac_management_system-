# Phase 6 Report — QR codes, scanning & public asset page

**Status: COMPLETE** (all offline-verifiable items green; physical phone/HTTPS test and live
end-to-end pending — the phone test is explicitly the user's task in the plan).
Date: 2026-10-07. Depends on Phase 1 (auth/permissions + API contract) and Phase 3 (asset
hooks); ran after Phase 5.

Goal met: every AC has a stable QR (`{FRONTEND_URL}/a/{asset_uuid}`) generated on demand,
technicians scan with `html5-qrcode` on a phone and land on the asset's detail page, the
admin QR panel drives the real endpoints (preview / PNG / SVG / batch sheet / regenerate),
and an anonymous scanner sees only the 10 allow-listed fields — verified by decoding actual
QR images in tests and asserting the exact public key set.

## Progress / what was built

### Backend (plan tasks 1–3)

1. **Migration `010_qr_labels.sql`** — `acs.qr_version INTEGER NOT NULL DEFAULT 1`, the
   per-asset *label* counter. The payload itself stays immutable, so `qr_version` only
   records how many times a label was re-issued (1 = first). Applied + verified on the
   localhost:5433 test DB via `python -m app.migrate up`; live Supabase application is
   blocked on credential rotation.
2. **`app/services/qr_service.py`** —
   - `frontend_host()` and a new **`ForeignHostError`**: `parse_qr_payload(payload,
     allowed_hosts=None)` now strictly parses absolute URLs and rejects any host outside
     the allowed set (default: FRONTEND_URL's host; callers may add their own, e.g. the
     request host). Bare refs (UUID / asset code) never need a host and still pass.
   - `qr_png_bytes(payload, size=None)` — optional exact-size PNG (clamped 128–2048 px,
     rounded down to whole QR modules) behind the plan's `&size=` parameter.
3. **`app/api/v1/qr.py`** — 4 routes, decorators ordered route → `require_auth` →
   `require_permission` → `rate_limit` like the rest of the codebase:
   - `GET /assets/<ref>/qr?format=png|svg|payload&size=` — the `size` param now actually
     works (400 `INVALID_SIZE` on non-integers); the payload is built from `asset_uuid`
     **only** (the old `asset_code or ac_id` fallback is gone — "never ac_id" is now
     structural).
   - `POST /qr/resolve` (plan task 2) — gained `@rate_limit(30, 60, "qr-resolve")` and the
     allowed-host gate: foreign hosts → **400 `FOREIGN_HOST`** (distinct from 400
     `INVALID_QR`), unknown ref → 404 `ASSET_NOT_FOUND`, before any lookup runs.
   - **NEW** `POST /assets/<ref>/qr/regenerate` — `EDIT_ASSET` (SUPER_ADMIN /
     FACILITY_MANAGER), 30/min. `UPDATE acs SET qr_version = qr_version + 1 … RETURNING`,
     commit, then `audit("REGENERATE_QR", "asset", …)` (old → new version). Returns
     `{ac_id, asset_uuid, asset_code, qr_version, payload}`; the payload is byte-identical
     before and after, which the tests prove by decoding both PNGs.
   - **NEW** `GET /assets/qr/batch?ids=…` — `EXPORT_DATA` (= SUPER_ADMIN /
     FACILITY_MANAGER, the plan's "SUPER_ADMIN/MANAGER"), 10/min. Comma-separated refs
     (uuid / code / ac_id), or omitted → every active asset; capped at 96 labels (4 A4
     sheets). reportlab renders **A4, 3 × 8 labels per page** with dashed cut lines; each
     label = QR + asset code + make/model + `Block > Floor > Room`. Returns
     `application/pdf` attachment `qr-labels.pdf`; nothing matched → 404 `NO_ASSETS`.
4. **`app/api/v1/public.py` rewritten (plan task 3)** — returns **exactly** the plan's
   field set: `asset_code, make, model, type, capacity, block, floor, room,
   operational_status, last_maintenance_date` (no serial, technician, remarks, attributes,
   history), `Cache-Control: public, max-age=60` on 200s (404s keep the app-wide
   `no-store`), strict rate limit kept (30/min). Two pre-existing bugs fixed on the way:
   the block join referenced `rooms.block_id`, a column that **does not exist** — the
   anonymous route 500'd on every call and had no test; and `DATE` columns are now
   serialized to ISO strings like the rest of the API (`capacity` is cast to float in SQL
   so the JSON encoder never sees a `Decimal`).
5. **`app/api/v1/assets.py`** — `a.qr_version` added to `BASE_SELECT`; the enriched detail
   now returns the **per-asset** version (`row.get("qr_version") or QR_VERSION`) instead
   of the global constant.
6. **Contracts pinned** — `openapi.yaml`: both new paths documented, `size` query param
   added, resolve summary now states the rate limit + `FOREIGN_HOST`, the public summary
   lists the reduced field set + cache header. `tests/test_openapi.py` +
   `tests/test_route_auth_coverage.py` green (public route stays on the anonymous
   allow-list by `/api/v1/public/` prefix).
7. **`tests/test_qr_v1.py` — 18 new tests** covering the plan's Verify list: PNG decodes
   (zxing-cpp) to `{FRONTEND_URL}/a/{asset_uuid}` and the internal `ac_id` never appears;
   SVG + payload formats; size param; auth required; foreign host rejected (absolute and
   `//host` protocol-relative); unknown ref 404 (resolve + public); invalid payload 400;
   public view returns the exact 10 keys and the seeded serial/technician/remarks values
   never appear anywhere in the response, plus the cache header; regenerate bumps 1 → 2,
   keeps the payload byte-identical, writes exactly one audit row, and 403s technician /
   viewer; batch renders `%PDF` with **2 pages for 25 labels**, 403s non-exporters, 404s
   unknown ids.
8. **Dependencies** — `reportlab>=4.0` → `requirements.txt` (runtime, label sheets);
   `zxing-cpp>=2.2` → `requirements-dev.txt` (test-only decoder).

### Frontend (plan tasks 1–4)

1. **`src/lib/api/client.ts`** — new `apiBlob()`: binary GET with the same auth /
   15 s timeout / 401-refresh-once / error-envelope semantics as `request()`, returning a
   `Blob` (QR images and PDFs are not JSON).
2. **`qr-service.ts` rewritten (plan task 1)** — `USE_MOCK_QR`, the in-memory
   `mockQrDatabase` and the dead `/api/super-admin/ac/…/qr*` fetches are gone. It is now a
   thin wrapper over the real endpoints: `fetchQrImage(ref, format, size?)`,
   `fetchQrPayload`, `regenerateQr` (returns `qr_version`), `fetchQrBatchPdf(refs?)`, plus
   a `downloadBlob` helper.
3. **`AcQrActions` rewritten** — on mount it loads the PNG preview (object URL, revoked on
   unmount) and the payload caption ("Scanning opens …"); buttons: **Download PNG**,
   **SVG**, **Print** (A4 sheet for `batchIds ?? [acId]`), and **Regenerate** whose confirm
   text was corrected — the old mock claimed "old QR codes stop working", the truth (and
   the test) is the opposite: *the encoded URL never changes, stickers already printed keep
   scanning*; success toast shows the new version. The SUPER_ADMIN gate and the existing
   visual style are preserved; `viewer/search` now passes
   `batchIds={acs.map(item => item.id)}` so Print yields a sheet of the current result set.
   `next/image` (`unoptimized`) renders the blob so `no-img-element` adds no warning.
4. **`/a/[ref]` (plan task 2)** — the server page keeps metadata + the anonymous fetch and
   renders a new colocated client component `PublicAssetView`: the reduced mobile-first
   card (code, status chip, make/model, type, capacity, `block / floor / room`, last
   service) with a **"Sign in for full details"** button → `/login`. When the visitor is
   signed in with an `APPROVED` profile, it `router.replace`s to their role's asset detail
   via the new `roleAssetDetail(role, ref)` helper in `permissions.ts`
   (SUPER_ADMIN→`/admin`, FACILITY_MANAGER→`/manager`, TECHNICIAN→`/technician`,
   VIEWER→`/viewer` — exactly the `ROUTE_ROLES` matrix, so the portal guard lets them in).
   Pending/rejected users deliberately keep the public view (they'd only get 403s
   otherwise; sign-in routes them to `/pending` as before).
5. **`technician/scan` rewritten on `html5-qrcode` (plan task 3)** — new dependency
   `html5-qrcode@2.3.8` (bundles its own TS types; the plan's "already used in
   Frontend_1" doesn't apply to this repo):
   - camera requested **only on the "Use camera" gesture**, QR-only format support
     (`formatsToSupport: [QR_CODE]`), 10 fps with a 220 px QR box;
   - **denied permission / no camera** produce distinct messages that point at the manual
     search fallback;
   - **torch toggle** appears only when the track reports `torch` in its capabilities
     (constraint applied via `advanced: [{ torch }]`; the non-standard DOM prop is cast,
     no `any`);
   - a **Stop** button, stop+clear on unmount, and after a hit the camera stops before
     navigating;
   - successful decode → **debounced** (2.5 s same-text window) → `POST /qr/resolve` →
     `router.push('/technician/assets/<uuid>')`; `FOREIGN_HOST`, 404 and offline/timeout
     get distinct human messages;
   - the manual ID/serial search and the offline cached lookup are retained as the
     fallback path (and the offline badge now initialises from `navigator.onLine` instead
     of a synchronous effect write).
6. **`Permissions-Policy: camera=(self)`** — already set in P2 (`next.config.ts`), verified
   unchanged (plan task 4's header line).
7. **Grandfather list shrank** — `src/lib/qr-service.ts` removed from the
   `eslint.config.mjs` lint-debt list (it was never in the localStorage list — its mock was
   in-memory).

## Files changed (created C / modified M / deleted D)

### Backend
C: `migrations/010_qr_labels.sql`, `tests/test_qr_v1.py` (18 tests);
M: `app/api/v1/qr.py` (resolve hardening + regenerate + batch routes),
`app/services/qr_service.py` (host gate, `ForeignHostError`, `frontend_host`, size-aware
PNG), `app/api/v1/public.py` (full read model + cache header + join/serialization fixes),
`app/api/v1/assets.py` (`qr_version` in `BASE_SELECT`, per-asset detail value),
`openapi.yaml` (both new paths, `size`, updated summaries), `requirements.txt`,
`requirements-dev.txt`;
`tests/test_openapi.py` + `tests/test_route_auth_coverage.py` unchanged and green;
D: none (temp `_apply_p6_migration.py` script used to apply migration 010 and removed)

### Frontend
C: `src/app/(public)/a/[ref]/PublicAssetView.tsx`;
M: `src/lib/api/client.ts` (`apiBlob`), `src/lib/qr-service.ts` (full rewrite),
`src/components/features/qr/AcQrActions.tsx` (full rewrite),
`src/app/(public)/a/[ref]/page.tsx` (fetch + client view),
`src/app/(portal)/technician/scan/page.tsx` (full rewrite),
`src/app/(portal)/viewer/search/page.tsx` (`batchIds` prop),
`src/lib/permissions.ts` (`roleAssetDetail`), `package.json` (`html5-qrcode`),
`eslint.config.mjs` (grandfather entry removed + comments);
D: none (the mock code inside `qr-service.ts` was replaced in place)

## Mock / direct-DB removal (grep evidence)

- `USE_MOCK_QR|BarcodeDetector|api/super-admin|getQrStatus` across `src` → **0 code
  matches** (3 residual matches are historical mentions inside doc comments of
  `qr-service.ts`, `AcQrActions.tsx` and the scan page).
- `localStorage.` (actual calls) → only `src/lib/storage/**` (the designated module) —
  the **only remaining mock/localStorage service is now `user-service.ts` (P7)**.
- Direct-DB (`pg`, `src/lib/server`, `src/app/api`): gone since P2; nothing reintroduced.
- QR data is never stored: no `qr` table exists anywhere; images/PDFs are minted per
  request (the "never stored" contract from plan #9/#10).

## API integrations (endpoint → screen)

| Endpoint | Consumers |
|---|---|
| `GET /assets/<ref>/qr?format=png` (`&size=` available) | AcQrActions preview (blob → `next/image`) |
| `GET /assets/<ref>/qr?format=svg` | AcQrActions SVG download |
| `GET /assets/<ref>/qr?format=payload` | AcQrActions "Scanning opens …" caption |
| `POST /assets/<ref>/qr/regenerate` | AcQrActions Regenerate (confirm → version toast) |
| `GET /assets/qr/batch?ids=…` | AcQrActions Print (current search result set, default the single asset) |
| `POST /qr/resolve` | technician scan (after a decode; navigates to `/technician/assets/<uuid>`) |
| `GET /public/assets/<ref>` (anonymous) | `/a/[ref]` server fetch → `PublicAssetView` |
| `GET /assets/<ref>` → `qr_version` | asset detail (consumers unchanged; value is now per-asset) |

## Validation results

Backend (`TEST_DATABASE_URL` → postgres container on **5433**; Docker Desktop was stopped
at the start of this phase — engine + the `acms-test` container were started, then
migration 010 applied):
- **Full suite: 216 passed, exit 0** (198 Phase-5 baseline + 18 new QR tests).
- Plan **Verify** subset `pytest tests/test_qr_v1.py -q` → **18 passed, exit 0**,
  including the plan's comment lines: **foreign host rejected**, **unknown ref 404**,
  **public view has no serial/technician**, and the QR PNG decoded with zxing-cpp
  asserting the exact `{FRONTEND_URL}/a/{asset_uuid}` URL. The regenerate test also proves
  the **Done-when**: decoded URL identical before/after (a sticker survives re-issues), and
  the public test asserts the exact 10-key set.
- `ruff check app tests` → all checks passed; `black --check app tests` → 72 files
  unchanged.
- `ruff check .` → 8 errors, **all in the two pre-existing backend-root scratch scripts**
  (`diagnose_db.py` 6, `test_endpoints.py` 2) — untouched on purpose (user tooling).
- `tests/test_openapi.py` + `tests/test_route_auth_coverage.py` → green with both new
  routes documented and auth-marked.

Frontend (`cd Frontend`, final tree):
- `npx tsc --noEmit` → **exit 0**
- `npx eslint . --quiet` → **0 errors**; `npx eslint .` → **46 warnings** — *below* the
  60-warning grandfathered baseline: the rewritten `qr-service.ts`, `AcQrActions.tsx` and
  scan page shed their old warnings, and **none of the files touched this phase produce
  any warning** (the grandfather list shrank by one entry).
- `npx vitest run` → **14 passed** (10 adapter, 4 offline-queue)
- `npm run build` (placeholder `NEXT_PUBLIC_*` vars) → **exit 0**, 36 routes + `ƒ Proxy`

Plan **Verify** section status:
- `pytest tests/test_qr_v1.py -q` → ✓ 18 passed.
- "decode generated PNG with zbar/pyzbar" → done with **zxing-cpp** (deviation note 1).
- `npx playwright test e2e/qr-landing.spec.ts` → **BLOCKED**: no playwright config /
  `e2e/` directory exists in the repo (same as Phases 4–5); behaviour is unit-covered by
  `test_qr_v1.py`.
- "physical test: print one label at 3 cm, scan with a mid-range Android + iPhone" →
  **your task** (needs a printer, HTTPS on the dev host, and the phones).

**Done-when walk-through:** a printed sticker encodes `{FRONTEND_URL}/a/{asset_uuid}` —
the stable UUID, never the internal `ac_id` — so asset edits, moves and even label
re-issues leave it resolving (tests decode the PNG before/after regenerate and assert the
same URL). An anonymous scanner hitting `/a/<uuid>` receives exactly the 10 allow-listed
fields (asserted as an exact key set, with the seeded serial/technician/remarks strings
searched-and-absent from the payload) behind a rate limit and a 60 s public cache.

## Deviations & notes

1. **zxing-cpp instead of zbar/pyzbar** — pyzbar needs the system `libzbar` DLL, which
   cannot be installed here; `zxing-cpp` ships self-contained wheels. Test-only, pinned in
   `requirements-dev.txt`; the test docstring records the substitution.
2. **`&size=` was documented as "already built" but never was** — the plan's kept route
   signature lists it, so it is now implemented (integer px, 128–2048, whole-module
   rounding; SVG ignores it; documented + 400-tested).
3. **Permissions** — plan says batch is "for SUPER_ADMIN/MANAGER": implemented as
   `EXPORT_DATA` for the batch and `EDIT_ASSET` for regenerate (both = SUPER_ADMIN +
   FACILITY_MANAGER on the backend matrix). The **UI** gate stays SUPER_ADMIN-only to
   preserve the existing surface (`AcQrActions` only ever rendered for that role); a
   manager can still call both endpoints directly. `ids` is optional (all active assets,
   capped) as a small convenience beyond the plan's `?ids=…`.
4. **Public view bugs found & fixed** — the P1 minimal route joined
   `blocks … ON b.block_id = r.block_id`, but `rooms` has no `block_id`, so the anonymous
   endpoint would 500 on every call (it had no test until now); `DATE` values were also
   going out through the encoder's non-ISO default. Both fixed and pinned by
   `test_public_asset_exact_reduced_fields`.
5. **Public response dropped `asset_uuid`** — the P1 minimal payload had it; the plan's
   field list does not, and the landing page already knows the ref from its own URL, so
   the endpoint now returns exactly the 10 plan fields.
6. **Signed-in redirect is client-side** — no Supabase server helper exists in the repo
   (only `src/lib/supabase/client.ts`) and the `(public)` layout has no session access, so
   `PublicAssetView` uses the existing `AuthContext` and `router.replace`s after bootstrap
   (the reduced view is SSR'd first, which also keeps the anonymous/no-JS case intact).
   `roleAssetDetail` mirrors `ROUTE_ROLES` exactly so the portal guard admits the target.
7. **Regenerate confirm text corrected** — the old mock UI said old codes stop working;
   the plan and the backend guarantee the opposite (payload is URL-stable), so the confirm,
   the toast and the caption all now say printed stickers keep scanning.
8. **Batch print scope** — the plan shows `AcQrActions` using "batch print" but the
   component is per-row; it gained an optional `batchIds` prop and `viewer/search` passes
   the current page's asset ids, so Print produces a real multi-label sheet (default is
   still this one asset).
9. **`html5-qrcode`** added (`^2.3.8`) — plan says "already used in Frontend_1"; this repo
   did not have it. Bundled types, QR-only decoding, `verbose: false`.
10. **Rate limits** — new scopes `qr-resolve` 30/min, `qr-regen` 30/min, `qr-batch`
    10/min; `public-asset` stays 30/min. The limiter's storage is a session-wide singleton,
    so all test counts were kept under budget.
11. **`openapi.yaml` / schema.d.ts** — hand-maintained spec updated and pinned by tests;
    `gen:api` was not re-run (FE helpers are hand-typed, the established pattern).
12. **Docker** — the local test DB container had exited with Docker Desktop; the engine and
    the `acms-test` container were started as part of this phase (no image/state changes).

## Remaining blockers

1. **Credential rotation** (yours) → then: apply migrations 001–010 + the auth trigger to
   the Supabase/dev DB, and run the live end-to-end (login → admin opens QR panel →
   download/print → phone scan → technician/asset redirect → public landing in a private
   window shows only reduced fields with the cache header; regenerate shows the audit row
   in the live `audit_logs`). Migration 010 was applied to the **localhost:5433 test DB
   only**.
2. **Physical phone test** (plan line — yours): print a label ~3 cm, serve the frontend
   over HTTPS (cameras need a secure context), scan with a mid-range Android + iPhone,
   including the torch toggle and the permission-denied path.
3. **Playwright e2e** not present in the repo → the plan's `e2e/qr-landing.spec.ts` line is
   skipped (behaviour is unit-covered by `test_qr_v1.py`, including decode-to-URL).
4. `docs/private-data/*.xlsx` acceptance dataset still absent (noted since Phase 1).
5. Baseline git commit is your task (no git used by me).

## Exact next task

**Phase 7 — Dashboards, reports & audit** (`acms-plan/phases/phase-7-dashboards-reports-audit.md`):
retire the last mock service `user-service.ts` onto `/api/v1/users` + profiles, wire the
role dashboards/reports/audit pages to real endpoints, and shrink the
`eslint.config.mjs` grandfather list to zero.
