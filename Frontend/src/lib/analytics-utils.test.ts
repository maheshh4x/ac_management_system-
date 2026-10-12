/**
 * Analytics pure-logic tests: chart recommendation rules, age banding,
 * insight wording (neutral, data-backed, max four), pivot shape, formatters.
 */
import { describe, expect, it } from "vitest";

import {
  AGE_BANDS,
  agedCount,
  applicableCharts,
  bucketAges,
  computeInsights,
  DIMENSION_LABELS,
  formatCount,
  formatTons,
  inPeriodCount,
  pivotSeries,
  recommendChart,
} from "./analytics-utils";

describe("recommendChart", () => {
  it("picks a line for a lone year dimension", () => {
    expect(recommendChart({ dim1: "year", dim2: null, d1Buckets: 10, cellCount: 10 })).toBe("line");
  });

  it("picks a donut for small type/status vocabularies, bars beyond that", () => {
    expect(recommendChart({ dim1: "type", dim2: null, d1Buckets: 3, cellCount: 3 })).toBe("donut");
    expect(recommendChart({ dim1: "status", dim2: null, d1Buckets: 5, cellCount: 5 })).toBe("donut");
    expect(recommendChart({ dim1: "type", dim2: null, d1Buckets: 9, cellCount: 9 })).toBe("hbar");
  });

  it("uses horizontal bars for many buckets, vertical otherwise", () => {
    expect(recommendChart({ dim1: "company", dim2: null, d1Buckets: 12, cellCount: 12 })).toBe("hbar");
    expect(recommendChart({ dim1: "company", dim2: null, d1Buckets: 4, cellCount: 4 })).toBe("bar");
  });

  it("stacks few series over years, heatmaps dense matrices", () => {
    expect(recommendChart({ dim1: "company", dim2: "year", d1Buckets: 4, cellCount: 20 })).toBe("stacked");
    expect(recommendChart({ dim1: "company", dim2: "year", d1Buckets: 12, cellCount: 60 })).toBe("heatmap");
    expect(recommendChart({ dim1: "department", dim2: "block", d1Buckets: 4, cellCount: 10 })).toBe("grouped");
    expect(recommendChart({ dim1: "department", dim2: "block", d1Buckets: 8, cellCount: 40 })).toBe("heatmap");
  });
});

describe("applicableCharts", () => {
  it("restricts manual overrides to sensible types", () => {
    expect(applicableCharts("year", null)).toContain("line");
    expect(applicableCharts("year", null)).not.toContain("donut");
    expect(applicableCharts("type", null)).toContain("donut");
    expect(applicableCharts("company", null)).not.toContain("stacked");
    expect(applicableCharts("company", "year")).toEqual(
      expect.arrayContaining(["stacked", "heatmap", "grouped", "line"]),
    );
  });
});

describe("age banding", () => {
  const installs = [
    { year: 2015, count: 1 },
    { year: 2021, count: 1 },
    { year: 2022, count: 2 },
    { year: 2024, count: 2 },
  ];

  it("buckets every unit into exactly one band", () => {
    const bands = bucketAges(installs, 2026);
    expect(bands.map((b) => b.name)).toEqual(AGE_BANDS.map((b) => b.label));
    expect(bands.reduce((sum, b) => sum + b.value, 0)).toBe(6);
    expect(bands.find((b) => b.name === "10–12")?.value).toBe(1);
  });

  it("counts 10+ and in-period units dynamically (no hardcoding)", () => {
    expect(agedCount(installs, 2026, 10)).toBe(1);
    expect(agedCount(installs, 2032, 10)).toBe(4);
    expect(inPeriodCount(installs, 2022, null)).toBe(4);
    expect(inPeriodCount(installs, null, 2021)).toBe(2);
    expect(inPeriodCount(installs, null, null)).toBe(6);
  });
});

describe("computeInsights", () => {
  const input = {
    total: 6,
    installByYear: [
      { year: 2015, count: 1 },
      { year: 2021, count: 1 },
      { year: 2022, count: 2 },
      { year: 2024, count: 2 },
    ],
    companyByYear: [
      { company: "Daikin", year: 2021, count: 1 },
      { company: "Daikin", year: 2022, count: 1 },
      { company: "Daikin", year: 2024, count: 1 },
      { company: "LG", year: 2022, count: 1 },
      { company: "Samsung", year: 2015, count: 1 },
      { company: "Carrier", year: 2024, count: 1 },
    ],
    departments: [
      { department: "Computer Science", count: 3 },
      { department: "Administration", count: 2 },
    ],
    currentYear: 2026,
  };

  it("emits at most four neutral, data-backed observations", () => {
    const insights = computeInsights(input);
    expect(insights.length).toBeLessThanOrEqual(4);
    expect(insights.join(" ")).toContain("Daikin");
    expect(insights.join(" ")).toContain("Computer Science");
    expect(insights.join(" ")).toMatch(/monitor/i);
    for (const text of insights) {
      expect(text).not.toMatch(/should replace|must replace|replace immediately/i);
    }
  });

  it("never invents conclusions for empty data", () => {
    expect(
      computeInsights({ total: 0, installByYear: [], companyByYear: [], departments: [], currentYear: 2026 }),
    ).toEqual([]);
  });
});

describe("pivotSeries", () => {
  it("pivots two-dimensional rows with Unknown for nulls", () => {
    const { rows, series } = pivotSeries([
      { d1: "Daikin", d2: 2021, value: 1 },
      { d1: "Daikin", d2: 2022, value: 2 },
      { d1: null, d2: 2022, value: 1 },
    ]);
    expect(series).toEqual(["2021", "2022"]);
    expect(rows).toEqual([
      { name: "Daikin", "2021": 1, "2022": 2 },
      { name: "Unknown", "2022": 1 },
    ]);
  });
});

describe("formatters and labels", () => {
  it("formats counts and tonnage consistently", () => {
    expect(formatCount(1234)).toBe("1,234");
    expect(formatTons(2)).toBe("2 Ton");
    expect(formatTons(1.5)).toBe("1.5 Ton");
  });

  it("labels every dimension", () => {
    expect(Object.keys(DIMENSION_LABELS)).toEqual([
      "company",
      "year",
      "department",
      "block",
      "floor",
      "type",
      "capacity",
      "status",
    ]);
  });
});
