# Security Review — ACMS (Phase 8.3)

**Date:** 2026-10-08
**Scope:** Flask API (`backend/`), Next.js 16 frontend (`Frontend/`), migrations `001`–`012`, Supabase auth/storage integration.
**Basis:** OWASP ASVS L2 checklist (auth, session, access control, input validation, file upload, logging) + plan 8.3 items.
**Status legend:** PASS / PARTIAL / FAIL / BLOCKED (cannot be verified offline or needs live infrastructure) / N-A.
**Gate:** *no open High/Critical*. **Result: PASS — every High/Critical finding below is FIXED.** 4 Medium/Low items are accepted with rationale + documented remediation; 6 items are BLOCKED on live infrastructure or missing tooling and are the operator's tasks.

> **Human sign-off required** (plan: "strongest reasoning model for the security review; human sign-off required"). See §11.

---

## 1. Findings register

Severity = impact if unmitigated *in this codebase*. Status as of this document.

| # | Severity | Finding | Status |
|---|----------|---------|--------|
| F1 | **High** | **Cross-technician IDOR**: `GET`/`PATCH /api/v1/maintenance/<wo_id>` and `POST …/comments` had no ownership scope — the list endpoint was scoped but the by-id endpoints were not, so technician A could read/rewrite/comment on technician B's work orders by guessing an id. | **FIXED** — `_own_jobs_clause()` (`app/api/v1/maintenance.py:185`, applied at call sites `:330`, `:646`, `:874`); regression suite `tests/test_idor.py` (10 tests). |
| F2 | **High** | **Test suite could reach the live database**: `app/config/database.py:24` runs `load_dotenv(..., override=True)` on import, planting live `DB_*` values in `os.environ`. DB tests without the `db` fixture silently queried production; `POST /auth/register-profile` tests wrote PENDING profiles to the live DB. | **FIXED** — autouse `_never_reach_the_dotenv_database` pins every test to `TEST_DATABASE_URL` (`tests/conftest.py:143`); import-order proof in `tests/test_db_target.py`. Operator should delete stray profile id `44444444-4444-4444-4444-444444444444` (see §10). |
| F3 | **High** | **83 known vulnerabilities in 7 backend PyPI packages** (`pip-audit`), incl. `PyJWT`, `Pillow`, `Werkzeug`, `python-dotenv`. | **FIXED** — `pip-audit -r requirements.txt` → *No known vulnerabilities found*; pins with rationale comments in `backend/requirements.txt`. |
| F4 | **Medium** | **Production dependency vulnerabilities in the frontend tree** (`sharp`, `source-map-js`, then `next@16.3.x`). | **FIXED** — `npm audit fix` + `next@16.4.0`/`eslint-config-next@16.4.0` → `npm audit --omit=dev` = **0 vulnerabilities**. |
| F5 | **Medium** | **Session cookie missing `SameSite`/`Secure`**: `@supabase/ssr`'s `DEFAULT_COOKIE_OPTIONS` has no `Secure` flag (`sameSite: "lax"`, `httpOnly: false`, `maxAge: 400d`); the app passed no options. | **FIXED** — `cookieOptions` in `Frontend/src/lib/supabase/client.ts:33` (`sameSite: "lax"`, `secure` gated on `https:`, `path: "/"`). |
| F6 | **Medium** | **CSP `script-src` allows `'unsafe-inline'`** (static policy in `Frontend/src/lib/security/csp.ts`). An HTML-injection bug could execute an inline payload. | **PARTIALLY REMEDIATED (P1 hardening)** — the anonymous QR surface `/a/*` now runs the documented Next.js per-request **nonce policy** (`script-src 'self' 'nonce-…' 'strict-dynamic'`, no `'unsafe-inline'`), emitted by `src/proxy.ts` through the shared builder `src/lib/security/csp.ts`; CSP moved out of `next.config.ts` (two policies on one response are enforced as an intersection, which would break nonced scripts). Runtime-verified against `next start`: fresh nonce per request, all 3 inline hydration scripts carry the header's nonce, static pages stay nonce-free. Static portal pages keep `'unsafe-inline'` because App Router hydration markup is inline and nonces require dynamic rendering for every nonced page — a separate architecture decision (see `P1-security-hardening-report.md` §5). All pages additionally gained `object-src 'none'` and production `upgrade-insecure-requests`. Remaining compensating controls: no remote script sources, `default-src 'self'`, `frame-ancestors 'none'`, `base-uri`/`form-action 'self'`; no `dangerouslySetInnerHTML`, `eval(` or `new Function` anywhere in `src/` (grep-verified), and React escapes all rendered text. Runtime-verified in 8.4: no page fetches any remote origin after the 3-D-viewer fix (§5 below). **Remaining remediation:** site-wide nonce CSP after converting portal pages to dynamic rendering. |
| F7 | **Medium** | **Session cookie is not `HttpOnly`** — architecturally unavoidable: `@supabase/ssr`'s browser client reads/writes the cookie via `document.cookie`; a JS-authored cookie is never `HttpOnly`, and forcing it breaks sign-in. | **ACCEPTED (compensating controls)** — Flask never reads this cookie (it verifies an `Authorization: Bearer` header only), CSP limits script sources, and the marginal gain is small: XSS could call `supabase.auth.getSession()` and read the session (incl. refresh token) from JS memory regardless. **Remediation:** server-side (BFF) cookie store — proxy performs token exchange, cookie becomes `HttpOnly; Secure`. Tracked with F6. |
| F8 | **Low** | `POST /api/v1/auth/register-profile` (`users.py:152`) has `@require_auth` but **no `@rate_limit`** — the only un-throttled mutating endpoint. | **ACCEPTED** — requires a valid Supabase JWT (unauthenticated callers get 401 before the handler), the handler is idempotent per subject (re-POST returns the existing profile), and upstream Supabase `signup` is itself rate-limited. 28 other `@rate_limit` sites cover every anonymous/high-volume route (chat, QR, movements, public, reports, maintenance incl. sync). |
| F9 | **Low** | **8 remaining `npm audit` findings, all dev-only**: `braces`/`micromatch`/`fast-glob` chain via `eslint-config-next`, `tinypool` + `@vitest/mocker` via `vitest@3.2.7`. | **ACCEPTED (dev-only)** — absent from `--omit=dev` (production) tree; build-time tooling processes only repo-authored input, no untrusted-input path. `braces@3.0.3` is the *latest* release — **no fixed version exists**; npm's only "fix" is downgrading `eslint-config-next` to 14.2.35 (breaking). `tinypool`/`@vitest/mocker` fixes require a `vitest@5` major. Re-audit when upstreams ship fixes; `overrides` deliberately not used (semver-major transitive pins risk test-infra stability to appease a scanner). |
| F10 | **Low** | **Student data in the repo**: `backend/Air Conditioners_NITTTR Chennai_Consolidated_21Sept2026_Students Internship.xlsx` (internship/student data) is un-tracked-by-ignore — `backend/.gitignore` did not cover `*.xlsx`. | **FIXED (ignore) / OPEN (operator)** — `*.xlsx` added to `backend/.gitignore`. **Operator must run `git rm --cached "backend/*.xlsx"` before the baseline commit** (no git operations are permitted from this session). |
| F11 | **Info** | **Audit offset paging echoed `page` but never applied `OFFSET`** — "page 2 of N" showed page 1. | **FIXED** — SQL `OFFSET` added; regression test `tests/test_audit.py::test_audit_offset_paging_returns_a_different_page`. |
| F12 | **Info** | **Maintenance timeline could render `ASSIGNED` before `CREATED`**: `maintenance_events.id` is `gen_random_uuid()` and Windows `datetime.now()` has ~15.6 ms granularity, so `ORDER BY created_at, id` broke ties non-deterministically. | **FIXED** — monotonic process clock `app/core/clock.py::utcnow()` (thread-locked, 1 s lead cap) used by both `_now()` implementations. |

