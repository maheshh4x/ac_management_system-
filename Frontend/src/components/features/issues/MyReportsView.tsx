'use client';

/**
 * My Reports: the reporter's own issue reports with live statuses. Links a
 * decided report to the work order created from it; rejected reports show
 * the recorded reason. A reporter only ever sees their own rows — the
 * backend scopes the query to the session identity.
 */
import React, { useState } from 'react';
import { Loader2 } from 'lucide-react';

import {
  reportCategoryLabel,
  reportStatusLabel,
  useIssueReport,
  useMyReports,
} from '@/lib/api/issue-reports';

function statusBadge(status: string): string {
  switch (status) {
    case 'PENDING_APPROVAL':
      return 'bg-amber-100 text-amber-800';
    case 'APPROVED':
      return 'bg-emerald-100 text-emerald-700';
    case 'REJECTED':
      return 'bg-red-100 text-red-700';
    default:
      return 'bg-slate-100 text-slate-500';
  }
}

function ReportDetail({ id }: { id: string }) {
  const { data, isLoading, isError } = useIssueReport(id);
  if (isLoading) return <p className="mt-2 text-xs text-slate-400">Loading details…</p>;
  if (isError || !data) return <p className="mt-2 text-xs text-red-600">Could not load this report.</p>;
  return (
    <div className="mt-2 space-y-1 text-xs text-slate-600">
      <p>
        <span className="font-semibold">Category:</span> {reportCategoryLabel(data.category)} ·{' '}
        <span className="font-semibold">Priority:</span> {data.priority}
      </p>
      <p>{data.description}</p>
      {data.asset_location_snapshot && (
        <p>
          <span className="font-semibold">Recorded location:</span> {data.asset_location_snapshot}
        </p>
      )}
      {data.location_text && (
        <p>
          <span className="font-semibold">Location note:</span> {data.location_text}
        </p>
      )}
      {data.status === 'REJECTED' && data.rejection_reason && (
        <p className="rounded-lg bg-red-50 p-2 text-red-700">
          <span className="font-semibold">Rejection reason:</span> {data.rejection_reason}
        </p>
      )}
      {data.work_order && (
        <p>
          <span className="font-semibold">Technician job:</span>{' '}
          <span className="font-mono">{data.work_order.id}</span> · {data.work_order.status}
          {data.work_order.technician_id ? ' · assigned' : ' · queued for assignment'}
        </p>
      )}
      {data.decided_by_profile && (
        <p className="text-slate-400">
          Decided by {data.decided_by_profile.name ?? 'admin'}
          {data.decided_at ? ` · ${new Date(data.decided_at).toLocaleString()}` : ''}
        </p>
      )}
    </div>
  );
}

export function MyReportsView() {
  const { data, isLoading, isError, refetch } = useMyReports();
  const [openId, setOpenId] = useState<string | null>(null);
  const reports = data?.reports ?? [];

  if (isLoading) {
    return (
      <p className="flex items-center gap-2 text-sm text-slate-500">
        <Loader2 size={16} className="animate-spin" /> Loading your reports…
      </p>
    );
  }
  if (isError) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        Could not load your reports.{' '}
        <button onClick={() => void refetch()} className="font-bold underline">
          Retry
        </button>
      </div>
    );
  }
  if (reports.length === 0) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">
        <p className="font-semibold text-slate-700">No reports filed yet</p>
        <p className="mt-1">Use Report Issue on any asset page to file your first report.</p>
      </div>
    );
  }
  return (
    <ul className="space-y-3">
      {reports.map((r) => (
        <li key={r.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-bold text-slate-800">{r.report_code}</span>
            <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-bold ${statusBadge(r.status)}`}>
              {reportStatusLabel(r.status)}
            </span>
            <span className="ml-auto text-xs text-slate-400">
              {r.created_at ? new Date(r.created_at).toLocaleString() : ''}
            </span>
          </div>
          <p className="mt-1 text-sm text-slate-600">
            {reportCategoryLabel(r.category)} · Asset{' '}
            <span className="font-mono">{r.ac_id ?? '—'}</span>
          </p>
          <button
            onClick={() => setOpenId(openId === r.id ? null : r.id)}
            className="mt-2 text-xs font-bold text-blue-600 hover:text-blue-800"
            aria-expanded={openId === r.id}
          >
            {openId === r.id ? 'Hide details' : 'View details'}
          </button>
          {openId === r.id && <ReportDetail id={r.id} />}
        </li>
      ))}
    </ul>
  );
}
