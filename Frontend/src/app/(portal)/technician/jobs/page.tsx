'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { AlertOctagon, CalendarDays, CheckCircle2, Clock3, Filter, Wrench } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { useJobs } from '@/lib/api/maintenance';
import type { MaintenanceJob } from '@/lib/types/maintenance';

const statuses = ['All', 'Pending', 'Accepted', 'In Progress', 'Completed', 'Overdue'] as const;
const statusStyle: Record<string, string> = {
  Pending: 'bg-amber-50 text-amber-700 border-amber-200',
  Accepted: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  'In Progress': 'bg-blue-50 text-blue-700 border-blue-200',
  Completed: 'bg-emerald-50 text-emerald-700 border-emerald-200',
};
const priorityStyle: Record<string, string> = {
  Low: 'text-slate-500', Medium: 'text-blue-600', High: 'text-orange-600', Critical: 'text-red-600',
};

function isOverdue(job: MaintenanceJob) {
  return job.status !== 'Completed' && job.status !== 'Cancelled' && (job.dueDate ?? job.scheduledDate) < new Date().toISOString().slice(0, 10);
}

export default function TechnicianJobsPage() {
  const { user } = useAuth();
  // Server-scoped to my jobs (assigned or self-created); the status/date
  // chips below stay client-side over the fetched set.
  const { jobs } = useJobs({ assignedTo: 'me', pageSize: 200 });
  const [status, setStatus] = useState<(typeof statuses)[number]>('All');
  const [range, setRange] = useState('all');
  const [now] = useState(() => Date.now());

  const filtered = useMemo(() => jobs.filter(job => {
    const matchesStatus = status === 'All' || (status === 'Overdue' ? isOverdue(job) : job.status === status);
    if (!matchesStatus) return false;
    if (range === 'all') return true;
    const days = range === 'week' ? 7 : 30;
    const threshold = now - days * 24 * 60 * 60 * 1000;
    return new Date(job.scheduledDate).getTime() >= threshold;
  }), [jobs, now, range, status]);

  const counts = statuses.reduce<Record<string, number>>((result, item) => {
    result[item] = item === 'All' ? jobs.length : item === 'Overdue' ? jobs.filter(isOverdue).length : jobs.filter(job => job.status === item).length;
    return result;
  }, {});

  return <div className="flex flex-col gap-5 pb-8">
    <div><h1 className="text-xl font-bold text-slate-900">My Jobs</h1><p className="mt-1 text-sm text-slate-500">Only jobs assigned to {user?.name ?? 'you'} are shown.</p></div>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">{statuses.map(item => <button key={item} onClick={() => setStatus(item)} className={`rounded-xl border p-3 text-left transition-colors ${status === item ? 'border-blue-300 bg-blue-50' : 'border-slate-200 bg-white hover:border-blue-200'}`}><p className="text-xl font-bold text-slate-900">{counts[item]}</p><p className="text-xs font-medium text-slate-500">{item}</p></button>)}</div>
    <div className="flex flex-wrap items-center gap-2"><Filter size={16} className="text-slate-400" /><span className="text-sm font-medium text-slate-600">Date range</span>{[['all', 'All dates'], ['week', 'This week'], ['month', 'This month']].map(([value, label]) => <button key={value} onClick={() => setRange(value)} className={`min-h-11 rounded-lg border px-3 text-sm font-medium ${range === value ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 bg-white text-slate-600'}`}>{label}</button>)}</div>
    <div className="space-y-3">
      {filtered.map(job => <Link href={`/technician/jobs/${encodeURIComponent(job.id)}`} key={job.id} className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition hover:border-blue-300 hover:shadow-md"><div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-sm font-bold text-blue-700">{job.id}</span><span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${statusStyle[job.status] ?? 'border-slate-200 bg-slate-50 text-slate-600'}`}>{job.status}</span></div><h2 className="mt-2 font-semibold text-slate-900">{job.description}</h2></div><span className={`flex items-center gap-1 text-xs font-bold ${priorityStyle[job.priority] ?? 'text-slate-500'}`}><AlertOctagon size={14} />{job.priority}</span></div><div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-slate-500"><span className="flex items-center gap-1"><Wrench size={13} />{job.acId}</span><span className="flex items-center gap-1"><CalendarDays size={13} />Due {job.dueDate ?? job.scheduledDate}</span><span className="flex items-center gap-1"><Clock3 size={13} />{job.type}</span></div>{isOverdue(job) && <p className="mt-3 text-xs font-semibold text-red-600">Overdue</p>}</Link>)}
      {!filtered.length && <div className="rounded-xl border border-dashed border-slate-300 bg-white px-5 py-14 text-center"><CheckCircle2 size={34} className="mx-auto mb-3 text-slate-300" /><p className="font-semibold text-slate-700">{jobs.length ? 'No jobs match this filter' : 'No assigned jobs yet'}</p><p className="mt-1 text-sm text-slate-500">New assignments will appear here when a manager assigns work to you.</p></div>}
    </div>
  </div>;
}
