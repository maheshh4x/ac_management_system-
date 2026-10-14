import type { NextConfig } from "next";

import { resolveApiBaseUrl } from "./src/lib/api/config";

/**
 * P1-A build-time gate: resolve (and VALIDATE) the API origin while the
 * config loads, so `next build` fails fast with an operator-actionable
 * message when production NEXT_PUBLIC_API_URL is missing, malformed, a
 * loopback address, or plain http on a public host. Development keeps the
 * documented local default (http://localhost:5000).
 *
 * P1-B: the Content-Security-Policy itself is emitted per request by
 * src/proxy.ts (static policy for all pages; strict nonce policy for the
 * anonymous QR surface /a/* — the documented Next.js nonce pattern). It is
 * intentionally NOT set here: two CSP headers on one response would be
 * enforced as an intersection and break nonce-based rendering.
 */
resolveApiBaseUrl(process.env.NEXT_PUBLIC_API_URL, process.env.NODE_ENV);

/**
 * Security headers (Phase 2, task 9) — everything except CSP (see proxy.ts).
 * The QR scanner needs `camera=(self)`.
 */
const securityHeaders = [
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // Camera for the QR scanner (this origin only); mic/geolocation unused.
  { key: "Permissions-Policy", value: "camera=(self), microphone=(), geolocation=()" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  images: {
    // Only self-hosted assets; the login page uses /images/nitttr-campus.jpg.
    remotePatterns: [],
  },
};

export default nextConfig;
