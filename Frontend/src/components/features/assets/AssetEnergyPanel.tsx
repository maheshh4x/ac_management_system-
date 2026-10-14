'use client';

/**
 * Energy tab body for the asset detail page: live usage-session ledger plus
 * the backend's estimated electricity consumption for a recent window.
 *
 * Sessions come from GET /api/v1/assets/<ref>/sessions (VIEW_ASSET); the
 * estimate comes from GET /api/v1/assets/<ref>/energy — the same backend
 * model as the dashboard (rated power first, capacity-derived only as a
 * labelled provisional tier, insufficient data never zeroed). Every figure
 * on this tab is ESTIMATED, never measured.
 *
 * The ON/OFF control renders only for roles holding RECORD_USAGE_SESSION
 * (SUPER_ADMIN / FACILITY_MANAGER / TECHNICIAN); the backend enforces the
 * same permission plus facility scope and asset availability, so hiding the
 * button is UX only. Sessions are record-only — no approval step exists.
 */
import React, { useState } from 'react';
import { Info, Loader2, Play, Power, Square } from 'lucide-react';

import { useAuth } from '@/contexts/AuthContext';
import { canRecordSession } from '@/lib/permissions';
import {
  formatDuration,
  useAssetSessions,
  useStartSession,
  useStopSession,
} from '@/lib/api/sessions';
import { useAssetEnergy } from '@/lib/api/energy';
import { formatKwh } from '@/lib/energy-calculations';
import { ApiError } from '@/lib/api/client';

function friendlyError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'SESSION_ALREADY_OPEN') return 'A session is already running for this unit.';
    if (err.code === 'NO_OPEN_SESSION') return 'There is no open session to stop.';
    if (err.code === 'ASSET_UNAVAILABLE')
      return 'This asset is decommissioned or inactive — sessions are disabled.';
    if (err.code === 'ASSET_OUT_OF_SCOPE')
      return 'This asset is outside your assigned facilities.';
    return err.message || 'The request failed. Please try again.';
  }
  return 'The request failed. Please try again.';
}

/** Human labels for the backend's evidence-honest quality codes. */
const QUALITY_LABELS: Record<string, string> = {
  NO_USAGE_RECORDED: 'No usage recorded in this period',
  RUNTIME_RECORDED: 'Runtime recorded — insufficient data for a kWh estimate',
  BASIC_ENERGY_ESTIMATE: 'Basic estimate (rated input power)',
  BASIC_ENERGY_ESTIMATE_CAPACITY_DERIVED: 'Capacity-derived estimate (provisional)',
  WEATHER_CONTEXT_AVAILABLE: 'Basic estimate (rated input power)',
  WEATHER_CONTEXT_AVAILABLE_CAPACITY_DERIVED: 'Capacity-derived estimate (provisional)',
};

function basisBadge(basis: string | null): { text: string; cls: string } {
  if (basis === 'rated') return { text: 'Rated power', cls: 'bg-emerald-100 text-emerald-700' };
  if (basis === 'capacity-derived')
    return { text: 'Capacity-derived (provisional)', cls: 'bg-amber-100 text-amber-800' };
  return { text: 'Insufficient data', cls: 'bg-slate-100 text-slate-500' };
}

