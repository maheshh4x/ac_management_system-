'use client';

/**
 * Energy KPI cards. Every card states its provenance: measured (session
 * ledger / inventory counts) or estimated (model + assumptions) or
 * unavailable (no tariff, no meters). Unknown/stale means: no session data
 * in range and/or UNKNOWN operational status — never a guess about state.
 */
import React from 'react';
import { Activity, AlertTriangle, Ban, Clock3, Layers, Power, Zap } from 'lucide-react';

import { formatHours, formatKwh } from '@/lib/energy-calculations';

export interface EnergyKpiInput {
  totalUnits: number;
  onUnits: number;
  offUnits: number;
  unknownUnits: number;
  runtimeHours: number;
  estimatedKwh: number | null;
  eligibleUnits: number;
  incompleteUnits: number;
  excludedSessions: number;
}

function Card({
  label,
  value,
  sub,
  icon,
  accent,
}: {
  label: string;
  value: string;
  sub: string;
  icon: React.ReactNode;
  accent: string;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-center justify-between">
        <p className="text-[11px] font-bold uppercase tracking-wide text-slate-400">{label}</p>
        <span className={accent}>{icon}</span>
      </div>
      <p className="mt-1 text-2xl font-bold text-slate-900">{value}</p>
      <p className="mt-0.5 text-xs text-slate-500">{sub}</p>
    </div>
  );
}

export function EnergyKpiCards({ kpis }: { kpis: EnergyKpiInput }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      <Card
        label="Total units"
        value={String(kpis.totalUnits)}
        sub="In authorized scope"
        icon={<Layers size={16} />}
        accent="text-slate-400"
      />
      <Card
        label="Currently ON"
        value={String(kpis.onUnits)}
        sub="Open sessions now"
        icon={<Power size={16} />}
        accent="text-emerald-600"
      />
      <Card
        label="Currently OFF"
        value={String(kpis.offUnits)}
        sub="Session in range, none open"
        icon={<Power size={16} />}
        accent="text-slate-400"
      />
      <Card
        label="Unknown / stale"
        value={String(kpis.unknownUnits)}
        sub="No session data in range"
        icon={<AlertTriangle size={16} />}
        accent="text-amber-500"
      />
      <Card
        label="Recorded runtime"
        value={formatHours(kpis.runtimeHours)}
        sub="Measured from sessions"
        icon={<Clock3 size={16} />}
        accent="text-blue-600"
      />
      <Card
        label="Est. consumption"
        value={formatKwh(kpis.estimatedKwh)}
        sub="Estimated — see assumptions"
        icon={<Zap size={16} />}
        accent="text-amber-600"
      />
      <Card
        label="Energy cost"
        value="—"
        sub="No tariff configured"
        icon={<Ban size={16} />}
        accent="text-slate-300"
      />
      <Card
        label="Estimation eligible"
        value={String(kpis.eligibleUnits)}
        sub="Units with power data"
        icon={<Activity size={16} />}
        accent="text-emerald-600"
      />
      <Card
        label="Incomplete inputs"
        value={String(kpis.incompleteUnits)}
        sub={`${kpis.excludedSessions} sessions w/o power data`}
        icon={<AlertTriangle size={16} />}
        accent="text-amber-500"
      />
    </div>
  );
}
