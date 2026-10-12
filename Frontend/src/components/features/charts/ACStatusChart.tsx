// src/components/features/charts/ACStatusChart.tsx
'use client';

import React from 'react';
import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip, Legend } from 'recharts';
import { AlertTriangle, RefreshCw, PieChart as PieIcon } from 'lucide-react';

interface ChartDataItem {
  name: string;
  value: number;
  color: string;
}

interface ACStatusChartProps {
  data?: ChartDataItem[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onSliceClick?: (name: string) => void;
  height?: number;
}

export const ACStatusChart: React.FC<ACStatusChartProps> = ({
  data = [],
  loading = false,
  error = null,
  onRetry,
  onSliceClick,
  height = 260
}) => {
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-[260px] bg-slate-50/50 rounded-xl border border-dashed border-slate-200 animate-pulse">
        <div className="w-24 h-24 rounded-full border-4 border-slate-200 border-t-blue-500 animate-spin mb-3" />
        <span className="text-xs text-slate-400">Loading AC status distribution...</span>
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

  if (!data || data.length === 0 || data.every(d => d.value === 0)) {
    return (
      <div className="flex flex-col items-center justify-center h-[260px] bg-slate-50/50 rounded-xl border border-dashed border-slate-200 p-6 text-center text-slate-400">
        <PieIcon size={32} className="mb-2 opacity-40" />
        <p className="text-sm font-medium text-slate-600">No AC data available yet.</p>
        <p className="text-xs text-slate-400 mt-1">Upload a college dataset or reset filters to see distribution.</p>
      </div>
    );
  }

  return (
    <div className="w-full" style={{ height: `${height}px` }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="45%"
            innerRadius={60}
            outerRadius={85}
            paddingAngle={4}
            dataKey="value"
            cursor="pointer"
            onClick={(entry: any) => {
              const name = entry?.name || entry?.payload?.name;
              if (onSliceClick && name) onSliceClick(name);
            }}
          >
            {data.map((entry, index) => (
              <Cell 
                key={`cell-${index}`} 
                fill={entry.color} 
                className="hover:opacity-85 transition-opacity outline-none" 
              />
            ))}
          </Pie>
          <Tooltip
            contentStyle={{
              borderRadius: '8px',
              border: 'none',
              boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
              fontSize: '13px',
              fontWeight: 500
            }}
          />
          <Legend
            iconType="circle"
            wrapperStyle={{ fontSize: '12px', paddingTop: '10px' }}
          />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
};
