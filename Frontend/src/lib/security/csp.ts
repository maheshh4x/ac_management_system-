/**
 * Content-Security-Policy construction (P1-B hardening).
 *
 * One builder, two policies — so the directives can never drift apart:
 *
 *   * Static policy (every page): the Phase 2 policy PLUS `object-src 'none'`
 *     and, in production, `upgrade-insecure-requests`. `script-src` keeps
 *     `'unsafe-inline'` because the portal pages are statically prerendered
 *     and Next.js App Router hydration/RSC payload scripts are inline:
 *     removing it without a per-request nonce would break every static page
 *     (see docs/security-review.md F6 for the accepted trade-off).
 *   * Nonce policy (`/a/*`, the anonymous QR surface): the documented Next.js
 *     nonce pattern — `script-src 'self' 'nonce-…' 'strict-dynamic'` with NO
 *     `'unsafe-inline'`. The QR page is dynamically rendered by construction
 *     (no-store server fetch), which nonce CSP requires.
 *
 * `connect-src` uses the VALIDATED API origin (resolveApiBaseUrl) — never an
 * accidental localhost fallback — plus the Supabase REST/Realtime origins.
 */
import { resolveApiBaseUrl } from "../api/config";

export interface CspOptions {
  /** Per-request nonce; when present, script-src switches to the strict nonce policy. */
  nonce?: string;
  /** Defaults to process.env.NODE_ENV — dev adds 'unsafe-eval' (React debugging, per Next docs). */
  isDev?: boolean;
  /** Injectable for tests; defaults to the validated env-derived API base. */
  apiBase?: string;
  /** Injectable for tests; defaults to NEXT_PUBLIC_SUPABASE_URL. */
  supabaseUrl?: string;
}

/** Supabase REST origin + its ws:// Realtime variant (as the Phase 2 CSP did). */
function supabaseOrigins(raw: string | undefined): string[] {
  if (!raw) return [];
  try {
    const origin = new URL(raw).origin;
    return [origin, origin.replace(/^http/, "ws")];
  } catch {
    return [];
  }
}

export function buildContentSecurityPolicy(options: CspOptions = {}): string {
  const isDev = options.isDev ?? process.env.NODE_ENV === "development";
  const apiBase = options.apiBase ?? resolveApiBaseUrl(process.env.NEXT_PUBLIC_API_URL, process.env.NODE_ENV);
  const supabaseUrl = options.supabaseUrl ?? process.env.NEXT_PUBLIC_SUPABASE_URL;

  const connectSrc = ["'self'", apiBase, ...supabaseOrigins(supabaseUrl)]
    .filter((v) => v && v.trim().length > 0)
    .join(" ");

  // Nonce pages: strict script policy per the Next.js CSP guide. Dev keeps
  // 'unsafe-eval' because React uses eval for debugging there only.
  const scriptSrc = options.nonce
    ? `'self' 'nonce-${options.nonce}' 'strict-dynamic'${isDev ? " 'unsafe-eval'" : ""}`
    : "'self' 'unsafe-inline'";

  const directives = [
    "default-src 'self'",
    // style-src keeps 'unsafe-inline' in BOTH policies: framer-motion and
    // React set inline style ATTRIBUTES, which a script-style nonce does not
    // cover; removing it breaks rendering and buys no script protection.
    `style-src 'self' 'unsafe-inline'`,
    `script-src ${scriptSrc}`,
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connectSrc}`,
    "media-src 'self'",
    "worker-src 'self' blob:",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    // New tightening (was missing): no plugins/object embeds at all.
    "object-src 'none'",
  ];

  // Production only: http://localhost is exempt from upgrade by browser
  // spec, but skipping it in dev avoids surprising local http debugging.
  if (!isDev) directives.push("upgrade-insecure-requests");

  return directives.join("; ");
}
