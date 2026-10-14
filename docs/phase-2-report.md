# Phase 2 Report — Frontend foundation

**Status: COMPLETE** (offline-verifiable items; live Supabase smoke test pending credential rotation).
Date: 2026-10-06. Depends on Phase 1 (complete).

## Progress / what was built

### 1. Authentication architecture (Supabase Auth)
- `src/lib/supabase/client.ts` — browser client via `@supabase/ssr` (`createBrowserClient`,
  cookie storage). Only `NEXT_PUBLIC_SUPABASE_URL` + `NEXT_PUBLIC_SUPABASE_ANON_KEY`
  (publishable key) reach the browser. No JWT secret, no DB credentials anywhere in FE.
- `src/contexts/AuthContext.tsx` — rewritten around the real flow:
  Supabase session → `GET /api/v1/me` → `mapProfileToUser`. Exposes
  `{user, status, isLoading, signIn, signUp, signOut, updateSession, refreshProfile}`.
  Status routing: `pending`/`rejected` → `/pending`; approved → role home.
  `signUp` → `supabase.signUp` + `POST /api/v1/auth/register-profile`.
  Role snapshot exported to `lib/permissions` (replaces the localStorage role read).
- `src/app/(public)/pending/page.tsx` — pending/rejected account wall.
- Login page: real `signInWithPassword`; demo chip accounts, `Admin@123` autofill and
  hot-linked Wikimedia image removed (local `public/images/nitttr-campus.jpg`).
- Signup page: real `signUp` + register-profile; only fields the backend accepts are sent
  (name, email, phone, password, requested role, staffId, designation).
- Password change in `UserProfileView`/`TechnicianProfileView`: verify current password via
  `signInWithPassword`, rotate via `auth.updateUser({password})`.

### 2. Route protection
- `src/proxy.ts` — Next 16 convention (middleware was renamed **proxy**; see
  `node_modules/next/dist/docs/01-app/01-getting-started/16-proxy.md`). Optimistic
  cookie-presence check only (`sb-*-auth-token`); no network calls in proxy.
  Portal routes (`/admin /manager /technician /viewer /campus`) and `/pending` `/403`
  require a session; authenticated users are bounced off `/login` `/signup`.
- `src/components/auth/ProtectedRoute.tsx` — loading/login/pending/403 screens.
- `src/lib/permissions.ts` — `canAccessRoute` now uses an explicit **route→roles table**;
  unknown paths are **denied** (previously default-allowed). Server (Flask) remains
  authoritative for every permission.

### 3. API client + query infrastructure
- `src/lib/api/client.ts` — single fetch wrapper: envelope contract
  (`{success,data,error,request_id}`), 15 s timeout, Bearer Supabase token,
  single-flight refresh-once retry on 401, sign-out when refresh fails,
  typed `ApiError{code,status,message,requestId,details}`. `apiList` handles the
  paginated envelope shape from `app/core/paginated()` (array at `data`,
  `pagination` sibling).
- `src/lib/api/query-provider.tsx` — TanStack Query (retries only 5xx/network,
  never 4xx; staleTime 30 s).
- `src/lib/api/assets.ts`, `src/lib/api/users.ts` — query-key factories + hooks:
  `useAssets/useAsset`, `useUsers/useApprovals/useApproveUser/useRejectUser/usePatchUser`,
  `useNotifications/useMarkNotificationRead/useMarkAllNotificationsRead`.
- `src/app/layout.tsx` — providers: `QueryProvider > AuthProvider`.

### 4. Adapter layer (zod-validated boundary)
- `src/lib/adapters/asset.ts` — `mapAsset` (API row → `ExtendedAC`), status vocabulary
  both directions (§4 table), capacity/star/power conversions, `toAssetPayload`.
- `src/lib/adapters/job.ts` — work orders → `MaintenanceJob` (SCHEDULED→Pending,
  ASSIGNED→Accepted, …; UPPERCASE→Title Case priority/type).
- `src/lib/adapters/movement.ts` — movements → `MovementRequest`.
- `src/lib/adapters/user.ts` — profile → `User`/`ManagedUser`; approval status casing
  (`PENDING`→`pending`); `toProfilePatch` whitelist (never sends role/status).

### 5. Direct PostgreSQL removed
- Deleted: `src/lib/server/db.ts`, `src/lib/server/asset-repository.ts`,
  `src/app/api/assets/route.ts`, `src/app/api/assets/[id]/route.ts`,
  `src/app/api/campus/buildings/route.ts`, `Frontend/database/` (schema.sql).
- Uninstalled `pg` + `@types/pg`. eslint `no-restricted-imports` bans `pg` forever.
- Former callers rewired: `technician-asset-service.ts` now calls `/api/v1/assets`
  through the client+adapter (with offline read-through cache in `src/lib/storage/`);
  `technician/jobs/[id]` fetches `/api/v1/assets/<id>` via `api()`.

