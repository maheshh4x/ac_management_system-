"use client";

/**
 * Assets resource hooks (Phase 2, task 3; extended Phase 3).
 *
 * One file per resource: query-key factories + hooks + pure valuation
 * helpers (ported from the retired localStorage store). Pages stay
 * presentational; all aggregation happens server-side (Plan #35).
 */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { mapAsset, toUiStatus, type ApiAsset, type LinkedUnitSummary } from "@/lib/adapters/asset";
import type {
  AssetFacets,
  ACStatistics,
  DistributionEntry,
  ExtendedAC,
} from "@/lib/types/asset";
import { api, apiList, type Paginated } from "./client";

/** Mirrors the GET /api/v1/assets query contract (openapi.yaml). */
export interface AssetQuery {
  page?: number;
  pageSize?: number;
  search?: string;
  status?: string;
  block?: string;
  college?: string;
  department?: string;
  assetType?: string;
  manufacturer?: string;
  capacityTon?: string;
  floor?: string;
  room?: string;
  installedFrom?: string;
  installedTo?: string;
  sort?: string;
  lifecycle?: string;
  locationStatus?: string;
  dueBefore?: string;
}

export const assetKeys = {
  all: ["assets"] as const,
  list: (q: AssetQuery) => ["assets", "list", q] as const,
  allRows: (q: Omit<AssetQuery, "page" | "pageSize" | "sort">) => ["assets", "all", q] as const,
  facets: () => ["assets", "facets"] as const,
  summary: (q: AssetQuery) => ["assets", "summary", q] as const,
  detail: (ref: string) => ["assets", "detail", ref] as const,
};

function toSearchParams(q: AssetQuery): string {
  const params = new URLSearchParams();
  if (q.page) params.set("page", String(q.page));
  if (q.pageSize) params.set("page_size", String(q.pageSize));
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
  if (q.sort) params.set("sort", q.sort);
  if (q.lifecycle) params.set("lifecycle", q.lifecycle);
  if (q.locationStatus) params.set("location_status", q.locationStatus);
  if (q.dueBefore) params.set("due_before", q.dueBefore);
  const s = params.toString();
  return s ? `?${s}` : "";
}

export function useAssets(query: AssetQuery, enabled = true) {
  return useQuery({
    queryKey: assetKeys.list(query),
    enabled,
    placeholderData: keepPreviousData, // smooth page/sort transitions
    queryFn: async (): Promise<Paginated<ExtendedAC>> => {
      const raw = await apiList<ApiAsset>(`/api/v1/assets${toSearchParams(query)}`);
      return { items: raw.items.map(mapAsset), pagination: raw.pagination };
    },
  });
}

/**
 * Every row matching the filters (reports/valuations need the full slice).
 * The list endpoint caps page_size at 200, so this walks the pages — at
 * most 10 requests (2 000 rows) to bound the work. Page 1 reveals
 * total_pages; the remaining pages fetch with bounded concurrency (4 in
 * flight) instead of one round trip at a time. Order is page-ascending and
 * any page failure rejects the whole query — partial data is never
 * presented as complete.
 */
const ALL_ASSETS_MAX_PAGES = 10;
const ALL_ASSETS_CONCURRENCY = 4;

export function useAllAssets(
  filters: Omit<AssetQuery, "page" | "pageSize" | "sort">,
  enabled = true,
) {
  return useQuery({
    queryKey: assetKeys.allRows(filters),
    enabled,
    queryFn: async (): Promise<ExtendedAC[]> => {
      const first = await apiList<ApiAsset>(
        `/api/v1/assets${toSearchParams({ ...filters, page: 1, pageSize: 200 })}`,
      );
      const collected = first.items.map(mapAsset);
      const rest: number[] = [];
      for (let page = 2; page <= first.pagination.total_pages && page <= ALL_ASSETS_MAX_PAGES; page++) {
        rest.push(page);
      }
      for (let i = 0; i < rest.length; i += ALL_ASSETS_CONCURRENCY) {
        const batch = await Promise.all(
          rest.slice(i, i + ALL_ASSETS_CONCURRENCY).map(async (page) => {
            const raw = await apiList<ApiAsset>(
              `/api/v1/assets${toSearchParams({ ...filters, page, pageSize: 200 })}`,
            );
            return raw.items.map(mapAsset);
          }),
        );
        for (const rows of batch) collected.push(...rows);
      }
      return collected;
    },
  });
}

