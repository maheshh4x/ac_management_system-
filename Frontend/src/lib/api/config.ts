/**
 * Single source of truth for the Flask API base URL (P1-A hardening).
 *
 * Every consumer (client.ts, the public QR page, the proxy CSP builder and
 * next.config.ts validation) resolves the API origin through this module, so
 * a production build can never silently fall back to `http://localhost:5000`
 * or accept a malformed origin.
 *
 * Policy — derived from the actual deployment model of this app (no
 * Dockerfile/vercel.json exists; `next build` output is served by
 * `next start` on a host that also has the env set):
 *
 *   * `NEXT_PUBLIC_*` variables are inlined at BUILD time by Next.js, so a
 *     missing value must fail the BUILD with a clear message, not surface
 *     as runtime localhost calls against a public site.
 *   * Development (`NODE_ENV !== "production"`): unset -> documented local
 *     default `http://localhost:5000` so `npm run dev` keeps working.
 *   * Production: unset -> hard failure. Loopback origins (localhost,
 *     127.0.0.0/8, ::1, 0.0.0.0, *.localhost) are rejected outright — an
 *     accidental localhost in a public build is the exact defect this fixes.
 *   * Production requires HTTPS, with ONE documented exception: plain HTTP
 *     is accepted for RFC1918 private-network origins (campus-intranet
 *     deployments behind the firewall, e.g. http://10.0.12.5:5000). Public
 *     or otherwise non-private hosts must use HTTPS.
 *   * Structural hygiene in every environment: absolute http(s) URL only,
 *     no embedded credentials, no query/fragment, trailing slashes trimmed.
 *
 * The value is never logged; validation errors name the variable only.
 */

/** Documented local default for `npm run dev` only — never valid in production. */
export const DEV_DEFAULT_API_URL = "http://localhost:5000";

/** Hosts that can never be a production API origin (loopback / unspecified). */
function isLoopbackHost(hostname: string): boolean {
  return (
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname === "0.0.0.0" ||
    hostname === "::1" ||
    hostname === "[::1]" ||
    /^127(\.\d{1,3}){3}$/.test(hostname)
  );
}

/** RFC1918 private network — the documented plain-HTTP allowance. */
function isPrivateNetworkHost(hostname: string): boolean {
  return (
    /^10(\.\d{1,3}){3}$/.test(hostname) ||
    /^192\.168(\.\d{1,3}){2}$/.test(hostname) ||
    /^172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2}$/.test(hostname)
  );
}

/**
 * Resolve (and validate) the API base URL.
 *
 * @param raw   the raw NEXT_PUBLIC_API_URL value (or undefined)
 * @param nodeEnv process.env.NODE_ENV — "production" enables strict rules
 * @returns normalized base URL WITHOUT a trailing slash
 * @throws Error with an operator-actionable message (variable name only,
 *         never the value) when production configuration is missing/invalid
 */
export function resolveApiBaseUrl(raw: string | undefined, nodeEnv: string | undefined): string {
  const isProd = nodeEnv === "production";
  const value = (raw ?? "").trim();

  if (!value) {
    if (isProd) {
      throw new Error(
        "NEXT_PUBLIC_API_URL is not set. A production build requires an explicit API base " +
          `URL (HTTPS). See Frontend/.env.example — local default "${DEV_DEFAULT_API_URL}" ` +
          "is intentionally NOT applied in production.",
      );
    }
    return DEV_DEFAULT_API_URL;
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(
      "NEXT_PUBLIC_API_URL is not a valid absolute URL (expected e.g. https://api.example.org).",
    );
  }

  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("NEXT_PUBLIC_API_URL must use http(s); other protocols are not allowed.");
  }
  if (url.username || url.password) {
    throw new Error("NEXT_PUBLIC_API_URL must not embed credentials; authenticate via headers.");
  }
  if (url.search || url.hash) {
    throw new Error("NEXT_PUBLIC_API_URL must not contain a query string or fragment.");
  }

  const hostname = url.hostname.toLowerCase();
  // `pathname` is "/" for a bare origin; trim trailing slashes so callers can
  // safely concatenate `${base}${path}` with path starting in "/".
  const pathname = url.pathname.replace(/\/+$/, "");

  if (isProd) {
    if (isLoopbackHost(hostname)) {
      throw new Error(
        "NEXT_PUBLIC_API_URL points at a loopback address, which is not a valid production " +
          "API origin. Set the public API URL for production builds.",
      );
    }
    if (url.protocol === "http:" && !isPrivateNetworkHost(hostname)) {
      throw new Error(
        "NEXT_PUBLIC_API_URL must use https:// in production. Plain http is accepted only " +
          "for private-network (RFC1918) API origins on a trusted intranet.",
      );
    }
  }

  return `${url.origin}${pathname}`;
}