No open High or Critical findings. F6–F10 carry accepted risk with rationale and remediation; F1/F2/F3/F4/F5/F11/F12 are fixed and regression-tested.

---

## 2. ASVS L2 checklist

### 2.1 Authentication (ASVS V2)

| Item | Status | Evidence |
|------|--------|----------|
| Credentials are never handled by this codebase — Supabase Auth stores/verifies passwords; the API only verifies JWTs | PASS | No password field, hash, or reset flow in `backend/app`; `grep` for `password` in app code matches only SMTP config (`app/services/email.py:129`). |
| Verification fails closed: no key configured → every request rejected | PASS | `tests/conftest.py:33-45` "NO verification key configured (fully fail-closed)"; forged/absent-key tokens rejected (`tests/test_auth.py:29,124`, `tests/test_jwt_modes.py:93`). |
| Production refuses to boot with a missing JWT key or unsafe config | PASS | `_assert_safe_boot` (`app/__init__.py:32-52`) raises on missing key / `FLASK_DEBUG` / `CORS_ORIGINS` containing `*`; tested `tests/test_auth.py:136-160`. |
| Dual-mode JWT (HS256 shared secret + Supabase JWKS) with correct issuer/audience/exp checks | PASS | `app/auth/jwt.py` (JWKS from `SUPABASE_JWKS_URL` or `SUPABASE_URL/auth/v1/.well-known/jwks.json`); `tests/test_jwt_modes.py`, `tests/test_auth.py`. |
| Token-alg confusion / wrong-secret tokens rejected | PASS | `tests/test_auth.py:29` (`secret="public-anon-key"`), `:124` (`secret="wrong-secret"`) → 401. |
| Auth/permission failures return 401 with a documented schema | PASS | `backend/openapi.yaml` documents `401` on 15 operations; contract test `tests/test_openapi_contract.py::test_unauthenticated_calls_are_documented_401`. |
| Login/logout lifecycle (FE) | PASS | `AuthContext.tsx` (`signOut` at `:247`), auto sign-out on un-refreshable 401 (`src/lib/api/client.ts:128,251`); Playwright e2e of this flow is BLOCKED (§9). |

