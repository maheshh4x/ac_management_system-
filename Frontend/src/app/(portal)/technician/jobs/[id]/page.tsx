'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { AlertTriangle, ArrowLeft, CheckCircle2, ChevronRight, Clock3, Loader2, MapPin, Plus, Save } from 'lucide-react';
import { useAddComment, useJob, useJobAction } from '@/lib/api/maintenance';
import { useAsset } from '@/lib/api/assets';
import type { JobPart } from '@/lib/types/maintenance';

type AssetDetails = { id: string; location: { block: string; floor: string; room: string }; details: { make: string; model: string; serialNumber: string; capacity: string; type: string }; technical: { status: string } };

export default function TechnicianJobDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const jobId = decodeURIComponent(params.id);

  // Server-backed job (detail includes the maintenance_events timeline and
  // comments) plus the asset panel data.
  const { data: detail, isLoading } = useJob(jobId);
  const job = detail?.job;
  const events = detail?.events ?? [];
  const comments = detail?.comments ?? [];

  const { data: assetRow } = useAsset(job?.acId ?? null);
  const asset = useMemo<AssetDetails | null>(() => {
    if (!assetRow) return null;
    return {
      id: assetRow.id,
      location: { block: assetRow.buildingName ?? '', floor: assetRow.floorName ?? '', room: assetRow.roomName ?? '' },
      details: { make: assetRow.make, model: assetRow.model, serialNumber: assetRow.serialNumber, capacity: assetRow.capacity, type: assetRow.type },
      technical: { status: assetRow.status },
    };
  }, [assetRow]);

  const runAction = useJobAction();
  const addComment = useAddComment();

  const [comment, setComment] = useState('');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [showCompletion, setShowCompletion] = useState(false);
  const [showEscalation, setShowEscalation] = useState(false);
  const [workPerformed, setWorkPerformed] = useState('');
  const [timeSpent, setTimeSpent] = useState('');
  const [parts, setParts] = useState<JobPart[]>([]);
  const [escalationReason, setEscalationReason] = useState('');

  if (isLoading) return <div className="mx-auto flex min-h-[60vh] max-w-xl items-center justify-center"><Loader2 className="animate-spin text-slate-400" size={32} /></div>;

  if (!job) return <div className="mx-auto flex min-h-[60vh] max-w-xl flex-col items-center justify-center text-center"><AlertTriangle className="mb-3 text-slate-300" size={42} /><h1 className="text-xl font-bold text-slate-800">Job not found</h1><p className="mt-1 text-sm text-slate-500">This job does not exist or is not visible to your technician account.</p><Link href="/technician/jobs" className="mt-5 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Back to My Jobs</Link></div>;

  // Next legal action per the server state machine (UX gating only — the
  // backend re-validates every transition).
  const isAssignedNotAccepted = job.status === 'Accepted' && !job.acceptedAt;
  const canStart = job.status === 'Accepted' && Boolean(job.acceptedAt);
  const canComplete = job.status === 'In Progress';
  const canEscalate = job.status === 'Accepted' || job.status === 'In Progress';
  const statusBadge = isAssignedNotAccepted ? 'Assigned' : job.status;

  const submitStatus = (next: 'Accepted' | 'In Progress') => {
    setError('');
    setMessage('');
    runAction.mutate(
      { id: job.id, action: next === 'Accepted' ? 'accept' : 'start', label: next === 'Accepted' ? 'Accept job' : 'Start job' },
      {
        onSuccess: (result) => setMessage(result.queued ? "You're offline — the change is queued and will sync when you're back online." : `Job marked ${next}.`),
        onError: (actionError) => setError(actionError instanceof Error ? actionError.message : 'Unable to update job.'),
      },
    );
  };

  const submitCompletion = () => {
    setError('');
    setMessage('');
    if (!workPerformed.trim() || !timeSpent || Number(timeSpent) <= 0) return setError('Work performed and a positive time duration are required.');
    const validParts = parts.filter(part => part.name.trim() && part.quantity > 0);
    runAction.mutate(
      {
        id: job.id,
        action: 'complete',
        label: 'Complete job',
        payload: {
          work_performed: workPerformed.trim(),
          labor_hours: Number(timeSpent) / 60,
          ...(validParts.length ? { parts_used: validParts.map(part => `${part.name} × ${part.quantity}`).join(', ') } : {}),
        },
      },
      {
        onSuccess: (result) => { setShowCompletion(false); setMessage(result.queued ? "You're offline — the completion is queued for sync." : 'Job completed.'); },
        onError: (actionError) => setError(actionError instanceof Error ? actionError.message : 'Unable to complete job.'),
      },
    );
  };

  const submitEscalation = () => {
    setError('');
    setMessage('');
    if (!escalationReason.trim()) return setError('An escalation reason is required.');
    runAction.mutate(
      { id: job.id, action: 'escalate', payload: { reason: escalationReason.trim() }, label: 'Escalate job' },
      {
        onSuccess: (result) => { setShowEscalation(false); setEscalationReason(''); setMessage(result.queued ? "You're offline — the escalation is queued for sync." : 'Escalation sent to administrators and facility managers.'); },
        onError: (actionError) => setError(actionError instanceof Error ? actionError.message : 'Unable to escalate job.'),
      },
    );
  };

  const submitComment = () => {
    if (!comment.trim()) return;
    addComment.mutate({ id: job.id, body: comment.trim() }, {
      onSuccess: () => setComment(''),
      onError: (commentError) => setError(commentError instanceof Error ? commentError.message : 'Unable to save your note.'),
    });
  };

  return <div className="mx-auto flex max-w-3xl flex-col gap-5 pb-10"><div className="flex flex-wrap items-center gap-2 text-sm text-slate-500"><button onClick={() => router.back()} className="flex min-h-11 items-center gap-1.5 font-medium hover:text-blue-600"><ArrowLeft size={16} />Back</button><ChevronRight size={14} className="text-slate-300" /><span className="font-semibold text-slate-900">{job.id}</span></div><div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="font-mono text-sm font-bold text-blue-700">{job.id}</p><h1 className="mt-1 text-xl font-bold text-slate-900">{job.description}</h1></div><span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-bold text-blue-700">{statusBadge}</span></div><div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-4"><Info label="Asset" value={job.acId} /><Info label="Type" value={asset?.details.type ?? job.type} /><Info label="Capacity" value={asset?.details.capacity ?? 'Not available'} /><Info label="Priority" value={job.priority} /></div><div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2"><Info label="Location" value={asset ? `${asset.location.block} / ${asset.location.floor} / ${asset.location.room}` : job.acLocation} icon={<MapPin size={14} />} /><Info label="Due date / SLA" value={job.dueDate ?? job.scheduledDate} icon={<Clock3 size={14} />} /><Info label="Assigned date" value={job.assignedAt ?? 'Not recorded'} /><Info label="Current asset status" value={asset?.technical.status ?? 'Not available'} /></div></div>{(error || message) && <div className={`rounded-xl border p-3 text-sm font-semibold ${error ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>{error || message}</div>}<div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-bold text-slate-900">Status update</h2><p className="mt-1 text-sm text-slate-500">Every transition is recorded on the server timeline with who, when and from → to.</p><div className="mt-3 flex flex-wrap gap-2">{isAssignedNotAccepted && <button disabled={runAction.isPending} onClick={() => submitStatus('Accepted')} className="min-h-11 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white disabled:opacity-60">Accept job</button>}{canStart && <button disabled={runAction.isPending} onClick={() => submitStatus('In Progress')} className="min-h-11 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white disabled:opacity-60">Start work</button>}{canComplete && <button disabled={runAction.isPending} onClick={() => setShowCompletion(true)} className="flex min-h-11 items-center gap-2 rounded-xl bg-emerald-600 px-4 text-sm font-semibold text-white disabled:opacity-60"><CheckCircle2 size={16} />Complete job</button>}{canEscalate && <button disabled={runAction.isPending} onClick={() => setShowEscalation(true)} className="flex min-h-11 items-center gap-2 rounded-xl border border-orange-200 bg-orange-50 px-4 text-sm font-semibold text-orange-700 disabled:opacity-60"><AlertTriangle size={16} />Escalate</button>}{job.status === 'Pending' && <p className="flex min-h-11 items-center text-sm font-medium text-slate-500">Waiting for a manager to assign this job to a technician.</p>}{job.status === 'Cancelled' && <p className="flex min-h-11 items-center text-sm font-medium text-slate-500">This job was cancelled.</p>}</div></div><div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-bold text-slate-900">Notes & history</h2>{job.notes && <p className="mt-3 rounded-xl bg-slate-50 p-3 text-sm text-slate-600">{job.notes}</p>}{events.length ? <div className="mt-3 space-y-3">{events.map(item => <div key={item.id} className="border-l-2 border-blue-200 pl-3"><p className="text-sm font-semibold text-slate-800">{item.event}{item.to_status ? ` · ${item.to_status}` : ''}</p>{item.note && <p className="text-sm text-slate-600">{item.note}</p>}<p className="mt-1 text-xs text-slate-400">{new Date(item.created_at).toLocaleString()}{item.actor_name ? ` · ${item.actor_name}` : ''}</p></div>)}</div> : <p className="mt-3 text-sm text-slate-500">No status history recorded yet.</p>}{comments.length > 0 && <div className="mt-4 space-y-2"><p className="text-xs font-bold uppercase text-slate-400">Comments</p>{comments.map(item => <div key={item.id} className="rounded-xl bg-slate-50 p-3 text-sm"><p className="whitespace-pre-wrap text-slate-700">{item.body}</p><p className="mt-1 text-xs text-slate-400">{new Date(item.created_at).toLocaleString()}{item.author_name ? ` · ${item.author_name}` : ''}</p></div>)}</div>}<div className="mt-4"><label className="block text-sm font-semibold text-slate-700">Add a note<textarea value={comment} onChange={event => setComment(event.target.value)} rows={2} placeholder="Observations for the audit trail" className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-200" /></label><button onClick={submitComment} disabled={!comment.trim() || addComment.isPending} className="mt-2 min-h-11 rounded-xl border border-slate-300 px-4 text-sm font-semibold text-slate-700 disabled:opacity-50">Save note</button></div>{job.escalatedAt && <p className="mt-3 rounded-xl bg-orange-50 p-3 text-sm text-orange-700">Escalated: {job.escalationReason}</p>}</div>
    {showCompletion && <Sheet title="Complete job" onClose={() => setShowCompletion(false)}><label className="block text-sm font-semibold text-slate-700">Work performed<textarea required value={workPerformed} onChange={event => setWorkPerformed(event.target.value)} rows={4} className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm" /></label><label className="block text-sm font-semibold text-slate-700">Time spent (minutes)<input required type="number" min="1" value={timeSpent} onChange={event => setTimeSpent(event.target.value)} className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm" /></label><div><div className="mb-2 flex items-center justify-between"><span className="text-sm font-semibold text-slate-700">Parts used</span><button type="button" onClick={() => setParts([...parts, { name: '', quantity: 1 }])} className="flex items-center gap-1 text-sm font-semibold text-blue-600"><Plus size={15} />Add part</button></div>{parts.map((part, index) => <div key={index} className="mb-2 flex gap-2"><input value={part.name} onChange={event => setParts(parts.map((item, itemIndex) => itemIndex === index ? { ...item, name: event.target.value } : item))} placeholder="Part name" className="min-w-0 flex-1 rounded-xl border border-slate-200 px-3 py-2 text-sm" /><input type="number" min="1" value={part.quantity} onChange={event => setParts(parts.map((item, itemIndex) => itemIndex === index ? { ...item, quantity: Number(event.target.value) } : item))} className="w-20 rounded-xl border border-slate-200 px-3 py-2 text-sm" /></div>)}</div><button onClick={submitCompletion} disabled={runAction.isPending} className="flex min-h-11 items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 text-sm font-semibold text-white disabled:opacity-60"><Save size={16} />Save completion</button></Sheet>}
    {showEscalation && <Sheet title="Escalate job" onClose={() => setShowEscalation(false)}><label className="block text-sm font-semibold text-slate-700">Reason<textarea value={escalationReason} onChange={event => setEscalationReason(event.target.value)} rows={4} placeholder="Explain why senior help is needed" className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm" /></label><button onClick={submitEscalation} disabled={runAction.isPending} className="min-h-11 rounded-xl bg-orange-600 px-4 text-sm font-semibold text-white disabled:opacity-60">Send escalation</button></Sheet>}
  </div>;
}

function Info({ label, value, icon, }: { label: string; value: string; icon?: React.ReactNode }) { return <div className="rounded-xl border border-slate-100 bg-slate-50 p-3"><p className="flex items-center gap-1 text-[10px] font-bold uppercase text-slate-400">{icon}{label}</p><p className="mt-1 break-words text-sm font-semibold text-slate-800">{value || 'Not available'}</p></div>; }
function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) { return <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/40 sm:items-center sm:p-4"><div className="max-h-[92vh] w-full overflow-y-auto rounded-t-2xl bg-white p-5 shadow-2xl sm:max-w-xl sm:rounded-2xl"><div className="mb-5 flex items-center justify-between"><h2 className="text-lg font-bold text-slate-900">{title}</h2><button onClick={onClose} className="min-h-11 min-w-11 rounded-xl text-2xl text-slate-500 hover:bg-slate-100" aria-label="Close">×</button></div><div className="space-y-4">{children}</div></div></div>; }
