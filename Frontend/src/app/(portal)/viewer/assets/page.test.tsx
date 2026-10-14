/**
 * Viewer AC inventory tests: the read-only list renders filterable assets
 * with detail links into /viewer/assets/<id> and exposes no mutation
 * affordance (no edit/inline-save/decommission/import/create UI).
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ViewerAssetsPage from "./page";

vi.mock("@/hooks/useAssetQueryState", () => ({
  useAssetQueryState: () => ({
    filters: {},
    searchInput: "",
    page: 1,
    pageSize: 20,
    sort: "ac_id",
    setFilters: vi.fn(),
    reset: vi.fn(),
    setPage: vi.fn(),
    setPageSize: vi.fn(),
    setSort: vi.fn(),
  }),
}));

vi.mock("@/hooks/useACDataset", () => ({
  useACDataset: () => ({
    acs: [
      {
        id: "AC-1",
        acId: "AC-1",
        visualId: "VID-1",
        make: "Daikin",
        model: "M1",
        type: "Split",
        capacity: "1.5 Ton",
        capacityTon: 1.5,
        status: "Working",
        buildingName: "Main Block",
        floorName: "Floor 1",
        roomName: "101",
        location: { buildingId: "B1", floorId: "F1", roomId: "R1" },
        serialNumber: "S1",
        lastMaintenanceDate: null,
        nextMaintenanceDate: null,
        lifecycleStatus: "ACTIVE",
        isActive: true,
      },
    ],
    stats: { total: 1, working: 1, faulty: 0, underMaintenance: 0 },
    loading: false,
    error: null,
    pagination: { page: 1, pageSize: 20, total: 1, totalPages: 1 },
  }),
}));

vi.mock("@/lib/api/assets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/assets")>();
  return {
    ...actual,
    useAssetFacets: () => ({
      data: { block: [{ value: "Main Block", count: 1 }] },
    }),
  };
});

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { role: "VIEWER" } }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ViewerAssetsPage — read-only inventory", () => {
  it("renders the list with a working detail link into the viewer portal", () => {
    render(<ViewerAssetsPage />);

    expect(screen.getByText("AC Asset Inventory")).toBeTruthy();
    expect(screen.getByText("Daikin M1")).toBeTruthy();
    const detail = screen.getByTitle("View detail page");
    expect(detail.closest("a")?.getAttribute("href")).toBe("/viewer/assets/AC-1");
  });

  it("exposes no mutation affordance", () => {
    render(<ViewerAssetsPage />);

    expect(screen.queryByText(/add ac/i)).toBeNull();
    expect(screen.queryByText(/import/i)).toBeNull();
    expect(screen.queryByTitle("Edit Record")).toBeNull();
    expect(screen.queryByTitle("Edit inline")).toBeNull();
    expect(screen.queryByTitle("Deactivate")).toBeNull();
  });
});
