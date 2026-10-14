/**
 * Energy estimation model for the Energy Management Dashboard.
 *
 * Data provenance (never fabricated):
 * - Runtime comes ONLY from the `ac_usage_sessions` ledger (measured
 *   start/stop timestamps; open sessions accrue up to `now`).
 * - Power per asset prefers `power_rating_kw` (rated electrical input);
 *   otherwise falls back to `capacity_ton × 1.2 kW` (the same connected-load
 *   rule of thumb `getConnectedPowerLoad` uses); otherwise the asset is
 *   EXCLUDED from kWh math and counted under missing inputs.
 * - Estimated energy = Σ power_kw × hours × average load factor. The load
 *   factor estimates average electrical input relative to rated input. It is
 *   an explicit, user-visible assumption — never a measurement, and NOT a
 *   compressor duty cycle (no validated relationship is claimed). Cost is
 *   NEVER computed: no tariff is configured anywhere in the system.
 * - Sessions cannot overlap per asset (partial unique index), so summing
 *   durations never double-counts runtime.
 * - Missing data is never treated as zero consumption: assets/sessions
 *   without usable power inputs are excluded and reported separately.
 */

/** Model version stamped on every estimate using this module. */
export const MODEL_VERSION_BASELINE = "baseline-v1";

export interface EnergySessionInput {
  id: string;
  ac_id: string;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  power_rating_kw?: number | null;
  capacity_ton?: number | null;
  manufacturer?: string | null;
  block?: string | null;
  department?: string | null;
  floor?: string | null;
  room?: string | null;
}

/** Explicit assumption, surfaced in the UI wherever estimates appear. */
export const DEFAULT_LOAD_FACTOR = 0.7;

/**
 * kW of estimated electrical input per ton of refrigeration — used ONLY for
 * the capacity-derived fallback when no rated power exists. 1 ton ≈ 3.517 kW
 * cooling, so 1.2 kW/ton implies EER ≈ 2.9 (typical non-inverter split unit
 * at full load). An OPERATIONAL PLACEHOLDER, not a manufacturer rating and
 * not calibrated. Kept in one place and mirrored by the backend's
 * `ENERGY_CAPACITY_KW_PER_TON` default so both tiers stay identical.
 */
export const CAPACITY_KW_PER_TON = 1.2;

/** Model versions, mirroring backend/app/services/energy_models.py. */
export const MODEL_VERSION_CAPACITY_DERIVED = "baseline-v1-capacity-derived";

/** kW for one asset, plus whether the figure is rated or capacity-derived. */
export function kwForAsset(input: {
  power_rating_kw?: number | null;
  capacity_ton?: number | null;
}): { kw: number | null; basis: "rated" | "capacity-derived" | "unknown"; modelVersion: string | null } {
  const rated =
    typeof input.power_rating_kw === "number" && Number.isFinite(input.power_rating_kw) && input.power_rating_kw > 0
      ? input.power_rating_kw
      : null;
  if (rated !== null) return { kw: rated, basis: "rated", modelVersion: MODEL_VERSION_BASELINE };
  const tons =
    typeof input.capacity_ton === "number" && Number.isFinite(input.capacity_ton) && input.capacity_ton > 0
      ? input.capacity_ton
      : null;
  if (tons !== null) {
    return {
      kw: tons * CAPACITY_KW_PER_TON,
      basis: "capacity-derived",
      modelVersion: MODEL_VERSION_CAPACITY_DERIVED,
    };
  }
  return { kw: null, basis: "unknown", modelVersion: null };
}

/** Hours for one session row (open sessions accrue to `now`). */
export function sessionHours(row: EnergySessionInput, nowMs: number): number {
  if (row.duration_seconds != null && Number.isFinite(row.duration_seconds) && row.duration_seconds >= 0) {
    return row.duration_seconds / 3600;
  }
  if (!row.started_at) return 0;
  const start = new Date(row.started_at).getTime();
  if (!Number.isFinite(start)) return 0;
  const end = row.ended_at ? new Date(row.ended_at).getTime() : nowMs;
  if (!Number.isFinite(end) || end <= start) return 0;
  return (end - start) / 3_600_000;
}

