'use client';

/**
 * Movement requests panel (admin/manager — renders inside
 * MaintenanceMovementShell on `maintenance?tab=movement`). Phase 5: all
 * data through /api/v1/movements (the localStorage movement-data-service
 * is gone). The server enforces every rule; the dialogs below are UX —
 * approve/complete confirm with from → to room, reject captures a reason,
 * and any 409 refreshes the list with an explanation.
 */
import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { MoveRight, Plus, Search, CheckCircle2, XCircle, Truck, X, Eye, Loader2, MapPin } from 'lucide-react';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import { useAssets } from '@/lib/api/assets';
import { useCampusTree } from '@/lib/api/campus';
import {
  isMovementConflict,
  movementErrorMessage,
  useCreateMovement,
  useMovementAction,
  useMovements,
} from '@/lib/api/movements';
import type { ExtendedAC } from '@/lib/types/asset';
import type { MovementRequest } from '@/lib/types/movement';

const STATUS_COLORS: Record<string, string> = {
  Pending: 'bg-amber-100 text-amber-700 border-amber-200',
  Approved: 'bg-blue-100 text-blue-700 border-blue-200',
  Rejected: 'bg-red-100 text-red-700 border-red-200',
  Completed: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  Cancelled: 'bg-slate-100 text-slate-600 border-slate-200',
};

function fromLocationOf(asset: ExtendedAC): string {
  return [asset.buildingName, asset.floorName, asset.roomName].filter(Boolean).join(' / ');
}

type ConfirmState = { type: 'approve' | 'complete'; mov: MovementRequest } | null;