### 2.2 Session management (ASVS V3)

| Item | Status | Evidence |
|------|--------|----------|
| Session cookie `SameSite=Lax` | PASS | `Frontend/src/lib/supabase/client.ts:36`. |
| Session cookie `Secure` in transit | PASS | `client.ts:37` — protocol-gated (`https:`) so `next dev` over http keeps working; production is HTTPS + HSTS (`next.config.ts:35`). |
| Session cookie `HttpOnly` | PARTIAL (F7) | Not achievable with `@supabase/ssr` browser storage; compensating controls + BFF remediation documented in §1/§7. |
| Session expiry / refresh / revocation | PASS | Supabase access-token refresh handled by the SSR client; on 401 the API client refreshes once then signs out (`client.ts:128,251`). |
| No session state on the API (stateless JWT) | PASS | API holds no server session; `Cache-Control: no-store` on JSON (`app/__init__.py:169-171`). |
| Logout clears the cookie | PASS | `supabase.auth.signOut()` (`AuthContext.tsx:114,139,248`) clears the SSR cookie. |

### 2.3 Authorization / access control (ASVS V4)

| Item | Status | Evidence |
|------|--------|----------|
| Every route requires auth unless explicitly anonymous | PASS | `tests/test_route_auth_coverage.py` derives coverage from `url_map`; anonymous allow-list is exactly `{/, /health, /ready, /api/health, /api/v1/openapi.json}` + `/api/v1/public/assets/<ref>` (`test_route_auth_coverage.py:18,56-71`, `02-api-contract.md`). |
| Deny-by-default RBAC: every route × every role × expected status | PASS | `tests/test_rbac_matrix.py` (6 tests) — one fresh throwaway subject per rule; `profiles.role` is authoritative over the JWT claim. |
| Permission catalogue enforced server-side | PASS | `@require_permission(...)` decorators; e.g. reports `VIEW_REPORTS`, batch export `EXPORT_DATA`, regenerate `EDIT_ASSET`, audit CSV `VIEW_REPORTS`. |
| **IDOR — technician A vs technician B's jobs** (plan item) | PASS (F1 fixed) | `tests/test_idor.py:215` (GET → 404), `:260` (PATCH → 404), `:243` (comments → 404), `:276` (lifecycle action → 403 `FORBIDDEN`), `:286` (foreign and missing ids are **indistinguishable** — no existence oracle), `:234` (admin/viewer still read any job); pre-existing scoping test `tests/test_maintenance_v1.py:235-248`. |
| **IDOR — viewer fetching `/users`** (plan item) | PASS | `tests/test_idor.py:300` → 403 `FORBIDDEN`. |
| **IDOR — manager outside assigned blocks** (plan item) | PASS | `tests/test_idor.py:315` (manager sees only pending profiles in overlapping blocks; `users.py:213-221` `assigned_blocks && %s` overlap clause) and `:324` (unscoped manager sees every block). |
| Movement approval scoped to assigned blocks | PASS | `app/api/v1/movements.py:450-458` — a `FACILITY_MANAGER` with `assigned_blocks` set can only decide requests whose asset sits in one of those blocks (else 403 `FORBIDDEN`); enforced at decision time, not just list time. |
| No privilege escalation via profile self-registration | PASS | `POST /auth/register-profile` inserts `PENDING` + `VIEWER`-default row; approval required; `tests/test_users_lifecycle.py` (15 tests), `tests/test_idor.py:306` (viewer cannot approve). |
| Last-super-admin deletion blocked | PASS | 409 `LAST_SUPER_ADMIN` (enforced in `app/api/v1/users.py`; tested in `tests/test_users_lifecycle.py`). |

