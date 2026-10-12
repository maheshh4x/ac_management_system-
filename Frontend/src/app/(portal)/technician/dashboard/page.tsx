'use client';

import Link from 'next/link';
import { useMemo } from 'react';
import { ArrowRight, CheckCircle2, Map, QrCode, Wrench } from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { useJobs } from '@/lib/api/maintenance';

export default function TechnicianDashboardPage() {
  const { user } = useAuth();
  const { jobs } = useJobs({ assignedTo: 'me', pageSize: 200 });
  const today = new Date().toISOString().slice(0, 10);
  const pending = jobs.filter(job => job.status === 'Pending' || job.status === 'Accepted' || job.status === 'In Progress');
  const completed = jobs.filter(job => job.status === 'Completed');
  const todayJobs = useMemo(() => jobs.filter(job => job.scheduledDate === today), [jobs, today]);

  return <div className="mx-auto flex max-w-3xl flex-col gap-6 pb-8">
    <div className="mt-4 flex flex-col items-center text-center"><div className="mb-2 flex h-16 w-16 items-center justify-center rounded-full border border-blue-200 bg-blue-100 text-blue-600 shadow-sm"><Wrench size={28} /></div><h1 className="text-xl font-bold tracking-tight">Welcome, {user?.name}</h1><p className="text-sm text-slate-500">Field Technician Portal</p></div>
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3"><div className="rounded-xl border border-slate-200 bg-white p-4 text-center shadow-sm"><span className="text-3xl font-bold text-slate-800">{pending.length}</span><span className="mt-1 block text-xs font-semibold uppercase tracking-wider text-slate-500">Pending Jobs</span></div><div className="rounded-xl border border-slate-200 bg-white p-4 text-center shadow-sm"><span className="text-3xl font-bold text-emerald-600">{completed.length}</span><span className="mt-1 block text-xs font-semibold uppercase tracking-wider text-slate-500">Completed</span></div><div className="col-span-2 rounded-xl border border-slate-200 bg-white p-4 text-center shadow-sm sm:col-span-1"><span className="text-3xl font-bold text-blue-600">{jobs.length}</span><span className="mt-1 block text-xs font-semibold uppercase tracking-wider text-slate-500">Assigned Total</span></div></div>
    <div className="flex flex-col gap-3"><h2 className="px-1 text-sm font-semibold uppercase tracking-widest text-slate-700">Quick Actions</h2><Link href="/technician/scan" className="flex min-h-14 items-center justify-between rounded-xl bg-blue-600 p-4 text-white shadow-sm transition-colors hover:bg-blue-700"><span className="flex items-center gap-3 text-lg font-medium"><QrCode size={24} />Scan AC QR Code</span><ArrowRight /></Link><Link href="/technician/jobs" className="flex min-h-14 items-center justify-between rounded-xl border border-slate-200 bg-white p-4 text-slate-800 shadow-sm hover:bg-slate-50"><span className="flex items-center gap-3 font-medium"><Wrench size={24} className="text-slate-500" />My Assigned Jobs</span><span className="rounded-full bg-blue-100 px-2.5 py-1 text-xs font-bold text-blue-700">{pending.length}</span></Link><Link href="/technician/campus" className="flex min-h-14 items-center justify-between rounded-xl border border-slate-200 bg-white p-4 text-slate-800 shadow-sm hover:bg-slate-50"><span className="flex items-center gap-3 font-medium"><Map size={24} className="text-slate-500" />Locate AC on Map</span><ArrowRight className="text-slate-400" /></Link></div>
    <div><div className="mb-3 flex items-center justify-between"><h2 className="px-1 text-sm font-semibold uppercase tracking-widest text-slate-700">Today&apos;s Jobs</h2><Link href="/technician/jobs" className="text-sm font-semibold text-blue-600">View all</Link></div>{todayJobs.length ? <div className="space-y-3">{todayJobs.map(job => <Link href={`/technician/jobs/${encodeURIComponent(job.id)}`} key={job.id} className="block rounded-xl border border-slate-200 bg-white p-4 shadow-sm"><div className="flex items-start justify-between gap-3"><div><p className="font-bold text-slate-800">{job.id}</p><p className="mt-1 text-sm text-slate-600">{job.description}</p></div><span className="rounded-full bg-amber-50 px-2 py-1 text-xs font-bold text-amber-700">{job.status}</span></div><p className="mt-3 text-xs font-medium text-slate-500">{job.acId} · {job.acLocation}</p></Link>)}</div> : <div className="rounded-xl border border-dashed border-slate-300 bg-white px-5 py-10 text-center"><CheckCircle2 size={32} className="mx-auto mb-2 text-slate-300" /><p className="font-semibold text-slate-700">No jobs scheduled for today</p><p className="mt-1 text-sm text-slate-500">Your schedule will appear here when work is assigned.</p></div>}</div>
  </div>;
}
