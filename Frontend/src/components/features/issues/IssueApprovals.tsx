'use client';

/**
 * Issue-report approval queue (Features B/C): rendered on the admin
 * approvals page for SUPER_ADMIN only. Approve creates exactly one
 * SCHEDULED work order (the button disables while pending, and replays
 * return the existing job); reject requires a recorded reason. The backend
 * enforces APPROVE_REPORTS — these buttons are UX only.
 */
import React, { useState } from 'react';
import Link from 'next/link';
import { Check, Clock3, Loader2, X } from 'lucide-react';

import { ApiError } from '@/lib/api/client';
import {
  reportCategoryLabel,
  reportStatusLabel,
  useAllReports,
  useApproveReport,
  usePendingReports,
  useRejectReport,
} from '@/lib/api/issue-reports';

export function IssueApprovals() {
  const pending = usePendingReports();
  const all = useAllReports();
  const approveMutation = useApproveReport();
  const rejectMutation = useRejectReport();
  const [tab, setTab] = useState<'pending' | 'decided'>('pending');
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [pageError, setPageError] = useState('');
  const [notice, setNotice] = useState('');

  const busy = approveMutation.isPending || rejectMutation.isPending;
  const reports = pending.data?.reports ?? [];
  // Decided = everything that left the pending queue (approved or rejected),
  // newest first — an approved report stays traceable here with its job.
  const decided = (all.data?.reports ?? []).filter((r) => r.status !== 'PENDING_APPROVAL');

  const approve = (id: string, code: string) => {
    setPageError('');
    setNotice('');
    approveMutation.mutate(id, {
      onSuccess: (result) => {
        if (result.already_processed) {
          setNotice(`Report ${code} was already decided — linked job ${result.id ?? 'none'}. No duplicate created.`);
        } else {
          setNotice(`Report ${code} approved — technician job ${result.id} queued.`);
        }
      },
      onError: (err) =>
        setPageError(err instanceof Error ? err.message : 'Approval failed.'),
    });
  };

  const reject = (id: string) => {
    if (!reason.trim()) {
      setPageError('A rejection reason is required.');
      return;
    }
    setPageError('');
    setNotice('');
    rejectMutation.mutate(
      { id, reason: reason.trim() },
      {
        onSuccess: () => {
          setRejectingId(null);
          setReason('');
          setNotice('Report rejected with the recorded reason. No technician job was created.');
        },
        onError: (err) =>
          setPageError(
            err instanceof ApiError && err.code === 'REPORT_ALREADY_DECIDED'
              ? 'This report was already decided.'
              : err instanceof Error
                ? err.message
                : 'Rejection failed.',
          ),
      },
    );
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-lg font-bold text-slate-900">Issue reports</h2>
        <p className="mt-0.5 text-sm text-slate-500">
          Approve to create exactly one technician job, or reject with a reason. Rejected reports keep their history.
        </p>
      </div>

      <div className="flex gap-2" role="tablist" aria-label="Issue report queues">
        {(['pending', 'decided'] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-lg px-3 py-1.5 text-xs font-bold capitalize transition-colors ${
              tab === t ? 'bg-slate-900 text-white' : 'bg-white text-slate-600 border border-slate-200 hover:border-slate-400'
            }`}
          >
            {t === 'pending' ? `Pending (${reports.length})` : `Decided (${decided.length})`}
          </button>
        ))}
      </div>

      {notice && (
        <p className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{notice}</p>
      )}
      {pageError && (
        <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{pageError}</p>
      )}
      {tab === 'pending' ? (
        <>
          {pending.isError && (
            <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              Could not load pending reports.{' '}
              <button onClick={() => void pending.refetch()} className="font-bold underline">
                Retry
              </button>
            </p>
          )}
          {pending.isLoading && (
            <p className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 size={16} className="animate-spin" /> Loading pending reports…
            </p>
          )}

          {!pending.isLoading && reports.length === 0 ? (
        <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-slate-500">
          <Clock3 className="mx-auto mb-3 text-slate-300" size={28} />
          <p className="font-semibold text-slate-700">No pending issue reports</p>
          <p className="mt-1 text-sm">New reports from any role will appear here for review.</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="border-b border-slate-200 bg-slate-50">
                <tr>
                  {['Report', 'Asset', 'Category', 'Reporter', 'Filed', 'Actions'].map((h) => (
                    <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {reports.map((r) => (
                  <React.Fragment key={r.id}>
                    <tr className="hover:bg-slate-50">
                      <td className="px-4 py-4">
                        <p className="font-mono font-bold text-slate-900">{r.report_code}</p>
                        <p className="mt-0.5 max-w-xs truncate text-xs text-slate-500">{r.description}</p>
                      </td>
                      <td className="px-4 py-4 font-mono text-xs text-slate-600">
                        {r.asset_code ?? r.ac_id ?? '—'}
                      </td>
                      <td className="px-4 py-4 text-slate-600">
                        {reportCategoryLabel(r.category)}
                        <p className="text-xs text-slate-400">{r.priority}</p>
                      </td>
                      <td className="px-4 py-4 text-xs text-slate-600">{r.reporter_name ?? '—'}</td>
                      <td className="px-4 py-4 text-xs text-slate-500">
                        {r.created_at ? new Date(r.created_at).toLocaleString() : '—'}
                      </td>
                      <td className="px-4 py-4">
                        {rejectingId === r.id ? (
                          <div className="flex items-center gap-2">
                            <input
                              value={reason}
                              onChange={(e) => setReason(e.target.value)}
                              placeholder="Reason (required)"
                              maxLength={1000}
                              className="w-40 rounded-md border border-slate-200 px-2 py-1.5 text-xs outline-none focus:ring-2 focus:ring-red-400"
                            />
                            <button disabled={busy} onClick={() => reject(r.id)} className="rounded-md bg-red-600 p-1.5 text-white disabled:opacity-40" title="Confirm rejection" aria-label="Confirm rejection">
                              <Check size={14} />
                            </button>
                            <button
                              onClick={() => {
                                setRejectingId(null);
                                setReason('');
                              }}
                              className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100"
                              title="Cancel"
                              aria-label="Cancel rejection"
                            >
                              <X size={14} />
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center gap-2">
                            <button
                              disabled={busy}
                              onClick={() => {
                                if (window.confirm(`Approve report ${r.report_code}? This creates one technician job.`)) approve(r.id, r.report_code);
                              }}
                              className="rounded-md bg-emerald-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                              Approve
                            </button>
                            <button
                              disabled={busy}
                              onClick={() => setRejectingId(r.id)}
                              className="rounded-md border border-red-200 px-2.5 py-1.5 text-xs font-semibold text-red-600 hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                              Reject
                            </button>
                            {r.work_order_id && (
                              <Link href={`/admin/maintenance`} className="text-xs font-bold text-blue-600 hover:text-blue-800">
                                Job {r.work_order_id.slice(0, 8)}…
                              </Link>
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
        </>
      ) : (
        <>
          {all.isError && (
            <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              Could not load decided reports.{' '}
              <button onClick={() => void all.refetch()} className="font-bold underline">
                Retry
              </button>
            </p>
          )}
          {all.isLoading && (
            <p className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 size={16} className="animate-spin" /> Loading decided reports…
            </p>
          )}
          {!all.isLoading && decided.length === 0 ? (
            <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-slate-500">
              <Clock3 className="mx-auto mb-3 text-slate-300" size={28} />
              <p className="font-semibold text-slate-700">No decided reports yet</p>
              <p className="mt-1 text-sm">Approved and rejected reports stay traceable here with their jobs.</p>
            </div>
          ) : (
            <div className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-sm">
                  <thead className="border-b border-slate-200 bg-slate-50">
                    <tr>
                      {['Report', 'Status', 'Asset', 'Reporter', 'Technician job', 'Decided'].map((h) => (
                        <th key={h} className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {decided.map((r) => (
                      <tr key={r.id} className="hover:bg-slate-50">
                        <td className="px-4 py-4">
                          <p className="font-mono font-bold text-slate-900">{r.report_code}</p>
                          <p className="mt-0.5 max-w-xs truncate text-xs text-slate-500">{r.description}</p>
                        </td>
                        <td className="px-4 py-4">
                          <span
                            className={`rounded-full px-2.5 py-0.5 text-[11px] font-bold ${
                              r.status === 'APPROVED' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'
                            }`}
                          >
                            {reportStatusLabel(r.status)}
                          </span>
                          {r.status === 'REJECTED' && r.rejection_reason && (
                            <p className="mt-1 max-w-xs text-xs text-slate-500">{r.rejection_reason}</p>
                          )}
                        </td>
                        <td className="px-4 py-4 font-mono text-xs text-slate-600">
                          {r.asset_code ?? r.ac_id ?? '—'}
                        </td>
                        <td className="px-4 py-4 text-xs text-slate-600">{r.reporter_name ?? '—'}</td>
                        <td className="px-4 py-4 text-xs text-slate-600">
                          {r.work_order_id ? (
                            <>
                              <span className="font-mono">{r.work_order_id.slice(0, 8)}…</span>
                              {r.work_order_status ? ` · ${r.work_order_status}` : ''}
                            </>
                          ) : (
                            '—'
                          )}
                        </td>
                        <td className="px-4 py-4 text-xs text-slate-500">
                          {r.decided_at ? new Date(r.decided_at).toLocaleString() : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
