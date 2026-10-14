'use client';

/**
 * Energy Management Dashboard view (shared by all four portal routes).
 *
 * Read-only for every role: all data comes from GET endpoints the caller may
 * already use (dashboard summary, fleet sessions, facets); the only writes
 * on this screen are CSV downloads. Backend role scoping (mine/blocks/all)
 * applies automatically — the `scope` badge shows which slice is displayed.
 *
 * Honesty rules enforced here, not just documented:
 * - kWh figures are always labeled estimated with the duty-cycle assumption;
 * - cost is never shown (no tariff exists);
 * - weather/schedules/metered-kWh sections render explicit unavailable
 *   states instead of fabricated values;
 * - truncated session lists (over the fetch cap) are disclosed, not hidden.
 */
import React, { useMemo, useState } from 'react';
import Link from 'next/link';
import { CloudOff, Download, Info } from 'lucide-react';

import { useAuth } from '@/contexts/AuthContext';
import { roleAssetDetail } from '@/lib/permissions';
import { useAssetFacets, useDashboardSummary, type AssetQuery } from '@/lib/api/assets';
import { useAnalytics } from '@/lib/api/analytics';
import { useFleetSessions, useEnergySummary, useWeatherNow } from '@/lib/api/energy';
import {
  buildMatrix,
  dailyBuckets,
  DEFAULT_LOAD_FACTOR,
  estimateSessions,
  totalEnergy,
  type DailyBucket,
  type EnergySessionInput,
} from '@/lib/energy-calculations';
import { formatDuration } from '@/lib/api/sessions';
import { buildEnergyCsv, downloadCsv } from '@/lib/energy-export';
import {
  EMPTY_ENERGY_FILTERS,
  EnergyFilters,
  resolvePeriod,
  type EnergyFilters as EnergyFilterState,
} from '@/components/features/energy/EnergyFilters';
import { EnergyKpiCards } from '@/components/features/energy/EnergyKpiCards';
import { DailyTrendChart, GroupBarChart, OnOffPieChart } from '@/components/features/energy/EnergyCharts';
import { EnergyMatrix } from '@/components/features/energy/EnergyMatrix';
import { EnergySimulator } from '@/components/features/energy/EnergySimulator';
import { buildRecommendations, EnergyRecommendations } from '@/components/features/energy/EnergyRecommendations';

const SESSION_FETCH_CAP = 1000;

function toAssetQuery(filters: EnergyFilterState): AssetQuery {
  return {
    block: filters.block || undefined,
    department: filters.department || undefined,
    floor: filters.floor || undefined,
    room: filters.room || undefined,
    manufacturer: filters.manufacturer || undefined,
    capacityTon: filters.capacityTon || undefined,
  };
}

