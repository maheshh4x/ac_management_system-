/**
 * Pure analytics helpers: chart recommendation, age banding, insight
 * computation, number formatting. No React, no fetching — fully unit-tested.
 *
 * Chart recommendation rules (documented so the UI never asks users to
 * understand visualization theory):
 * - single dimension + year            -> line (installations over time)
 * - single dimension, small vocabulary -> donut (type/status with <=6 slices)
 * - single dimension, many/long labels -> horizontal bars, else vertical bars
 * - dimension × year, few series (<=6) -> stacked bars, else heatmap
 * - any other pair, dense (>24 cells) -> heatmap, else grouped bars
 */

import type {
  AnalyticsDimension,
  AnalyticsMeasure,
  CompanyYearCount,
  HeatCell,
  YearCount,
} from "@/lib/api/analytics";

export type ExplorerChart = "bar" | "hbar" | "line" | "stacked" | "grouped" | "heatmap" | "donut";

export const DIMENSION_LABELS: Record<AnalyticsDimension, string> = {
  company: "Company",
  year: "Year",
  department: "Department",
  block: "Block",
  floor: "Floor",
  type: "AC Type",
  capacity: "Capacity",
  status: "Status",
};

export const MEASURE_LABELS: Record<AnalyticsMeasure, string> = {
  count: "AC Count",
  avg_age: "Average Age",
  total_capacity: "Total Capacity",
};

const SMALL_VOCABULARY_DIMS: AnalyticsDimension[] = ["type", "status"];

export function recommendChart(args: {
  dim1: AnalyticsDimension;
  dim2: AnalyticsDimension | null;
  d1Buckets: number;
  cellCount: number;
}): ExplorerChart {
  const { dim1, dim2, d1Buckets, cellCount } = args;
  if (dim2 === null) {
    if (dim1 === "year") return "line";
    if (SMALL_VOCABULARY_DIMS.includes(dim1) && d1Buckets <= 6) return "donut";
    return d1Buckets > 8 ? "hbar" : "bar";
  }
  if (dim2 === "year") return d1Buckets <= 6 ? "stacked" : "heatmap";
  return cellCount > 24 ? "heatmap" : "grouped";
}

/** Manual overrides offered for a selection (recommended first). */
export function applicableCharts(dim1: AnalyticsDimension, dim2: AnalyticsDimension | null): ExplorerChart[] {
  if (dim2 === null) {
    const base: ExplorerChart[] = ["bar", "hbar"];
    if (dim1 === "year") return ["line", ...base];
    if (SMALL_VOCABULARY_DIMS.includes(dim1)) return ["donut", ...base];
    return base;
  }
  const pair: ExplorerChart[] = ["grouped", "stacked", "heatmap", "hbar"];
  if (dim2 === "year" || dim1 === "year") return ["stacked", "heatmap", "grouped", "line"];
  return pair;
}

export interface AgeBand {
  label: string;
  min: number;
  max: number | null; // null = open-ended (13+)
}

export const AGE_BANDS: AgeBand[] = [
  { label: "0–3", min: 0, max: 3 },
  { label: "4–6", min: 4, max: 6 },
  { label: "7–9", min: 7, max: 9 },
  { label: "10–12", min: 10, max: 12 },
  { label: "13+", min: 13, max: null },
];

/** Bucket per-year install counts into the fixed age bands (current year dynamic). */
export function bucketAges(installByYear: YearCount[], currentYear: number): Array<{ name: string; value: number }> {
  const bands = AGE_BANDS.map((b) => ({ name: b.label, value: 0 }));
  for (const { year, count } of installByYear) {
    const age = currentYear - year;
    const idx = AGE_BANDS.findIndex((b) => age >= b.min && (b.max === null || age <= b.max));
    if (idx >= 0) bands[idx].value += count;
  }
  return bands;
}

/** Units at least `cutoffYears` old (10+ drives the aging KPI/insight). */
export function agedCount(installByYear: YearCount[], currentYear: number, cutoffYears = 10): number {
  return installByYear
    .filter(({ year }) => currentYear - year >= cutoffYears)
    .reduce((sum, { count }) => sum + count, 0);
}

/** Units installed inside [from, to] (open ends default to the data range). */
export function inPeriodCount(
  installByYear: YearCount[],
  from: number | null,
  to: number | null,
): number {
  return installByYear
    .filter(({ year }) => (from == null || year >= from) && (to == null || year <= to))
    .reduce((sum, { count }) => sum + count, 0);
}

export function formatCount(n: number): string {
  return n.toLocaleString("en-US");
}

export function formatTons(n: number): string {
  return `${Number(n.toFixed(1)).toLocaleString("en-US")} Ton`;
}

/**
 * Pivot two-dimensional rows into recharts stacked/grouped shape:
 * [{name: d1, [d2a]: v, [d2b]: v}] + ordered series keys. Nulls become
 * "Unknown" so nothing silently drops.
 */
export function pivotSeries(
  rows: Array<{ d1: string | number | null; d2: string | number | null; value: number | null }>,
): { rows: Array<Record<string, string | number>>; series: string[] } {
  const seriesOrder: string[] = [];
  const byName = new Map<string, Record<string, string | number>>();
  const key = (v: string | number | null) => (v === null || v === undefined || v === "" ? "Unknown" : String(v));
  for (const row of rows) {
    const name = key(row.d1);
    const column = key(row.d2);
    if (!seriesOrder.includes(column)) seriesOrder.push(column);
    const entry = byName.get(name) ?? { name };
    entry[column] = row.value ?? 0;
    byName.set(name, entry);
  }
  return { rows: [...byName.values()], series: seriesOrder };
}

export interface InsightInput {
  total: number;
  installByYear: YearCount[];
  companyByYear: CompanyYearCount[];
  departments: Array<{ department: string | null; count: number }>;
  currentYear: number;
}

/**
 * At most four data-backed observations in neutral terminology (monitor /
 * review / potential replacement candidate — never "replace", never
 * invented). Empty data yields no insights, never placeholders.
 */
export function computeInsights(input: InsightInput): string[] {
  const { total, installByYear, companyByYear, departments, currentYear } = input;
  if (total <= 0) return [];
  const insights: string[] = [];

  const old = agedCount(installByYear, currentYear, 10);
  if (old > 0) {
    const pct = Math.round((old / total) * 100);
    insights.push(
      `${pct}% of filtered ACs were installed over 10 years ago (${formatCount(old)} units) — monitor for review.`,
    );
  }

  const byCompany = new Map<string, number>();
  for (const row of companyByYear) {
    byCompany.set(row.company, (byCompany.get(row.company) ?? 0) + row.count);
  }
  if (byCompany.size > 0) {
    const [topCompany, topCount] = [...byCompany.entries()].sort((a, b) => b[1] - a[1])[0];
    const pct = Math.round((topCount / total) * 100);
    insights.push(`${topCompany} represents the largest share of the selected inventory (${pct}%).`);
  }

  const ranked = [...departments].sort((a, b) => b.count - a.count).filter((d) => d.count > 0);
  if (ranked.length > 0) {
    const top = ranked[0];
    insights.push(
      `${top.department ?? "Unknown"} has the highest AC count (${formatCount(top.count)}).`,
    );
  }

  const latest = installByYear.length > 0 ? Math.max(...installByYear.map((r) => r.year)) : null;
  if (latest !== null) {
    const latestCount = installByYear
      .filter((r) => r.year === latest)
      .reduce((sum, r) => sum + r.count, 0);
    insights.push(`${formatCount(latestCount)} ACs were installed in ${latest} (latest year in range).`);
  }

  return insights.slice(0, 4);
}
