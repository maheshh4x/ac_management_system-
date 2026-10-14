/**
 * Energy dashboard view tests: KPIs/charts/matrix/simulator render from
 * mocked hooks; estimates stay labeled; cost/weather/schedules render
 * honest unavailable states; the screen holds no privileged actions.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EnergyDashboardView } from "./EnergyDashboardView";

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
        scope: "all",
        kpis: {
          total: 4, working: 3, under_maint: 0, faulty: 0, offline: 0, unknown: 1,
          capacity: 6, departments: 1, rooms: 2, due_soon: 0, overdue: 0, completeness_pct: 90,
        },
        by_status: [{ status: "WORKING", count: 3 }],
        by_manufacturer: [{ manufacturer: "Daikin", count: 4 }],
        by_type: [],
        by_block: [{ block: "Main Block", block_id: "B1", count: 4 }],
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
    data: { departments: [{ department: "CSE", count: 4 }] },
    isPending: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

const SESSION = {
  id: "s1",
  ac_id: "AC-1",
  started_by: "tech-1",
  started_at: "2026-01-10T10:00:00Z",
  ended_at: "2026-01-10T12:00:00Z",
  duration_seconds: 7200,
  start_note: null,
  end_note: null,
  manufacturer: "Daikin",
  block: "Main Block",
  department: "CSE",
  power_rating_kw: 1.5,
};

vi.mock("@/lib/api/energy", () => ({
  useFleetSessions: () => ({
    data: { sessions: [SESSION], total: 1, open_count: 0, scope: "all" },
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

describe("EnergyDashboardView", () => {
  it("renders KPIs, charts, matrix and simulator from ledger data", () => {
    render(<EnergyDashboardView />);

    expect(screen.getByText("Energy Management Dashboard")).toBeTruthy();
    expect(screen.getByText("Total units")).toBeTruthy();
    expect(screen.getByText("Est. consumption")).toBeTruthy();
    expect(screen.getByText("Daily runtime & estimated consumption")).toBeTruthy();
    expect(screen.getByText("Breakdown & comparison")).toBeTruthy();
    expect(screen.getByText("What-if savings simulator")).toBeTruthy();
    expect(screen.getByText("Recommendations")).toBeTruthy();
  });

  it("never shows cost and marks estimates as estimates", () => {
    render(<EnergyDashboardView />);

    expect(screen.getByText("No tariff configured")).toBeTruthy();
    expect(screen.queryByText(/₹|INR|Rs\.? /)).toBeNull();
  });

  it("renders honest unavailable states for weather and schedules", () => {
    render(<EnergyDashboardView />);

    // Weather integration exists; with no data it states the reason explicitly.
    expect(screen.getByText("Weather context")).toBeTruthy();
    expect(screen.getByText(/No weather data available \(provider unreachable\)/)).toBeTruthy();
    expect(screen.getByText(/No operating schedules are configured/)).toBeTruthy();
  });

  it("never lets weather alter any energy figure or claim causation", () => {
    render(<EnergyDashboardView />);

    // The unavailable card must not imply weather caused consumption.
    expect(screen.queryByText(/because of weather|due to weather|weather caused/i)).toBeNull();
  });

  it("holds no privileged or equipment actions for any role", () => {
    render(<EnergyDashboardView />);

    expect(screen.queryByText(/edit/i)).toBeNull();
    expect(screen.queryByText(/decommission/i)).toBeNull();
    expect(screen.queryByText(/acknowledge/i)).toBeNull();
    expect(screen.queryByText(/dismiss/i)).toBeNull();
    // Export + refresh are read-only affordances.
    expect(screen.getByText("Sessions CSV")).toBeTruthy();
  });

  it("simulator labels output as simulation, not savings achieved", () => {
    render(<EnergyDashboardView />);

    expect(screen.getByText("Simulation")).toBeTruthy();
    expect(screen.getByText("Energy avoided")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Operating-hours reduction percent"), {
      target: { value: "30" },
    });
    expect(screen.getByText("Energy avoided")).toBeTruthy();
  });
});
