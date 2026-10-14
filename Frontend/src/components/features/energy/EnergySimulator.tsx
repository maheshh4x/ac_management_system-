'use client';

/**
 * What-if operating-hours simulator. Pure client-side arithmetic on the
 * dashboard's own estimated baseline — no backend, no persistence, and the
 * output is labeled SIMULATION everywhere it appears. Monetary savings are
 * never shown: no tariff is configured.
 */
import React, { useMemo, useState } from 'react';

import {
  formatKwh,
  simulateSavings,
} from '@/lib/energy-calculations';

export function EnergySimulator({
  baselineKwh,
  baselineLoadFactor,
  excludedHours,
  totalHours,
}: {
  baselineKwh: number;
  baselineLoadFactor: number;
  excludedHours: number;
  totalHours: number;
}) {
  const [reductionPct, setReductionPct] = useState(15);
  const [loadFactor, setLoadFactor] = useState(baselineLoadFactor);

  // kWh scales linearly with the load factor (energy = power × hours × L), so
  // re-baselining at a new factor is exact arithmetic on the same estimate.
  const scaledBaseline = baselineLoadFactor > 0 ? (baselineKwh / baselineLoadFactor) * loadFactor : 0;
  const result = useMemo(
    () => simulateSavings(scaledBaseline, reductionPct, loadFactor, excludedHours, totalHours),
    [scaledBaseline, reductionPct, loadFactor, excludedHours, totalHours],
  );

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h3 className="text-sm font-bold text-slate-900">What-if savings simulator</h3>
        <span className="rounded-full bg-violet-100 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide text-violet-700">
          Simulation
        </span>
      </div>
      <p className="mb-4 text-xs text-slate-500">
        Project the effect of running units fewer hours. Results are projections, not measurements
        or guaranteed savings.
      </p>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-xs font-semibold text-slate-600">
          Operating-hours reduction: {reductionPct}%
          <input
            aria-label="Operating-hours reduction percent"
            type="range"
            min={5}
            max={50}
            step={5}
            value={reductionPct}
            onChange={(event) => setReductionPct(Number(event.target.value))}
            className="w-full accent-violet-600"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-xs font-semibold text-slate-600">
          Average load factor: {Math.round(loadFactor * 100)}%
          <input
            aria-label="Average load factor assumption"
            type="range"
            min={30}
            max={100}
            step={5}
            value={Math.round(loadFactor * 100)}
            onChange={(event) => setLoadFactor(Number(event.target.value) / 100)}
            className="w-full accent-violet-600"
          />
        </label>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div className="rounded-xl bg-slate-50 p-3.5 text-center">
          <div className="text-lg font-bold text-slate-800">{formatKwh(result.baselineKwh)}</div>
          <div className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Baseline (est.)
          </div>
        </div>
        <div className="rounded-xl bg-slate-50 p-3.5 text-center">
          <div className="text-lg font-bold text-slate-800">{formatKwh(result.simulatedKwh)}</div>
          <div className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Simulated
          </div>
        </div>
        <div className="rounded-xl bg-emerald-50 p-3.5 text-center">
          <div className="text-lg font-bold text-emerald-700">{formatKwh(result.avoidedKwh)}</div>
          <div className="mt-0.5 text-[11px] font-semibold uppercase tracking-wide text-emerald-600">
            Energy avoided
          </div>
        </div>
      </div>

      <ul className="mt-4 space-y-1 text-xs text-slate-500">
        <li>
          • Baseline = recorded runtime × rated/derived power × {Math.round(loadFactor * 100)}% average load
          factor (an assumption, not a measurement).
        </li>
        <li>• Reduction applies uniformly to all metered runtime in the selected period.</li>
        {result.excludedKwhShare && <li>• Excluded from both figures: {result.excludedKwhShare}.</li>}
        <li>• No cost figures: no electricity tariff is configured.</li>
      </ul>
    </div>
  );
}
