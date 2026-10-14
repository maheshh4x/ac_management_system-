# Phase 1 report — Backend auth, users & contract foundation

Status: **implementation complete; verified locally without a live DB.**
Two verify items remain CI/DB-bound (see §4).

## 1. What was implemented

### 1.0 Contract & tooling
- `backend/openapi.yaml` — hand-maintained OpenAPI 3.0 contract for every `/api/v1`
  route; served at `GET /api/v1/openapi.json`; enforced in CI by
  `tests/test_openapi.py` (any implemented v1 route missing from the spec fails).
- `backend/pyproject.toml` — ruff + black config, pytest, coverage scaffolding.
- One-time black/ruff baseline applied repo-wide (mechanical, no logic changes).
- `backend/app/migrate.py` — `python -m app.migrate up|status`; records
  `schema_migrations(version, checksum, applied_at)`; idempotent; **refuses to run
  if an applied migration's checksum changed**. `_`-prefixed SQL files (e.g.
  `_supabase_auth_trigger.sql`, Supabase-side only) are never applied.
- `init_db.py` now delegates to the runner (legacy runbooks keep working).

### 1.1 JWT verification
`app/auth/jwt.py` rewritten:
- HS256 mode (`SUPABASE_JWT_SECRET`/`JWT_SECRET`) **or** JWKS mode
  (`SUPABASE_JWKS_URL`, or derived from `SUPABASE_URL`); explicit algorithm
  allow-lists per mode (`HS256` vs `ES256/RS256`) — the token header's `alg` is
  never trusted unchecked.
- Claims validated: signature, `exp`, `aud=authenticated`, `iss={SUPABASE_URL}/auth/v1`
  (when SUPABASE_URL set), `sub` must be UUID.
- JWKS provider: TTL cache + throttled forced refetch on unknown `kid` (rotation
  safe, DoS-safe).
- Tests cover: expired, wrong issuer, wrong audience, `alg=none`, non-UUID sub,
  RS256 happy path, HS256-signed-with-public-key confusion attack (hand-crafted
  token, since pyjwt refuses to encode it), unknown-kid rotation refetch,
  refetch throttling.

### 1.2 Fail closed
- `create_app` **refuses to boot in production** without a verification key,
  with `FLASK_DEBUG` on, or with `CORS_ORIGINS=*` (RuntimeError, logged CRITICAL).
- Removed `allow_anonymous_legacy`, `optional_auth`, and the `AUTH_DISABLED`
  bypass from all non-test code. Tests use a test-only JWT secret fixture
  (`tests/conftest.py`; `AUTH_DISABLED` is now inert — verified by test).
- All v1 reads now require `require_auth` + permission (only `/health`, `/ready`,
  `/api/v1/openapi.json`, `/api/v1/public/assets/<ref>` are anonymous).
- Legacy `/api/*` locked centrally by `app/auth/legacy_guard.py`
  (GET→VIEW_ASSET, POST/PUT→CREATE/EDIT, DELETE→DECOMMISSION_ASSET,
  imports→IMPORT_DATA, `/api/db-test`→MANAGE_SETTINGS, `/api/chat`→authenticated);
  unmapped legacy routes fail closed with 403.
- `tests/test_route_auth_coverage.py` enumerates `app.url_map` and fails if any
  rule lacks auth coverage without being on the public allow-list.
- `run.py`: no `debug=True`, binds 127.0.0.1 by default, `FLASK_DEBUG` dev-only.
  New `wsgi.py` + `gunicorn.conf.py` (workers=2×CPU+1, timeout 30, access log).

### 1.3 Profiles, signup approval, user admin
- `migrations/006_profiles_approval.sql`: profiles + (status PENDING/APPROVED/
  REJECTED, requested_role, phone, address, staff_id unique, designation,
  assigned_blocks, years_experience, certification_no, reporting_manager_id,
  approved_by/at, rejection_reason); pre-existing rows grandfathered to APPROVED;
  `notifications` table.
- `migrations/_supabase_auth_trigger.sql`: `handle_new_auth_user()` trigger for
  Supabase (role always VIEWER, status always PENDING, role never from metadata) —
  apply manually in the Supabase SQL editor; the Flask fallback
  `POST /api/v1/auth/register-profile` is idempotent and equivalent.
- `require_auth` gates non-APPROVED profiles (403 ACCOUNT_PENDING/ACCOUNT_REJECTED)
  except `GET /api/v1/me` + register-profile (test-verified with a stubbed profile).
- New `app/api/v1/users.py`: `GET/PATCH /me` (whitelisted fields; VIEWER read-only),
  `GET /approvals` (manager-scoped listing), `POST /approvals/<id>/approve|reject`
  (manager may grant only TECHNICIAN/VIEWER + block-scope check via pure
  `evaluate_approval`, no self-approval), `GET/PATCH /users` (never self role/active,
  last-SUPER_ADMIN demote/deactivate guard → 409), notifications
  (`GET /notifications?unread=1`, `POST /<id>/read`, `POST /read-all`).
