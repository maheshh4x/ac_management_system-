/**
 * Public scanned-page tests (§10 frontend): logged-out visitors see the
 * allow-listed fields with NO edit affordance; signed-in users are routed
 * to their portal detail; unknown codes render not-found (never internals).
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PublicAssetView, type PublicAsset } from "./PublicAssetView";

const shared = vi.hoisted(() => ({
  user: null as null | { role: string },
  status: null as null | string,
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: shared.replace, push: vi.fn(), back: vi.fn() }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href?: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: shared.user, status: shared.status, isLoading: false }),
}));

const ASSET: PublicAsset = {
  asset_code: "AC-LOC-TEST-1",
  make: "Daikin",
  model: "FTKM50",
  type: "Split",
  capacity: 1.5,
  block: "Main Academic Block",
  floor: "Floor 1",
  room: "ADMIN OFFICE",
  operational_status: "WORKING",
  last_maintenance_date: "2026-01-05",
};

beforeEach(() => {
  shared.user = null;
  shared.status = null;
  shared.replace.mockClear();
});

afterEach(() => {
  cleanup();
});

describe("PublicAssetView — logged out (§10)", () => {
  it("shows the allow-listed fields with no edit affordance", () => {
    render(<PublicAssetView slug="ASSET-UUID-1" asset={ASSET} failed={false} />);

    expect(screen.getByText("AC-LOC-TEST-1")).toBeTruthy();
    expect(screen.getByText("Main Academic Block / Floor 1 / ADMIN OFFICE")).toBeTruthy();
    expect(screen.getByText("Split")).toBeTruthy();
    // No edit surface anywhere for logged-out visitors...
    expect(screen.queryByText(/edit/i)).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
    // ...only the sign-in doorway, plus the privacy notice.
    expect(screen.getByText("Sign in for full details")).toBeTruthy();
    expect(screen.getByText(/serial numbers, technicians/)).toBeTruthy();
  });

  it("renders the derived visual id when the server provides one", () => {
    render(
      <PublicAssetView
        slug="ASSET-UUID-1"
        asset={{ ...ASSET, visual_id: "NITTTR-MAB-1F-AO-0CA6C87C" }}
        failed={false}
      />
    );
    expect(screen.getByTestId("public-visual-id").textContent).toBe("NITTTR-MAB-1F-AO-0CA6C87C");
  });

  it("renders cleanly when the visual id is absent (older payload)", () => {
    render(<PublicAssetView slug="ASSET-UUID-1" asset={ASSET} failed={false} />);
    expect(screen.queryByTestId("public-visual-id")).toBeNull();
    expect(screen.getByText("AC-LOC-TEST-1")).toBeTruthy();
  });

  it("renders not-found for unknown codes without leaking internals", () => {
    render(<PublicAssetView slug="NOPE" asset={null} failed={false} />);

    expect(screen.getByText("Asset not found")).toBeTruthy();
    expect(screen.queryByText(/edit/i)).toBeNull();
  });
});

describe("PublicAssetView — logged in", () => {
  it("routes approved users to their portal detail instead of the reduced view", () => {
    shared.user = { role: "SUPER_ADMIN" };
    shared.status = "approved";
    render(<PublicAssetView slug="ASSET-UUID-1" asset={ASSET} failed={false} />);

    expect(shared.replace).toHaveBeenCalledWith("/admin/assets/ASSET-UUID-1");
  });
});
