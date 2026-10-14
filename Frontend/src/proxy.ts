/**
 * Route proxy (Next.js 16 renamed "middleware" to "proxy" — see
 * node_modules/next/dist/docs/01-app/01-getting-started/16-proxy.md).
 *
 * Optimistic session check ONLY (per the Next.js auth guide: read the session
 * cookie, never call the database here). A `sb-*-auth-token` cookie written by
 * @supabase/ssr proves a Supabase session probably exists; the authoritative
 * checks are:
 *   - Flask: every /api/v1 route verifies the JWT + profile status, and
 *   - ProtectedRoute: redirects pending/rejected users to /pending.
 *
 * Public: /, /login, /signup, /a/<ref> (QR landing), /pending is session-only.
 *
 * P1-B: this proxy also EMITS the Content-Security-Policy for every page
 * response (see src/lib/security/csp.ts):
 *   - /a/* (anonymous QR surface) gets the strict nonce policy from the
 *     Next.js CSP guide — `script-src 'self' 'nonce-…' 'strict-dynamic'`,
 *     no 'unsafe-inline'. That page is dynamically rendered by construction
 *     (no-store server fetch in app/(public)/a/[ref]/page.tsx), which nonce
 *     CSP requires; Next.js picks the nonce up from the request header and
 *     applies it to its own inline hydration/RSC scripts automatically.
 *   - every other page keeps the static policy ('unsafe-inline' in
 *     script-src) because the portal pages are statically prerendered —
 *     converting them to dynamic rendering to use nonces is a separate
 *     architecture decision (docs/security-review.md F6).
 * CSP is deliberately NOT also set in next.config.ts: two policies on one
 * response are enforced as an intersection, which would break nonced scripts.
 */
import { NextResponse, type NextRequest } from "next/server";

import { buildContentSecurityPolicy } from "./lib/security/csp";

const PORTAL_PREFIXES = ["/admin", "/manager", "/technician", "/viewer", "/campus"];
const PUBLIC_PAGES = ["/login", "/signup"];
const SESSION_ONLY_PAGES = ["/pending", "/403"];

/** Anonymous QR landing — dynamic by construction, so it can carry a nonce. */
const NONCE_PREFIXES = ["/a/"];

function hasSupabaseSessionCookie(request: NextRequest): boolean {
  return request.cookies
    .getAll()
    .some((c) => c.name.startsWith("sb-") && c.name.includes("-auth-token") && c.value.length > 0);
}

export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const authed = hasSupabaseSessionCookie(request);

  const isPortal = PORTAL_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(p + "/"),
  );
  const isSessionOnly = SESSION_ONLY_PAGES.some(
    (p) => pathname === p || pathname.startsWith(p + "/"),
  );

  if ((isPortal || isSessionOnly) && !authed) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  if (authed && PUBLIC_PAGES.some((p) => pathname === p)) {
    const url = request.nextUrl.clone();
    url.pathname = "/";
    url.searchParams.delete("next");
    return NextResponse.redirect(url);
  }

  // ── CSP (P1-B) ────────────────────────────────────────────────────────────
  const wantsNonce = NONCE_PREFIXES.some((p) => pathname.startsWith(p));
  const nonce = wantsNonce ? Buffer.from(crypto.randomUUID()).toString("base64") : undefined;
  const csp = buildContentSecurityPolicy({ nonce });

  const requestHeaders = new Headers(request.headers);
  if (nonce) {
    // Next.js reads the request-side CSP and applies the nonce to its own
    // inline scripts during server-side rendering (Next.js CSP guide).
    requestHeaders.set("x-nonce", nonce);
    requestHeaders.set("Content-Security-Policy", csp);
  }

  const response = NextResponse.next({
    request: { headers: requestHeaders },
  });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    // Run on pages only — skip Next internals, public assets, and /api.
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|svg|webp|ico|css|js|glb|html)$).*)",
  ],
};
