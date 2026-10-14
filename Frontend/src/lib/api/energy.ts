/**
 * Fleet-wide usage sessions for the Energy Management Dashboard.
 *
 * Server aggregates nothing here beyond row retrieval: GET /api/v1/sessions
 * (VIEW_ASSET) returns session rows with asset labels inside the caller's
 * role scope (technician mine-blocks, manager assigned-blocks — mirrored
 * from dashboard_summary). All kWh/runtime math happens client-side in
 * lib/energy-calculations.ts against the documented estimation model.
 */
import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { AssetQuery } from "./assets";

export interface FleetSession {
  id: string;
  ac_id: string;
  started_by: string | null;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  start_note: string | null;
  end_note: string | null;
  asset_code?: string | null;
  manufacturer?: string | null;
  model?: string | null;
  capacity_ton?: number | null;
  ac_type?: string | null;
  power_rating_kw?: number | null;
  block?: string | null;
  floor?: string | null;
  room?: string | null;
  department?: string | null;
}

export interface FleetSessionsResponse {
  sessions: FleetSession[];
  total: number;
  open_count: number;
  scope: string;
}

export interface EnergySessionQuery extends AssetQuery {
  state?: "open" | "closed" | "all";
  from?: string;
  to?: string;
  limit?: number;
}

export const energyKeys = {
  all: ["energy-sessions"] as const,
  fleet: (q: EnergySessionQuery) => ["energy-sessions", "fleet", q] as const,
};

function toSessionParams(q: EnergySessionQuery): string {
  const params = new URLSearchParams();
  if (q.state) params.set("state", q.state);
  if (q.from) params.set("from", q.from);
  if (q.to) params.set("to", q.to);
  if (q.block) params.set("block", q.block);
  if (q.college) params.set("college", q.college);
  if (q.department) params.set("department", q.department);
  if (q.manufacturer) params.set("manufacturer", q.manufacturer);
  if (q.floor) params.set("floor", q.floor);
  if (q.room) params.set("room", q.room);
  if (q.capacityTon) params.set("capacity_ton", q.capacityTon);
  if (q.limit) params.set("limit", String(q.limit));
  const s = params.toString();
  return s ? `?${s}` : "";
}

