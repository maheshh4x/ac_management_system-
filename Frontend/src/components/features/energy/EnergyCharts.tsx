'use client';

/**
 * Energy charts (recharts): daily runtime/kWh trend, ON/OFF distribution,
 * consumption by block and by manufacturer. Same loading/error/empty
 * contract as the existing chart components; empty ranges render honest
 * no-data states, never fabricated history.
 */
import React from 'react';
import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  Pie,
  PieChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AlertTriangle, BarChart3, RefreshCw } from 'lucide-react';

import type { DailyBucket } from '@/lib/energy-calculations';

const tooltipStyle = {
  borderRadius: '8px',
  border: 'none',
  boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
  fontSize: '13px',
};

function ChartShell({
  title,
  height,
  loading,
  error,
  empty,
  emptyText,
  onRetry,
  children,
}: {
  title: string;
  height: number;
  loading?: boolean;
  error?: string | null;
  empty?: boolean;
  emptyText?: string;
  onRetry?: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <h3 className="mb-4 text-sm font-bold text-slate-900">{title}</h3>
      {loading ? (
        <div
          className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/50 animate-pulse"
          style={{ height }}
        >
          <div className="mb-3 h-24 w-3/4 rounded bg-slate-200" />
          <span className="text-xs text-slate-400">Loading chart data…</span>
        </div>
      ) : error ? (
        <div
          className="flex flex-col items-center justify-center rounded-xl border border-red-200 bg-red-50/50 p-6 text-center"
          style={{ height }}
        >
          <AlertTriangle size={28} className="mb-2 text-red-500" />
          <p className="mb-1 text-sm font-semibold text-red-900">Unable to load chart data</p>
          <p className="mb-4 text-xs text-red-600">{error}</p>
          {onRetry && (
            <button
              onClick={onRetry}
              className="flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-red-700"
            >
              <RefreshCw size={13} /> Retry
            </button>
          )}
        </div>
      ) : empty ? (
        <div
          className="flex flex-col items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50/50 p-6 text-center text-slate-400"
          style={{ height }}
        >
          <BarChart3 size={32} className="mb-2 opacity-40" />
          <p className="text-sm font-medium text-slate-600">No data in this range.</p>
          <p className="mt-1 text-xs text-slate-400">{emptyText ?? 'Recorded sessions will appear here.'}</p>
        </div>
      ) : (
        <div className="w-full" style={{ height }}>
          <ResponsiveContainer width="100%" height="100%">
            {children}
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

export function DailyTrendChart({
  data,
  loading,
  error,
  onRetry,
}: {
  data: DailyBucket[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}) {
  const empty = !data.some((d) => d.sessions > 0);
  return (
    <ChartShell title="Daily runtime & estimated consumption" height={260} loading={loading} error={error} empty={empty} onRetry={onRetry}
      emptyText="No usage sessions recorded in the selected period.">
      <ComposedChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
        <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#64748B' }} tickLine={false} axisLine={false} minTickGap={24} tickFormatter={(v: string) => v.slice(5)} />
        <YAxis yAxisId="left" tick={{ fontSize: 11, fill: '#64748B' }} tickLine={false} axisLine={false} width={40} />
        <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 11, fill: '#64748B' }} tickLine={false} axisLine={false} width={44} />
        <Tooltip contentStyle={tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Area yAxisId="left" type="monotone" dataKey="runtimeHours" name="Runtime (h)" fill="#DBEAFE" stroke="#3B82F6" strokeWidth={2} />
        <Line yAxisId="right" type="monotone" dataKey="estimatedKwh" name="Est. kWh" stroke="#F59E0B" strokeWidth={2} dot={false} />
      </ComposedChart>
    </ChartShell>
  );
}

export function OnOffPieChart({
  onUnits,
  offUnits,
  unknownUnits,
  loading,
  error,
  onRetry,
}: {
  onUnits: number;
  offUnits: number;
  unknownUnits: number;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
}) {
  const data = [
    { name: 'ON', value: onUnits, fill: '#10B981' },
    { name: 'OFF', value: offUnits, fill: '#94A3B8' },
    { name: 'Unknown', value: unknownUnits, fill: '#F59E0B' },
  ];
  return (
    <ChartShell title="ON / OFF distribution (session-derived)" height={260} loading={loading} error={error}
      empty={onUnits + offUnits + unknownUnits === 0} onRetry={onRetry} emptyText="No units in scope.">
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius={52} outerRadius={88} paddingAngle={2} label={{ fontSize: 11 }}>
          {data.map((entry) => (
            <Cell key={entry.name} fill={entry.fill} />
          ))}
        </Pie>
        <Tooltip contentStyle={tooltipStyle} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ChartShell>
  );
}

export function GroupBarChart({
  title,
  data,
  dataKey,
  nameKey,
  loading,
  error,
  onRetry,
  emptyText,
}: {
  title: string;
  data: Array<Record<string, string | number | null>>;
  dataKey: string;
  nameKey: string;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  emptyText?: string;
}) {
  const palette = ['#6366F1', '#3B82F6', '#0EA5E9', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6'];
  return (
    <ChartShell title={title} height={260} loading={loading} error={error}
      empty={!data.some((d) => Number(d[dataKey] ?? 0) > 0)} onRetry={onRetry} emptyText={emptyText}>
      <BarChart data={data} layout="vertical" margin={{ top: 10, right: 30, left: 20, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
        <XAxis type="number" axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: '#64748B' }} />
        <YAxis dataKey={nameKey} type="category" axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: '#475569' }} width={120} />
        <Tooltip contentStyle={tooltipStyle} cursor={{ fill: '#F8FAFC' }} />
        <Bar dataKey={dataKey} radius={[0, 4, 4, 0]}>
          {data.map((_, index) => (
            <Cell key={`cell-${index}`} fill={palette[index % palette.length]} />
          ))}
        </Bar>
      </BarChart>
    </ChartShell>
  );
}
