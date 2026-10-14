'use client';

/**
 * Comparative matrix table (block / department / manufacturer switchable).
 * Every cell traces to either inventory counts (dashboard summary) or the
 * session ledger; estimated kWh is blank (—) for groups with no usable power
 * data instead of a misleading zero.
 */
import React, { useState } from 'react';

import { formatHours, formatKwh, type GroupRow } from '@/lib/energy-calculations';

export type MatrixDimension = 'block' | 'department' | 'manufacturer';

const TABS: Array<{ value: MatrixDimension; label: string }> = [
  { value: 'block', label: 'By building' },
  { value: 'department', label: 'By department' },
  { value: 'manufacturer', label: 'By make' },
];

export function EnergyMatrix({
  byBlock,
  byDepartment,
  byManufacturer,
}: {
  byBlock: GroupRow[];
  byDepartment: GroupRow[];
  byManufacturer: GroupRow[];
}) {
  const [dimension, setDimension] = useState<MatrixDimension>('block');
  const rows = dimension === 'block' ? byBlock : dimension === 'department' ? byDepartment : byManufacturer;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-bold text-slate-900">Breakdown & comparison</h3>
        <div className="flex gap-1 rounded-xl bg-slate-100 p-1" role="group" aria-label="Matrix dimension">
          {TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              onClick={() => setDimension(tab.value)}
              aria-pressed={dimension === tab.value}
              className={`rounded-lg px-3 py-1.5 text-xs font-bold transition-colors ${
                dimension === tab.value ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>
      {rows.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-200 bg-slate-50/50 p-6 text-center text-sm text-slate-500">
          No groups in scope for the selected filters.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="border-b border-slate-100 text-left text-[11px] font-bold uppercase tracking-wide text-slate-400">
                <th className="py-2 pr-3">Group</th>
                <th className="py-2 pr-3 text-right">Units</th>
                <th className="py-2 pr-3 text-right">ON</th>
                <th className="py-2 pr-3 text-right">Runtime</th>
                <th className="py-2 pr-3 text-right">Est. kWh</th>
                <th className="py-2 text-right">Power data</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row) => (
                <tr key={row.key} className="text-slate-700">
                  <td className="py-2.5 pr-3 font-semibold text-slate-900">{row.key}</td>
                  <td className="py-2.5 pr-3 text-right tabular-nums">{row.units}</td>
                  <td className="py-2.5 pr-3 text-right tabular-nums">{row.onUnits}</td>
                  <td className="py-2.5 pr-3 text-right tabular-nums">{formatHours(row.runtimeHours)}</td>
                  <td className="py-2.5 pr-3 text-right tabular-nums">{formatKwh(row.estimatedKwh)}</td>
                  <td className="py-2.5 text-right text-xs tabular-nums text-slate-500">
                    {row.sessionsTotal === 0
                      ? 'no sessions'
                      : `${row.sessionsWithPower}/${row.sessionsTotal} sessions`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
