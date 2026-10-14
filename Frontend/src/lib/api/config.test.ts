/**
 * P1-A regression tests — the API origin resolution path used by client.ts,
 * the public QR page, the proxy CSP builder and the next.config.ts build gate.
 *
 * Security scenarios covered:
 *   - production can NEVER silently fall back to localhost (the reported defect)
 *   - missing/invalid production configuration fails loudly, naming the variable
 *   - malformed URLs, unexpected protocols, embedded credentials, query/fragment
 *   - HTTPS required in production, with the documented RFC1918 exception
 *   - development keeps the documented local default
 */
import { describe, expect, it } from "vitest";

import { DEV_DEFAULT_API_URL, resolveApiBaseUrl } from "./config";

const PROD = "production";
const DEV = "development";

describe("resolveApiBaseUrl — development", () => {
  it("applies the documented local default when unset", () => {
    expect(resolveApiBaseUrl(undefined, DEV)).toBe("http://localhost:5000");
    expect(DEV_DEFAULT_API_URL).toBe("http://localhost:5000");
  });

  it("accepts a custom local http URL and trims trailing slashes", () => {
    expect(resolveApiBaseUrl("http://127.0.0.1:5001///", DEV)).toBe("http://127.0.0.1:5001");
    expect(resolveApiBaseUrl("  http://localhost:5000/", DEV)).toBe("http://localhost:5000");
  });

  it("still rejects malformed values even in development", () => {
    expect(() => resolveApiBaseUrl("not a url", DEV)).toThrow(/NEXT_PUBLIC_API_URL/);
    expect(() => resolveApiBaseUrl("ftp://files.example.org", DEV)).toThrow(/http/);
    expect(() => resolveApiBaseUrl("https://user:pass@example.org", DEV)).toThrow(/credentials/);
  });
});

describe("resolveApiBaseUrl — production fails closed", () => {
  it("rejects a missing URL instead of falling back to localhost", () => {
    expect(() => resolveApiBaseUrl(undefined, PROD)).toThrow(/NEXT_PUBLIC_API_URL is not set/);
    expect(() => resolveApiBaseUrl("", PROD)).toThrow(/NEXT_PUBLIC_API_URL is not set/);
    expect(() => resolveApiBaseUrl("   ", PROD)).toThrow(/NEXT_PUBLIC_API_URL is not set/);
  });

  it("rejects every loopback form (the reported P1 defect)", () => {
    for (const bad of [
      "http://localhost:5000",
      "https://localhost:5000",
      "http://127.0.0.1:5000",
      "https://127.0.0.1",
      "http://0.0.0.0:5000",
      "http://[::1]:5000",
      "https://app.localhost",
    ]) {
      expect(() => resolveApiBaseUrl(bad, PROD), bad).toThrow(/loopback/);
    }
  });

  it("rejects plain http on public hosts", () => {
    expect(() => resolveApiBaseUrl("http://acms-api.example.org", PROD)).toThrow(/https/);
    expect(() => resolveApiBaseUrl("http://8.8.8.8:5000", PROD)).toThrow(/https/);
    // 172.32.x.x is PUBLIC (outside RFC1918 172.16/12).
    expect(() => resolveApiBaseUrl("http://172.32.0.10", PROD)).toThrow(/https/);
  });

  it("accepts a valid production HTTPS origin, normalized", () => {
    expect(resolveApiBaseUrl("https://acms-api.example.org/", PROD)).toBe(
      "https://acms-api.example.org",
    );
    expect(resolveApiBaseUrl("https://acms-api.example.org:8443/base/", PROD)).toBe(
      "https://acms-api.example.org:8443/base",
    );
  });

  it("accepts the documented RFC1918 private-network http allowance", () => {
    expect(resolveApiBaseUrl("http://10.0.12.5:5000", PROD)).toBe("http://10.0.12.5:5000");
    expect(resolveApiBaseUrl("http://192.168.1.50:5000", PROD)).toBe("http://192.168.1.50:5000");
    expect(resolveApiBaseUrl("http://172.16.0.9:5000", PROD)).toBe("http://172.16.0.9:5000");
    expect(resolveApiBaseUrl("http://172.31.255.1", PROD)).toBe("http://172.31.255.1");
  });

  it("rejects malformed or dangerous production values", () => {
    expect(() => resolveApiBaseUrl("/api/v1", PROD)).toThrow(/absolute URL/);
    expect(() => resolveApiBaseUrl("javascript:alert(1)", PROD)).toThrow(/http/);
    expect(() => resolveApiBaseUrl("file:///etc/passwd", PROD)).toThrow(/http/);
    expect(() => resolveApiBaseUrl("https://acms.example.org?x=1", PROD)).toThrow(/query/);
    expect(() => resolveApiBaseUrl("https://acms.example.org#frag", PROD)).toThrow(/fragment/);
  });

  it("never echoes the configured value back in its error messages", () => {
    const secretish = "https://internal-user:super-secret@10.0.0.9";
    try {
      resolveApiBaseUrl(secretish, PROD);
      throw new Error("expected a throw");
    } catch (err) {
      expect(String(err)).not.toContain("super-secret");
      expect(String(err)).toContain("NEXT_PUBLIC_API_URL");
    }
  });
});

describe("actual configuration path — next.config.ts loads under validation", () => {
  it("imports cleanly (dev/test default) and keeps the non-CSP security headers", async () => {
    const config = (await import("../../../next.config")).default;
    const rows = await config.headers!();
    const headers = rows.flatMap((r) => r.headers).map((h) => h.key);
    // CSP intentionally moved to src/proxy.ts (nonce per request)…
    expect(headers).not.toContain("Content-Security-Policy");
    // …while the origin-independent headers remain.
    expect(headers).toContain("Strict-Transport-Security");
    expect(headers).toContain("X-Content-Type-Options");
    expect(headers).toContain("X-Frame-Options");
    expect(headers).toContain("Referrer-Policy");
    expect(headers).toContain("Permissions-Policy");
  });
});
