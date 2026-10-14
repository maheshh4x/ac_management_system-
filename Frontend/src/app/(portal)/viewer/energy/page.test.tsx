/**
 * Viewer energy route: the page renders the shared dashboard with no edit
 * affordance (the view itself holds no mutations; the backend remains
 * authoritative regardless).
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import ViewerEnergyPage from "./page";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { role: "VIEWER" } }),
}));

vi.mock("@/lib/api/assets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/assets")>();
  return {
    ...actual,
    useAssetFacets: () => ({ data: undefined, error: null, refetch: vi.fn() }),
    useDashboardSummary: () => ({
      data: {
        kpis: {
          total: 0, working: 0, under_maint: 0, faulty: 0, offline: 0, unknown: 0,
          capacity: 0, departments: 0, rooms: 0, due_soon: 0, overdue: 0, completeness_pct: 0,
        },
        by_status: [],
        by_manufacturer: [],
        by_type: [],
        by_block: [],
        by_capacity: [],
        overdue: [],
      },
      isPending: false,
      isFetching: false,
      error: null,
      refetch: vi.fn(),
    }),
  };
});

vi.mock("@/lib/api/analytics", () => ({
  useAnalytics: () => ({
    data: { departments: [] },
    isPending: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

vi.mock("@/lib/api/energy", () => ({
  useFleetSessions: () => ({
    data: { sessions: [], total: 0, open_count: 0, scope: "all" },
    isPending: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useEnergySummary: () => ({
    data: undefined,
    isPending: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useWeatherNow: () => ({
    data: { available: false, reason: "provider unreachable", source: "Open-Meteo" },
    isPending: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("ViewerEnergyPage — read-only energy dashboard", () => {
  it("renders the dashboard with honest empty states and no privileged actions", () => {
    render(<ViewerEnergyPage />);

    expect(screen.getByText("Energy Management Dashboard")).toBeTruthy();
    expect(screen.getByText("Total units")).toBeTruthy();
    expect(screen.getByText(/No weather data available/)).toBeTruthy();
    expect(screen.queryByText(/edit/i)).toBeNull();
    expect(screen.queryByText(/decommission/i)).toBeNull();
    expect(screen.queryByText(/acknowledge/i)).toBeNull();
  });
});