export function useFleetSessions(query: EnergySessionQuery, enabled = true) {
  return useQuery({
    queryKey: energyKeys.fleet(query),
    queryFn: () => api<FleetSessionsResponse>(`/api/v1/sessions${toSessionParams(query)}`),
    enabled,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}

export function useOpenSessions(query: EnergySessionQuery, enabled = true) {
  return useFleetSessions({ ...query, state: "open", limit: 500 }, enabled);
}

// ── GET /api/v1/energy/summary (server-computed aggregates) ──────────────────

export interface EnergySummaryGroup {
  key: string;
  units: number | null;
  runtime_hours: number;
  baseline_kwh: number;
  sessions: number;
  assets: number;
}

export interface EnergySummaryResponse {
  range: { from: string; to: string };
  scope: string;
  model: {
    version: string;
    load_factor: number;
    assumptions: string[];
    power_basis_counts?: { rated?: number; "capacity-derived"?: number };
    derived_model_version?: string;
  };
  totals: {
    runtime_hours: number;
    baseline_kwh: number;
    upper_bound_kwh: number;
    sessions: number;
    open_sessions: number;
    assets: number;
    excluded: number;
  };
  daily: { date: string; runtime_hours: number; baseline_kwh: number; sessions: number }[];
  groups: { room: EnergySummaryGroup[]; department: EnergySummaryGroup[]; building: EnergySummaryGroup[] };
  campus: { runtime_hours: number; baseline_kwh: number };
  overlaps: { ac_id: string; session_a: string; session_b: string; overlap_hours: number }[];
  cost: null | { amount: number; currency: string; tariff_per_kwh: number; basis: string };
  cost_unavailable_reason: string | null;
  weather: {
    available: boolean;
    source: string;
    mean_temp_c?: number | null;
    covered_hours?: number;
    demand_band?: string | null;
    demand_note?: string | null;
    reason?: string;
  };
  calculated_at: string;
}

export interface EnergySummaryQuery extends AssetQuery {
  from?: string;
  to?: string;
  loadFactor?: number;
  limit?: number;
}

export const energySummaryKeys = {
  all: ["energy-summary"] as const,
  detail: (q: EnergySummaryQuery) => ["energy-summary", q] as const,
};

export function useEnergySummary(query: EnergySummaryQuery, enabled = true) {
  return useQuery({
    queryKey: energySummaryKeys.detail(query),
    queryFn: () => {
      const params = new URLSearchParams();
      if (query.from) params.set("from", query.from);
      if (query.to) params.set("to", query.to);
      if (query.loadFactor != null) params.set("load_factor", String(query.loadFactor));
      if (query.block) params.set("block", query.block);
      if (query.department) params.set("department", query.department);
      if (query.manufacturer) params.set("manufacturer", query.manufacturer);
      if (query.floor) params.set("floor", query.floor);
      if (query.room) params.set("room", query.room);
      if (query.limit) params.set("limit", String(query.limit));
      const s = params.toString();
      return api<EnergySummaryResponse>(`/api/v1/energy/summary${s ? `?${s}` : ""}`);
    },
    enabled,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}

// ── GET /api/v1/weather/now (campus conditions with freshness) ───────────────

export interface WeatherNow {
  available: boolean;
  source: string;
  stale?: boolean;
  observed_at?: string;
  fetched_at?: string;
  temp_c?: number | null;
  humidity_pct?: number | null;
  dew_point_c?: number | null;
  apparent_c?: number | null;
  rain_mm?: number | null;
  demand_band?: string | null;
  demand_note?: string | null;
  reason?: string;
}

export const weatherKeys = {
  all: ["weather-now"] as const,
};

export function useWeatherNow(enabled = true) {
  return useQuery({
    queryKey: weatherKeys.all,
    queryFn: () => api<WeatherNow>("/api/v1/weather/now"),
    enabled,
    staleTime: 300_000,
    refetchInterval: 600_000,
  });
}

// ── GET /api/v1/assets/{ref}/energy (per-AC estimate) ────────────────────────

export interface AssetEnergySession {
  id: string;
  started_at: string;
  ended_at: string | null;
  status: "open" | "closed";
  runtime_hours: number;
  baseline_kwh: number | null;
  upper_bound_kwh: number | null;
  power_kw: number | null;
  power_basis: "rated" | "capacity-derived" | "unknown";
  excluded_reason: string | null;
  weather: {
    aligned: boolean;
    mean_temp_c: number | null;
    mean_humidity_pct: number | null;
    missing_intervals: string[];
    demand_band: string | null;
    demand_note: string | null;
    forecast_hours: number;
  };
}

export interface AssetEnergyResponse {
  ac_id: string;
  asset_uuid: string | null;
  asset_code: string | null;
  specs: {
    make: string | null;
    model: string | null;
    type: string | null;
    serial: string | null;
    power_rating_kw: number | null;
    capacity_ton: number | null;
    star_rating: number | null;
    status: string | null;
    lifecycle_status: string | null;
    room: string | null;
    floor: string | null;
    block: string | null;
    department: string | null;
    college: string | null;
    installation_year: number | null;
    installation_date: string | null;
  };
  range: { from: string; to: string };
  model: { version: string | null; load_factor: number; power_basis: string; assumptions: string[] };
  sessions: AssetEnergySession[];
  totals: { runtime_hours: number; baseline_kwh: number; upper_bound_kwh: number };
  daily: { date: string; runtime_hours: number; baseline_kwh: number; sessions: number }[];
  overlaps: { session_a: string; session_b: string; overlap_hours: number }[];
  invalid_sessions: string[];
  cost: null | { amount: number; currency: string; tariff_per_kwh: number; basis: string };
  cost_unavailable_reason: string | null;
  quality: string;
  quality_reasons: string[];
  weather: { available: boolean; source: string; reason?: string };
  calculated_at: string;
}

export const assetEnergyKeys = {
  all: ["asset-energy"] as const,
  detail: (ref: string, from: string, to: string) => ["asset-energy", ref, from, to] as const,
};

export function useAssetEnergy(ref: string | null, range: { from: string; to: string }, enabled = true) {
  return useQuery({
    queryKey: assetEnergyKeys.detail(ref ?? "", range.from, range.to),
    queryFn: () =>
      api<AssetEnergyResponse>(
        `/api/v1/assets/${encodeURIComponent(ref as string)}/energy?from=${range.from}&to=${range.to}`,
      ),
    enabled: Boolean(ref) && enabled,
    staleTime: 30_000,
    placeholderData: keepPreviousData,
  });
}
