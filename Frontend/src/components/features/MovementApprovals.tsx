'use client';

/**
 * Movement approval section (Phase 5) — the movement half of the approvals
 * page (`ApprovalQueue` handles user signups only). Lists PENDING
 * relocation requests; approve shows a from → to confirmation, reject
 * captures a reason (server: 400 REASON_REQUIRED). The server enforces
 * approver rules (no self-approval, manager block scoping); on 409 the
 * list is refreshed with an explanation.
 */
import { useState } from 'react';
import { motion } from 'framer-motion';
import { CheckCircle2, Clock3, MoveRight, Truck, XCircle, X, Loader2 } from 'lucide-react';
import {
  isMovementConflict,
  movementErrorMessage,
  useMovementAction,
  useMovements,
} from '@/lib/api/movements';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import type { MovementRequest } from '@/lib/types/movement';

const STATUS_BADGE = 'bg-amber-50 text-amber-700 border-amber-200';

export function MovementApprovals() {
  const { movements, isLoading, isError, error, refetch } = useMovements({ status: 'PENDING' });
  const action = useMovementAction();

  const [pageError, setPageError] = useState('');
  const [confirmMov, setConfirmMov] = useState<MovementRequest | null>(null);
  const [rejectMov, setRejectMov] = useState<MovementRequest | null>(null);
  const [rejectReason, setRejectReason] = useState('');

  const busy = action.isPending;

  const handleError = (err: unknown) => {
    setPageError(movementErrorMessage(err));
    if (isMovementConflict(err)) void refetch();
  };

  const approve = (mov: MovementRequest) => {
    setPageError('');
    action.mutate(
      { id: mov.id, action: 'approve' },
      {
        onSuccess: () => setConfirmMov(null),
        onError: err => {
          handleError(err);
          setConfirmMov(null);
        },
      },
    );
  };

  const reject = (mov: MovementRequest) => {
    if (!rejectReason.trim()) return;
    setPageError('');
    action.mutate(
      { id: mov.id, action: 'reject', payload: { rejection_reason: rejectReason.trim() } },
      {
        onSuccess: () => {
          setRejectMov(null);
          setRejectReason('');
        },
        onError: err => {
          handleError(err);
          setRejectMov(null);
          setRejectReason('');
        },
      },
    );
  };

  return (
    <div className="flex flex-col gap-6 pb-8">
      <div>
        <h2 className="text-xl font-bold text-slate-900">Movement approvals</h2>
        <p className="text-sm text-slate-500 mt-1">Review requests to relocate AC units. Approvals are audited and the requester is notified automatically.</p>
      </div>

      {pageError && <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{pageError}</p>}
      {isError && (
        <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center justify-between gap-3">
          <span>Could not load pending movement requests. {error instanceof Error ? error.message : ''}</span>
          <button onClick={() => refetch()} className="px-3 py-1.5 rounded-lg bg-white border border-red-200 text-xs font-semibold">Retry</button>
        </p>
      )}
      {isLoading && <p className="text-sm text-slate-500">Loading pending movement requests…</p>}

      {!isLoading && !isError && movements.length === 0 ? (
        <div className="bg-white border border-slate-200 rounded-xl p-10 text-center text-slate-500">
          <Clock3 className="mx-auto mb-3 text-slate-300" size={32} />
          <p className="font-semibold text-slate-700">No pending movement requests</p>
          <p className="text-sm mt-1">New relocation requests will appear here for review.</p>
        </div>
      ) : (
        <ProtectedAction permission="APPROVE_MOVEMENT">
          <div className="grid gap-4">
            {movements.map((mov, i) => (
              <motion.div key={mov.id} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }} className="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
                <div className="flex items-start justify-between flex-wrap gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap"><span className="font-mono text-sm font-bold text-blue-700">{mov.id}</span><span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${STATUS_BADGE}`}>{mov.status}</span><span className="text-xs text-slate-400">AC: <strong className="text-slate-700">{mov.acId}</strong></span></div>
                    <div className="flex items-center gap-2 mt-2.5 text-xs flex-wrap"><div className="bg-slate-100 border border-slate-200 rounded-lg px-2.5 py-1.5 text-slate-700 flex-1 min-w-[110px]"><p className="text-[10px] uppercase text-slate-400 font-semibold tracking-wider mb-0.5">FROM</p><p className="font-medium">{mov.fromLocation || '—'}</p></div><MoveRight size={16} className="text-blue-500 flex-shrink-0" /><div className="bg-blue-50 border border-blue-100 rounded-lg px-2.5 py-1.5 text-blue-800 flex-1 min-w-[110px]"><p className="text-[10px] uppercase text-blue-400 font-semibold tracking-wider mb-0.5">TO</p><p className="font-medium">{mov.toLocation || '—'}</p></div></div>
                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 mt-2"><span>👤 {mov.requestedBy || 'Unknown'} ({(mov.requestedByRole || 'TECHNICIAN').replace('_', ' ')})</span><span>📅 {mov.requestDate ? new Date(mov.requestDate).toLocaleDateString() : '—'}</span></div>
                    <p className="text-xs text-slate-600 mt-1.5 italic">&quot;{mov.reason}&quot;</p>
                  </div>
                  <div className="flex flex-col gap-2 flex-shrink-0">
                    <button disabled={busy} onClick={() => setConfirmMov(mov)} className="px-3 py-1.5 rounded-lg bg-emerald-100 hover:bg-emerald-200 text-emerald-700 text-xs font-medium flex items-center gap-1.5 disabled:opacity-50"><CheckCircle2 size={13} /> Approve</button>
                    <button disabled={busy} onClick={() => { setRejectMov(mov); setRejectReason(''); }} className="px-3 py-1.5 rounded-lg bg-red-100 hover:bg-red-200 text-red-700 text-xs font-medium flex items-center gap-1.5 disabled:opacity-50"><XCircle size={13} /> Reject</button>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>
        </ProtectedAction>
      )}

      {/* Approve confirmation — from → to room */}
      {confirmMov && <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"><motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden"><div className="p-5 bg-slate-50 border-b border-slate-100 flex items-center justify-between"><h3 className="font-bold text-lg">Approve movement</h3><button onClick={() => setConfirmMov(null)} className="p-2 rounded-full hover:bg-slate-200"><X size={18} /></button></div><div className="p-6 space-y-3 text-sm"><p className="text-slate-600">Asset <strong className="font-mono text-slate-900">{confirmMov.acId}</strong> will move:</p><div className="flex items-center gap-2 text-xs"><div className="flex-1 rounded-lg bg-slate-50 border border-slate-200 p-2.5"><p className="text-[10px] uppercase font-semibold text-slate-400 mb-0.5">From</p><p className="font-medium text-slate-700">{confirmMov.fromLocation || '—'}</p></div><MoveRight size={16} className="text-blue-500 shrink-0" /><div className="flex-1 rounded-lg bg-blue-50 border border-blue-100 p-2.5"><p className="text-[10px] uppercase font-semibold text-blue-400 mb-0.5">To</p><p className="font-medium text-blue-800">{confirmMov.toLocation || '—'}</p></div></div><p className="text-xs text-slate-500 italic">&quot;{confirmMov.reason}&quot;</p></div><div className="p-5 border-t border-slate-100 flex justify-end gap-3"><button onClick={() => setConfirmMov(null)} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium">Cancel</button><button onClick={() => approve(confirmMov)} disabled={busy} className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm font-medium flex items-center gap-1.5 disabled:opacity-60">{busy ? <Loader2 size={14} className="animate-spin" /> : <CheckCircle2 size={14} />} Confirm approval</button></div></motion.div></div>}

      {/* Reject with required reason */}
      {rejectMov && <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"><motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden"><div className="p-5 bg-red-50 border-b border-red-100"><h3 className="font-bold text-lg text-red-800">Reject Request {rejectMov.id}</h3><p className="text-xs text-red-600 mt-1">{rejectMov.acId}: {rejectMov.fromLocation} → {rejectMov.toLocation}</p></div><div className="p-6"><label className="text-xs font-semibold text-slate-500 uppercase">Rejection Reason (required)</label><textarea autoFocus className="mt-2 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm" rows={3} value={rejectReason} onChange={e => setRejectReason(e.target.value)} placeholder="Explain why this request is being rejected..." /></div><div className="p-5 border-t border-slate-100 flex justify-end gap-3"><button onClick={() => { setRejectMov(null); setRejectReason(''); }} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium">Cancel</button><button onClick={() => reject(rejectMov)} disabled={!rejectReason.trim() || busy} className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-1.5">{busy ? <Loader2 size={14} className="animate-spin" /> : <XCircle size={14} />} Confirm Rejection</button></div></motion.div></div>}

      <p className="text-xs text-slate-400 flex items-center gap-1.5"><Truck size={12} /> Completing an approved move updates the asset&apos;s room and its location history in one transaction.</p>
    </div>
  );
}
