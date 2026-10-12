/**
 * Dashboard wiring regression test (§9): the admin dashboard's maintenance
 * trend must receive the SAME selected building as every other
 * visualization — All Buildings sends no block filter, a selected building
 * sends it. The backend filtering itself is covered by
 * backend/tests/test_dashboard_building_filter.py; this pins the page side.
 */
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AdminDashboardPage from "./page";
import { useMaintenanceTrend } from "@/lib/api/maintenance";

const dashboardState = vi.hoisted(() => ({ buildingId: "All" }));

vi.mock("@/hooks/useAssetQueryState", () => ({
  useAssetQueryState: () => ({
    filters: { buildingId: dashboardState.buildingId },
    page: 1,
    pageSize: 20,
    sort: "ac_id",
    searchInput: "",
    setFilters: vi.fn(),
    reset: vi.fn(),
    setPage: vi.fn(),
    setPageSize: vi.fn(),
    setSort: vi.fn(),
  }),
}));

vi.mock("@/hooks/useACDataset", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/hooks/useACDataset")>();
  return {
    ...actual,
    useACDataset: () => ({
      acs: [],
      stats: null,
      loading: false,
      error: null,
      refreshData: vi.fn(),
      statusDistribution: [],
      typeDistribution: [],
      buildingDistribution: [],
      capacityDistribution: [],
      pagination: { page: 1, page_size: 20, total: 0, total_pages: 0 },
      updateACAsset: vi.fn(),
      decommissionAC: vi.fn(),
    }),
  };
});

vi.mock("@/lib/api/maintenance", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/maintenance")>();
  return { ...actual, useMaintenanceTrend: vi.fn(() => ({ data: undefined, isPending: false })) };
});

vi.mock("@/lib/api/assets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/assets")>();
  return { ...actual, useAssetFacets: () => ({ data: { block: [] } }) };
});

vi.mock("@/lib/api/analytics", () => ({
  useAnalytics: () => ({
    data: undefined,
    isPending: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/components/auth/ProtectedRoute", () => ({
  ProtectedAction: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

vi.mock("@/contexts/AuthContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AuthContext")>();
  return {
    ...actual,
    useAuth: () => ({
      user: { id: "test-admin", email: "admin@example.test", role: "SUPER_ADMIN" },
      status: "approved",
      isLoading: false,
    }),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AdminDashboardPage — maintenance trend building filter (§9)", () => {
  it("sends no block filter for All Buildings", () => {
    dashboardState.buildingId = "All";
    render(<AdminDashboardPage />);
    expect(useMaintenanceTrend).toHaveBeenCalledWith({});
  });

  it("sends the selected building to the trend endpoint", () => {
    dashboardState.buildingId = "MAIN-ACADEMIC-BLOCK";
    render(<AdminDashboardPage />);
    expect(useMaintenanceTrend).toHaveBeenCalledWith({ block: "MAIN-ACADEMIC-BLOCK" });
  });

  it("mounts the shared analytics dashboard (filters, explorer, insights)", () => {
    dashboardState.buildingId = "All";
    const { getByText } = render(<AdminDashboardPage />);
    expect(getByText("Explore Data")).toBeTruthy();
    expect(getByText("Insights")).toBeTruthy();
    expect(getByText("Department × Year")).toBeTruthy();
  });
});