export function AssetEnergyPanel({ acId, powerRating }: { acId: string; powerRating?: string }) {
  const { user } = useAuth();
  const canRecord = canRecordSession(user?.role ?? null);
  const { data: sessions, isLoading, isError, refetch } = useAssetSessions(acId);
  const startSession = useStartSession(acId);
  const stopSession = useStopSession(acId);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Estimate window: the last 30 days, fixed at mount so refetches and
  // re-renders never shift the range under the user.
  const [energyRange] = useState(() => {
    const to = new Date();
    const from = new Date(to.getTime() - 30 * 24 * 3600 * 1000);
    const iso = (d: Date) => d.toISOString().slice(0, 10);
    return { from: iso(from), to: iso(to) };
  });
  const energy = useAssetEnergy(acId, energyRange);
  const est = energy.data;

  const open = (sessions ?? []).find((s) => !s.ended_at) ?? null;
  const totalSeconds = (sessions ?? []).reduce((sum, s) => sum + (s.duration_seconds ?? 0), 0);

  const run = async (kind: 'start' | 'stop') => {
    setActionError(null);
    setBusy(true);
    try {
      if (kind === 'start') await startSession.mutateAsync(null);
      else await stopSession.mutateAsync(null);
      await refetch();
    } catch (err) {
      setActionError(friendlyError(err));
      await refetch();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
      <div className="flex items-start gap-4 mb-6">
        <div className="p-3 bg-amber-50 rounded-xl text-amber-600">
          <Power size={22} />
        </div>
        <div className="flex-1">
          <h3 className="font-bold text-slate-900">Energy Consumption</h3>
          <p className="text-sm text-slate-500 mt-0.5">Recorded usage sessions for this unit</p>
        </div>
        {canRecord && !isLoading && !isError && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void run(open ? 'stop' : 'start')}
            className={`inline-flex min-h-11 items-center gap-2 rounded-xl px-4 text-sm font-bold text-white shadow-md transition-all active:scale-[0.98] disabled:opacity-70 ${
              open ? 'bg-slate-700 hover:bg-slate-800' : 'bg-emerald-600 hover:bg-emerald-700'
            }`}
          >
            {busy ? (
              <Loader2 size={16} className="animate-spin" />
            ) : open ? (
              <Square size={16} />
            ) : (
              <Play size={16} />
            )}
            {open ? 'Stop session' : 'Start session'}
          </button>
        )}
      </div>

      {actionError && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 p-3 text-sm font-medium text-red-700">
          {actionError}
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
          {[0, 1, 2].map((i) => (
            <div key={i} className="rounded-xl border border-slate-100 bg-slate-50 p-4 text-center animate-pulse">
              <div className="mx-auto h-7 w-20 rounded bg-slate-200" />
              <div className="mx-auto mt-2 h-3 w-24 rounded bg-slate-200" />
            </div>
          ))}
        </div>
      ) : isError || !sessions ? (
        <div className="mb-6 rounded-xl border border-slate-200 bg-slate-50 p-4 text-center text-sm text-slate-500">
          Usage history is unavailable right now.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
            <div className="bg-slate-50 rounded-xl border border-slate-100 p-4 text-center">
              <div className={`text-2xl font-bold ${open ? 'text-emerald-600' : 'text-slate-400'}`}>
                {open ? 'ON' : 'OFF'}
              </div>
              <div className="text-xs text-slate-500 mt-1 font-semibold">Current state</div>
            </div>
            <div className="bg-slate-50 rounded-xl border border-slate-100 p-4 text-center">
              <div className="text-2xl font-bold text-slate-800">{sessions.length}</div>
              <div className="text-xs text-slate-500 mt-1 font-semibold">Recorded sessions</div>
            </div>
            <div className="bg-slate-50 rounded-xl border border-slate-100 p-4 text-center">
              <div className="text-2xl font-bold text-slate-800">{formatDuration(totalSeconds)}</div>
              <div className="text-xs text-slate-500 mt-1 font-semibold">Total ON time</div>
            </div>
          </div>

          {sessions.length > 0 && (
            <div className="mb-6 overflow-hidden rounded-xl border border-slate-100">
              <div className="bg-slate-50 px-4 py-2 text-[10px] font-bold uppercase tracking-wide text-slate-400">
                Recent sessions
              </div>
              <ul className="divide-y divide-slate-100">
                {sessions.slice(0, 5).map((s) => (
                  <li key={s.id} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                    <span className="text-slate-600">
                      {s.started_at ? new Date(s.started_at).toLocaleString() : '—'}
                    </span>
                    <span className="font-semibold text-slate-800">
                      {s.ended_at ? formatDuration(s.duration_seconds) : 'Running…'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {/* Estimated electricity — backend model, last 30 days. ESTIMATED, never measured. */}
      <div className="mb-6 rounded-xl border border-slate-200 p-4">
        <div className="flex items-center justify-between gap-3">
          <div className="text-xs font-bold uppercase tracking-wide text-slate-400">
            Estimated electricity · last 30 days
          </div>
          <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-slate-500">
            Estimated
          </span>
        </div>
        {energy.isPending ? (
          <div className="mt-3 flex items-center gap-2 text-sm text-slate-400">
            <Loader2 size={14} className="animate-spin" /> Loading estimate…
          </div>
        ) : energy.isError || !est ? (
          <p className="mt-3 text-sm text-slate-500">
            Estimate unavailable right now. Session history above is unaffected.
          </p>
        ) : (
          <div className="mt-3 space-y-2 text-sm text-slate-700">
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-2xl font-bold text-slate-800">
                {est.totals.baseline_kwh > 0 ? formatKwh(est.totals.baseline_kwh) : '0.0 kWh'}
              </span>
              <span className="text-xs text-slate-500">
                over {est.totals.runtime_hours.toFixed(1)} recorded hours
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={`rounded-full px-2.5 py-0.5 text-[11px] font-bold ${basisBadge(est.model.power_basis).cls}`}
              >
                {basisBadge(est.model.power_basis).text}
              </span>
              {est.model.version && (
                <span className="rounded-full bg-slate-100 px-2.5 py-0.5 text-[11px] font-semibold text-slate-500">
                  {est.model.version}
                </span>
              )}
              {est.sessions.some((s) => s.status === 'open') && (
                <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-[11px] font-bold text-amber-800">
                  Includes provisional open session
                </span>
              )}
            </div>
            <p className="text-xs font-semibold text-slate-600">
              {QUALITY_LABELS[est.quality] ?? est.quality}
            </p>
            <ul className="space-y-1 text-xs text-slate-500">
              {est.quality_reasons.map((reason) => (
                <li key={reason}>• {reason}</li>
              ))}
            </ul>
            <p className="text-[11px] text-slate-400">
              Calculated {new Date(est.calculated_at).toLocaleString()} ·{' '}
              {est.cost_unavailable_reason ?? 'Cost is not configured, so no currency figure appears.'}
            </p>
          </div>
        )}
      </div>

      <div className="flex items-start gap-3 p-4 bg-blue-50 border border-blue-100 rounded-xl text-sm text-blue-700">
        <Info size={16} className="mt-0.5 shrink-0" />
        <div>
          <span className="font-bold">Session ledger:</span> each start/stop is recorded with
          who and when — no approval step. Estimated connected load based on rated power:{' '}
          <strong>{powerRating || 'N/A'}</strong>.
        </div>
      </div>
    </div>
  );
}
