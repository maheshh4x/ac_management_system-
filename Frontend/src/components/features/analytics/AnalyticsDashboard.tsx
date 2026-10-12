/**
 * Filter-driven inventory analytics dashboard (shared by the admin and
 * viewer dashboards): global filters in, one analytics request out, and a
 * compact set of decision-oriented sections — KPIs, company/department /
 * block concentration, installation trend, procurement over time, capacity,
 * type/status composition, fleet age, one heatmap, a dynamic explorer and a
 * short insights strip.
 *
 * Read-only by construction: no mutations, no edit affordances, no role
 * checks inside (page-level routing + backend VIEW_ASSET scoping decide who
 * may open it). Existing RBAC is untouched.
 */
'use client';

import React, { useMemo, useState } from 'react';

import type { ACFilters } from '@/lib/types/asset';
import { useAssetFacets } from '@/lib/api/assets';
import { toAssetQuery } from '@/hooks/useACDataset';
import { ACFilterBar, type BuildingOption } from '@/components/features/ACFilterBar';
import { useAnalytics, type AnalyticsQuery } from '@/lib/api/analytics';
import {
  agedCount,
  bucketAges,
  computeInsights,
  formatCount,
  formatTons,
  inPeriodCount,
  pivotSeries,
} from '@/lib/analytics-utils';
import {
  AnalyticsCard,
  DonutChart,
  HBarChart,
  HeatmapGrid,
  SeriesBarChart,
  TrendChart,
  VBarChart,
  type NameValue,
} from './AnalyticsCharts';
import { AnalyticsExplorer } from './AnalyticsExplorer';

interface AnalyticsDashboardProps {
  filters: ACFilters;
  /** Live (un-debounced) search text for the input; defaults to filters.search. */
  searchInput?: string;
  onFiltersChange: (patch: Partial<ACFilters>) => void;
  onResetFilters: () => void;
  /** Admin already shows ACStatCards: render only the extended KPIs there. */
  kpiMode: 'full' | 'extended';
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-2xs">
      <div className="text-[11px] font-bold uppercase tracking-wide text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-bold text-slate-900">{value}</div>
      {sub && <div className="text-[11px] text-slate-500 mt-0.5">{sub}</div>}
    </div>
  );
}

function KpiLoading() {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-2xs">
      <div className="h-3 w-20 rounded bg-slate-100 animate-pulse" />
      <div className="mt-2 h-7 w-16 rounded bg-slate-100 animate-pulse" />
    </div>
  );
}

const asNameValue = (rows: Array<{ [k: string]: string | number | null }>, nameKey: string): NameValue[] =>
  rows.map((r) => ({ name: String(r[nameKey] ?? 'Unknown'), value: Number(r.count ?? 0) }));

