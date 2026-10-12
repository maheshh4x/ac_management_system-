'use client';

/**
 * Technician movement requests (Phase 5): list is `mine=1` from
 * /api/v1/movements, creation posts { ac_id, to_room_id, reason } with the
 * destination picked from the campus tree (Block → Floor → Room — no more
 * free text), and cancel/comment/complete run through the movement
 * actions. The server enforces requester/ownership rules; 409s refresh
 * the list with an explanation.
 */
import { useState } from 'react';
import { MoveRight, Plus, X, Loader2, Truck } from 'lucide-react';
import { useCampusTree } from '@/lib/api/campus';
import {
  isMovementConflict,
  movementErrorMessage,
  useAddMovementComment,
  useCreateMovement,
  useMovementAction,
  useMovements,
} from '@/lib/api/movements';
import { searchTechnicianAssets, TechnicianAsset } from '@/lib/technician-asset-service';

const statusStyle: Record<string, string> = { Pending: 'bg-amber-50 text-amber-700 border-amber-200', Approved: 'bg-blue-50 text-blue-700 border-blue-200', Rejected: 'bg-red-50 text-red-700 border-red-200', Completed: 'bg-emerald-50 text-emerald-700 border-emerald-200', Cancelled: 'bg-slate-50 text-slate-600 border-slate-200' };