export interface SessionEnergy {
  sessionId: string;
  acId: string;
  hours: number;
  kw: number | null;
  kwh: number | null;
  /** Provenance of `kw`: rated nameplate vs capacity-derived vs unknown. */
  basis: "rated" | "capacity-derived" | "unknown";
  modelVersion: string | null;
  excludedReason: string | null;
}

/**
 * Per-session energy rows. Sessions without usable power are excluded
 * (never zeroed). A non-finite load factor would silently turn every kWh
 * into NaN, so it is clamped to the documented default instead.
 */
export function estimateSessions(
  sessions: EnergySessionInput[],
  loadFactor: number,
  nowMs: number,
): SessionEnergy[] {
  const lf = Number.isFinite(loadFactor) && loadFactor >= 0 && loadFactor <= 1 ? loadFactor : DEFAULT_LOAD_FACTOR;
  return sessions.map((s) => {
    const hours = sessionHours(s, nowMs);
    const { kw, basis, modelVersion } = kwForAsset(s);
    if (kw === null || hours <= 0) {
      return {
        sessionId: s.id,
        acId: s.ac_id,
        hours,
        kw,
        kwh: null,
        basis,
        modelVersion,
        excludedReason:
          hours <= 0 ? "no measurable runtime" : "no power rating or capacity on record",
      };
    }
    return {
      sessionId: s.id,
      acId: s.ac_id,
      hours,
      kw,
      kwh: kw * hours * lf,
      basis,
      modelVersion,
      excludedReason: null,
    };
  });
}

export interface EnergyTotals {
  sessionCount: number;
  openCount: number;
  runtimeHours: number;
  estimatedKwh: number;
  excludedSessions: number;
  excludedHours: number;
  assetsWithPower: number;
  /** Sessions whose input power came from capacity, not a nameplate rating. */
  capacityDerivedSessions: number;
  loadFactor: number;
}

/** Roll per-session rows up to dashboard totals. */
export function totalEnergy(rows: SessionEnergy[], openIds: Set<string>, loadFactor: number): EnergyTotals {
  let runtimeHours = 0;
  let estimatedKwh = 0;
  let excludedSessions = 0;
  let excludedHours = 0;
  let capacityDerivedSessions = 0;
  const assetsWithPower = new Set<string>();
  for (const r of rows) {
    runtimeHours += r.hours;
    if (r.kwh === null) {
      excludedSessions += 1;
      excludedHours += r.hours;
    } else {
      estimatedKwh += r.kwh;
      assetsWithPower.add(r.acId);
      if (r.basis === "capacity-derived") capacityDerivedSessions += 1;
    }
  }
  const openInRows = rows.filter((r) => openIds.has(r.sessionId)).length;
  return {
    sessionCount: rows.length,
    openCount: openInRows,
    runtimeHours,
    estimatedKwh,
    excludedSessions,
    excludedHours,
    assetsWithPower: assetsWithPower.size,
    capacityDerivedSessions,
    loadFactor,
  };
}

export interface DailyBucket {
  date: string;
  runtimeHours: number;
  estimatedKwh: number;
  sessions: number;
}

/**
 * Per-day buckets across [from, to] (inclusive, YYYY-MM-DD). Days without
 * sessions appear with zeros — zero-filled, never backfilled with estimates.
 * Multi-day sessions attribute their whole duration to the start day (stated
 * assumption; sessions are short operational windows, not meter streams).
 */
export function dailyBuckets(
  rows: SessionEnergy[],
  sessionsById: Map<string, EnergySessionInput>,
  from: string,
  to: string,
): DailyBucket[] {
  const days: DailyBucket[] = [];
  // UTC calendar days: local-midnight parsing shifts the date in positive
  // offsets, so bounds are constructed explicitly in UTC.
  const start = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || start > end) return [];
  for (let d = new Date(start); d <= end; d = new Date(d.getTime() + 86_400_000)) {
    days.push({ date: d.toISOString().slice(0, 10), runtimeHours: 0, estimatedKwh: 0, sessions: 0 });
  }
  const byDate = new Map(days.map((d) => [d.date, d]));
  for (const r of rows) {
    const s = sessionsById.get(r.sessionId);
    if (!s?.started_at) continue;
    const day = s.started_at.slice(0, 10);
    const bucket = byDate.get(day);
    if (!bucket) continue;
    bucket.runtimeHours += r.hours;
    bucket.estimatedKwh += r.kwh ?? 0;
    bucket.sessions += 1;
  }
  return days;
}