export function AnalyticsDashboard({ filters, searchInput, onFiltersChange, onResetFilters, kpiMode }: AnalyticsDashboardProps) {
  const [locationDim, setLocationDim] = useState<'department' | 'block'>('department');
  const [compositionDim, setCompositionDim] = useState<'type' | 'status'>('type');

  const query: AnalyticsQuery = useMemo(() => toAssetQuery(filters) as AnalyticsQuery, [filters]);
  const analytics = useAnalytics(query);
  const facets = useAssetFacets();

  const data = analytics.data;
  const loading = analytics.isPending;
  const isFetching = analytics.isFetching;
  const error = analytics.error ? (analytics.error as Error).message : null;
  const retry = () => void analytics.refetch();
  const currentYear = new Date().getFullYear();

  const yearRange = data?.year_range ?? { min: null, max: null };
  const yearFrom = filters.yearFrom ? Number(filters.yearFrom) : null;
  const yearTo = filters.yearTo ? Number(filters.yearTo) : null;

  const facetOptions = useMemo(() => {
    const blocks = facets.data?.block ?? [];
    const map = (entries: Array<{ value: string; id?: string | null }>): BuildingOption[] =>
      entries.map((e) => ({ id: e.id ?? e.value, name: e.value }));
    return {
      buildingList: map(blocks),
      collegeList: map(facets.data?.college ?? []),
      floorList: map(facets.data?.floor ?? []),
      departmentList: map(facets.data?.department ?? []),
      manufacturerList: (facets.data?.manufacturer ?? []).map((e) => e.value),
    };
  }, [facets.data]);

  // Company totals across all years (derived from the company×year matrix).
  const companyTotals = useMemo(() => {
    const totals = new Map<string, number>();
    for (const row of data?.company_by_year ?? []) {
      totals.set(row.company, (totals.get(row.company) ?? 0) + row.count);
    }
    return [...totals.entries()]
      .map(([name, value]) => ({ name, value }))
      .sort((a, b) => b.value - a.value);
  }, [data]);

  const locationRows: NameValue[] = useMemo(() => {
    if (locationDim === 'department') {
      return (data?.departments ?? []).map((r) => ({ name: r.department ?? 'Unknown', value: r.count }));
    }
    return (data?.blocks ?? []).map((r) => ({ name: r.block ?? 'Unknown', value: r.count }));
  }, [data, locationDim]);

  const installTrend: NameValue[] = useMemo(
    () => (data?.install_by_year ?? []).map((r) => ({ name: String(r.year), value: r.count })),
    [data],
  );

  const companyYear = useMemo(() => {
    const pivoted = pivotSeries(
      (data?.company_by_year ?? []).map((r) => ({ d1: r.company, d2: r.year, value: r.count })),
    );
    return pivoted;
  }, [data]);
  const companyCount = companyYear.series.length;

  const capacityRows: NameValue[] = useMemo(
    () => (data?.by_capacity ?? []).map((r) => ({ name: `${r.capacity_ton} T`, value: r.count })),
    [data],
  );

  const compositionRows: NameValue[] = useMemo(() => {
    if (compositionDim === 'type') {
      return (data?.by_type ?? []).map((r) => ({ name: r.type, value: r.count }));
    }
    const labels: Record<string, string> = {
      WORKING: 'Active',
      FAULT: 'Fault',
      MAINTENANCE: 'Maintenance',
      OFFLINE: 'Offline',
      UNKNOWN: 'Unknown',
    };
    return (data?.by_status ?? []).map((r) => ({ name: labels[r.status] ?? r.status, value: r.count }));
  }, [data, compositionDim]);

  const ageRows = useMemo(
    () => bucketAges(data?.install_by_year ?? [], currentYear),
    [data, currentYear],
  );

  const heat = useMemo(() => {
    const rows = data?.heatmap ?? [];
    const xs = [...new Set(rows.map((r) => r.year))].sort((a, b) => a - b);
    const totals = new Map<string, number>();
    for (const r of rows) {
      const key = r.department ?? 'Unknown';
      totals.set(key, (totals.get(key) ?? 0) + r.count);
    }
    const ys = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([name]) => name);
    const values = new Map(rows.map((r) => [`${r.department ?? 'Unknown'}|${r.year}`, r.count]));
    return { xs, ys, values };
  }, [data]);

  const aged = useMemo(
    () => agedCount(data?.install_by_year ?? [], currentYear, 10),
    [data, currentYear],
  );
  const inPeriod = useMemo(
    () => inPeriodCount(data?.install_by_year ?? [], yearFrom, yearTo),
    [data, yearFrom, yearTo],
  );

  const insights = useMemo(
    () =>
      data
        ? computeInsights({
            total: data.kpis.total,
            installByYear: data.install_by_year,
            companyByYear: data.company_by_year,
            departments: data.departments.map((d) => ({ department: d.department, count: d.count })),
            currentYear,
          })
        : [],
    [data, currentYear],
  );

  const kpis = data?.kpis;
  const showInitialLoaders = loading && !data;

  return (
    <div className="flex flex-col gap-6">
      <ACFilterBar
        filters={{ ...filters, search: searchInput ?? filters.search ?? '' }}
        onChange={(next) => onFiltersChange(next)}
        onReset={onResetFilters}
        buildingList={facetOptions.buildingList}
        collegeList={facetOptions.collegeList}
        floorList={facetOptions.floorList}
        departmentList={facetOptions.departmentList}
        manufacturerList={facetOptions.manufacturerList}
        capacityList={data?.capacities ?? []}
        yearRange={yearRange}
      />

      {/* Executive overview */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        {showInitialLoaders || !kpis ? (
          <>
            <KpiLoading />
            <KpiLoading />
            <KpiLoading />
            <KpiLoading />
            <KpiLoading />
          </>
        ) : kpiMode === 'full' ? (
          <>
            <Kpi label="Total ACs" value={formatCount(kpis.total)} sub="Filtered inventory" />
            <Kpi label="Active / Working" value={formatCount(kpis.working)} sub="Operational now" />
            <Kpi
              label="Offline / Maintenance"
              value={formatCount(kpis.offline_maintenance)}
              sub="Needs attention"
            />
            <Kpi label="10+ Year ACs" value={formatCount(aged)} sub="Monitor for review" />
            <Kpi
              label="Installed in Period"
              value={formatCount(inPeriod)}
              sub={yearFrom || yearTo ? `${yearFrom ?? '…'}–${yearTo ?? '…'}` : 'All years'}
            />
          </>
        ) : (
          <>
            <Kpi label="10+ Year ACs" value={formatCount(aged)} sub="Monitor for review" />
            <Kpi
              label="Installed in Period"
              value={formatCount(inPeriod)}
              sub={yearFrom || yearTo ? `${yearFrom ?? '…'}–${yearTo ?? '…'}` : 'All years'}
            />
            <Kpi label="Total Capacity" value={formatTons(kpis.total_capacity)} sub="Installed tonnage" />
          </>
        )}
      </div>

      {/* Core visualizations */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <AnalyticsCard
          title="ACs by Company"
          subtitle="Inventory concentration across makers"
          loading={showInitialLoaders}
          isFetching={isFetching}
          error={error}
          onRetry={retry}
          empty={!showInitialLoaders && !error && companyTotals.length === 0}
        >
          <HBarChart data={companyTotals} />
        </AnalyticsCard>

        <AnalyticsCard
          title={locationDim === 'department' ? 'ACs by Department' : 'ACs by Block'}
          subtitle="Where the inventory sits"
          loading={showInitialLoaders}
          isFetching={isFetching}
          error={error}
          onRetry={retry}
          empty={!showInitialLoaders && !error && locationRows.length === 0}
        >
          <div className="flex gap-1 mb-2">
            {(['department', 'block'] as const).map((dim) => (
              <button
                key={dim}
                onClick={() => setLocationDim(dim)}
                className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition-all ${
                  locationDim === dim
                    ? 'bg-slate-900 text-white border-slate-900'
                    : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300'
                }`}
              >
                {dim === 'department' ? 'Department' : 'Block'}
              </button>
            ))}
          </div>
          <HBarChart data={locationRows} height={228} />
        </AnalyticsCard>

        <AnalyticsCard
          title="Installation Trend"
          subtitle="Installations across years (follows the year range)"
          loading={showInitialLoaders}
          isFetching={isFetching}
          error={error}
          onRetry={retry}
          empty={!showInitialLoaders && !error && installTrend.length === 0}
        >
          <TrendChart data={installTrend} />
        </AnalyticsCard>

        <AnalyticsCard
          title="Company × Year"
          subtitle={companyCount <= 6 ? 'Procurement mix per year' : 'Dense matrix rendered as a heatmap'}
          loading={showInitialLoaders}
          isFetching={isFetching}
          error={error}
          onRetry={retry}
          empty={!showInitialLoaders && !error && companyYear.rows.length === 0}
        >
          {companyCount <= 6 ? (
            <SeriesBarChart data={companyYear.rows} series={companyYear.series} stacked />
          ) : (
            <HeatmapGrid
              xLabels={(data?.install_by_year ?? []).map((r) => r.year)}
              yLabels={companyYear.series}
              values={
                new Map(
                  (data?.company_by_year ?? []).map((r) => [`${r.company}|${r.year}`, r.count]),
                )
              }
            />
          )}
        </AnalyticsCard>

        <AnalyticsCard
          title="Capacity Distribution"
          subtitle="Installed tonnage buckets"
          loading={showInitialLoaders}
          isFetching={isFetching}
          error={error}
          onRetry={retry}
          empty={!showInitialLoaders && !error && capacityRows.length === 0}
        >
          <VBarChart data={capacityRows} />
        </AnalyticsCard>

        <AnalyticsCard
          title={compositionDim === 'type' ? 'AC Type Mix' : 'Status Mix'}
          subtitle="Composition of the filtered inventory"
          loading={showInitialLoaders}
          isFetching={isFetching}
          error={error}
          onRetry={retry}
          empty={!showInitialLoaders && !error && compositionRows.length === 0}
        >
          <div className="flex gap-1 mb-2">
            {(['type', 'status'] as const).map((dim) => (
              <button
                key={dim}
                onClick={() => setCompositionDim(dim)}
                className={`px-2.5 py-1 rounded-full text-[11px] font-bold border transition-all ${
                  compositionDim === dim
                    ? 'bg-slate-900 text-white border-slate-900'
                    : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300'
                }`}
              >
                {dim === 'type' ? 'AC Type' : 'Status'}
              </button>
            ))}
          </div>
          {compositionRows.length <= 6 ? (
            <DonutChart data={compositionRows} height={228} />
          ) : (
            <VBarChart data={compositionRows} height={228} />
          )}
        </AnalyticsCard>

        <AnalyticsCard
          title="Fleet Age"
          subtitle="Age bands make replacement planning obvious"
          loading={showInitialLoaders}
          isFetching={isFetching}
          error={error}
          onRetry={retry}
          empty={!showInitialLoaders && !error && ageRows.every((r) => r.value === 0)}
        >
          <VBarChart data={ageRows} />
        </AnalyticsCard>

        <AnalyticsCard
          title="Department × Year"
          subtitle="Where installations concentrate across time"
          loading={showInitialLoaders}
          isFetching={isFetching}
          error={error}
          onRetry={retry}
          empty={!showInitialLoaders && !error && heat.ys.length === 0}
        >
          <HeatmapGrid xLabels={heat.xs} yLabels={heat.ys} values={heat.values} />
        </AnalyticsCard>
      </div>

      <AnalyticsExplorer baseQuery={query} />

      {/* Decision-oriented insights (data-backed, neutral wording) */}
      <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-2xs">
        <h3 className="text-sm font-bold text-slate-900">Insights</h3>
        <p className="text-xs text-slate-500 mb-3">Computed from the filtered inventory — monitor, don&apos;t act on age alone.</p>
        {showInitialLoaders ? (
          <div className="space-y-2">
            <div className="h-4 rounded bg-slate-100 animate-pulse" />
            <div className="h-4 rounded bg-slate-100 animate-pulse" />
          </div>
        ) : error ? (
          <p className="text-xs text-red-600">Insights unavailable: {error}</p>
        ) : insights.length === 0 ? (
          <p className="text-xs text-slate-400">No insights for the current filters — widen them to see more.</p>
        ) : (
          <ul className="space-y-2">
            {insights.map((text) => (
              <li key={text} className="flex items-start gap-2 text-xs text-slate-700">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-blue-600" />
                {text}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