export default function TechnicianMovementsPage() {
  const requests = useMovements({ mine: true });
  const create = useCreateMovement();
  const action = useMovementAction();
  const commentMutation = useAddMovementComment();

  const [showCreate, setShowCreate] = useState(false);
  const [assets, setAssets] = useState<TechnicianAsset[]>([]);
  const [assetQuery, setAssetQuery] = useState('');
  const [selectedAsset, setSelectedAsset] = useState<TechnicianAsset | null>(null);
  const [toBlockId, setToBlockId] = useState('');
  const [toFloorId, setToFloorId] = useState('');
  const [toRoomId, setToRoomId] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [listError, setListError] = useState('');
  const [commentId, setCommentId] = useState<string | null>(null);
  const [comment, setComment] = useState('');

  const treeQuery = useCampusTree(showCreate);
  const blocks = treeQuery.data
    ? [...treeQuery.data.colleges.flatMap(college => college.blocks), ...treeQuery.data.orphan_blocks]
    : [];
  const selectedBlock = blocks.find(block => block.block_id === toBlockId) ?? null;
  const selectedFloor = selectedBlock?.floors.find(floor => floor.floor_id === toFloorId) ?? null;

  const busy = create.isPending || action.isPending || commentMutation.isPending;

  const searchAssets = async (value: string) => { setAssetQuery(value); if (!value.trim()) return setAssets([]); try { setAssets(await searchTechnicianAssets(value)); } catch { setError('Unable to search assets.'); } };

  /** Server rejection: explain, and refresh a stale list on conflict. */
  const handleError = (err: unknown) => {
    const message = movementErrorMessage(err);
    setError(message);
    setListError(message);
    if (isMovementConflict(err)) void requests.refetch();
  };

  const resetForm = () => {
    setSelectedAsset(null); setAssetQuery(''); setAssets([]);
    setToBlockId(''); setToFloorId(''); setToRoomId(''); setReason(''); setError('');
  };

  const submit = () => {
    if (!selectedAsset || !toRoomId || !reason.trim()) return setError('Asset, destination room, and reason are required.');
    setError('');
    create.mutate(
      { acId: selectedAsset.acId ?? selectedAsset.id, toRoomId, reason: reason.trim() },
      {
        onSuccess: () => { resetForm(); setShowCreate(false); },
        onError: handleError,
      },
    );
  };

  const sendComment = (movementId: string) => {
    if (!comment.trim()) return;
    setListError('');
    commentMutation.mutate(
      { id: movementId, body: comment.trim() },
      {
        onSuccess: () => { setComment(''); setCommentId(null); },
        onError: handleError,
      },
    );
  };

  const cancelRequest = (movementId: string) => {
    setListError('');
    action.mutate(
      { id: movementId, action: 'cancel' },
      { onError: handleError },
    );
  };

  const completeMove = (movementId: string) => {
    setListError('');
    action.mutate(
      { id: movementId, action: 'complete' },
      { onError: handleError },
    );
  };

  return <div className="flex flex-col gap-5 pb-8"><div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-xl font-bold text-slate-900">Movement Requests</h1><p className="mt-1 text-sm text-slate-500">Request AC relocation and follow the approval status.</p></div><button onClick={() => setShowCreate(true)} className="flex min-h-11 items-center gap-2 rounded-xl bg-blue-600 px-3 text-sm font-semibold text-white hover:bg-blue-700"><Plus size={16} />New request</button></div>{listError && <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm font-semibold text-red-600">{listError}</p>}{requests.isError && !requests.isLoading && <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-600">Could not load your requests. <button onClick={() => requests.refetch()} className="font-semibold underline">Retry</button></p>}{requests.isLoading && <p className="py-6 text-center text-sm text-slate-500">Loading your movement requests…</p>}<div className="space-y-3">{requests.movements.map(request => <article key={request.id} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-sm font-bold text-blue-700">{request.id}</span><span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${statusStyle[request.status]}`}>{request.status}</span><span className="text-xs text-slate-400">{request.acId}</span></div><div className="mt-3 flex flex-col gap-2 text-xs sm:flex-row sm:items-center"><div className="flex-1 rounded-lg bg-slate-50 px-3 py-2"><p className="font-semibold uppercase text-slate-400">From</p><p className="mt-1 font-medium text-slate-700">{request.fromLocation || '—'}</p></div><MoveRight className="hidden shrink-0 text-blue-400 sm:block" size={17} /><div className="flex-1 rounded-lg bg-blue-50 px-3 py-2"><p className="font-semibold uppercase text-blue-400">To</p><p className="mt-1 font-medium text-slate-700">{request.toLocation || '—'}</p></div></div><p className="mt-3 text-sm text-slate-600">{request.reason}</p><p className="mt-2 text-xs text-slate-400">Submitted {request.requestDate ? new Date(request.requestDate).toLocaleString() : '—'}{request.approvedBy && ` · Approved by ${request.approvedBy}`}{request.completedBy && ` · Completed by ${request.completedBy}`}</p>{request.rejectionReason && <p className="mt-2 text-xs font-semibold text-red-600">{request.rejectionReason}</p>}{request.comments.length > 0 && <div className="mt-3 space-y-1">{request.comments.map(item => <p key={item.id} className="rounded-lg bg-slate-50 p-2 text-xs text-slate-600"><strong>{item.author}:</strong> {item.message}</p>)}</div>}{request.status === 'Pending' && <div className="mt-3 flex flex-wrap gap-2"><button onClick={() => setCommentId(commentId === request.id ? null : request.id)} className="min-h-11 rounded-lg border border-slate-200 px-3 text-xs font-semibold text-slate-700">Add comment</button><button disabled={busy} onClick={() => cancelRequest(request.id)} className="min-h-11 rounded-lg border border-red-200 px-3 text-xs font-semibold text-red-600 disabled:opacity-50">Cancel request</button></div>}{request.status === 'Approved' && <div className="mt-3 flex flex-wrap gap-2"><button disabled={busy} onClick={() => completeMove(request.id)} className="flex min-h-11 items-center gap-1.5 rounded-lg bg-blue-600 px-3 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-50">{action.isPending ? <Loader2 size={13} className="animate-spin" /> : <Truck size={13} />} Mark as completed</button></div>}{commentId === request.id && <div className="mt-3 flex gap-2"><input value={comment} onChange={event => setComment(event.target.value)} placeholder="Add a comment" className="min-h-11 min-w-0 flex-1 rounded-lg border border-slate-200 px-3 text-sm" /><button onClick={() => sendComment(request.id)} disabled={!comment.trim() || busy} className="min-h-11 rounded-lg bg-slate-900 px-3 text-xs font-semibold text-white disabled:opacity-50">Send</button></div>}</article>)}{!requests.isLoading && !requests.isError && !requests.movements.length && <div className="rounded-xl border border-dashed border-slate-300 bg-white px-5 py-14 text-center"><MoveRight size={34} className="mx-auto mb-3 text-slate-300" /><p className="font-semibold text-slate-700">No movement requests yet</p><p className="mt-1 text-sm text-slate-500">Create a request when an asset needs to be relocated.</p></div>}</div>{showCreate && <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/40 sm:items-center sm:p-4"><div className="max-h-[95vh] w-full overflow-y-auto rounded-t-2xl bg-white p-5 shadow-2xl sm:max-w-xl sm:rounded-2xl"><div className="mb-5 flex items-center justify-between"><h2 className="text-lg font-bold text-slate-900">New movement request</h2><button onClick={() => setShowCreate(false)} className="min-h-11 min-w-11 rounded-xl text-slate-500 hover:bg-slate-100" aria-label="Close"><X /></button></div>{error && <p className="mb-3 rounded-lg bg-red-50 p-3 text-sm font-semibold text-red-600">{error}</p>}<div className="space-y-4"><label className="block text-sm font-semibold text-slate-700">Asset<input value={assetQuery} onChange={event => { searchAssets(event.target.value); setSelectedAsset(null); }} placeholder="Search asset ID or serial" className="mt-1 min-h-11 w-full rounded-xl border border-slate-200 px-3 text-sm" />{assets.length > 0 && !selectedAsset && <div className="mt-1 max-h-36 overflow-y-auto rounded-xl border border-slate-200 bg-white">{assets.map(asset => <button type="button" key={asset.id} onClick={() => { setSelectedAsset(asset); setAssetQuery(asset.acId ?? asset.id); setAssets([]); }} className="block min-h-11 w-full border-b border-slate-100 px-3 text-left text-sm hover:bg-blue-50">{asset.id} – {asset.location.block} / {asset.location.room}</button>)}</div>}{selectedAsset && <p className="mt-2 text-xs text-emerald-700">From: {selectedAsset.location.block} / {selectedAsset.location.floor} / {selectedAsset.location.room}</p>}</label><div><p className="mb-2 text-sm font-semibold text-slate-700">Destination</p><div className="grid grid-cols-1 gap-2 sm:grid-cols-3"><select value={toBlockId} onChange={event => { setToBlockId(event.target.value); setToFloorId(''); setToRoomId(''); }} className="min-h-11 rounded-xl border border-slate-200 px-3 text-sm bg-white"><option value="">{treeQuery.isLoading ? 'Loading blocks…' : 'Block'}</option>{blocks.map(block => <option key={block.block_id} value={block.block_id}>{block.block_name}</option>)}</select><select value={toFloorId} onChange={event => { setToFloorId(event.target.value); setToRoomId(''); }} disabled={!selectedBlock} className="min-h-11 rounded-xl border border-slate-200 px-3 text-sm bg-white disabled:bg-slate-50 disabled:text-slate-400"><option value="">{selectedBlock ? 'Floor' : 'Pick a block'}</option>{selectedBlock?.floors.map(floor => <option key={floor.floor_id} value={floor.floor_id}>{floor.floor_name}</option>)}</select><select value={toRoomId} onChange={event => setToRoomId(event.target.value)} disabled={!selectedFloor} className="min-h-11 rounded-xl border border-slate-200 px-3 text-sm bg-white disabled:bg-slate-50 disabled:text-slate-400"><option value="">{selectedFloor ? 'Room' : 'Pick a floor'}</option>{selectedFloor?.rooms.map(room => <option key={room.room_id} value={room.room_id}>{room.room_name}</option>)}</select></div></div><label className="block text-sm font-semibold text-slate-700">Reason<textarea value={reason} onChange={event => setReason(event.target.value)} rows={4} className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm" /></label><button onClick={submit} disabled={busy} className="min-h-12 w-full rounded-xl bg-blue-600 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60">{create.isPending ? 'Submitting…' : 'Submit request'}</button></div></div></div>}</div>;
}
