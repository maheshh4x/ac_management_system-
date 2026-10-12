// src/components/features/charts/MaintenanceTrendChart.tsx
'use client';

import React from 'react';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts';
import { AlertTriangle, RefreshCw, TrendingUp } from 'lucide-react';

/**
 * Phase 7: the hardcoded `DEFAULT_TREND_DATA` fallback was removed — an empty
 * chart must show the empty state, never invented numbers. Callers feed this
 * from `useMaintenanceTrend()` (GET /api/v1/reports/maintenance-trend).
 */
interface TrendItem {
  month: string;
  jobs: number;
  preventive?: number;
  breakdown?: number;
}

interface MaintenanceTrendChartProps {
  data?: TrendItem[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  height?: number;
}

export const MaintenanceTrendChart: React.FC<MaintenanceTrendChartProps> = ({
  data = [],
  loading = false,
  error = null,
  onRetry,
  height = 260
}) => {
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-[260px] bg-slate-50/50 rounded-xl border border-dashed border-slate-200 animate-pulse">
        <div className="h-32 w-3/4 bg-slate-200 rounded mb-3" />
        <span className="text-xs text-slate-400">Loading maintenance trend data...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-[260px] bg-red-50/50 rounded-xl border border-red-200 p-6 text-center">
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
    );
  }

  if (!data || data.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-[260px] bg-slate-50/50 rounded-xl border border-dashed border-slate-200 p-6 text-center text-slate-400">
        <TrendingUp size={32} className="mb-2 opacity-40" />
        <p className="text-sm font-medium text-slate-600">No maintenance trend data available yet.</p>
      </div>
    );
  }

  return (
    <div className="w-full" style={{ height: `${height}px` }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 15, right: 20, left: -10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#F1F5F9" />
          <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} />
          <YAxis axisLine={false} tickLine={false} tick={{ fontSize: 12, fill: '#64748B' }} />
          <Tooltip
            contentStyle={{
              borderRadius: '8px',
              border: 'none',
              boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
              fontSize: '13px'
            }}
          />
          <Line 
            type="monotone" 
            dataKey="jobs" 
            name="Total Maintenance Jobs"
            stroke="#2563EB" 
            strokeWidth={3} 
            dot={{ r: 4, fill: '#2563EB' }} 
            activeDot={{ r: 6 }} 
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};