export function useAssetFacets() {
  return useQuery({
    queryKey: assetKeys.facets(),
    // Facet vocabularies derive only from acs rows, and every asset
    // mutation invalidates assetKeys.all (this key lives underneath), so a
    // 5-minute staleness window can never hide data after a write — it
    // only skips repeat GETs (facets measured ~891 ms server-side) when
    // navigating back to a dashboard. Bounded, never indefinite.
    staleTime: 300_000,
    queryFn: () => api<AssetFacets>("/api/v1/assets/facets"),
  });
}

// ── dashboard summary (Plan #35 — DB-side aggregation) ──────────────────────

export interface SummaryKpis {
  total: number;
  working: number;
  under_maint: number;
  faulty: number;
  offline: number;
  unknown: number;
  capacity: number;
  departments: number;
  rooms: number;
  due_soon: number;
  overdue: number;
  completeness_pct: number;
}

export interface DashboardSummary {
  scope?: string;
  kpis: SummaryKpis;
  by_status: { status: string; count: number }[];
  by_manufacturer: { manufacturer: string; count: number }[];
  by_type: { type: string; count: number }[];
  by_block: { block: string; block_id?: string | null; count: number }[];
  by_capacity: { capacity_ton: number | null; count: number }[];
  overdue: { ac_id: string; next_service_date: string | null }[];
}

export function useDashboardSummary(query: AssetQuery) {
  return useQuery({
    queryKey: assetKeys.summary(query),
    queryFn: () => api<DashboardSummary>(`/api/v1/dashboard/summary${toSearchParams(query)}`),
  });
}

// ── detail (Phase 3 task 4: enriched payload) ───────────────────────────────

export interface AssetDetail extends ExtendedAC {
  maintenanceHistory: { [k: string]: unknown }[];
  movementHistory: { [k: string]: unknown }[];
  /** asset_location_history rows (Phase 5): completed room changes. */
  locationHistory: { [k: string]: unknown }[];
  attributes: { attribute_name: string; attribute_value: string | null; attribute_type?: string }[];
  qrVersion?: number;
  /** Linked OUTDOOR row for an indoor asset (Feature A); null = not linked. */
  outdoorUnit?: LinkedUnitSummary | null;
  /** Indoor parent of an outdoor row; null for indoor assets. */
  parentUnit?: LinkedUnitSummary | null;
}

function mapAssetDetail(raw: Record<string, unknown>): AssetDetail {
  const asset = mapAsset(raw as ApiAsset);
  return {
    ...asset,
    maintenanceHistory: (raw.maintenance_history as AssetDetail["maintenanceHistory"]) ?? [],
    movementHistory: (raw.movement_history as AssetDetail["movementHistory"]) ?? [],
    locationHistory: (raw.location_history as AssetDetail["locationHistory"]) ?? [],
    attributes: (raw.attributes as AssetDetail["attributes"]) ?? [],
    qrVersion: typeof raw.qr_version === "number" ? raw.qr_version : undefined,
    outdoorUnit: (raw.outdoor_unit as LinkedUnitSummary | null) ?? null,
    parentUnit: (raw.parent_unit as LinkedUnitSummary | null) ?? null,
  };
}

/** <ref> resolves asset_uuid / asset_code / ac_id (triple-ref contract). */
export function useAssetDetail(ref: string | null) {
  return useQuery({
    queryKey: assetKeys.detail(ref ?? ""),
    enabled: Boolean(ref),
    queryFn: async (): Promise<AssetDetail> =>
      mapAssetDetail(await api<Record<string, unknown>>(`/api/v1/assets/${encodeURIComponent(ref!)}`)),
  });
}

export function useAsset(ref: string | null) {
  return useQuery({
    queryKey: assetKeys.detail(ref ?? ""),
    enabled: Boolean(ref),
    queryFn: async (): Promise<ExtendedAC> =>
      mapAsset(await api<ApiAsset>(`/api/v1/assets/${encodeURIComponent(ref!)}`)),
  });
}

// ── mutations (writes always address the raw ac_id) ─────────────────────────

function useInvalidateAssets() {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: assetKeys.all });
  };
}

