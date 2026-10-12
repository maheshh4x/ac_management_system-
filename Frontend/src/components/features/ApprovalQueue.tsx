'use client';

/**
 * Signup approval queue (Phase 2): backed by /api/v1/approvals (Phase 1).
 * The server enforces the approver rules (manager: TECHNICIAN/VIEWER only,
 * no self-approval); the buttons below are UX only. Audit + notifications
 * happen server-side on approve/reject.
 */
import { useState } from 'react';
import { Check, X, Clock3, UserRound, Mail, Phone } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { ApiError } from '@/lib/api/client';
import { useApproveUser, useApprovals, useRejectUser } from '@/lib/api/users';
import { mapProfileToManagedUser, type ManagedUser } from '@/lib/adapters/user';
import type { Role } from '@/lib/permissions';

function roleLabel(role: Role | string | undefined): string {
  switch (role) {
    case 'FACILITY_MANAGER': return 'Manager';
    case 'SUPER_ADMIN': return 'Admin';
    case 'TECHNICIAN': return 'Technician';
    default: return 'Viewer';
  }
}

export default function ApprovalQueue() {
  const { user } = useAuth();
  const approvals = useApprovals('PENDING');
  const approveMutation = useApproveUser();
  const rejectMutation = useRejectUser();
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [pageError, setPageError] = useState('');

  const isManager = user?.role === 'FACILITY_MANAGER';
  const requests = (approvals.data?.items ?? []).map(mapProfileToManagedUser);

  const approve = (request: ManagedUser) => {
    setPageError('');
    approveMutation.mutate(
      { id: request.id },
      {
        onError: (err) =>
          setPageError(
            err instanceof ApiError && err.code === 'FORBIDDEN'
              ? 'You can only approve technician or viewer accounts within your assigned blocks.'
              : err instanceof Error ? err.message : 'Approval failed.',
          ),
      },
    );
  };

  const reject = (request: ManagedUser) => {
    if (!reason.trim()) {
      setPageError('A rejection reason is required.');
      return;
    }
    setPageError('');
    rejectMutation.mutate(
      { id: request.id, reason: reason.trim() },
      {
        onSuccess: () => {
          setRejectingId(null);
          setReason('');
        },
        onError: (err) =>
          setPageError(err instanceof Error ? err.message : 'Rejection failed.'),
      },
    );
  };

  const busy = approveMutation.isPending || rejectMutation.isPending;

  return (
    <div className="flex flex-col gap-6 pb-8">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Pending Approvals</h1>
        <p className="text-sm text-slate-500 mt-1">Review account requests. Admins can approve every role; Faculty Managers can approve Technician and Viewer requests.</p>
      </div>

      {pageError && <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{pageError}</p>}
      {approvals.isError && (
        <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">
          Could not load pending requests. {approvals.error instanceof Error ? approvals.error.message : ''}
        </p>
      )}
      {approvals.isLoading && <p className="text-sm text-slate-500">Loading pending requests…</p>}

      {!approvals.isLoading && requests.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-xl p-10 text-center text-slate-500">
          <Clock3 className="mx-auto mb-3 text-slate-300" size={32} />
          <p className="font-semibold text-slate-700">No pending account requests</p>
          <p className="text-sm mt-1">New requests will appear here for review.</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[850px] text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>{['Applicant', 'Contact', 'Requested Role', 'Staff / Designation', 'Requested', 'Actions'].map(heading => <th key={heading} className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">{heading}</th>)}</tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {requests.map(request => {
                  // Server decides: managers may only grant TECHNICIAN/VIEWER.
                  const managerRestricted =
                    isManager &&
                    (request.requestedRole ?? request.role) !== 'TECHNICIAN' &&
                    (request.requestedRole ?? request.role) !== 'VIEWER';
                  return (
                    <tr key={request.id} className="hover:bg-slate-50">
                      <td className="px-4 py-4"><p className="font-semibold text-slate-900">{request.name || '—'}</p></td>
                      <td className="px-4 py-4 text-slate-600"><p className="flex items-center gap-1.5"><Mail size={13} />{request.email}</p>{request.phone && <p className="flex items-center gap-1.5 mt-1"><Phone size={13} />{request.phone}</p>}</td>
                      <td className="px-4 py-4 text-slate-600"><span className="font-semibold">{roleLabel(request.requestedRole ?? request.role)}</span></td>
                      <td className="px-4 py-4 text-xs text-slate-500">
                        {request.staffId && <p><span className="font-semibold text-slate-700">Staff ID:</span> {request.staffId}</p>}
                        {request.designation && <p><span className="font-semibold text-slate-700">Designation:</span> {request.designation}</p>}
                        {!request.staffId && !request.designation && '—'}
                      </td>
                      <td className="px-4 py-4 text-slate-500 text-xs">{request.createdAt ? new Date(request.createdAt).toLocaleString() : '—'}</td>
                      <td className="px-4 py-4">
                        {rejectingId === request.id ? (
                          <div className="flex items-center gap-2">
                            <input value={reason} onChange={event => setReason(event.target.value)} placeholder="Reason (required)" className="w-40 border border-slate-200 rounded-md px-2 py-1.5 text-xs outline-none focus:ring-2 focus:ring-red-400" />
                            <button disabled={busy} onClick={() => reject(request)} className="p-1.5 rounded-md bg-red-600 text-white disabled:opacity-40" title="Confirm rejection"><Check size={14} /></button>
                            <button onClick={() => { setRejectingId(null); setReason(''); }} className="p-1.5 rounded-md hover:bg-slate-100 text-slate-500" title="Cancel"><X size={14} /></button>
                          </div>
                        ) : (
                          <div className="flex items-center gap-2">
                            <button disabled={busy || managerRestricted} onClick={() => approve(request)} className="px-2.5 py-1.5 rounded-md bg-emerald-600 text-white text-xs font-semibold hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed">Approve</button>
                            <button disabled={busy || managerRestricted} onClick={() => setRejectingId(request.id)} className="px-2.5 py-1.5 rounded-md border border-red-200 text-red-600 text-xs font-semibold hover:bg-red-50 disabled:opacity-40 disabled:cursor-not-allowed">Reject</button>
                          </div>
                        )}
                        {managerRestricted && <p className="mt-1 text-[11px] text-slate-400">Admin approval required</p>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <p className="text-xs text-slate-400 flex items-center gap-1.5"><UserRound size={12} /> Approvals and rejections are audited server-side and the applicant is notified automatically.</p>
    </div>
  );
}
