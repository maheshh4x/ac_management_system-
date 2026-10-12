import { describe, expect, it } from "vitest";

import { statusDistributionFrom, type DashboardSummary } from "./assets";

/**
 * `operational_status` is a constrained vocabulary (migration 007):
 * WORKING / FAULT / MAINTENANCE / OFFLINE / UNKNOWN. UNKNOWN is a real
 * persisted state (rows imported without a status land there) and the
 * backend's /dashboard/summary reports it (`kpis.unknown`, `by_status`) —
 * the chart must show it instead of silently dropping the slice.
 */
function summaryWith(by_status: { status: string; count: number }[]): DashboardSummary {
  const total = by_status.reduce((sum, e) => sum + e.count, 0);
  const countOf = (s: string) => by_status.find((e) => e.status === s)?.count ?? 0;
  return {
    kpis: {
      total,
      working: countOf("WORKING"),
      under_maint: countOf("MAINTENANCE"),
      faulty: countOf("FAULT"),
      offline: countOf("OFFLINE"),
      unknown: countOf("UNKNOWN"),
      capacity: 0,
      departments: 0,
      rooms: 0,
      due_soon: 0,
      overdue: 0,
      completeness_pct: 0,
    },
    by_status,
    by_manufacturer: [],
    by_type: [],
    by_block: [],
    by_capacity: [],
    overdue: [],
  };
}

describe("statusDistributionFrom", () => {
  it("returns [] without a summary (loading / error state)", () => {
    expect(statusDistributionFrom(undefined)).toEqual([]);
  });

  it("represents persisted UNKNOWN statuses instead of dropping them", () => {
    const distribution = statusDistributionFrom(
      summaryWith([
        { status: "WORKING", count: 5 },
        { status: "FAULT", count: 2 },
        { status: "MAINTENANCE", count: 1 },
        { status: "OFFLINE", count: 1 },
        { status: "UNKNOWN", count: 3 },
      ]),
    );

    const unknown = distribution.find((e) => e.name === "Unknown");
    expect(unknown).toBeDefined();
    expect(unknown?.value).toBe(3);
    expect(unknown?.color).toBeTruthy();

    // No count is converted into another status: the total is preserved.
    expect(distribution.find((e) => e.name === "Working")?.value).toBe(5);
    expect(distribution.reduce((sum, e) => sum + e.value, 0)).toBe(12);
  });

  it("never re-labels UNKNOWN as Working/Faulty/Inactive", () => {
    const distribution = statusDistributionFrom(
      summaryWith([{ status: "UNKNOWN", count: 4 }]),
    );

    expect(distribution).toHaveLength(1);
    expect(distribution[0]?.name).toBe("Unknown");
    expect(distribution.find((e) => e.name === "Working")).toBeUndefined();
    expect(distribution.find((e) => e.name === "Faulty")).toBeUndefined();
    expect(distribution.find((e) => e.name === "Inactive")).toBeUndefined();
  });

  it("hides zero-count statuses but keeps every non-zero one", () => {
    const distribution = statusDistributionFrom(
      summaryWith([
        { status: "WORKING", count: 0 },
        { status: "FAULT", count: 2 },
        { status: "UNKNOWN", count: 0 },
      ]),
    );

    expect(distribution.map((e) => e.name)).toEqual(["Faulty"]);
  });
});