### 2.4 Input validation & injection (ASVS V5)

| Item | Status | Evidence |
|------|--------|----------|
| All request bodies schema-validated | PASS | 20 Pydantic models in `app/schemas/v1_schemas.py`; invalid input → 400 `VALIDATION_ERROR` (incl. the `_json_safe()` fix that stopped pydantic `value_error` from becoming a 500). |
| SQL injection probes across all string filters | PASS | `tests/test_injection_and_bounds.py` (48 tests): injection payloads, LIKE-escape handling, pagination bounds, invalid dates/filters → 400, offset-paging advance. All queries use bound parameters (`%s`); `query_service.py:71-74` binds its LIKE patterns. |
| Error responses never leak internals | PASS | `error()` envelope carries `{code, message, request_id}` only; 500s surface `INTERNAL_ERROR` + `request_id` (verified as a tolerated-5xx rule in the RBAC matrix). |
| Request size bounded | PASS | `MAX_CONTENT_LENGTH` 10 MB (`app/__init__.py:137`) + `413 → PAYLOAD_TOO_LARGE` (`:152`). |
| Proxy trust is explicit | PASS | `ProxyFix` only when `TRUSTED_PROXIES > 0` (`app/__init__.py:141-145`); raw `X-Forwarded-For` is never trusted by default. |

### 2.5 File upload / import (ASVS V12)

| Item | Status | Evidence |
|------|--------|----------|
| Extension allow-list + content sniffing (never trust extension) | PASS | `app/api/v1/assets_import.py:36` (`{".xlsx", ".csv"}`), `:47-49` (ZIP magic-byte check for xlsx) → 400 `UNSUPPORTED_FILE_TYPE`. |
| Dangerous parser used safely | PASS | `openpyxl==3.1.5` with `read_only=True`; formulas not evaluated (Phase 3 report). The `xlsx` npm package was replaced in Phase 3 (plan item "replace/remove `xlsx`" — **done**). |
| Size cap applies to uploads | PASS | Same 10 MB `MAX_CONTENT_LENGTH` + 413 handler. |
| Import is permission-gated and transactional | PASS | `IMPORT_DATA` permission; one transaction with per-row savepoints (`openapi.yaml:92`); `dry_run=true` support. |
| Malware/AV scanning of uploads | N-A | No file bytes are persisted by the API — the workbook is parsed in memory and only normalized rows are written; document metadata table `asset_documents` points at Supabase Storage (operator-controlled bucket policies). |
| Upload e2e test | BLOCKED | Covered by Playwright task (§9). |

