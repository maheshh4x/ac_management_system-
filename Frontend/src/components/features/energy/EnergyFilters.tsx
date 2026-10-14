'use client';

/**
 * Energy dashboard filter bar: reporting period + date range + inventory
 * dimensions. Controlled (the view owns state); every control maps to a
 * backend-supported query parameter so filters move all metrics together.
 */
import React from 'react';

import type { AssetFacets } from '@/lib/types/asset';

export type EnergyPeriod = 'day' | 'week' | 'month' | 'custom';

export interface EnergyFilters {
  period: EnergyPeriod;
  from: string;
  to: string;
  block: string;
  department: string;
  floor: string;
  room: string;
  manufacturer: string;
  capacityTon: string;
}

export const EMPTY_ENERGY_FILTERS: EnergyFilters = {
  period: 'week',
  from: '',
  to: '',
  block: '',
  department: '',
  floor: '',
  room: '',
  manufacturer: '',
  capacityTon: '',
};

const isoToday = () => new Date().toISOString().slice(0, 10);
const shiftDays = (days: number) => {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
};

/** Resolve preset periods to concrete bounds (custom keeps user dates). */
export function resolvePeriod(filters: EnergyFilters): { from: string; to: string } {
  const to = isoToday();
  if (filters.period === 'day') return { from: to, to };
  if (filters.period === 'week') return { from: shiftDays(6), to };
  if (filters.period === 'month') return { from: shiftDays(29), to };
  return { from: filters.from || shiftDays(6), to: filters.to || to };
}

const selectClass =
  'w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm focus:border-blue-500 focus:outline-none';

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: Array<{ value: string; label: string }>;
}) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] font-bold uppercase tracking-wide text-slate-400">{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={selectClass}
      >
        <option value="">All</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function EnergyFilters({
  filters,
  onChange,
  facets,
  onRefresh,
  refreshing,
}: {
  filters: EnergyFilters;
  onChange: (next: EnergyFilters) => void;
  facets: AssetFacets | undefined;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const set = (patch: Partial<EnergyFilters>) => onChange({ ...filters, ...patch });
  const opts = (entries: { value: string; count: number }[] | undefined) =>
    (entries ?? []).map((e) => ({ value: e.value, label: `${e.value} (${e.count})` }));

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
      <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Reporting period">
        {(
          [
            ['day', 'Day'],
            ['week', 'Week'],
            ['month', 'Month'],
            ['custom', 'Custom'],
          ] as Array<[EnergyPeriod, string]>
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => set({ period: value })}
            aria-pressed={filters.period === value}
            className={`rounded-lg px-3 py-1.5 text-xs font-bold transition-colors ${
              filters.period === value
                ? 'bg-blue-600 text-white shadow-sm'
                : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
            }`}
          >
            {label}
          </button>
        ))}
        {filters.period === 'custom' && (
          <span className="flex items-center gap-2 text-xs text-slate-500">
            <input
              aria-label="From date"
              type="date"
              value={filters.from}
              max={filters.to || isoToday()}
              onChange={(event) => set({ from: event.target.value })}
              className="rounded-lg border border-slate-200 px-2 py-1.5 shadow-sm"
            />
            <span>to</span>
            <input
              aria-label="To date"
              type="date"
              value={filters.to}
              min={filters.from || undefined}
              max={isoToday()}
              onChange={(event) => set({ to: event.target.value })}
              className="rounded-lg border border-slate-200 px-2 py-1.5 shadow-sm"
            />
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-7">
        <FilterSelect label="Building" value={filters.block} onChange={(v) => set({ block: v })} options={opts(facets?.block)} />
        <FilterSelect label="Department" value={filters.department} onChange={(v) => set({ department: v })} options={opts(facets?.department)} />
        <FilterSelect label="Floor" value={filters.floor} onChange={(v) => set({ floor: v })} options={opts(facets?.floor)} />
        <label className="flex min-w-0 flex-col gap-1">
          <span className="text-[11px] font-bold uppercase tracking-wide text-slate-400">Room</span>
          <input
            aria-label="Room"
            type="text"
            value={filters.room}
            onChange={(event) => set({ room: event.target.value })}
            placeholder="Room no. or name"
            className={selectClass}
          />
        </label>
        <FilterSelect label="Make" value={filters.manufacturer} onChange={(v) => set({ manufacturer: v })} options={opts(facets?.manufacturer)} />
        <FilterSelect
          label="Capacity"
          value={filters.capacityTon}
          onChange={(v) => set({ capacityTon: v })}
          options={[{ value: '1', label: '1 T' }, { value: '1.5', label: '1.5 T' }, { value: '2', label: '2 T' }]}
        />
        <div className="flex items-end gap-2">
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex min-h-10 w-full items-center justify-center gap-1.5 rounded-xl bg-slate-900 px-3 text-xs font-bold text-white shadow-sm transition-colors hover:bg-slate-700 disabled:opacity-60"
          >
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>
    </div>
  );
}
