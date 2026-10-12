/**
 * Generic analytics chart primitives (recharts — already the dashboard
 * library; no new dependencies). Same visual language as the existing
 * dashboard charts: card container, loader/error/empty states, restrained
 * palette, useful tooltips. Heatmap is dependency-free divs.
 */
'use client';

import React from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AlertTriangle, BarChart3, RefreshCw } from 'lucide-react';

export const CHART_PALETTE = [
  '#2563eb',
  '#059669',
  '#d97706',
  '#7c3aed',
  '#dc2626',
  '#0891b2',
  '#4d7c0f',
  '#be185d',
  '#475569',
  '#ea580c',
];

export interface NameValue {
  name: string;
  value: number;
}

interface CardProps {
  title: string;
  subtitle?: string;
  loading?: boolean;
  isFetching?: boolean;
  error?: string | null;
  onRetry?: () => void;
  empty?: boolean;
  emptyHint?: string;
  height?: number;
  children: React.ReactNode;
}

/** Card chrome shared by every analytics visualization. */
export function AnalyticsCard({
  title,
  subtitle,
  loading,
  isFetching,
  error,
  onRetry,
  empty,
  emptyHint,
  height = 260,
  children,
}: CardProps) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-5 shadow-2xs flex flex-col">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-bold text-slate-900">{title}</h3>
          {subtitle && <p className="text-xs text-slate-500">{subtitle}</p>}
        </div>
        {isFetching && !loading && (
          <span className="text-[11px] font-medium text-slate-500 bg-slate-100 px-2 py-0.5 rounded-full">
            Updating…
          </span>
        )}
      </div>
      {loading ? (
        <div
          className="flex flex-col items-center justify-center bg-slate-50/50 rounded-xl border border-dashed border-slate-200 animate-pulse"
          style={{ height: `${height}px` }}
        >
          <span className="text-xs text-slate-400">Loading {title.toLowerCase()}…</span>
        </div>
      ) : error ? (
        <div
          className="flex flex-col items-center justify-center bg-red-50/50 rounded-xl border border-red-200 p-6 text-center"
          style={{ height: `${height}px` }}
        >
          <AlertTriangle size={28} className="text-red-500 mb-2" />
          <p className="text-sm font-semibold text-red-900 mb-1">Unable to load chart data</p>
          <p className="text-xs text-red-600 mb-4">{error}</p>
          {onRetry && (
            <button
              onClick={onRetry}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white rounded-lg text-xs font-medium hover:bg-red-700 transition-colors shadow-2xs"
            >
              <RefreshCw size={13} /> Retry
            </button>
          )}
        </div>
      ) : empty ? (
        <div
          className="flex flex-col items-center justify-center bg-slate-50/50 rounded-xl border border-dashed border-slate-200 p-6 text-center text-slate-400"
          style={{ height: `${height}px` }}
        >
          <BarChart3 size={32} className="mb-2 opacity-40" />
          <p className="text-sm font-medium text-slate-600">No data for the current filters.</p>
          <p className="text-xs text-slate-400 mt-1">{emptyHint ?? 'Widen the filters to see this chart.'}</p>
        </div>
      ) : (
        children
      )}
    </div>
  );
}

interface ChartProps {
  data: NameValue[];
  height?: number;
}

function chartColors(index: number): string {
  return CHART_PALETTE[index % CHART_PALETTE.length];
}

/** Vertical bars (years, capacities, short labels). */
export function VBarChart({ data, height = 260 }: ChartProps) {
  return (
    <div className="w-full" style={{ height: `${height}px` }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} stroke="#64748b" />
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} stroke="#64748b" />
          <Tooltip />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((entry, i) => (
              <Cell key={entry.name} fill={chartColors(i)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal bars (companies, departments — long labels). */
export function HBarChart({ data, height = 260 }: ChartProps) {
  return (
    <div className="w-full" style={{ height: `${height}px` }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis type="number" allowDecimals={false} tick={{ fontSize: 11 }} stroke="#64748b" />
          <YAxis type="category" dataKey="name" width={130} tick={{ fontSize: 11 }} stroke="#64748b" />
          <Tooltip />
          <Bar dataKey="value" radius={[0, 4, 4, 0]}>
            {data.map((entry, i) => (
              <Cell key={entry.name} fill={chartColors(i)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Installations over time. */
export function TrendChart({ data, height = 260 }: ChartProps) {
  return (
    <div className="w-full" style={{ height: `${height}px` }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} stroke="#64748b" />
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} stroke="#64748b" />
          <Tooltip />
          <Line type="monotone" dataKey="value" stroke="#2563eb" strokeWidth={2.5} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Small-vocabulary composition only (type/status with few slices). */
export function DonutChart({ data, height = 260 }: ChartProps) {
  return (
    <div className="w-full" style={{ height: `${height}px` }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={data} cx="50%" cy="45%" innerRadius={60} outerRadius={85} paddingAngle={4} dataKey="value">
            {data.map((entry, i) => (
              <Cell key={entry.name} fill={chartColors(i)} />
            ))}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

interface MultiProps {
  data: Array<Record<string, string | number>>;
  series: string[];
  height?: number;
}

/** Two dimensions: stacked (few series) or grouped. */
export function SeriesBarChart({ data, series, height = 260, stacked = false }: MultiProps & { stacked?: boolean }) {
  return (
    <div className="w-full" style={{ height: `${height}px` }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 8, left: -8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
          <XAxis dataKey="name" tick={{ fontSize: 11 }} stroke="#64748b" />
          <YAxis allowDecimals={false} tick={{ fontSize: 11 }} stroke="#64748b" />
          <Tooltip />
          <Legend />
          {series.map((key, i) => (
            <Bar key={key} dataKey={key} stackId={stacked ? "total" : undefined} fill={chartColors(i)} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export interface HeatmapProps {
  xLabels: Array<string | number>;
  yLabels: string[];
  /** value per "y|x" key (missing = 0). */
  values: Map<string, number>;
  formatValue?: (n: number) => string;
}

/** Dense two-dimensional relationships (dependency-free divs). */
export function HeatmapGrid({ xLabels, yLabels, values, formatValue }: HeatmapProps) {
  const max = Math.max(1, ...values.values());
  return (
    <div className="overflow-x-auto">
      <div
        className="grid gap-1 min-w-max"
        role="table"
        aria-label="Heatmap"
        style={{ gridTemplateColumns: `140px repeat(${xLabels.length}, minmax(44px, 1fr))` }}
      >
        <div />
        {xLabels.map((x) => (
          <div key={String(x)} className="text-center text-[11px] font-bold text-slate-500 pb-1">
            {x}
          </div>
        ))}
        {yLabels.map((y) => (
          <React.Fragment key={y}>
            <div className="text-[11px] font-semibold text-slate-600 truncate pr-2 py-1" title={y}>
              {y}
            </div>
            {xLabels.map((x) => {
              const v = values.get(`${y}|${x}`) ?? 0;
              const alpha = v === 0 ? 0.04 : 0.15 + 0.85 * (v / max);
              return (
                <div
                  key={`${y}|${x}`}
                  role="cell"
                  aria-label={`${y}, ${x}: ${v}`}
                  title={`${y} × ${x}: ${formatValue ? formatValue(v) : v}`}
                  className="rounded-md text-center text-[11px] font-bold py-2"
                  style={{
                    backgroundColor: `rgba(37, 99, 235, ${alpha.toFixed(2)})`,
                    color: v / max > 0.5 ? "#ffffff" : "#1e293b",
                  }}
                >
                  {v === 0 ? "" : v}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}