- Every mutation audited; notifications emitted on approve/reject/role change.
  `app/services/notify.py publishes `notify_job_assigned` / `notify_movement_decision`
  hooks with stable signatures for P4/P5.
- Permissions matrix extended: `APPROVE_USERS`, `COMPLETE_MOVEMENT`,
  `ASSIGN_MAINTENANCE`, `EXPORT_DATA` (manager = admin minus MANAGE_USERS/
  MANAGE_SETTINGS/DECOMMISSION_ASSET/VIEW_AUDIT, plus APPROVE_USERS).

### 1.4 Hardening
- `app/extensions.py` + rewritten `app/middleware/rate_limit.py`: flask-limiter,
  Redis (`REDIS_URL`) or in-memory dev fallback (production warns loudly); key =
  verified user id, else ProxyFix-aware remote IP — raw `X-Forwarded-For` never read.
- `ProxyFix` only behind `TRUSTED_PROXIES` explicit count (default 0).
- Security headers (nosniff, frame DENY, `Cache-Control: no-store` on JSON),
  `MAX_CONTENT_LENGTH` (default 10 MB), envelope 413/429 handlers.
- Structured JSON logging (`python-json-logger`, JSON default in production) with
  request_id/user_id/route/status/duration_ms; per-request access log.
- DB pool: `statement_timeout`, `application_name=acms-api`, pool size from env;
  `/ready` checks DB **and** migration head vs latest file.

## 2. Verification performed

| Verify item | Result |
|---|---|
| `pytest -p no:warnings` | **59 passed, 39 skipped** (skips = DB-bound, need TEST_DATABASE_URL) |
| `tests/test_route_auth_coverage.py` | pass — every route authed or allow-listed |
| `ruff check app tests` | clean |
| `black --check` | clean |
| Live smoke (dev server) | `/health` 200, `GET /api/acs` 401, `DELETE /api/acs/X` 401, `GET /api/v1/assets` 401, `/api/db-test` 401, `/api/v1/openapi.json` 200 |
| Production boot without keys / with debug / with CORS `*` | refused (RuntimeError) — unit + manual |
| Coverage of `app/auth` | 79–90% |

## 3. Coverage gate note
`--cov-fail-under=80` on app/api+app/auth currently reads ~41% **without a live
DB** because endpoint bodies are exercised by the 39 skipped tests. The gate is
designed for CI with the Postgres service container; `app/auth` alone is ~85%.

## 4. Pending (need a database — deploy decisions, not code)
1. `python -m app.migrate up` twice (idempotency) — requires TEST_DATABASE_URL or
   the target DB. Locally blocked: local Postgres 17 runs but needs a password;
   Docker Desktop installed but not started; the real Supabase DB was deliberately
   not touched (write access + credential rotation pending — human task).
2. Apply `006_profiles_approval.sql` to the real DB (via `python -m app.migrate up`)
   and run `_supabase_auth_trigger.sql` in the Supabase SQL editor when ready.
3. CI workflow (`.github/workflows/ci.yml` with a Postgres service) — Phase 0 remainder.

## 5. Files created / modified

Created: `app/extensions.py`, `app/auth/legacy_guard.py`, `app/migrate.py`,
`app/services/notify.py`, `app/api/v1/users.py`, `app/api/v1/public.py`,
`migrations/006_profiles_approval.sql`, `migrations/_supabase_auth_trigger.sql`,
`openapi.yaml`, `wsgi.py`, `gunicorn.conf.py`, `pyproject.toml`,
`requirements-dev.txt`, `tests/conftest.py`, `tests/test_jwt_modes.py`,
`tests/test_route_auth_coverage.py`, `tests/test_openapi.py`,
`tests/test_users_api.py`, `tests/test_migrate.py`, `docs/phase-1-report.md`.

Modified: `app/__init__.py`, `app/auth/jwt.py`, `app/auth/permissions.py`,
`app/middleware/rate_limit.py`, `app/config/database.py`,
`app/api/v1/{assets,assets_write,maintenance,movements,qr,system}.py`,
`app/schemas/v1_schemas.py`, `run.py`, `init_db.py`, `requirements.txt`,
`.env.example`, `tests/{test_auth,test_api_v1,test_audit,test_lifecycle,
test_transactions,test_campus_import,test_import,test_import_canonical,
test_import_flexible}.py` (auth fixtures + TEST_DATABASE_URL skip guards),
plus whole-repo black/ruff formatting baseline.

## 6. Next step
**Phase 2 — frontend foundation.** It needs Phase 1 task 1.0 (OpenAPI ✅ merged here)
and works against a Supabase test project (`APP_ENV=development` + JWT secret, which
you will have after credential rotation). Before P2 wiring can be smoke-tested
end-to-end: apply migration 006 + the auth trigger to the (dev) Supabase DB.