### 6. Mock/credential machinery retired (only where real API existed)
- `hashPassword`, `verifyPassword`, `MOCK_PASSWORD` (`Admin@123`), `demo_auth`,
  `passwordHash` — **all removed**; grep returns 0 matches outside comments.
- `admin/users` page → real `GET /api/v1/users` + `PATCH /api/v1/users/<id>`
  (role select, activate/deactivate; account creation = signup→approval, no passwords).
- `ApprovalQueue` (admin+manager approvals) → real `GET /api/v1/approvals`,
  `POST /approvals/<id>/approve|reject` (reason required client-side as server requires).
- localStorage confined: `src/lib/storage/*` is the only sanctioned module
  (eslint `no-restricted-globals`); grandfathered legacy list documents which mock
  services remain until their phase (P3–P6).

### 7. Config / security
- `next.config.ts` — security headers: CSP (self + API origin + Supabase origin/wss;
  `frame-ancestors 'none'`), HSTS, Referrer-Policy, Permissions-Policy
  (`camera=(self)` for the QR scanner), X-Frame-Options DENY, nosniff.
- `.env.example` — documents the 4 public vars; explicit warnings that the JWT
  secret / service-role key / DB credentials must never appear in FE env.
- `src/app/(public)/a/[ref]/page.tsx` — public QR landing against
  `GET /api/v1/public/assets/<ref>` (server-side fetch, no auth, reduced fields).
- eslint: `no-restricted-imports` (pg), `no-restricted-globals` (localStorage).
- scripts: `typecheck`, `test` (vitest), `gen:api` (openapi-typescript).

## Files changed (created C / modified M / deleted D)
C: `src/lib/api/{client.ts,query-provider.tsx,assets.ts,users.ts}`,
`src/lib/supabase/client.ts`, `src/lib/adapters/{asset,job,movement,user,adapters.test}.ts`,
`src/lib/storage/index.ts`, `src/proxy.ts`, `src/app/(public)/pending/page.tsx`,
`src/app/(public)/a/[ref]/page.tsx`, `.env.example`, `vitest.config.ts`, `vitest.setup.ts`,
`docs/phase-2-report.md`
M: `package.json`, `eslint.config.mjs`, `next.config.ts`, `src/app/layout.tsx`,
`src/contexts/AuthContext.tsx`, `src/components/auth/ProtectedRoute.tsx`,
`src/lib/permissions.ts`, `src/app/(public)/login/page.tsx`,
`src/app/(public)/signup/page.tsx`, `src/app/(portal)/admin/users/page.tsx`,
`src/components/features/ApprovalQueue.tsx`, `src/components/layout/TopNavbar.tsx`,
`src/components/features/profile/{UserProfileView,TechnicianProfileView}.tsx`,
`src/lib/{user-service,mock-data,technician-asset-service}.ts`,
`src/app/(portal)/technician/jobs/[id]/page.tsx`
D: `src/lib/server/{db,asset-repository}.ts`, `src/app/api/` (3 routes), `database/`

## Validation results
- `npx tsc --noEmit` → **TYPECHECK OK**
- `npm run lint` → **0 errors** (warnings only: documented pre-existing debt in files
  P3–P6 rewrite, scoped to `warn` in eslint.config.mjs)
- `npx vitest run` → **9 passed** (adapter vocabulary tests)
- `npm run build` → **success** (all routes; `ƒ Proxy (Middleware)` registered)
- Verify greps: `demo_auth|Admin@123|hashPassword|verifyPassword` → 0 code matches;
  `pg`/`@types/pg` → gone from package.json; `src/app/api`, `src/lib/server`,
  `database/` → gone.

## Remaining blockers
1. **Live smoke test** (sign-up/approve/login against real Supabase + Flask): blocked on
   your credential rotation (backend `.env` + frontend `.env.local`). Needs:
   `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, backend
   `SUPABASE_URL`/`SUPABASE_PUBLISHABLE_KEY`/JWT config; then run migration 006 +
   `_supabase_auth_trigger.sql` (Phase 1 DB-gated items).
2. `npm run gen:api` → `src/lib/api/schema.d.ts` requires the Flask server running
   (offline: can be pointed at `backend/openapi.yaml` instead — not yet wired).
3. Baseline git commit is your task (no git used by me).
4. Public-safe manager-list endpoint for signup dropdown: **not invented** — signup
   now says approver assigns department/blocks/manager. Raise in P3/P7 if wanted.

## Exact next task
**Phase 3 — Assets, campus & import** (`acms-plan/phases/phase-3-assets-campus-import.md`):
backend migration `007_asset_status.sql`, widened asset write schemas, filters/facets,
detail with histories, transactional `POST /assets/import`, campus tree/CRUD, CSV export;
then frontend asset/campus screens onto `/api/v1`, import modal replacing `xlsx`.
