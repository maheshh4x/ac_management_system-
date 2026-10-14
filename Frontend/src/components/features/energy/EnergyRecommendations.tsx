'use client';

/**
 * Rule-based energy recommendations, computed from observed data only.
 * Display-only: there is no recommendation workflow on the backend, so
 * nothing here can be acknowledged or dismissed — each card states its
 * evidence, the action a human should consider, and what is missing.
 * Thresholds below are labeled heuristics, never diagnoses.
 */

/** Sessions open longer than this are worth a human glance. */
export const LONG_SESSION_HOURS = 12;

export interface EnergyRecommendation {
  id: string;
  severity: "high" | "medium" | "info";
  title: string;
  observed: string;
  reason: string;
  action: string;
  missing?: string;
}

export function buildRecommendations(input: {
  openSessions: Array<{ id: string; ac_id: string; started_at: string | null; block?: string | null }>;
  unknownStatusCount: number;
  excludedSessions: number;
  totalSessions: number;
  nowMs: number;
}): EnergyRecommendation[] {
  const recs: EnergyRecommendation[] = [];

  for (const s of input.openSessions) {
    if (!s.started_at) continue;
    const hours = (input.nowMs - new Date(s.started_at).getTime()) / 3_600_000;
    if (Number.isFinite(hours) && hours > LONG_SESSION_HOURS) {
      recs.push({
        id: `long-session-${s.id}`,
        severity: "medium",
        title: "Long-running open session",
        observed: `${s.ac_id} has been ON for ${hours.toFixed(1)} h${s.block ? ` (${s.block})` : ""}.`,
        reason: `Open longer than the ${LONG_SESSION_HOURS} h review threshold.`,
        action: "Verify the unit is still needed and stop the session if not.",
        missing: "No schedule data exists, so this is elapsed time only — not proof of waste.",
      });
    }
  }

  if (input.unknownStatusCount > 0) {
    recs.push({
      id: "unknown-status",
      severity: "info",
      title: "Units with unknown status",
      observed: `${input.unknownStatusCount} unit(s) report UNKNOWN operational status.`,
      reason: "Unknown state cannot be counted as ON or OFF.",
      action: "Survey these units and update their status so future reports classify them.",
    });
  }

  if (input.excludedSessions > 0) {
    recs.push({
      id: "missing-power",
      severity: "info",
      title: "Sessions excluded from estimates",
      observed: `${input.excludedSessions} of ${input.totalSessions} session(s) lack power-rating and capacity data.`,
      reason: "Without a power basis the model excludes rather than zeroes them.",
      action: "Record power ratings (or capacity tonnage) for these units to close the gap.",
    });
  }

  return recs;
}

const severityStyle: Record<EnergyRecommendation["severity"], string> = {
  high: "border-red-200 bg-red-50/60 text-red-800",
  medium: "border-amber-200 bg-amber-50/60 text-amber-800",
  info: "border-blue-200 bg-blue-50/60 text-blue-800",
};

export function EnergyRecommendations({ items }: { items: EnergyRecommendation[] }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <div className="mb-1 flex items-center justify-between gap-3">
        <h3 className="text-sm font-bold text-slate-900">Recommendations</h3>
        <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide text-slate-500">
          Rule-based · display only
        </span>
      </div>
      <p className="mb-4 text-xs text-slate-500">
        Computed from observed sessions and statuses. Nothing here changes equipment state.
      </p>
      {items.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-200 bg-slate-50/50 p-6 text-center text-sm text-slate-500">
          No recommendations for the selected scope and period.
        </p>
      ) : (
        <ul className="space-y-3">
          {items.map((r) => (
            <li key={r.id} className={`rounded-xl border p-4 ${severityStyle[r.severity]}`}>
              <div className="flex items-center gap-2">
                <span className="rounded-full bg-white/70 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide">
                  {r.severity}
                </span>
                <p className="text-sm font-bold text-slate-900">{r.title}</p>
              </div>
              <p className="mt-1.5 text-sm text-slate-700">{r.observed}</p>
              <p className="mt-1 text-xs text-slate-500">
                <span className="font-semibold">Why: </span>
                {r.reason} <span className="font-semibold">Suggested action: </span>
                {r.action}
              </p>
              {r.missing && <p className="mt-1 text-xs italic text-slate-500">{r.missing}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