/** POST /api/v1/assets — CREATE_ASSET. Returns the created row. */
export function useCreateAsset() {
  const invalidate = useInvalidateAssets();
  return useMutation({
    mutationFn: (payload: Record<string, unknown>) =>
      api<Record<string, unknown>>("/api/v1/assets", { method: "POST", body: payload }),
    onSuccess: invalidate,
  });
}

/**
 * Overlay a backend PATCH payload onto a mapped row — used to preview the
 * edit instantly (optimistic update) until the server confirms it.
 */
function applyAssetPatch<T extends ExtendedAC>(item: T, payload: Record<string, unknown>): T {
  const next: T = { ...item };
  if ("current_status" in payload) {
    const api = payload.current_status as string | null;
    next.status = api == null ? "Unknown" : toUiStatus(api);
  }
  if ("ac_make_company" in payload) next.make = (payload.ac_make_company as string) ?? "";
  if ("ac_model_number" in payload) next.model = (payload.ac_model_number as string) ?? "";
  if ("ac_capacity_ton" in payload) {
    const raw = payload.ac_capacity_ton as number | string | null;
    const ton = raw == null ? undefined : Number(raw);
    next.capacityTon = ton !== undefined && Number.isFinite(ton) ? ton : undefined;
    next.capacity = next.capacityTon !== undefined ? `${next.capacityTon} Ton` : "";
  }
  if ("ac_type" in payload) next.type = (payload.ac_type as string) ?? "";
  if ("technician" in payload) next.technician = (payload.technician as string) || undefined;
  if ("remarks" in payload) next.remarks = (payload.remarks as string) || undefined;
  if ("next_service_date" in payload) next.nextMaintenanceDate = (payload.next_service_date as string) || undefined;
  if ("installation_year" in payload) {
    next.installationYear = payload.installation_year == null ? undefined : String(payload.installation_year);
  }
  if ("manufacturing_year" in payload) {
    next.manufacturingYear = payload.manufacturing_year == null ? undefined : String(payload.manufacturing_year);
  }
  if ("ac_star_rating" in payload) {
    next.starRating = payload.ac_star_rating == null ? undefined : String(payload.ac_star_rating);
  }
  if ("power_rating_kw" in payload) {
    next.powerRating = payload.power_rating_kw == null ? undefined : `${payload.power_rating_kw} kW`;
  }
  if ("room_id" in payload) next.location = { ...next.location, roomId: (payload.room_id as string) ?? "" };
  return next;
}

/** PATCH /api/v1/assets/<ref> — EDIT_ASSET. Optimistic, rolls back on error. */
export function useUpdateAsset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ acId, payload }: { acId: string; payload: Record<string, unknown> }) =>
      api<Record<string, unknown>>(`/api/v1/assets/${encodeURIComponent(acId)}`, {
        method: "PATCH",
        body: payload,
      }),
    onMutate: async ({ acId, payload }) => {
      await qc.cancelQueries({ queryKey: assetKeys.all });
      const previous = qc.getQueriesData({ queryKey: assetKeys.all });
      const matches = (it: ExtendedAC) => it.acId === acId || it.id === acId;
      qc.setQueriesData<Paginated<ExtendedAC>>({ queryKey: ["assets", "list"] }, (old) =>
        old ? { ...old, items: old.items.map((it) => (matches(it) ? applyAssetPatch(it, payload) : it)) } : old,
      );
      qc.setQueriesData<ExtendedAC[]>({ queryKey: ["assets", "all"] }, (old) =>
        Array.isArray(old) ? old.map((it) => (matches(it) ? applyAssetPatch(it, payload) : it)) : old,
      );
      qc.setQueriesData<AssetDetail>({ queryKey: ["assets", "detail"] }, (old) =>
        old && matches(old) ? applyAssetPatch(old, payload) : old,
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      // Roll every touched query back to its pre-edit snapshot.
      context?.previous.forEach(([key, data]) => qc.setQueryData(key, data));
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: assetKeys.all });
    },
  });
}

/** POST /api/v1/assets/<ac_id>/decommission — DECOMMISSION_ASSET. */
export function useDecommissionAsset() {
  const invalidate = useInvalidateAssets();
  return useMutation({
    mutationFn: (acId: string) =>
      api<Record<string, unknown>>(`/api/v1/assets/${encodeURIComponent(acId)}/decommission`, {
        method: "POST",
      }),
    onSuccess: invalidate,
  });
}