export function MovementPanel() {
  const { movements, isLoading, isError, error, refetch } = useMovements();
  const action = useMovementAction();
  const create = useCreateMovement();

  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('All');
  const [pageError, setPageError] = useState('');
  const [viewMov, setViewMov] = useState<MovementRequest | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState>(null);
  const [rejectMov, setRejectMov] = useState<MovementRequest | null>(null);
  const [rejectReason, setRejectReason] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [assetQuery, setAssetQuery] = useState('');
  const [selectedAsset, setSelectedAsset] = useState<ExtendedAC | null>(null);
  const [toBlockId, setToBlockId] = useState('');
  const [toFloorId, setToFloorId] = useState('');
  const [toRoomId, setToRoomId] = useState('');
  const [reason, setReason] = useState('');

  // Create modal sources: asset search + Block → Floor → Room from the tree.
  const assetResults = useAssets(
    { search: assetQuery, pageSize: 8 },
    showCreate && assetQuery.trim() !== '' && selectedAsset === null,
  );
  const treeQuery = useCampusTree(showCreate);
  const blocks = React.useMemo(() => {
    const tree = treeQuery.data;
    if (!tree) return [];
    return [...tree.colleges.flatMap(college => college.blocks), ...tree.orphan_blocks];
  }, [treeQuery.data]);
  const selectedBlock = blocks.find(block => block.block_id === toBlockId) ?? null;
  const selectedFloor = selectedBlock?.floors.find(floor => floor.floor_id === toFloorId) ?? null;

  const filtered = movements.filter(m => {
    const matchSearch = [m.id, m.acId, m.fromLocation, m.toLocation, m.requestedBy].join(' ').toLowerCase().includes(search.toLowerCase());
    return matchSearch && (statusFilter === 'All' || m.status === statusFilter);
  });

  const stats = {
    pending: movements.filter(m => m.status === 'Pending').length,
    approved: movements.filter(m => m.status === 'Approved').length,
    completed: movements.filter(m => m.status === 'Completed').length,
  };

  const busy = action.isPending || create.isPending;

  /** Server rejection: explain it, and on 409 refresh the stale list. */
  const handleActionError = (err: unknown) => {
    setPageError(movementErrorMessage(err));
    if (isMovementConflict(err)) void refetch();
  };

  const runConfirm = () => {
    if (!confirm) return;
    setPageError('');
    action.mutate(
      { id: confirm.mov.id, action: confirm.type },
      {
        onSuccess: () => setConfirm(null),
        onError: err => {
          handleActionError(err);
          setConfirm(null);
        },
      },
    );
  };

  const runReject = () => {
    if (!rejectMov || !rejectReason.trim()) return;
    setPageError('');
    action.mutate(
      { id: rejectMov.id, action: 'reject', payload: { rejection_reason: rejectReason.trim() } },
      {
        onSuccess: () => {
          setRejectMov(null);
          setRejectReason('');
        },
        onError: err => {
          handleActionError(err);
          setRejectMov(null);
          setRejectReason('');
        },
      },
    );
  };

  const closeCreate = () => {
    setShowCreate(false);
    setAssetQuery('');
    setSelectedAsset(null);
    setToBlockId('');
    setToFloorId('');
    setToRoomId('');
    setReason('');
  };

  const submitCreate = () => {
    if (!selectedAsset) {
      setPageError('Choose the AC unit to move.');
      return;
    }
    if (!toRoomId) {
      setPageError('Choose the destination room.');
      return;
    }
    if (!reason.trim()) {
      setPageError('A reason is required.');
      return;
    }
    setPageError('');
    create.mutate(
      { acId: selectedAsset.acId ?? selectedAsset.id, toRoomId, reason: reason.trim() },
      {
        onSuccess: closeCreate,
        onError: handleActionError,
      },
    );
  };

  return (
    <div className="flex flex-col gap-6 pb-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">AC Movement Requests</h1>
          <p className="text-sm text-slate-500 mt-1">Review and approve requests to relocate AC units across campus.</p>
        </div>
        <ProtectedAction permission="CREATE_MOVEMENT_REQUEST">
          <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium shadow-sm transition-colors"><Plus size={16} /> New Request</button>
        </ProtectedAction>
      </div>

      {pageError && <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3">{pageError}</p>}

      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Awaiting Approval', count: stats.pending, color: 'text-amber-600', bg: 'bg-amber-50 border-amber-100' },
          { label: 'Approved', count: stats.approved, color: 'text-blue-600', bg: 'bg-blue-50 border-blue-100' },
          { label: 'Completed', count: stats.completed, color: 'text-emerald-600', bg: 'bg-emerald-50 border-emerald-100' },
        ].map(item => <div key={item.label} className={`rounded-xl border p-4 ${item.bg}`}><p className={`text-2xl font-bold ${item.color}`}>{item.count}</p><p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mt-1">{item.label}</p></div>)}
      </div>

      <div className="flex gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[200px]"><Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><input type="text" placeholder="Search by request ID, AC, requester..." className="w-full pl-10 pr-4 py-2.5 border border-slate-200 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none" value={search} onChange={e => setSearch(e.target.value)} /></div>
        <div className="flex gap-2 flex-wrap">{['All', 'Pending', 'Approved', 'Rejected', 'Completed', 'Cancelled'].map(s => <button key={s} onClick={() => setStatusFilter(s)} className={`px-3 py-2 rounded-lg text-xs font-semibold border transition-all ${statusFilter === s ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-600 border-slate-200 hover:border-slate-400'}`}>{s}</button>)}</div>
      </div>

      {isLoading && <p className="text-sm text-slate-500 py-6 text-center">Loading movement requests…</p>}
      {isError && !isLoading && (
        <p className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg p-3 flex items-center justify-between gap-3">
          <span>Could not load movement requests. {error instanceof Error ? error.message : ''}</span>
          <button onClick={() => refetch()} className="px-3 py-1.5 rounded-lg bg-white border border-red-200 text-xs font-semibold">Retry</button>
        </p>
      )}

      <div className="grid gap-4">
        {filtered.map((mov, i) => (
          <motion.div key={mov.id} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.06 }} className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
            <div className="flex items-start justify-between flex-wrap gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap mb-2"><span className="font-mono text-sm font-bold text-blue-700">{mov.id}</span><span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${STATUS_COLORS[mov.status]}`}>{mov.status}</span><span className="text-xs text-slate-400">• AC: <strong className="text-slate-700">{mov.acId}</strong></span></div>
                <div className="flex items-center gap-2 mt-3 text-sm flex-wrap"><div className="bg-slate-100 border border-slate-200 rounded-lg px-3 py-1.5 text-slate-700 flex-1 min-w-[120px]"><p className="text-[10px] uppercase text-slate-400 font-semibold tracking-wider mb-0.5">FROM</p><p className="font-medium text-xs">{mov.fromLocation || '—'}</p></div><MoveRight size={20} className="text-blue-500 flex-shrink-0" /><div className="bg-blue-50 border border-blue-100 rounded-lg px-3 py-1.5 text-blue-800 flex-1 min-w-[120px]"><p className="text-[10px] uppercase text-blue-400 font-semibold tracking-wider mb-0.5">TO</p><p className="font-medium text-xs">{mov.toLocation || '—'}</p></div></div>
                <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 mt-3"><span>👤 {mov.requestedBy || 'Unknown'} ({(mov.requestedByRole || 'TECHNICIAN').replace('_', ' ')})</span><span>📅 {mov.requestDate ? new Date(mov.requestDate).toLocaleDateString() : '—'}</span>{mov.approvedBy && <span>✅ {mov.approvedBy}</span>}{mov.completedBy && <span>🚚 {mov.completedBy}</span>}</div>
                <p className="text-xs text-slate-600 mt-2 italic">&quot;{mov.reason}&quot;</p>{mov.rejectionReason && <p className="text-xs text-red-600 mt-1 font-medium">⚠️ Rejection: {mov.rejectionReason}</p>}
              </div>
              <div className="flex flex-col gap-2 flex-shrink-0">
                <button onClick={() => setViewMov(mov)} className="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-medium flex items-center gap-1.5"><Eye size={13} /> View</button>
                <ProtectedAction permission="APPROVE_MOVEMENT">
                  {mov.status === 'Pending' && <>
                    <button disabled={busy} onClick={() => setConfirm({ type: 'approve', mov })} className="px-3 py-1.5 rounded-lg bg-emerald-100 hover:bg-emerald-200 text-emerald-700 text-xs font-medium flex items-center gap-1.5 disabled:opacity-50"><CheckCircle2 size={13} /> Approve</button>
                    <button disabled={busy} onClick={() => { setRejectMov(mov); setRejectReason(''); }} className="px-3 py-1.5 rounded-lg bg-red-100 hover:bg-red-200 text-red-700 text-xs font-medium flex items-center gap-1.5 disabled:opacity-50"><XCircle size={13} /> Reject</button>
                  </>}
                </ProtectedAction>
                <ProtectedAction permission="COMPLETE_MOVEMENT">
                  {mov.status === 'Approved' && <button disabled={busy} onClick={() => setConfirm({ type: 'complete', mov })} className="px-3 py-1.5 rounded-lg bg-blue-100 hover:bg-blue-200 text-blue-700 text-xs font-medium flex items-center gap-1.5 disabled:opacity-50"><Truck size={13} /> Complete move</button>}
                </ProtectedAction>
              </div>
            </div>
          </motion.div>
        ))}
        {!isLoading && !isError && filtered.length === 0 && <div className="text-center py-16 text-slate-400 bg-white rounded-xl border border-slate-200"><Truck size={36} className="mx-auto mb-3 opacity-30" /><p className="font-medium">No movement requests found.</p></div>}
      </div>

      {/* Approve / Complete confirmation — always shows from → to room */}
      {confirm && <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"><motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden"><div className="p-5 bg-slate-50 border-b border-slate-100 flex items-center justify-between"><h3 className="font-bold text-lg">{confirm.type === 'approve' ? 'Approve movement' : 'Complete move'}</h3><button onClick={() => setConfirm(null)} className="p-2 rounded-full hover:bg-slate-200"><X size={18} /></button></div><div className="p-6 space-y-3 text-sm"><p className="text-slate-600">Asset <strong className="font-mono text-slate-900">{confirm.mov.acId}</strong> will move:</p><div className="flex items-center gap-2 text-xs"><div className="flex-1 rounded-lg bg-slate-50 border border-slate-200 p-2.5"><p className="text-[10px] uppercase font-semibold text-slate-400 mb-0.5">From</p><p className="font-medium text-slate-700">{confirm.mov.fromLocation || '—'}</p></div><MoveRight size={16} className="text-blue-500 shrink-0" /><div className="flex-1 rounded-lg bg-blue-50 border border-blue-100 p-2.5"><p className="text-[10px] uppercase font-semibold text-blue-400 mb-0.5">To</p><p className="font-medium text-blue-800">{confirm.mov.toLocation || '—'}</p></div></div><p className="text-xs text-slate-500 italic">&quot;{confirm.mov.reason}&quot;</p>{confirm.type === 'complete' && <p className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-lg p-2.5">The asset&apos;s room is updated atomically with its location history when the move completes.</p>}</div><div className="p-5 border-t border-slate-100 flex justify-end gap-3"><button onClick={() => setConfirm(null)} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium">Cancel</button><button onClick={runConfirm} disabled={busy} className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm font-medium flex items-center gap-1.5 disabled:opacity-60">{busy ? <Loader2 size={14} className="animate-spin" /> : confirm.type === 'approve' ? <CheckCircle2 size={14} /> : <Truck size={14} />}{confirm.type === 'approve' ? 'Confirm approval' : 'Confirm completion'}</button></div></motion.div></div>}

      {rejectMov && <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"><motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden"><div className="p-5 bg-red-50 border-b border-red-100"><h3 className="font-bold text-lg text-red-800">Reject Request {rejectMov.id}</h3><p className="text-xs text-red-600 mt-1">{rejectMov.acId}: {rejectMov.fromLocation} → {rejectMov.toLocation}</p></div><div className="p-6"><label className="text-xs font-semibold text-slate-500 uppercase">Rejection Reason (required)</label><textarea autoFocus className="mt-2 w-full border border-slate-200 rounded-lg px-3 py-2 text-sm" rows={3} value={rejectReason} onChange={e => setRejectReason(e.target.value)} placeholder="Explain why this request is being rejected..." /></div><div className="p-5 border-t border-slate-100 flex justify-end gap-3"><button onClick={() => { setRejectMov(null); setRejectReason(''); }} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium">Cancel</button><button onClick={runReject} disabled={!rejectReason.trim() || busy} className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-1.5">{busy ? <Loader2 size={14} className="animate-spin" /> : <XCircle size={14} />} Confirm Rejection</button></div></motion.div></div>}

      {/* Detail view — request metadata + movement_comments thread */}
      {viewMov && <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"><motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden"><div className="p-5 bg-slate-50 border-b border-slate-100 flex items-center justify-between"><div className="flex items-center gap-2"><h3 className="font-bold text-lg font-mono">{viewMov.id}</h3><span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${STATUS_COLORS[viewMov.status]}`}>{viewMov.status}</span></div><button onClick={() => setViewMov(null)} className="p-2 rounded-full hover:bg-slate-200"><X size={18} /></button></div><div className="p-6 space-y-4 text-sm max-h-[60vh] overflow-y-auto"><div className="grid grid-cols-2 gap-3 text-xs"><div><p className="text-[10px] uppercase font-semibold text-slate-400">Asset</p><p className="font-medium text-slate-800 font-mono">{viewMov.acId}</p>{viewMov.assetCode && <p className="text-slate-500">{viewMov.assetMake} {viewMov.assetModel}</p>}</div><div><p className="text-[10px] uppercase font-semibold text-slate-400">Requested by</p><p className="font-medium text-slate-800">{viewMov.requestedBy || 'Unknown'}</p><p className="text-slate-500">{viewMov.requestDate ? new Date(viewMov.requestDate).toLocaleString() : '—'}</p></div></div><div className="flex items-center gap-2 text-xs"><div className="flex-1 rounded-lg bg-slate-50 border border-slate-200 p-2.5"><p className="text-[10px] uppercase font-semibold text-slate-400 mb-0.5">From</p><p className="font-medium text-slate-700">{viewMov.fromLocation || '—'}</p></div><MoveRight size={16} className="text-blue-500 shrink-0" /><div className="flex-1 rounded-lg bg-blue-50 border border-blue-100 p-2.5"><p className="text-[10px] uppercase font-semibold text-blue-400 mb-0.5">To</p><p className="font-medium text-blue-800">{viewMov.toLocation || '—'}</p></div></div><div><p className="text-[10px] uppercase font-semibold text-slate-400 mb-1">Reason</p><p className="text-slate-600 italic">&quot;{viewMov.reason}&quot;</p></div>{viewMov.approvedBy && <p className="text-xs text-slate-500">Approved by {viewMov.approvedBy}{viewMov.approvedDate ? ` on ${new Date(viewMov.approvedDate).toLocaleString()}` : ''}</p>}{viewMov.completedBy && <p className="text-xs text-slate-500">Completed by {viewMov.completedBy}{viewMov.completedDate ? ` on ${new Date(viewMov.completedDate).toLocaleString()}` : ''}</p>}{viewMov.rejectionReason && <p className="text-xs text-red-600 font-medium">Rejection: {viewMov.rejectionReason}</p>}<div><p className="text-[10px] uppercase font-semibold text-slate-400 mb-1">Comments ({viewMov.comments.length})</p>{viewMov.comments.length ? <div className="space-y-1.5">{viewMov.comments.map(item => <p key={item.id} className="rounded-lg bg-slate-50 p-2.5 text-xs text-slate-600"><strong>{item.author}:</strong> {item.message}</p>)}</div> : <p className="text-xs text-slate-400">No comments yet.</p>}</div></div><div className="p-5 border-t border-slate-100 flex justify-end"><button onClick={() => setViewMov(null)} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium">Close</button></div></motion.div></div>}

      {/* Create: asset search + campus Block → Floor → Room + reason */}
      {showCreate && <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4"><motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden"><div className="p-5 bg-slate-50 border-b border-slate-100 flex items-center justify-between"><h3 className="font-bold text-lg">Create Movement Request</h3><button onClick={closeCreate} className="p-2 rounded-full hover:bg-slate-200"><X size={18} /></button></div><div className="p-6 space-y-4 text-sm max-h-[65vh] overflow-y-auto">
        <div className="relative">
          <label className="text-xs font-semibold text-slate-500 uppercase">AC Unit</label>
          <input
            className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2"
            placeholder="Search by asset code, serial, make..."
            value={assetQuery}
            onChange={e => { setAssetQuery(e.target.value); setSelectedAsset(null); }}
          />
          {!selectedAsset && (assetResults.data?.items.length ?? 0) > 0 && (
            <div className="mt-1 max-h-40 overflow-y-auto rounded-lg border border-slate-200 bg-white">
              {assetResults.data?.items.map(asset => (
                <button
                  key={asset.id}
                  type="button"
                  onClick={() => { setSelectedAsset(asset); setAssetQuery(asset.acId ?? asset.id); }}
                  className="block w-full border-b border-slate-100 px-3 py-2 text-left text-xs hover:bg-blue-50"
                >
                  <span className="font-mono font-semibold text-slate-800">{asset.acId ?? asset.id}</span>
                  <span className="text-slate-500"> — {asset.make} {asset.model}</span>
                  <span className="block text-slate-400">{fromLocationOf(asset) || 'No room recorded'}</span>
                </button>
              ))}
            </div>
          )}
          {selectedAsset && (
            <p className="mt-2 text-xs text-emerald-700 flex items-center gap-1.5"><MapPin size={12} /> From: {fromLocationOf(selectedAsset) || 'No room recorded'}</p>
          )}
        </div>
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase">Destination</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 mt-1">
            <select value={toBlockId} onChange={e => { setToBlockId(e.target.value); setToFloorId(''); setToRoomId(''); }} className="border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white">
              <option value="">{treeQuery.isLoading ? 'Loading blocks…' : 'Block'}</option>
              {blocks.map(block => <option key={block.block_id} value={block.block_id}>{block.block_name}</option>)}
            </select>
            <select value={toFloorId} onChange={e => { setToFloorId(e.target.value); setToRoomId(''); }} disabled={!selectedBlock} className="border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white disabled:bg-slate-50 disabled:text-slate-400">
              <option value="">{selectedBlock ? 'Floor' : 'Pick a block'}</option>
              {selectedBlock?.floors.map(floor => <option key={floor.floor_id} value={floor.floor_id}>{floor.floor_name}</option>)}
            </select>
            <select value={toRoomId} onChange={e => setToRoomId(e.target.value)} disabled={!selectedFloor} className="border border-slate-200 rounded-lg px-3 py-2 text-sm bg-white disabled:bg-slate-50 disabled:text-slate-400">
              <option value="">{selectedFloor ? 'Room' : 'Pick a floor'}</option>
              {selectedFloor?.rooms.map(room => <option key={room.room_id} value={room.room_id}>{room.room_name}</option>)}
            </select>
          </div>
        </div>
        <div><label className="text-xs font-semibold text-slate-500 uppercase">Reason</label><textarea className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2" rows={3} value={reason} onChange={e => setReason(e.target.value)} placeholder="Explain why this AC needs to be moved..." /></div>
      </div><div className="p-5 border-t border-slate-100 flex justify-end gap-3"><button onClick={closeCreate} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium">Cancel</button><button onClick={submitCreate} disabled={busy} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium flex items-center gap-1.5 disabled:opacity-60">{busy ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Submit Request</button></div></motion.div></div>}
    </div>
  );
}
