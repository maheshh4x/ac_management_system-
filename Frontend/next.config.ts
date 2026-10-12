import type { NextConfig } from "next";

/**
 * Security headers (Phase 2, task 9). CSP allows exactly the origins the app
 * talks to: self, the Supabase project, the Flask API, and blob:/data: for the
 * QR scanner canvas and 3D viewer. The QR scanner needs `camera=(self)`.
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:5000";
const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const SUPABASE_HOST = SUPABASE_URL ? new URL(SUPABASE_URL).origin : "";
const SUPABASE_WSS = SUPABASE_HOST ? SUPABASE_HOST.replace(/^http/, "ws") : "";

const cspDirectives = [
  "default-src 'self'",
  // Tailwind/injected styles require unsafe-inline; no remote scripts allowed.
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${API_URL} ${SUPABASE_HOST} ${SUPABASE_WSS}`.trim(),
  "media-src 'self'",
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: cspDirectives },
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