// ── pure valuation helpers (ported from ac-data-service, run on server data) ─

export function calculateACUnitValue(ac: ExtendedAC): number {
  const tons = ac.capacityTon || (ac.capacity ? parseFloat(ac.capacity.replace(/[^0-9.]/g, "")) : 1.5) || 1.5;
  const type = (ac.type || "").toLowerCase();

  if (type.includes("central") || type.includes("vrf") || type.includes("vrv") || type.includes("chiller")) {
    return Math.round(tons * 50000);
  }
  if (type.includes("cassette") || type.includes("duct")) {
    return Math.round(tons * 38000);
  }
  if (tons <= 1.0) return 32000;
  if (tons <= 1.5) return 45000;
  if (tons <= 2.0) return 62000;
  return Math.round(tons * 30000);
}

export function getTotalAssetValue(acs: ExtendedAC[]) {
  let totalValueINR = 0;
  acs.forEach((ac) => {
    totalValueINR += calculateACUnitValue(ac);
  });

  let formattedValue = `₹${(totalValueINR / 100000).toFixed(1)}L`;
  if (totalValueINR >= 10000000) {
    formattedValue = `₹${(totalValueINR / 10000000).toFixed(2)} Cr`;
  }

  return { totalValueINR, formattedValue, acsValuedCount: acs.length };
}

export function getAverageACAge(acs: ExtendedAC[]) {
  const currentYear = new Date().getFullYear();
  let totalAge = 0;
  let count = 0;

  acs.forEach((ac) => {
    let yearStr = ac.installationYear;
    if (!yearStr && ac.installationDate) {
      yearStr = ac.installationDate.split("-")[0];
    }
    if (yearStr) {
      const year = parseInt(yearStr, 10);
      if (!isNaN(year) && year > 1990 && year <= currentYear) {
        totalAge += currentYear - year;
        count++;
      }
    }
  });

  const avgAgeYears = count > 0 ? parseFloat((totalAge / count).toFixed(1)) : 2.5;
  const formattedAge = count > 0 ? `${avgAgeYears} yrs` : "N/A";

  return { avgAgeYears, formattedAge, unitsWithAgeCount: count };
}

export function getConnectedPowerLoad(acs: ExtendedAC[]) {
  let totalKw = 0;
  let count = 0;

  acs.forEach((ac) => {
    let kw = 0;
    if (ac.powerRating) {
      kw = parseFloat(ac.powerRating.replace(/[^0-9.]/g, "")) || 0;
    }
    if (!kw) {
      const tons = ac.capacityTon || (ac.capacity ? parseFloat(ac.capacity.replace(/[^0-9.]/g, "")) : 1.5) || 1.5;
      kw = tons * 1.2; // standard rule of thumb: ~1.2 kW per ton
    }
    totalKw += kw;
    count++;
  });

  const formattedKw = `${totalKw.toFixed(1)} kW`;
  return { totalKw: parseFloat(totalKw.toFixed(1)), formattedKw, unitsWithPowerCount: count };
}

export function getAssetValueByBuilding(acs: ExtendedAC[]) {
  const map = new Map<string, { building: string; totalValue: number; count: number }>();

  acs.forEach((ac) => {
    const bName = ac.buildingName || ac.location.buildingId || "Unassigned";
    const val = calculateACUnitValue(ac);
    const existing = map.get(bName) || { building: bName, totalValue: 0, count: 0 };
    map.set(bName, { building: bName, totalValue: existing.totalValue + val, count: existing.count + 1 });
  });

  return Array.from(map.values()).map((item) => ({
    building: item.building,
    totalValue: item.totalValue,
    formattedValue: `₹${(item.totalValue / 100000).toFixed(1)}L`,
    count: item.count,
  }));
}

export function getAssetValueByType(acs: ExtendedAC[]) {
  const map = new Map<string, { type: string; totalValue: number; count: number }>();

  acs.forEach((ac) => {
    const tName = ac.type || "Other";
    const val = calculateACUnitValue(ac);
    const existing = map.get(tName) || { type: tName, totalValue: 0, count: 0 };
    map.set(tName, { type: tName, totalValue: existing.totalValue + val, count: existing.count + 1 });
  });

  return Array.from(map.values()).map((item) => ({
    name: item.type,
    totalValue: item.totalValue,
    formattedValue: `₹${(item.totalValue / 100000).toFixed(1)}L`,
    count: item.count,
  }));
}