export interface GroupRow {
  key: string;
  units: number;
  onUnits: number;
  runtimeHours: number;
  estimatedKwh: number | null;
  sessionsWithPower: number;
  sessionsTotal: number;
}

/**
 * Comparative matrix over an arbitrary grouping key. `unitsByKey` comes from
 * the inventory side (dashboard summary groupings); session aggregates join
 * by the same label. Completeness = session-hours with usable power ÷ total
 * session-hours for the group (null when the group has no sessions at all).
 */
export function buildMatrix(
  unitsByKey: Map<string, number>,
  rows: SessionEnergy[],
  keyOf: (sessionId: string) => string | null,
  openSessionIds: Set<string>,
): GroupRow[] {
  const acc = new Map<string, { on: Set<string>; hours: number; kwh: number; withPower: number; total: number }>();
  for (const r of rows) {
    const key = keyOf(r.sessionId) ?? "Unknown";
    let entry = acc.get(key);
    if (!entry) {
      entry = { on: new Set<string>(), hours: 0, kwh: 0, withPower: 0, total: 0 };
      acc.set(key, entry);
    }
    entry.total += 1;
    entry.hours += r.hours;
    if (openSessionIds.has(r.sessionId)) entry.on.add(r.acId);
    if (r.kwh === null) continue;
    entry.kwh += r.kwh;
    entry.withPower += 1;
  }
  const out: GroupRow[] = [];
  for (const [key, units] of unitsByKey) {
    const entry = acc.get(key);
    out.push({
      key,
      units,
      onUnits: entry?.on.size ?? 0,
      runtimeHours: entry?.hours ?? 0,
      estimatedKwh: entry && entry.total > 0 ? entry.kwh : null,
      sessionsWithPower: entry?.withPower ?? 0,
      sessionsTotal: entry?.total ?? 0,
    });
  }
  // Session-only groups (labels the inventory side never emitted).
  for (const [key, entry] of acc) {
    if (!unitsByKey.has(key)) {
      out.push({
        key,
        units: 0,
        onUnits: 0,
        runtimeHours: entry.hours,
        estimatedKwh: entry.total > 0 ? entry.kwh : null,
        sessionsWithPower: entry.withPower,
        sessionsTotal: entry.total,
      });
    }
  }
  return out.sort((a, b) => b.runtimeHours - a.runtimeHours);
}

export interface SimulationResult {
  baselineKwh: number;
  simulatedKwh: number;
  avoidedKwh: number;
  reductionPct: number;
  loadFactor: number;
  excludedKwhShare: string | null;
}

/** What-if math on an estimated baseline. Pure arithmetic; labeled simulation. */
export function simulateSavings(
  baselineKwh: number,
  reductionPct: number,
  loadFactor: number,
  excludedHours: number,
  totalHours: number,
): SimulationResult {
  const clamped = Math.min(100, Math.max(0, reductionPct));
  const simulatedKwh = baselineKwh * (1 - clamped / 100);
  return {
    baselineKwh,
    simulatedKwh,
    avoidedKwh: baselineKwh - simulatedKwh,
    reductionPct: clamped,
    loadFactor,
    excludedKwhShare:
      totalHours > 0 ? `${((excludedHours / totalHours) * 100).toFixed(1)}% of runtime excluded (no power data)` : null,
  };
}

export function formatKwh(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value.toLocaleString("en-US", { maximumFractionDigits: 1 })} kWh`;
}

export function formatHours(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  if (value < 1) return `${Math.round(value * 60)} min`;
  return `${value.toLocaleString("en-US", { maximumFractionDigits: 1 })} h`;
}
