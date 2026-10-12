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
 */
import { NextResponse, type NextRequest } from "next/server";

const PORTAL_PREFIXES = ["/admin", "/manager", "/technician", "/viewer", "/campus"];
const PUBLIC_PAGES = ["/login", "/signup"];
const SESSION_ONLY_PAGES = ["/pending", "/403"];

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

  return NextResponse.next();
}

export const config = {
  matcher: [
    // Run on pages only — skip Next internals, public assets, and /api.
    "/((?!api|_next/static|_next/image|favicon.ico|.*\\.(?:png|jpg|jpeg|svg|webp|ico|css|js|glb|html)$).*)",
  ],
};