export function getFaultFrequencyByBuilding(acs: ExtendedAC[]) {
  const map = new Map<string, { building: string; faultyCount: number; totalCount: number }>();

  acs.forEach((ac) => {
    const bName = ac.buildingName || ac.location.buildingId || "Unassigned";
    const isFaultyOrMaint = ac.status === "Fault" || ac.status === "Maintenance";
    const existing = map.get(bName) || { building: bName, faultyCount: 0, totalCount: 0 };
    map.set(bName, {
      building: bName,
      faultyCount: existing.faultyCount + (isFaultyOrMaint ? 1 : 0),
      totalCount: existing.totalCount + 1,
    });
  });

  return Array.from(map.values()).map((item) => ({
    building: item.building,
    faultyCount: item.faultyCount,
    totalCount: item.totalCount,
    faultRate: item.totalCount > 0 ? Math.round((item.faultyCount / item.totalCount) * 100) : 0,
  }));
}

// ── chart distributions (built from the dashboard summary) ──────────────────

const STATUS_COLORS = {
  Working: "#10B981",
  Faulty: "#F43F5E",
  "Under Maintenance": "#F59E0B",
  Inactive: "#6366F1",
  // operational_status vocabulary (migration 007) includes UNKNOWN — a real
  // persisted state (rows imported without a status land here), so it gets
  // its own neutral slice instead of being silently dropped from the chart.
  Unknown: "#64748B",
} as const;

export function statusDistributionFrom(
  summary: DashboardSummary | undefined,
): { name: string; value: number; color: string }[] {
  if (!summary) return [];
  const count = (status: string) => summary.by_status.find((e) => e.status === status)?.count ?? 0;
  return [
    { name: "Working", value: count("WORKING"), color: STATUS_COLORS.Working },
    { name: "Faulty", value: count("FAULT"), color: STATUS_COLORS.Faulty },
    { name: "Under Maintenance", value: count("MAINTENANCE"), color: STATUS_COLORS["Under Maintenance"] },
    { name: "Inactive", value: count("OFFLINE"), color: STATUS_COLORS.Inactive },
    { name: "Unknown", value: count("UNKNOWN"), color: STATUS_COLORS.Unknown },
  ].filter((item) => item.value > 0);
}

export function typeDistributionFrom(summary: DashboardSummary | undefined): DistributionEntry[] {
  return summary?.by_type.map((e) => ({ name: e.type, value: e.count })) ?? [];
}

export function buildingDistributionFrom(summary: DashboardSummary | undefined) {
  return summary?.by_block.map((e) => ({ building: e.block, count: e.count })) ?? [];
}

export function capacityDistributionFrom(summary: DashboardSummary | undefined) {
  const rows = (summary?.by_capacity ?? [])
    .filter((e) => e.capacity_ton !== null)
    .map((e) => ({ capacity: `${e.capacity_ton} Ton`, count: e.count }));
  return rows.sort((a, b) => {
    const tonA = parseFloat(a.capacity.replace(/[^0-9.]/g, "")) || 0;
    const tonB = parseFloat(b.capacity.replace(/[^0-9.]/g, "")) || 0;
    return tonA - tonB;
  });
}

export function brandDistributionFrom(summary: DashboardSummary | undefined) {
  const rows = (summary?.by_manufacturer ?? []).map((e) => ({ make: e.manufacturer, count: e.count }));
  return rows.sort((a, b) => b.count - a.count);
}

export function statisticsFrom(summary: DashboardSummary | undefined): ACStatistics | undefined {
  if (!summary) return undefined;
  const k = summary.kpis;
  return {
    total: k.total,
    working: k.working,
    faulty: k.faulty,
    underMaintenance: k.under_maint,
    inactive: k.offline,
    dueForMaintenance: k.due_soon,
    overdue: k.overdue,
    installedCapacity: k.capacity,
    departmentsCount: k.departments,
    locationsCount: k.rooms,
    dataCompletenessPct: k.completeness_pct,
  };
}
