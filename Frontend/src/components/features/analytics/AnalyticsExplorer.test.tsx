/**
 * Explorer tests: automatic chart recommendation, manual override,
 * query wiring (dims/measure flow into the hook), and the empty state.
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnalyticsExplorer } from "./AnalyticsExplorer";
import { useAnalytics } from "@/lib/api/analytics";

vi.mock("@/lib/api/analytics", () => ({
  useAnalytics: vi.fn(),
}));

const ROWS = [
  { d1: "Daikin", d2: 2021, value: 1 },
  { d1: "Daikin", d2: 2022, value: 2 },
  { d1: "LG", d2: 2022, value: 1 },
];

function mockExplorer(rows: typeof ROWS = ROWS) {
  vi.mocked(useAnalytics).mockReturnValue({
    data: { explorer: { dim1: "company", dim2: "year", measure: "count", rows } },
    isPending: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useAnalytics>);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AnalyticsExplorer", () => {
  it("recommends stacked bars for company × year and queries with the selection", () => {
    mockExplorer();
    render(<AnalyticsExplorer baseQuery={{}} />);

    expect(screen.getByText(/recommended: stacked bars/i)).toBeTruthy();
    expect(useAnalytics).toHaveBeenCalledWith(
      expect.objectContaining({ dim1: "company", dim2: "year", measure: "count" }),
    );
  });

  it("collapses to a single dimension and re-queries without dim2", () => {
    mockExplorer();
    render(<AnalyticsExplorer baseQuery={{}} />);

    fireEvent.change(screen.getByLabelText("Second dimension"), { target: { value: "" } });
    const last = vi.mocked(useAnalytics).mock.calls.at(-1)?.[0] as Record<string, unknown>;
    expect(last).toMatchObject({ dim1: "company", measure: "count" });
    expect("dim2" in last).toBe(false);
  });

  it("allows a manual chart override", () => {
    mockExplorer();
    render(<AnalyticsExplorer baseQuery={{}} />);

    fireEvent.change(screen.getByLabelText("Chart type"), { target: { value: "heatmap" } });
    // Manual choice replaces the recommendation note and renders a heatmap.
    expect(screen.getByText("Heatmap", { selector: "option" })).toBeTruthy();
    expect(screen.getByRole("table", { name: "Heatmap" })).toBeTruthy();
  });

  it("shows an empty state when the combination has no rows", () => {
    mockExplorer([]);
    render(<AnalyticsExplorer baseQuery={{}} />);

    expect(screen.getByText("No data for the current filters.")).toBeTruthy();
  });
});
