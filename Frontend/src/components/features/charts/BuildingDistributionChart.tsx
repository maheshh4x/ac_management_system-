// src/components/features/charts/BuildingDistributionChart.tsx
'use client';

import React from 'react';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell } from 'recharts';
import { AlertTriangle, RefreshCw, Building } from 'lucide-react';

interface BuildingDistributionItem {
  building: string;
  count: number;
}

interface BuildingDistributionChartProps {
  data?: BuildingDistributionItem[];
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  onBuildingClick?: (building: string) => void;
  height?: number;
}

export const BuildingDistributionChart: React.FC<BuildingDistributionChartProps> = ({
  data = [],
  loading = false,
  error = null,
  onRetry,
  onBuildingClick,
  height = 260
}) => {
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-[260px] bg-slate-50/50 rounded-xl border border-dashed border-slate-200 animate-pulse">
        <div className="h-32 w-3/4 bg-slate-200 rounded mb-3" />
        <span className="text-xs text-slate-400">Loading building-wise AC distribution...</span>
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

  if (!data || data.length === 0 || data.every(d => d.count === 0)) {
    return (
      <div className="flex flex-col items-center justify-center h-[260px] bg-slate-50/50 rounded-xl border border-dashed border-slate-200 p-6 text-center text-slate-400">
        <Building size={32} className="mb-2 opacity-40" />
        <p className="text-sm font-medium text-slate-600">No AC data available yet.</p>
        <p className="text-xs text-slate-400 mt-1">Upload a college dataset or reset filters to see distribution.</p>
      </div>
    );
  }

  return (
    <div className="w-full" style={{ height: `${height}px` }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 10, right: 30, left: 20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#F1F5F9" />
          <XAxis type="number" axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: '#64748B' }} />
          <YAxis 
            dataKey="building" 
            type="category" 
            axisLine={false} 
            tickLine={false} 
            tick={{ fontSize: 11, fill: '#475569' }} 
            width={120}
          />
          <Tooltip
            cursor={{ fill: '#F8FAFC' }}
            contentStyle={{
              borderRadius: '8px',
              border: 'none',
              boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
              fontSize: '13px'
            }}
          />
          <Bar 
            dataKey="count" 
            fill="#0284C7" 
            radius={[0, 4, 4, 0]}
            cursor="pointer"
            onClick={(entry: any) => onBuildingClick && entry?.payload?.building && onBuildingClick(entry.payload.building)}
          >
            {data.map((_, index) => {
              const bgColors = ['#6366F1', '#3B82F6', '#0EA5E9', '#10B981', '#F59E0B', '#EC4899', '#8B5CF6'];
              return <Cell key={`cell-${index}`} fill={bgColors[index % bgColors.length]} className="hover:opacity-85" />;
            })}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
};
