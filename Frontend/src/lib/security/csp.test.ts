/**
 * P1-B regression tests — Content-Security-Policy construction.
 *
 * Security scenarios covered:
 *   - nonce policy (anonymous QR surface) runs NO 'unsafe-inline' scripts
 *   - static policy keeps rendering-compatible 'unsafe-inline' (documented)
 *   - connect-src uses the VALIDATED API origin (no localhost fallback)
 *   - object-src 'none' + frame-ancestors 'none' + base/form hardening
 *   - upgrade-insecure-requests only in production (dev stays debuggable)
 */
import { describe, expect, it, vi } from "vitest";

import { buildContentSecurityPolicy } from "./csp";

function directives(csp: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of csp.split(";")) {
    const [name, ...rest] = part.trim().split(/\s+/);
    if (name) out[name] = rest.join(" ");
  }
  return out;
}

const API = "https://acms-api.example.org";

describe("static policy (every page)", () => {
  const csp = buildContentSecurityPolicy({ isDev: false, apiBase: API, supabaseUrl: undefined });
  const d = directives(csp);

  it("keeps the documented inline-script allowance but adds no nonce", () => {
    expect(d["script-src"]).toBe("'self' 'unsafe-inline'");
    expect(csp).not.toContain("nonce-");
    expect(csp).not.toContain("strict-dynamic");
  });

  it("pins connect-src to the validated API origin (no wildcard, no fallback)", () => {
    expect(d["connect-src"]).toBe(`'self' ${API}`);
  });

  it("includes the new hardening directives", () => {
    expect(d["object-src"]).toBe("'none'");
    expect(d["frame-ancestors"]).toBe("'none'");
    expect(d["base-uri"]).toBe("'self'");
    expect(d["form-action"]).toBe("'self'");
    expect(d["default-src"]).toBe("'self'");
  });

  it("adds upgrade-insecure-requests in production only", () => {
    expect(d["upgrade-insecure-requests"]).toBe("");
    const dev = directives(
      buildContentSecurityPolicy({ isDev: true, apiBase: API, supabaseUrl: undefined }),
    );
    expect(dev["upgrade-insecure-requests"]).toBeUndefined();
  });
});

describe("nonce policy (anonymous QR surface /a/*)", () => {
  const nonce = "test-nonce-abc123";
  const d = directives(
    buildContentSecurityPolicy({
      nonce,
      isDev: false,
      apiBase: API,
      supabaseUrl: "https://proj.supabase.co",
    }),
  );

  it("runs ONLY nonced or same-origin scripts — no 'unsafe-inline'", () => {
    expect(d["script-src"]).toBe(`'self' 'nonce-${nonce}' 'strict-dynamic'`);
    expect(d["script-src"]).not.toContain("unsafe-inline");
  });

  it("keeps Supabase REST + Realtime origins in connect-src", () => {
    expect(d["connect-src"]).toBe(`'self' ${API} https://proj.supabase.co wss://proj.supabase.co`);
  });

  it("retains style-src inline allowance (inline style ATTRIBUTES, not scripts)", () => {
    expect(d["style-src"]).toBe("'self' 'unsafe-inline'");
  });

  it("adds 'unsafe-eval' for development debugging only", () => {
    const dev = directives(
      buildContentSecurityPolicy({ nonce, isDev: true, apiBase: API, supabaseUrl: undefined }),
    );
    expect(dev["script-src"]).toContain("'unsafe-eval'");
    expect(d["script-src"]).not.toContain("unsafe-eval");
  });
});

describe("origin derivation is shared and crash-safe", () => {
  it("skips an unset Supabase URL instead of emitting an empty token", () => {
    const d = directives(buildContentSecurityPolicy({ isDev: true, apiBase: API }));
    expect(d["connect-src"]).toBe(`'self' ${API}`);
  });

  it("derives the API origin through the validated resolver in production", () => {
    // No apiBase injected: the builder must use resolveApiBaseUrl(env) — in a
    // production NODE_ENV with no NEXT_PUBLIC_API_URL that MUST fail closed.
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://localhost:5000");
    try {
      expect(() => buildContentSecurityPolicy({ isDev: false })).toThrow(/loopback/);
    } finally {
      vi.unstubAllEnvs();
    }
  });
});
