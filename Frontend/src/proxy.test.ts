/**
 * P1-B regression tests — src/proxy.ts: CSP emission + preserved auth flow.
 *
 * Security scenarios covered:
 *   - every page response carries a CSP (no response may ship unprotected)
 *   - /a/* (anonymous QR surface) gets a FRESH nonce policy without
 *     'unsafe-inline' scripts; other pages keep the static policy
 *   - protected routes still redirect anonymous visitors to /login
 *   - authed visitors are still bounced off /login and /signup
 */
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";

import { proxy } from "./proxy";

function get(path: string, headers: Record<string, string> = {}): NextRequest {
  return new NextRequest(`http://localhost:3000${path}`, { headers });
}

function cspOf(request: NextRequest): string {
  return proxy(request).headers.get("content-security-policy") ?? "";
}

describe("proxy — auth redirects preserved", () => {
  it("redirects anonymous visitors away from portal routes to /login", () => {
    const res = proxy(get("/admin/assets"));
    expect(res.status).toBeGreaterThanOrEqual(300);
    expect(res.status).toBeLessThan(400);
    expect(new URL(res.headers.get("location") ?? "").pathname).toBe("/login");
  });

  it("keeps /a/<ref> reachable without a session (public QR landing)", () => {
    const res = proxy(get("/a/some-uuid"));
    expect(res.headers.get("location")).toBeNull();
  });

  it("still bounces signed-in visitors off /login", () => {
    const res = proxy(get("/login", { cookie: "sb-project-auth-token.0=abc123" }));
    expect(new URL(res.headers.get("location") ?? "").pathname).toBe("/");
  });
});

describe("proxy — CSP emission (P1-B)", () => {
  it("attaches a CSP header to ordinary page responses (static policy)", () => {
    const csp = cspOf(get("/login"));
    expect(csp).toContain("script-src 'self' 'unsafe-inline'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).not.toContain("nonce-");
  });

  it("switches the QR landing surface to a per-request nonce policy", () => {
    const first = cspOf(get("/a/some-uuid"));
    const second = cspOf(get("/a/some-uuid"));

    expect(first).toMatch(/script-src 'self' 'nonce-[^']+' 'strict-dynamic/);
    // The SCRIPT directive must carry no 'unsafe-inline' (style-src keeps it
    // for inline style attributes — see csp.ts).
    const scriptSrc = first
      .split(";")
      .map((p) => p.trim())
      .find((p) => p.startsWith("script-src "));
    expect(scriptSrc).not.toContain("unsafe-inline");
    // Fresh, unpredictable nonce on every request.
    const nonceOf = (csp: string) => csp.match(/'nonce-([^']+)'/)?.[1];
    expect(nonceOf(first)).toBeTruthy();
    expect(nonceOf(first)).not.toBe(nonceOf(second));
  });

  it("never emits a CSP with a nonce for non-QR pages (static rendering safe)", () => {
    for (const path of ["/", "/login", "/signup", "/admin/assets", "/a", "/assets/x"]) {
      expect(cspOf(get(path)), path).not.toContain("nonce-");
    }
  });
});