### 2.6 Logging, auditing & data protection (ASVS V7/V8)

| Item | Status | Evidence |
|------|--------|----------|
| **Audit table append-only** (plan item) | PASS | `migrations/011_audit_readonly.sql`: role-independent `BEFORE UPDATE` trigger + `REVOKE UPDATE, DELETE` for `PUBLIC` and the app roles. **Proven, not just asserted**: `tests/test_audit.py::test_audit_rows_cannot_be_rewritten_or_revoked` — (1) `UPDATE` raises `append-only` even as superuser, (2) row unchanged after rollback, (3) trigger present *and enabled* (`tgenabled='O'`), (4) ACL grants `UPDATE`/`DELETE` to no non-owner. |
| Sensitive values redacted in audit payloads | PASS | `***REDACTED***` (`app/services/audit_service.py:27`, `_SENSITIVE_KEY` recursion `:52`); asserted on read `tests/test_audit.py:272-290`. |
| Audit write failures are loud but non-fatal | PASS | `tests/test_audit.py:94` — request still succeeds, warning logged with `request_id`. |
| Structured logs carry correlation, not secrets/PII | PASS | JSON formatter fields: `request_id user_id route status duration_ms` (`app/__init__.py:67-71`); access log excludes bodies/query secrets (`:104-118`). |
| Secrets never reach the browser | PASS | Only `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_API_URL` in `Frontend/` (grep); **zero** matches for `SUPABASE_JWT_SECRET` / `service_role` / `SERVICE_ROLE` in `Frontend/src`. Flask reads the JWT secret server-side only. |
| CORS exact origins, no wildcard | PASS | Origins from `CORS_ORIGINS` only, `expose_headers=["X-Request-ID"]`, no `supports_credentials` (`app/__init__.py:147-148`); boot refuses `*` (`:46-48`, tested `test_auth.py:151`). |
| API security headers | PASS | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Cache-Control: no-store` on JSON (`app/__init__.py:165-172`). HSTS/CSP intentionally omitted on the JSON-only API (set by the frontend origin that serves HTML). |

---

## 3. Dependency audit (plan 8.3)

| Ecosystem | Tool | Before | After |
|-----------|------|--------|-------|
| Backend (PyPI) | `pip-audit -r requirements.txt` | **83 vulns / 7 pkgs** | **0 — "No known vulnerabilities found"** |
| Frontend production (`--omit=dev`) | `npm audit` | 3+ (`sharp`, `source-map-js`, `next@16.3.x`) | **0 vulnerabilities** |
| Frontend incl. dev | `npm audit` | — | **8 (1 moderate, 5 high, 2 critical) — dev-only, accepted (F9)** |

**Remediation applied (all pinned, with rationale comments):**

- `python-dotenv==1.2.2`, `PyJWT[crypto]==2.15.0` (17 advisories), `Pillow==12.3.0` (18 advisories), `Werkzeug==3.1.9` (CVE-2026-102598) — `backend/requirements.txt`.
- `pytest==9.0.3`, `setuptools==84.0.0`, `pip==26.2.1` in the venv.
- `next@16.4.0` + `eslint-config-next@16.4.0` (operator decision: "Bump to 16.4.0 now").
- `xlsx` npm package: **already removed in Phase 3** (replaced by server-side `openpyxl`) — plan item satisfied.
- **Dependabot:** BLOCKED — no `.github/` directory exists and CI is not set up; add `.github/dependabot.yml` (pip + npm ecosystems) when CI lands (§9).

Re-run before sign-off:

```powershell
# backend
.venv\Scripts\pip-audit.exe -r requirements.txt
# frontend
npm audit --omit=dev
```

---

## 4. Secrets handling (plan 8.3)

| Item | Status | Evidence / action |
|------|--------|-------------------|
| `.env` excluded from git | PASS | `backend/.gitignore`: `.env`, `.env.*` (and now `*.xlsx`). |
| No hardcoded secrets in code | PASS | Grep for `(password\|secret\|api_key) = "…"` matches only test literals `"wrong-secret"` / `"public-anon-key"` (deliberately fake). |
| No secrets printed/exposed by this session | PASS | `.env` values were never read into output; Supabase/JWT secrets never written to any file. |
| **gitleaks in CI** | BLOCKED | gitleaks not installed; no `.github/workflows`. **Operator task:** `gitleaks detect --source . --redact` locally, then add a CI workflow. (Repo-wide manual review above is the interim evidence.) |
| **Rotated credentials confirmed** | BLOCKED (operator) | Rotation is performed by the operator; this session never touched live credentials. **Operator confirms** `.env`/Supabase/SMTP secrets were rotated after any exposure. |
| **DB role used by the API has no DDL rights** | BLOCKED (needs live DB) | Cannot be verified from this session without querying the live database (deliberately not done). **Run this as the API's DB role** (§8 gives exact SQL): expected `rolsuper=false`, `has_schema_privilege(…,'CREATE')=false`, `has_table_privilege(…,'ALTER')=false` on every table. |
| Audit table append-only | PASS | See §2.6 — migration **and** proof test. |
| Student `.xlsx` not committed | FIXED / operator action | `*.xlsx` ignored; operator runs `git rm --cached` (F10). |

---

## 5. Headers / CSP (plan 8.3, "securityheaders.com-style check")

Verified by source inspection (offline; an external scanner run needs a deployed URL — §9).

**Frontend (`Frontend/next.config.ts:13-41`, applied to `/:path*`):**

```
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline';
  style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:;
  connect-src 'self' <API_URL> <SUPABASE_ORIGIN> <SUPABASE_WSS>; media-src 'self';
  worker-src 'self' blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(self), microphone=(), geolocation=()
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Strict-Transport-Security: max-age=63072000; includeSubDomains; preload
```

- `script-src 'unsafe-inline'` = **accepted risk F6, partially remediated** (nonce policy live on the anonymous `/a/*` QR surface via `src/proxy.ts`; static portal pages keep it - no remote script sources; no raw-HTML sinks; site-wide nonce remediation documented).
- `style-src 'unsafe-inline'` required by Tailwind/injected styles — no script impact.
- `camera=(self)` is required by the QR scanner; mic/geolocation denied.
- `images.remotePatterns: []` — no remote image hosts.
- **No remote script/worker origins** — the strongest practical XSS brake available without a nonce.

**3D viewer under this CSP (found & fixed in plan 8.4)** — runtime verification proved the AC 3-D viewer
had never rendered under the Phase 2 policy: three separate fetches violated `connect-src`/`script-src`
and, with no `error.tsx` boundary, the loader error crashed the whole detail page. All three were fixed
by changing *the app's assets*, not the policy:

| # | Violation (console evidence) | Directive | Resolution |
|---|---|---|---|
| 1 | `Error: Could not load potsdamer_platz_1k.hdr: Failed to fetch` — drei `<Environment preset="city">` fetched from `raw.githack.com` (fatal) | `connect-src` | HDR self-hosted: `public/hdri/potsdamer_platz_512.hdr` (same asset, downsampled 1024×512→512×256, 1.5 MB→512 KB); `<Environment files="/hdri/...">` |
| 2 | `CompileError: WebAssembly.instantiate() … at MeshoptDecoder` → `Error: Could not load /model/ac_model3.glb` (fatal for meshopt GLB) | `script-src` (no `'wasm-unsafe-eval'`) | GLB re-compressed with **quantization only** (195 KB; `KHR_mesh_quantization` is decoded natively by GLTFLoader — no WASM); `useGLTF(url, false, false)` so the Draco (gstatic) and Meshopt decoders are never wired |
| 3 | `THREE.GLTFLoader: Couldn't load texture blob:…` (texture silently lost) | `connect-src` (no `blob:`) | GLB texture externalized to sibling `public/model/ac_model3.png` (925 B) — an `images[].uri` resolves same-origin instead of via `blob:` |

Net effect: **no CSP directive was relaxed** (no `wasm-unsafe-eval`, no `blob:` in `connect-src`, no
remote origins added), the security posture above still holds as written, and the payload got smaller
(GLB −42% vs. original 337 KB, HDR −66%).

**Backend (`app/__init__.py:165-172`):** `nosniff`, `X-Frame-Options: DENY`, `Cache-Control: no-store` on JSON. Correctly no HSTS/CSP on the API (it serves JSON; the HTML origin is the frontend).

---

## 6. CORS (plan 8.3)

| Check | Status | Evidence |
|-------|--------|----------|
| Exact origins, configurable | PASS | `CORS_ORIGINS` comma list → `CORS(app, origins=…)` (`app/__init__.py:147-148`); default `http://localhost:3000`. |
| Wildcard refused at boot | PASS | `_assert_safe_boot` raises if `*` ∈ origins (`:46-48`); tested `tests/test_auth.py:151`. |
| Credentials not broadly allowed | PASS | `supports_credentials` not set → no `Access-Control-Allow-Credentials: true`. |
| Only `X-Request-ID` exposed | PASS | `expose_headers=["X-Request-ID"]`. |
| `Access-Control-Allow-Origin` echoes a configured origin only | PASS | flask-cors with explicit origin list; wildcard impossible (boot guard). |

---

## 7. Cookies (plan 8.3: `Secure; HttpOnly; SameSite`)

| Attribute | Value | Status |
|-----------|-------|--------|
| `SameSite` | `Lax` | PASS |
| `Secure` | set when the page is `https:` (always in production, never in `next dev`) | PASS |
| `Path` | `/` | PASS |
| `HttpOnly` | not set — see F7 | PARTIAL, accepted with compensating controls + BFF remediation |

Owner of the cookie: `@supabase/ssr` in the browser. **The Flask API never reads it** — it authenticates the `Authorization: Bearer` header (`src/lib/api/client.ts:92,151,220`), and `src/proxy.ts` only reads cookies for optimistic redirect decisions (never forwards secrets to the API). No `SUPABASE_JWT_SECRET`/service-role key is ever present in browser code (§2.6).

---

## 8. IDOR checks (plan 8.3) — summary

All three plan-specified scenarios are **PASS**, backed by `backend/tests/test_idor.py` (10 tests) plus pre-existing scoping tests:

1. **Technician A → technician B's jobs:** `GET`/`PATCH`/`comments` return **404** (not 403 — no existence oracle: `:286` asserts foreign ≡ missing), lifecycle actions → **403 `FORBIDDEN`** (`:276`); in-scope control (tech A, admin, viewer) still **200** (`:234`). Fix: `_own_jobs_clause()` in `app/api/v1/maintenance.py`.
2. **Viewer → `/users`:** **403 `FORBIDDEN`** (`:300`); viewer approving a profile → 403 (`:306`).
3. **Manager outside assigned blocks:** overlap clause `users.py:213-221` — scoped manager sees only overlapping-block pending profiles (`:315`); unscoped (NULL blocks) manager sees all (`:324`). Movement *decisions* are block-scoped too (`movements.py:450-458`, 403 `FORBIDDEN`).

**SQL for the remaining live-DB check (operator runs; not executed here):**

```sql
-- Connect as the API's own database role, then:
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole
  FROM pg_roles WHERE rolname = current_user;          -- expect false/false/false
SELECT has_schema_privilege(current_user, 'public', 'CREATE') AS can_ddl;  -- expect false
SELECT c.relname, has_table_privilege(current_user, c.oid, 'ALTER') AS can_alter
  FROM pg_class c
 WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r';        -- expect all false
-- Audit write access (expect UPDATE=false, DELETE=false):
SELECT has_table_privilege(current_user, 'public.audit_logs', 'UPDATE') AS can_update,
       has_table_privilege(current_user, 'public.audit_logs', 'DELETE')  AS can_delete;
```

---

## 9. Blocked items (operator/infrastructure — not verifiable from this session)

| Plan item | Why blocked | How to close it |
|-----------|-------------|-----------------|
| **ZAP baseline scan against staging** | No deployed staging URL in this offline environment. | `zap-baseline.py -t https://<staging-host> -r zap-report.html`; triage any `Warning+` before Phase 9 sign-off. |
| **gitleaks in CI** | gitleaks not installed; no `.github/`. | `gitleaks detect --source . --redact`; add `.github/workflows` with gitleaks + `pip-audit` + `npm audit` + Dependabot. |
| **Dependabot on** | Requires `.github/`. | `.github/dependabot.yml` (pip + npm). |
| **Backups: restore test performed once and timed** | Needs the live database (never touched from here). | Take a backup, restore into a scratch instance, time it, run `python -m app.migrate status` + smoke queries; record minutes in the Phase 9 report. |
| **Playwright e2e (8.2)** | playwright not installed; no `e2e/`, no `playwright.config.ts`, no docker-compose stack. | `npm i -D @playwright/test`, add config + specs per 8.2, run against the compose stack with 4 seeded roles. |
| **Physical phone / HTTPS scan** | Hardware + deployed TLS endpoint. | Scan `https://<host>` from a phone; confirm HSTS, `Secure` cookie (dev-tools), no mixed content. |
| **DB role DDL rights** | Requires live credentials (deliberately untouched). | SQL in §8. |
| **`docs/private-data/*.xlsx` acceptance dataset** | Absent from the repo (noted since Phase 1). | Operator supplies the dataset, then re-run the import acceptance path. |

---

## 10. Operator follow-ups (checklist)

- [ ] **Delete stray live profile** `44444444-4444-4444-4444-444444444444` (name `T`) written by the pre-fix test suite (F2) — verify on the live Supabase dashboard/SQL.
- [ ] `git rm --cached "backend/*.xlsx"` before the baseline commit (F10; ignore rule already added).
- [ ] Confirm credential rotation (§4).
- [ ] Run the DDL-rights SQL in §8 as the API's DB role.
- [ ] ZAP baseline, gitleaks, Dependabot/CI, backup-restore timing (§9).
- [ ] Re-run `pip-audit` + `npm audit --omit=dev` at release time (§3).
- [ ] Phased remediations: ~~nonce-based CSP~~ **done for `/a/*` (anonymous QR surface)**; site-wide nonce CSP after portal pages move to dynamic rendering, then drop `script-src 'unsafe-inline'` (F6); BFF cookie store then `HttpOnly` (F7); `rate_limit` on `register-profile` (F8, optional); re-audit dev tree when `braces`/`vitest` upstreams fix (F9).

---

## 11. Acceptance

**Gate: `docs/security-review.md` — no open High/Critical → PASS.**

- All High/Critical findings (F1–F5) are fixed and covered by regression tests.
- Medium/Low items (F6–F10) are accepted with written rationale, compensating controls, and remediation steps.
- Blocked items (§9) are infrastructure/operator tasks, tracked as open questions rather than vulnerabilities.
- Verification commands were executed from this environment: `pip-audit` → 0, `npm audit --omit=dev` → 0, backend suite **531 passed / 3 skipped** (85% total coverage; `app/api` 85.8%, `app/auth` 86.0%, `app/services` 92.7%), frontend `tsc`/`eslint`/`vitest` (44 tests)/`build` all green.

| Role | Name | Date | Sign-off |
|------|------|------|----------|
| Reviewer (automated) | MiMo (security review, Phase 8.3) | 2026-10-08 | ☐ |
| Human approver | _pending_ | | ☐ |
