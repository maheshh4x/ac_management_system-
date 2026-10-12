import { describe, expect, it } from "vitest";

import { cleanFilter, toAssetQuery } from "./useACDataset";

/**
 * Regression tests for the "All"-sentinel bug: `useAssetQueryState.parse()`
 * defaults every filter dimension to `"All"`, and `toAssetQuery()` used to
 * forward it verbatim — so the dashboard/list sent `status=All&block=All&…`
 * and the backend's literal `operational_status ILIKE 'All'` predicate
 * returned zero rows on a database that held imported assets.
 */
describe("cleanFilter", () => {
  it("maps the UI's sentinel and blanks to undefined", () => {
    expect(cleanFilter("All")).toBeUndefined();
    expect(cleanFilter(undefined)).toBeUndefined();
    expect(cleanFilter(null)).toBeUndefined();
    expect(cleanFilter("")).toBeUndefined();
  });

  it("preserves legitimate values verbatim (only the exact sentinel is dropped)", () => {
    expect(cleanFilter("Working")).toBe("Working");
    expect(cleanFilter("All Saints Block")).toBe("All Saints Block");
    expect(cleanFilter("Alley")).toBe("Alley");
    expect(cleanFilter(" chiller ")).toBe(" chiller "); // no hidden trimming
  });
});

describe("toAssetQuery", () => {
  it("omits every default 'All' filter from the API query", () => {
    const query = toAssetQuery({
      search: "",
      buildingId: "All",
      acType: "All",
      status: "All",
      departmentId: "All",
      floorId: "All",
    });

    expect(query.status).toBeUndefined();
    expect(query.block).toBeUndefined();
    expect(query.assetType).toBeUndefined();
    expect(query.department).toBeUndefined();
    expect(query.floor).toBeUndefined();
    expect(query.search).toBeUndefined();
    // No filter value remains that could reach toSearchParams().
    for (const [key, value] of Object.entries(query)) {
      if (key === "page" || key === "pageSize" || key === "sort") continue;
      expect(value, key).toBeUndefined();
    }
  });

  it("passes specific filters through unchanged", () => {
    const query = toAssetQuery({
      search: "chiller",
      buildingId: "Main Block",
      acType: "Split",
      status: "Working",
      departmentId: "Computer Science",
      floorId: "Floor 1",
      collegeId: "NITTTR-CHENNAI",
      manufacturer: "Daikin",
      capacityTon: "1.5",
      yearFrom: "2020",
      yearTo: "2024",
    });

    expect(query).toMatchObject({
      search: "chiller",
      block: "Main Block",
      assetType: "Split",
      status: "Working",
      department: "Computer Science",
      floor: "Floor 1",
      college: "NITTTR-CHENNAI",
      manufacturer: "Daikin",
      capacityTon: "1.5",
      installedFrom: "2020",
      installedTo: "2024",
    });
  });

  it("carries pagination, sort and dueBefore through", () => {
    const query = toAssetQuery(
      { dateRange: { start: "2026-01-01", end: "2026-12-31" }, status: "All" },
      { page: 2, pageSize: 25, sort: "ac_id" },
    );

    expect(query.page).toBe(2);
    expect(query.pageSize).toBe(25);
    expect(query.sort).toBe("ac_id");
    expect(query.dueBefore).toBe("2026-12-31");
    expect(query.status).toBeUndefined();
  });

  it("works with no filters at all (manager dashboard / viewer path)", () => {
    expect(toAssetQuery()).toEqual({
      page: undefined,
      pageSize: undefined,
      sort: undefined,
      search: undefined,
      status: undefined,
      block: undefined,
      college: undefined,
      department: undefined,
      assetType: undefined,
      manufacturer: undefined,
      capacityTon: undefined,
      floor: undefined,
      installedFrom: undefined,
      installedTo: undefined,
      dueBefore: undefined,
    });
  });
});