export function EnergyDashboardView() {
  const { user } = useAuth();
  const [filters, setFilters] = useState<EnergyFilterState>(EMPTY_ENERGY_FILTERS);
  const [loadFactor] = useState(DEFAULT_LOAD_FACTOR);
  // Fixed once per mount: open-session accrual and recommendation ages are
  // computed against this instant (a ticking clock would re-render charts
  // every second and break memo purity rules).
  const [nowMs] = useState(() => Date.now());
  const { from, to } = useMemo(() => resolvePeriod(filters), [filters]);
  const assetQuery = useMemo(() => toAssetQuery(filters), [filters]);

  const facets = useAssetFacets();
  const summary = useDashboardSummary(assetQuery);
  const analytics = useAnalytics(assetQuery);
  const sessions = useFleetSessions(
    { ...assetQuery, from, to, limit: SESSION_FETCH_CAP },
    Boolean(from && to),
  );
  // Server-computed aggregates (single source of truth for daily/campus
  // figures — the backend splits sessions at campus-local midnight).
  const energySummary = useEnergySummary(
    { ...assetQuery, from, to, loadFactor },
    Boolean(from && to),
  );
  // Campus weather card — never load-bearing for kWh; a failure shows an
  // explicit unavailable state, and freshness is always displayed.
  const weather = useWeatherNow();

  const loading = summary.isPending || sessions.isPending;
  const refreshing = summary.isFetching || sessions.isFetching || energySummary.isFetching;
  const queryError =
    (summary.error as Error | null)?.message ??
    (sessions.error as Error | null)?.message ??
    (energySummary.error as Error | null)?.message ??
    (facets.error as Error | null)?.message ??
    (analytics.error as Error | null)?.message ??
    null;
  const refetchAll = () => {
    void summary.refetch();
    void sessions.refetch();
    void analytics.refetch();
    void facets.refetch();
    void energySummary.refetch();
    void weather.refetch();
  };

  const computed = useMemo(() => {
    const rows = (sessions.data?.sessions ?? []) as EnergySessionInput[];
    const estimated = estimateSessions(rows, loadFactor, nowMs);
    const openIds = new Set(rows.filter((s) => !s.ended_at).map((s) => s.id));
    const totals = totalEnergy(estimated, openIds, loadFactor);
    const byId = new Map(rows.map((s) => [s.id, s]));
    // Daily series: prefer the backend's campus-midnight-split buckets; the
    // client-side split (UTC calendar days) is the fallback when the summary
    // endpoint is unavailable, so the chart never renders empty by accident.
    const serverDaily = energySummary.data?.daily;
    const daily: DailyBucket[] =
      serverDaily && serverDaily.length > 0
        ? serverDaily.map((d) => ({
            date: d.date,
            runtimeHours: d.runtime_hours,
            estimatedKwh: d.baseline_kwh,
            sessions: d.sessions,
          }))
        : dailyBuckets(estimated, byId, from, to);

    const totalUnits = summary.data?.kpis.total ?? 0;
    const assetsWithSessions = new Set(rows.map((s) => s.ac_id));
    const assetsOpen = new Set(
      rows.filter((s) => !s.ended_at).map((s) => s.ac_id),
    );
    const offUnits = [...assetsWithSessions].filter((id) => !assetsOpen.has(id)).length;
    const unknownUnits = Math.max(0, totalUnits - assetsWithSessions.size);
    const incompleteAssets = new Set(
      estimated.filter((r) => r.kwh === null).map((r) => r.acId),
    );

    const labelOf = (id: string, field: 'block' | 'department' | 'manufacturer') =>
      byId.get(id)?.[field] ?? null;
    const unitsByBlock = new Map((summary.data?.by_block ?? []).map((e) => [e.block, e.count]));
    const unitsByDept = new Map(
      (analytics.data?.departments ?? []).map((e) => [e.department ?? 'Unknown', e.count]),
    );
    const unitsByMake = new Map((summary.data?.by_manufacturer ?? []).map((e) => [e.manufacturer, e.count]));
    const byBlock = buildMatrix(unitsByBlock, estimated, (id) => labelOf(id, 'block'), openIds);
    const byDepartment = buildMatrix(unitsByDept, estimated, (id) => labelOf(id, 'department'), openIds);
    const byManufacturer = buildMatrix(unitsByMake, estimated, (id) => labelOf(id, 'manufacturer'), openIds);

    const openRows = rows
      .filter((s) => !s.ended_at)
      .sort((a, b) => String(a.started_at ?? '').localeCompare(String(b.started_at ?? '')));

    return {
      rows,
      estimated,
      totals,
      daily,
      totalUnits,
      onUnits: assetsOpen.size,
      offUnits,
      unknownUnits,
      incompleteAssets: incompleteAssets.size,
      byBlock,
      byDepartment,
      byManufacturer,
      openRows,
      byId,
    };
  }, [sessions.data, summary.data, analytics.data, energySummary.data, loadFactor, from, to, nowMs]);

  const recommendations = useMemo(
    () =>
      buildRecommendations({
        openSessions: computed.openRows.map((s) => ({
          id: s.id,
          ac_id: s.ac_id,
          started_at: s.started_at,
          block: s.block ?? null,
        })),
        unknownStatusCount: computed.unknownUnits,
        excludedSessions: computed.totals.excludedSessions,
        totalSessions: computed.totals.sessionCount,
        nowMs,
      }),
    [computed, nowMs],
  );

  const exportMatrix = (dimension: 'block' | 'department' | 'manufacturer') => {
    const matrix = dimension === 'block' ? computed.byBlock : dimension === 'department' ? computed.byDepartment : computed.byManufacturer;
    const csv = buildEnergyCsv(
      ['group', 'units', 'on_units', 'runtime_hours', 'estimated_kwh', 'sessions_with_power', 'sessions_total'],
      matrix.map((r) => [
        r.key,
        r.units,
        r.onUnits,
        r.runtimeHours.toFixed(2),
        r.estimatedKwh === null ? '' : r.estimatedKwh.toFixed(2),
        r.sessionsWithPower,
        r.sessionsTotal,
      ]),
    );
    downloadCsv(`energy-matrix-${dimension}-${from}-to-${to}.csv`, csv);
  };

  const exportSessions = () => {
    const csv = buildEnergyCsv(
      ['session_id', 'ac_id', 'started_at', 'ended_at', 'duration_hours', 'block', 'department', 'manufacturer', 'estimated_kwh'],
      computed.estimated.map((r) => {
        const s = computed.byId.get(r.sessionId);
        return [
          r.sessionId,
          r.acId,
          s?.started_at ?? '',
          s?.ended_at ?? '',
          r.hours.toFixed(2),
          s?.block ?? '',
          s?.department ?? '',
          s?.manufacturer ?? '',
          r.kwh === null ? '' : r.kwh.toFixed(2),
        ];
      }),
    );
    downloadCsv(`energy-sessions-${from}-to-${to}.csv`, csv);
  };

  const truncated = (sessions.data?.total ?? 0) > computed.rows.length;
  const scope = sessions.data?.scope ?? summary.data?.scope ?? analytics.data?.scope ?? 'all';

  return (
    <div className="flex flex-col gap-6 pb-8" data-testid="energy-dashboard">
      <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Energy Management Dashboard</h1>
          <p className="mt-0.5 text-sm text-slate-500">
            Runtime, estimated consumption and operational monitoring from the recorded usage-session
            ledger — estimates labeled, gaps disclosed.
            <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-bold text-slate-500">
              Scope: {scope}
            </span>
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={exportSessions}
            className="inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
          >
            <Download size={14} /> Sessions CSV
          </button>
          <button
            type="button"
            onClick={() => exportMatrix('block')}
            className="inline-flex min-h-10 items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 text-xs font-bold text-slate-700 shadow-sm transition-colors hover:bg-slate-50"
          >
            <Download size={14} /> Matrix CSV
          </button>
        </div>
      </div>

      <EnergyFilters
        filters={filters}
        onChange={setFilters}
        facets={facets.data}
        onRefresh={refetchAll}
        refreshing={refreshing}
      />

      {queryError && (
        <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm font-medium text-red-700">
          Couldn&apos;t load energy data: {queryError}{' '}
          <button type="button" onClick={refetchAll} className="ml-2 font-bold underline">
            Retry
          </button>
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5" aria-label="Loading">
          {Array.from({ length: 9 }).map((_, i) => (
            <div key={i} className="h-28 animate-pulse rounded-2xl border border-slate-200 bg-slate-50" />
          ))}
        </div>
      ) : (
        <EnergyKpiCards
          kpis={{
            totalUnits: computed.totalUnits,
            onUnits: computed.onUnits,
            offUnits: computed.offUnits,
            unknownUnits: computed.unknownUnits,
            runtimeHours: computed.totals.runtimeHours,
            estimatedKwh: computed.totals.sessionCount > 0 ? computed.totals.estimatedKwh : null,
            eligibleUnits: computed.totals.assetsWithPower,
            incompleteUnits: computed.incompleteAssets,
            excludedSessions: computed.totals.excludedSessions,
          }}
        />
      )}

      {truncated && (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
          Showing the {computed.rows.length} most recent of {sessions.data?.total} sessions in range —
          totals below cover the shown rows only. Narrow the date range for complete coverage.
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <DailyTrendChart
          data={computed.daily}
          loading={loading}
          error={queryError}
          onRetry={refetchAll}
        />
        <OnOffPieChart
          onUnits={computed.onUnits}
          offUnits={computed.offUnits}
          unknownUnits={computed.unknownUnits}
          loading={loading}
          error={queryError}
          onRetry={refetchAll}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <GroupBarChart
          title="Estimated consumption by building (kWh)"
          data={computed.byBlock.map((r) => ({ name: r.key, kwh: r.estimatedKwh ?? 0 }))}
          dataKey="kwh"
          nameKey="name"
          loading={loading}
          error={queryError}
          onRetry={refetchAll}
          emptyText="No estimated consumption in range — record sessions with power data first."
        />
        <GroupBarChart
          title="Estimated consumption by make (kWh)"
          data={computed.byManufacturer.map((r) => ({ name: r.key, kwh: r.estimatedKwh ?? 0 }))}
          dataKey="kwh"
          nameKey="name"
          loading={loading}
          error={queryError}
          onRetry={refetchAll}
          emptyText="No estimated consumption in range."
        />
      </div>

      <EnergyMatrix
        byBlock={computed.byBlock}
        byDepartment={computed.byDepartment}
        byManufacturer={computed.byManufacturer}
      />

      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <h3 className="mb-1 text-sm font-bold text-slate-900">Currently ON units</h3>
        <p className="mb-4 text-xs text-slate-500">
          Open usage sessions right now — elapsed time accrues until someone stops them.
        </p>
        {loading ? (
          <div className="h-20 animate-pulse rounded-xl bg-slate-50" />
        ) : computed.openRows.length === 0 ? (
          <p className="rounded-xl border border-dashed border-slate-200 bg-slate-50/50 p-6 text-center text-sm text-slate-500">
            No open sessions. Units whose sessions were never started — or whose history predates
            session recording — show as unknown, never as OFF.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {computed.openRows.slice(0, 10).map((s) => (
              <li key={s.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
                <Link
                  href={roleAssetDetail(user?.role ?? null, s.ac_id)}
                  className="font-bold text-blue-700 hover:underline"
                >
                  {s.ac_id}
                </Link>
                <span className="text-xs text-slate-500">
                  {[s.block, s.room].filter(Boolean).join(' / ') || 'Unlocated'}
                </span>
                <span className="font-semibold tabular-nums text-emerald-700">
                  ON · {formatDuration(s.duration_seconds ?? elapsedSince(s.started_at))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <EnergySimulator
        baselineKwh={computed.totals.estimatedKwh}
        baselineLoadFactor={loadFactor}
        excludedHours={computed.totals.excludedHours}
        totalHours={computed.totals.runtimeHours}
      />

      <EnergyRecommendations items={recommendations} />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <div className="flex items-center gap-3">
            <span className="rounded-xl bg-slate-100 p-2.5 text-slate-400">
              <CloudOff size={20} />
            </span>
            <div>
              <h3 className="text-sm font-bold text-slate-900">Weather context</h3>
              <p className="text-xs text-slate-500">Outdoor conditions at observation time</p>
            </div>
          </div>
          {(() => {
            const wx = weather.data;
            const wxError = (weather.error as Error | null)?.message ?? null;
            if (weather.isPending) {
              return (
                <p className="mt-4 rounded-xl border border-dashed border-slate-200 bg-slate-50/50 p-6 text-center text-sm text-slate-500">
                  Loading campus weather…
                </p>
              );
            }
            if (wxError) {
              return (
                <p className="mt-4 rounded-xl border border-dashed border-amber-200 bg-amber-50 p-4 text-sm text-amber-800">
                  Weather unavailable ({wxError}). Energy estimates are unaffected — the
                  baseline never depends on weather.
                </p>
              );
            }
            if (!wx || !wx.available) {
              return (
                <p className="mt-4 rounded-xl border border-dashed border-slate-200 bg-slate-50/50 p-6 text-center text-sm text-slate-500">
                  No weather data available{wx?.reason ? ` (${wx.reason})` : ''}. Weather is
                  never inferred or treated as a cause of consumption changes here.
                </p>
              );
            }
            const stale = wx.stale === true;
            const observed = wx.observed_at ? new Date(wx.observed_at) : null;
            const observedLabel =
              observed && !Number.isNaN(observed.getTime())
                ? observed.toLocaleString()
                : 'unknown time';
            return (
              <div className="mt-4 space-y-3 text-sm text-slate-700">
                {stale && (
                  <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                    Showing the last successful reading — the live provider is unreachable,
                    so this value may be out of date.
                  </p>
                )}
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-xl bg-slate-50 p-3.5">
                    <div className="text-lg font-bold text-slate-800">
                      {wx.temp_c != null ? `${wx.temp_c.toFixed(1)} °C` : '—'}
                    </div>
                    <div className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                      Outdoor temp
                    </div>
                  </div>
                  <div className="rounded-xl bg-slate-50 p-3.5">
                    <div className="text-lg font-bold text-slate-800">
                      {wx.humidity_pct != null ? `${Math.round(wx.humidity_pct)} %` : '—'}
                    </div>
                    <div className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                      Humidity
                    </div>
                  </div>
                </div>
                {wx.demand_band && (
                  <p className="rounded-xl bg-slate-50 p-3 text-xs text-slate-600">
                    Cooling demand: <span className="font-bold">{wx.demand_band}</span>
                    {wx.demand_note ? ` — ${wx.demand_note}` : ''}. This indicator never
                    changes any kWh figure on this page.
                  </p>
                )}
                <p className="text-xs text-slate-500">
                  Observed {observedLabel} · {wx.source}
                  {wx.fetched_at
                    ? ` · fetched ${new Date(wx.fetched_at).toLocaleTimeString()}`
                    : ''}
                </p>
              </div>
            );
          })()}
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
          <div className="flex items-center gap-3">
            <span className="rounded-xl bg-slate-100 p-2.5 text-slate-400">
              <Info size={20} />
            </span>
            <div>
              <h3 className="text-sm font-bold text-slate-900">Schedules & staleness</h3>
              <p className="text-xs text-slate-500">How to read gaps in this dashboard</p>
            </div>
          </div>
          <ul className="mt-4 space-y-2 text-sm text-slate-600">
            <li>• No operating schedules are configured, so no unit is ever labeled “outside schedule”.</li>
            <li>• Units without session history show as unknown — not OFF, not zero consumption.</li>
            <li>• Runtime before usage-session recording began does not exist in this ledger.</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

function elapsedSince(startedAt: string | null | undefined): number | null {
  if (!startedAt) return null;
  const ms = Date.now() - new Date(startedAt).getTime();
  return Number.isFinite(ms) && ms >= 0 ? Math.floor(ms / 1000) : null;
}
