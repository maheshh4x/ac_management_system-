'use client';

import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { Airplay, ArrowLeft, ClipboardList, Loader2, MapPin, MoveRight, Plus, Wrench } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { useCreateJob, useJobs } from '@/lib/api/maintenance';
import { useMovements } from '@/lib/api/movements';
import { getTechnicianAsset, TechnicianAsset } from '@/lib/technician-asset-service';

export default function TechnicianAssetDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { user } = useAuth();
  const assetId = decodeURIComponent(id);
  const createJob = useCreateJob();
  const [asset, setAsset] = useState<TechnicianAsset | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [issue, setIssue] = useState('');
  const [showIssue, setShowIssue] = useState(false);

  // Server scope: technicians only ever see their own jobs (assigned or
  // self-created), filtered here to this asset. Route ids may be UUIDs — the
  // work-order filter and create need the business key (AC-…).
  const jobsAcId = asset?.acId ?? assetId;
  const { jobs } = useJobs({ acId: jobsAcId }, Boolean(asset));
  // Movement history for this asset (same business-key caveat).
  const { movements } = useMovements({ acId: jobsAcId }, Boolean(asset));

  useEffect(() => {
    getTechnicianAsset(assetId)
      .then(loadedAsset => { setAsset(loadedAsset); setLoading(false); })
      .catch(() => { setError('Unable to load this asset.'); setLoading(false); });
  }, [assetId]);

  if (loading) return <div className="flex min-h-[50vh] items-center justify-center text-slate-500"><Loader2 className="mr-2 animate-spin" size={20} />Loading asset...</div>;
  if (!asset) return <EmptyAsset error={error} />;

  const startJob = () => {
    if (!issue.trim() || !user) return setError('Describe the issue before creating a job.');
    setError('');
    // Self-report: a technician may assign the job to themselves — the server
    // records ASSIGNED straight away.
    createJob.mutate(
      {
        ac_id: asset.acId ?? asset.id,
        maintenance_type: 'Repair',
        priority: 'MEDIUM',
        scheduled_date: new Date().toISOString().slice(0, 10),
        problem_description: issue.trim(),
        technician_id: user.id,
      },
      {
        onSuccess: (data) => {
          const created = data as { id?: string };
          setShowIssue(false);
          setIssue('');
          if (created.id) router.push(`/technician/jobs/${encodeURIComponent(created.id)}`);
        },
        onError: (createError) => setError(createError instanceof Error ? createError.message : 'Unable to create the job.'),
      },
    );
  };

  return <div className="mx-auto flex max-w-3xl flex-col gap-5 pb-10">
    <button onClick={() => router.back()} className="flex min-h-11 w-fit items-center gap-1.5 text-sm font-semibold text-slate-600 hover:text-blue-600"><ArrowLeft size={16} />Back</button>
    <section className="rounded-2xl bg-slate-900 p-5 text-white shadow-sm sm:p-6"><div className="flex flex-wrap items-start justify-between gap-3"><div><p className="font-mono text-sm text-blue-300">{asset.id}</p><h1 className="mt-1 text-2xl font-bold">{asset.details.make} {asset.details.model}</h1><p className="mt-1 text-sm text-slate-300">Serial {asset.details.serialNumber}</p></div><span className="rounded-full bg-white/10 px-3 py-1 text-xs font-bold">{asset.technical.status}</span></div><div className="mt-5 flex items-start gap-2 text-sm text-slate-300"><MapPin size={17} className="mt-0.5 shrink-0 text-blue-300" />{asset.location.block} / {asset.location.floor} / {asset.location.room}</div></section>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4"><Info label="Type" value={asset.details.type} /><Info label="Capacity" value={asset.details.capacity} /><Info label="Last service" value={asset.maintenance.lastDate ?? 'Not recorded'} /><Info label="Next service" value={asset.maintenance.nextDate ?? 'Not scheduled'} /></div>
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center gap-2"><Wrench className="text-blue-600" size={18} /><h2 className="font-bold text-slate-900">My service history</h2></div>{jobs.length ? <div className="mt-4 space-y-3">{jobs.map(job => <Link href={`/technician/jobs/${encodeURIComponent(job.id)}`} key={job.id} className="block rounded-xl border border-slate-100 bg-slate-50 p-3 hover:border-blue-200"><div className="flex justify-between gap-2"><span className="font-mono text-sm font-bold text-blue-700">{job.id}</span><span className="text-xs font-semibold text-slate-600">{job.status}</span></div><p className="mt-1 text-sm text-slate-700">{job.description}</p></Link>)}</div> : <p className="mt-3 text-sm text-slate-500">No service jobs assigned to you for this asset.</p>}</section>
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><h2 className="font-bold text-slate-900">Full maintenance history</h2>{asset.maintenanceHistory?.length ? <div className="mt-4 space-y-3">{asset.maintenanceHistory.map(item => <div key={item.id} className="rounded-xl bg-slate-50 p-3"><div className="flex flex-wrap justify-between gap-2"><span className="font-mono text-sm font-bold text-blue-700">{item.id}</span><span className="text-xs font-semibold text-slate-600">{item.status}</span></div><p className="mt-1 text-sm text-slate-700">{item.description}</p><p className="mt-1 text-xs text-slate-500">{item.type} · {item.priority} · {item.scheduledDate}</p></div>)}</div> : <p className="mt-3 text-sm text-slate-500">No maintenance history recorded for this asset.</p>}</section>
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"><div className="flex items-center gap-2"><ClipboardList className="text-blue-600" size={18} /><h2 className="font-bold text-slate-900">Movement history</h2></div>{movements.length ? <div className="mt-4 space-y-3">{movements.map(movement => <div key={movement.id} className="rounded-xl bg-slate-50 p-3"><div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-800"><span>{movement.fromLocation || 'Initial location'}</span><MoveRight size={15} className="text-blue-500" /><span>{movement.toLocation}</span><span className="ml-auto text-xs text-slate-500">{movement.status}</span></div><p className="mt-1 text-xs text-slate-500">{movement.reason}</p></div>)}</div> : <p className="mt-3 text-sm text-slate-500">No movement history for this asset.</p>}</section>
    {showIssue ? <section className="rounded-2xl border border-blue-200 bg-blue-50 p-5"><h2 className="font-bold text-slate-900">Report an issue</h2><textarea value={issue} onChange={event => setIssue(event.target.value)} rows={4} placeholder="Describe what needs attention" className="mt-3 w-full rounded-xl border border-blue-200 bg-white px-3 py-2.5 text-sm outline-none focus:ring-2 focus:ring-blue-400" />{error && <p className="mt-2 text-xs font-semibold text-red-600">{error}</p>}<div className="mt-3 flex gap-2"><button onClick={startJob} disabled={createJob.isPending} className="flex min-h-11 items-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white disabled:opacity-60"><Plus size={16} />{createJob.isPending ? 'Creating…' : 'Create job'}</button><button onClick={() => setShowIssue(false)} className="min-h-11 rounded-xl border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700">Cancel</button></div></section> : <button onClick={() => setShowIssue(true)} className="flex min-h-12 items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-semibold text-white shadow-sm hover:bg-blue-700"><Plus size={17} />Report issue / Start job</button>}
  </div>;
}

function EmptyAsset({ error }: { error: string }) { return <div className="flex min-h-[50vh] flex-col items-center justify-center text-center"><Airplay size={42} className="mb-3 text-slate-300" /><h1 className="text-xl font-bold text-slate-800">Asset not found</h1><p className="mt-1 max-w-sm text-sm text-slate-500">{error || 'This asset is not available online or in your offline cache.'}</p><Link href="/technician/scan" className="mt-5 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white">Back to scan</Link></div>; }
function Info({ label, value }: { label: string; value: string }) { return <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm"><p className="text-[10px] font-bold uppercase text-slate-400">{label}</p><p className="mt-1 break-words text-sm font-semibold text-slate-800">{value || 'Not available'}</p></div>; }
