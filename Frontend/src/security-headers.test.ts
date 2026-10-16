/**
 * 3D campus-map iframe regression tests — next.config.ts header exception.
 *
 * Production defect: the global `X-Frame-Options: DENY` rule (source
 * `/:path*`) also stamped `public/campus_map.html`, so the same-origin
 * iframe on /(role)/campus refused to render. The fix is an exception rule
 * serving the identical header set with `SAMEORIGIN` for that one
 * exception served after the global rule (Next.js lets later matching rules
 * overwrite earlier ones for the same key). These tests pin both halves: the
 * map stays frameable by our own origin, and anti-clickjacking (DENY) stays
 * in force everywhere else with no other header weakened.
 */
import { describe, expect, it } from "vitest";

import nextConfig from "../next.config";

interface Header {
  key: string;
  value: string;
}

async function rules(): Promise<{ source: string; headers: Header[] }[]> {
  if (typeof nextConfig.headers !== "function") throw new Error("next.config exposes no headers()");
  return nextConfig.headers();
}

describe("campus map framing exception", () => {
  it("serves /campus_map.html with SAMEORIGIN (later rule overwrites DENY)", async () => {
    const all = await rules();
    expect(all.length).toBeGreaterThanOrEqual(2);
    // The exception must come AFTER the global rule: Next.js applies
    // matching header rules in order, later entries winning per key.
    const globalIdx = all.findIndex((r) => r.source === "/:path*");
    const mapIdx = all.findIndex((r) => r.source === "/campus_map.html");
    expect(mapIdx).toBeGreaterThan(globalIdx);
    const xfo = all[mapIdx].headers.find((h) => h.key === "X-Frame-Options");
    expect(xfo?.value).toBe("SAMEORIGIN");
  });

  it("weakens nothing else on the map response (only XFO differs)", async () => {
    const all = await rules();
    const map = all.find((r) => r.source === "/campus_map.html")?.headers ?? [];
    const global = all.find((r) => r.source === "/:path*")?.headers ?? [];
    for (const g of global) {
      const m = map.find((h) => h.key === g.key);
      expect(m, g.key).toBeDefined();
      expect(m?.value).toBe(g.key === "X-Frame-Options" ? "SAMEORIGIN" : g.value);
    }
    expect(map.filter((h) => h.key === "X-Frame-Options")).toHaveLength(1);
  });

  it("keeps DENY on the global rule covering every other route", async () => {
    const all = await rules();
    const global = all.find((r) => r.source === "/:path*");
    expect(global).toBeDefined();
    expect(global?.headers.find((h) => h.key === "X-Frame-Options")?.value).toBe("DENY");
    // Spot-check the rest of the hardening is intact.
    const get = (k: string) => global?.headers.find((h) => h.key === k)?.value;
    expect(get("X-Content-Type-Options")).toBe("nosniff");
    expect(get("Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(get("Strict-Transport-Security")).toContain("includeSubDomains");
  });
});
