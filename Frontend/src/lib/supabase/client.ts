/** Supabase browser client (Phase 2, task 4).
 *
 * Browser-side code ONLY receives the publishable key (NEXT_PUBLIC_*). The
 * Supabase JWT secret and every DB credential stay server-side (Flask).
 * Cookie storage via @supabase/ssr keeps the session visible to proxy.ts
 * for optimistic route protection and lets the client auto-refresh tokens.
 */
import { createBrowserClient } from "@supabase/ssr";

let client: ReturnType<typeof createBrowserClient> | null = null;

export function getSupabaseBrowserClient() {
  if (!client) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !key) {
      throw new Error(
        "Supabase is not configured: set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.",
      );
    }
    // Cookie attributes for the Supabase session (plan 8.3: Secure/SameSite).
    // `secure` follows the page protocol: browsers drop a Secure cookie on
    // plain http:// (how `next dev` runs), so hard-coding true would silently
    // break sign-in in development.
    //
    // `httpOnly` is deliberately NOT set. @supabase/ssr's browser client reads
    // and writes this cookie through document.cookie, and a JS-authored cookie
    // is never HttpOnly — forcing it would break authentication outright. The
    // compensating controls are the CSP in next.config.ts (no remote script
    // sources) plus the fact that Flask never reads this cookie (it verifies
    // the Bearer token instead). The durable fix — a server-side cookie store —
    // is recorded in docs/security-review.md.
    client = createBrowserClient(url, key, {
      cookieOptions: {
        path: "/",
        sameSite: "lax",
        secure: typeof window !== "undefined" && window.location.protocol === "https:",
      },
    });
  }
  return client;
}
