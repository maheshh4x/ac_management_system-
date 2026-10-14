import { describe, expect, it } from "vitest";

import { getSafeNext, loginUrl } from "./redirect";

describe("getSafeNext — post-login destination allow-list", () => {
  it("honors QR landings and portal deep links", () => {
    expect(getSafeNext("/a/11111111-2222-3333-4444-555555555555")).toBe(
      "/a/11111111-2222-3333-4444-555555555555",
    );
    expect(getSafeNext("/admin/assets/AC-1")).toBe("/admin/assets/AC-1");
    expect(getSafeNext("/technician/scan")).toBe("/technician/scan");
    expect(getSafeNext("/campus")).toBe("/campus");
  });

  it("rejects empty, external and protocol-relative destinations", () => {
    expect(getSafeNext(null)).toBeNull();
    expect(getSafeNext("")).toBeNull();
    expect(getSafeNext("   ")).toBeNull();
    expect(getSafeNext("https://evil.example/a/1")).toBeNull();
    expect(getSafeNext("//evil.example/a/1")).toBeNull();
    expect(getSafeNext("javascript:alert(1)")).toBeNull();
    expect(getSafeNext("/a/1\\..\\x")).toBeNull();
  });

  it("rejects path traversal and non-app paths", () => {
    expect(getSafeNext("/a/../admin")).toBeNull();
    expect(getSafeNext("/..")).toBeNull();
    expect(getSafeNext("/signup")).toBeNull();
    expect(getSafeNext("/login")).toBeNull();
    expect(getSafeNext("/api/v1/assets")).toBeNull();
    expect(getSafeNext("/pending")).toBeNull();
  });

  it("builds login URLs that preserve the destination", () => {
    expect(loginUrl("/a/abc")).toBe("/login?next=%2Fa%2Fabc");
  });
});
