/**
 * Dynamic analytics explorer: two dimensions + one measure, with an
 * automatic chart recommendation (the user never picks blindly) and a
 * manual override where it makes sense. Queries run only on committed
 * selection changes, debounced for keyboard-driven selects.
 */
'use client';

import React, { useMemo, useState } from 'react';

import {
  useAnalytics,
  type AnalyticsDimension,
  type AnalyticsMeasure,
  type AnalyticsQuery,
} from '@/lib/api/analytics';
import {
  DIMENSION_LABELS,
  MEASURE_LABELS,
  applicableCharts,
  pivotSeries,
  recommendChart,
  type ExplorerChart,
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

const DIMENSIONS: AnalyticsDimension[] = [
  'company',
  'year',
  'department',
  'block',
  'floor',
  'type',
  'capacity',
  'status',
];

const MEASURES: AnalyticsMeasure[] = ['count', 'avg_age', 'total_capacity'];

const CHART_LABELS: Record<ExplorerChart, string> = {
  bar: 'Bars',
  hbar: 'Horizontal bars',
  line: 'Line',
  stacked: 'Stacked bars',
  grouped: 'Grouped bars',
  heatmap: 'Heatmap',
  donut: 'Donut',
};

const selectClass =
  'px-2.5 py-1.5 border border-slate-200 rounded-lg text-xs font-medium bg-white text-slate-700 focus:ring-2 focus:ring-blue-500 outline-none';

function labelOf(value: string | number | null): string {
  return value === null || value === undefined || value === '' ? 'Unknown' : String(value);
}

function formatMeasure(measure: AnalyticsMeasure, value: number | null): number {
  if (value == null) return 0;
  if (measure === 'avg_age') return Math.round(value * 10) / 10;
  if (measure === 'total_capacity') return Math.round(value * 100) / 100;
  return value;
}

export function AnalyticsExplorer({ baseQuery }: { baseQuery: AnalyticsQuery }) {
  const [dim1, setDim1] = useState<AnalyticsDimension>('company');
  const [dim2, setDim2] = useState<AnalyticsDimension | ''>('year');
  const [measure, setMeasure] = useState<AnalyticsMeasure>('count');
  const [chartMode, setChartMode] = useState<'auto' | ExplorerChart>('auto');

  const query: AnalyticsQuery = useMemo(
    () => ({
      ...baseQuery,
      dim1,
      ...(dim2 ? { dim2 } : {}),
      measure,
    }),
    [baseQuery, dim1, dim2, measure],
  );
  const explorerQuery = useAnalytics(query);
  const rows = explorerQuery.data?.explorer?.rows ?? [];
  const loading = explorerQuery.isPending;
  const error = explorerQuery.error ? (explorerQuery.error as Error).message : null;

  const stats = useMemo(() => {
    const d1 = new Set(rows.map((r) => labelOf(r.d1)));
    return { d1Buckets: d1.size, cellCount: rows.length };
  }, [rows]);

  const recommended = recommendChart({ dim1, dim2: dim2 || null, ...stats });
  const chart: ExplorerChart = chartMode === 'auto' ? recommended : chartMode;
  const options = applicableCharts(dim1, dim2 || null);

  const single: NameValue[] = useMemo(
    () => rows.map((r) => ({ name: labelOf(r.d1), value: formatMeasure(measure, r.value) })),
    [rows, measure],
  );
  const pivoted = useMemo(() => pivotSeries(rows), [rows]);
  const heat = useMemo(() => {
    const xs = [...new Set(rows.map((r) => labelOf(r.d2)))].sort();
    const ys = [...new Set(rows.map((r) => labelOf(r.d1)))].sort();
    const values = new Map(rows.map((r) => [`${labelOf(r.d1)}|${labelOf(r.d2)}`, r.value ?? 0]));
    return { xs, ys, values };
  }, [rows]);

  const subtitle = `${DIMENSION_LABELS[dim1]}${dim2 ? ` × ${DIMENSION_LABELS[dim2]}` : ''} · ${MEASURE_LABELS[measure]} · ${
    chartMode === 'auto' ? `recommended: ${CHART_LABELS[recommended]}` : CHART_LABELS[chart]
  }`;

  return (
    <AnalyticsCard
      title="Explore Data"
      subtitle={subtitle}
      loading={loading}
      isFetching={explorerQuery.isFetching}
      error={error}
      onRetry={() => void explorerQuery.refetch()}
      empty={!loading && !error && rows.length === 0}
      height={300}
    >
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <select value={dim1} onChange={(e) => setDim1(e.target.value as AnalyticsDimension)} className={selectClass} aria-label="First dimension">
          {DIMENSIONS.map((d) => (
            <option key={d} value={d}>
              {DIMENSION_LABELS[d]}
            </option>
          ))}
        </select>
        <select
          value={dim2}
          onChange={(e) => setDim2(e.target.value as AnalyticsDimension | '')}
          className={selectClass}
          aria-label="Second dimension"
        >
          <option value="">— single dimension —</option>
          {DIMENSIONS.filter((d) => d !== dim1).map((d) => (
            <option key={d} value={d}>
              {DIMENSION_LABELS[d]}
            </option>
          ))}
        </select>
        <select value={measure} onChange={(e) => setMeasure(e.target.value as AnalyticsMeasure)} className={selectClass} aria-label="Measure">
          {MEASURES.map((m) => (
            <option key={m} value={m}>
              {MEASURE_LABELS[m]}
            </option>
          ))}
        </select>
        <select
          value={chartMode}
          onChange={(e) => setChartMode(e.target.value as 'auto' | ExplorerChart)}
          className={selectClass}
          aria-label="Chart type"
        >
          <option value="auto">Auto ({CHART_LABELS[recommended]})</option>
          {options
            .filter((c) => !(chartMode === 'auto' && c === recommended))
            .map((c) => (
              <option key={c} value={c}>
                {CHART_LABELS[c]}
              </option>
            ))}
        </select>
      </div>
      {chart === 'bar' && <VBarChart data={single} height={260} />}
      {chart === 'hbar' && <HBarChart data={single} height={260} />}
      {chart === 'line' && <TrendChart data={single} height={260} />}
      {chart === 'donut' && <DonutChart data={single} height={260} />}
      {chart === 'stacked' && <SeriesBarChart data={pivoted.rows} series={pivoted.series} stacked height={260} />}
      {chart === 'grouped' && <SeriesBarChart data={pivoted.rows} series={pivoted.series} height={260} />}
      {chart === 'heatmap' && <HeatmapGrid xLabels={heat.xs} yLabels={heat.ys} values={heat.values} />}
    </AnalyticsCard>
  );
}
