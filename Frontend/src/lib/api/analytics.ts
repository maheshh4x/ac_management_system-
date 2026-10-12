/**
 * Filter-driven inventory analytics client (viewer-safe: aggregates only).
 *
 * One hook, one request per filter/explorer state: the backend aggregates
 * server-side (GET /api/v1/dashboard/analytics, VIEW_ASSET) so charts never
 * fan out per visualization. `placeholderData: keepPreviousData` keeps the
 * current figures visible while a filter change reloads (no stale-data
 * bugs: the query key always matches the requested filters).
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { AssetQuery } from "./assets";

export type AnalyticsDimension =
  | "company"
  | "year"
  | "department"
  | "block"
  | "floor"
  | "type"
  | "capacity"
  | "status";

export type AnalyticsMeasure = "count" | "avg_age" | "total_capacity";

export interface AnalyticsKpis {
  total: number;
  working: number;
  offline_maintenance: number;
  total_capacity: number;
}

export interface AnalyticsYearRange {
  min: number | null;
  max: number | null;
}

export interface YearCount {
  year: number;
  count: number;
}

export interface CompanyYearCount {
  company: string;
  year: number;
  count: number;
}

export interface NamedCount {
  name: string;
  count: number;
}

export interface HeatCell {
  department: string | null;
  year: number;
  count: number;
}

export interface ExplorerRow {
  d1: string | number | null;
  d2: string | number | null;
  value: number | null;
}

export interface AnalyticsExplorer {
  dim1: AnalyticsDimension;
  dim2: AnalyticsDimension | null;
  measure: AnalyticsMeasure;
  rows: ExplorerRow[];
}

export interface AnalyticsResponse {
  scope: string;
  kpis: AnalyticsKpis;
  year_range: AnalyticsYearRange;
  capacities: number[];
  by_status: Array<{ status: string; count: number }>;
  by_type: Array<{ type: string; count: number }>;
  by_capacity: Array<{ capacity_ton: number | null; count: number }>;
  install_by_year: YearCount[];
  company_by_year: CompanyYearCount[];
  departments: Array<{ department: string | null; count: number }>;
  blocks: Array<{ block: string | null; block_id: string | null; count: number }>;
  heatmap: HeatCell[];
  explorer: AnalyticsExplorer | null;
}

export interface AnalyticsQuery extends AssetQuery {
  dim1?: AnalyticsDimension;
  dim2?: AnalyticsDimension;
  measure?: AnalyticsMeasure;
}

export const analyticsKeys = {
  all: ["analytics"] as const,
  overview: (q: AnalyticsQuery) => ["analytics", "overview", q] as const,
};

function toAnalyticsParams(q: AnalyticsQuery): string {
  const params = new URLSearchParams();
  if (q.search) params.set("search", q.search);
  if (q.status) params.set("status", q.status);
  if (q.block) params.set("block", q.block);
  if (q.college) params.set("college", q.college);
  if (q.department) params.set("department", q.department);
  if (q.assetType) params.set("asset_type", q.assetType);
  if (q.manufacturer) params.set("manufacturer", q.manufacturer);
  if (q.capacityTon) params.set("capacity_ton", q.capacityTon);
  if (q.floor) params.set("floor", q.floor);
  if (q.room) params.set("room", q.room);
  if (q.installedFrom) params.set("installed_from", q.installedFrom);
  if (q.installedTo) params.set("installed_to", q.installedTo);
  if (q.lifecycle) params.set("lifecycle", q.lifecycle);
  if (q.locationStatus) params.set("location_status", q.locationStatus);
  if (q.dim1) params.set("dim1", q.dim1);
  if (q.dim2) params.set("dim2", q.dim2);
  if (q.measure) params.set("measure", q.measure);
  const s = params.toString();
  return s ? `?${s}` : "";
}

export function useAnalytics(query: AnalyticsQuery, enabled = true) {
  return useQuery({
    queryKey: analyticsKeys.overview(query),
    queryFn: () => api<AnalyticsResponse>(`/api/v1/dashboard/analytics${toAnalyticsParams(query)}`),
    enabled,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}
