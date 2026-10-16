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

/**
 * Campus-map framing exception (3D map iframe fix).
 *
 * `public/campus_map.html` is a static, unauthenticated, read-only WebGL
 * visualization (system fonts only, one cdnjs script, no cookies/session —
 * the host page forwards stats via postMessage). The global rule above
 * stamps it `X-Frame-Options: DENY` (verified: local prod server returns
 * DENY on /campus_map.html), which makes the same-origin iframe on
 * /(role)/campus refuse to render.
 *
 * This rule serves the IDENTICAL header set with the single difference
 * `X-Frame-Options: SAMEORIGIN`, so only our own origin may frame the map.
 * It is listed AFTER the global rule because Next.js applies matching
 * header rules in order with later entries overwriting earlier ones for the
 * same key — DENY stays in force for every other route, and no header is
 * weakened globally.
 */
const campusMapHeaders = securityHeaders.map((h) =>
  h.key === "X-Frame-Options" ? { ...h, value: "SAMEORIGIN" } : h,
);

const nextConfig: NextConfig = {
  async headers() {
    return [
      { source: "/:path*", headers: securityHeaders },
      { source: "/campus_map.html", headers: campusMapHeaders },
    ];
  },
  images: {
    // Only self-hosted assets; the login page uses /images/nitttr-campus.jpg.
    remotePatterns: [],
  },
};

export default nextConfig;
