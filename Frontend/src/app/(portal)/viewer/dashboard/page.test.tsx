/**
 * Viewer dashboard tests (§11): a VIEWER opens the analytics dashboard,
 * changes filters and year ranges, and uses the explorer — with no edit
 * affordance anywhere (mutations, import, decommission stay admin-side;
 * the backend remains authoritative regardless).
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ViewerDashboardPage from "./page";

const queryState = vi.hoisted(() => ({ setFilters: vi.fn(), reset: vi.fn() }));

vi.mock("@/hooks/useAssetQueryState", () => ({
  useAssetQueryState: () => ({
    filters: {},
    searchInput: "",
    page: 1,
    pageSize: 20,
    sort: "",
    setFilters: queryState.setFilters,
    reset: queryState.reset,
    setPage: vi.fn(),
    setPageSize: vi.fn(),
    setSort: vi.fn(),
  }),
}));

vi.mock("@/lib/api/assets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/assets")>();
  return {
    ...actual,
    useAssetFacets: () => ({
      data: {
        block: [],
        college: [],
        floor: [],
        department: [],
        manufacturer: [{ value: "Daikin", count: 3 }],
      },
    }),
  };
});

vi.mock("@/lib/api/analytics", () => ({
  useAnalytics: vi.fn(() => ({
    data: {
      scope: "counts",
      kpis: { total: 6, working: 4, offline_maintenance: 1, total_capacity: 10 },
      year_range: { min: 2015, max: 2024 },
      capacities: [1.0, 1.5, 2.0, 2.5],
      by_status: [{ status: "WORKING", count: 4 }],
      by_type: [{ type: "Split", count: 4 }],
      by_capacity: [{ capacity_ton: 1.5, count: 3 }],
      install_by_year: [{ year: 2024, count: 2 }],
      company_by_year: [{ company: "Daikin", year: 2024, count: 2 }],
      departments: [{ department: "Computer Science", count: 3 }],
      blocks: [],
      heatmap: [{ department: "Computer Science", year: 2024, count: 2 }],
      explorer: null,
    },
    isPending: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  })),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ViewerDashboardPage — read-only analytics (§11)", () => {
  it("renders KPIs, charts, explorer and insights with no edit affordance", () => {
    render(<ViewerDashboardPage />);

    expect(screen.getByText("Total ACs")).toBeTruthy();
    expect(screen.getByText("Explore Data")).toBeTruthy();
    expect(screen.getByText("Insights")).toBeTruthy();
    expect(screen.getByText("Department × Year")).toBeTruthy();
    // Nothing here can mutate: no edit/save/decommission/import buttons.
    expect(screen.queryByText(/edit/i)).toBeNull();
    expect(screen.queryByText(/decommission/i)).toBeNull();
    expect(screen.queryByText(/import/i)).toBeNull();
    expect(screen.queryByText(/save changes/i)).toBeNull();
  });

  it("forwards filter changes to the filter state", () => {
    render(<ViewerDashboardPage />);

    fireEvent.change(screen.getByLabelText("Make"), { target: { value: "Daikin" } });
    expect(queryState.setFilters).toHaveBeenCalledWith(
      expect.objectContaining({ manufacturer: "Daikin" }),
    );
  });

  it("offers a dynamic year range from the data (never hardcoded)", () => {
    render(<ViewerDashboardPage />);

    const from = screen.getByLabelText("From") as HTMLSelectElement;
    const options = [...from.options].map((o) => o.value).filter(Boolean);
    expect(options[0]).toBe("2015");
    expect(options[options.length - 1]).toBe("2024");
    expect(options).toHaveLength(10);
    fireEvent.change(from, { target: { value: "2020" } });
    expect(queryState.setFilters).toHaveBeenCalledWith(expect.objectContaining({ yearFrom: "2020" }));
  });
});
