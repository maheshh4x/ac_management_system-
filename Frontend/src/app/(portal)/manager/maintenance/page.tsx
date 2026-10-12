'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import { MaintenanceJob } from '@/lib/types/maintenance';
import { Wrench, Search, Plus, CheckCircle2, Clock, Eye, X } from 'lucide-react';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import { MaintenanceMovementShell } from '@/components/features/MaintenanceMovementShell';
import { MovementPanel } from '@/components/features/MovementPanel';

import { useCreateJob, useJob, useJobAction, useJobs } from '@/lib/api/maintenance';
import { useUsers } from '@/lib/api/users';
import { toApiJobPriority, toApiJobStatus } from '@/lib/adapters/job';

const STATUS_COLORS: Record<string, string> = {
  Pending: 'bg-amber-100 text-amber-700 border-amber-200',
  Accepted: 'bg-indigo-100 text-indigo-700 border-indigo-200',
  'In Progress': 'bg-blue-100 text-blue-700 border-blue-200',
  Completed: 'bg-emerald-100 text-emerald-700 border-emerald-200',
  Cancelled: 'bg-slate-100 text-slate-600 border-slate-200',
};

const PRIORITY_COLORS: Record<string, string> = {
  Low: 'bg-slate-100 text-slate-600',
  Medium: 'bg-blue-100 text-blue-700',
  High: 'bg-orange-100 text-orange-700',
  Critical: 'bg-red-100 text-red-700',
};

export default function MaintenancePage() {
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('All');
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);

  // Assign flow (SCHEDULED → ASSIGNED) and complete flow (IN_PROGRESS →
  // COMPLETED — the server requires work_performed).
  const [assignJobId, setAssignJobId] = useState<string | null>(null);
  const [assignTechId, setAssignTechId] = useState('');
  const [completeJobId, setCompleteJobId] = useState<string | null>(null);
  const [completeWork, setCompleteWork] = useState('');

  // Form state
  const [newAcId, setNewAcId] = useState('AC-127');
  const [newType, setNewType] = useState<MaintenanceJob['type']>('Preventive');
  const [newPriority, setNewPriority] = useState<MaintenanceJob['priority']>('Medium');
  const [newAssignee, setNewAssignee] = useState(''); // technician profile id ('' = assign later)
  const [newDate, setNewDate] = useState(new Date().toISOString().split('T')[0]);
  const [newDesc, setNewDesc] = useState('');

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(search), 300);
    return () => window.clearTimeout(timer);
  }, [search]);

  // Server-side filters (q / status); stats mirror the old behaviour of
  // counting over the filtered set.
  const apiStatus = statusFilter === 'All' ? undefined : toApiJobStatus(statusFilter as MaintenanceJob['status']);
  const { jobs, isLoading } = useJobs({ q: debouncedSearch || undefined, status: apiStatus, pageSize: 200 });

  const techniciansQuery = useUsers({ role: 'TECHNICIAN', page: 1 });
  const technicians = useMemo(
    () => (techniciansQuery.data?.items ?? []).filter(profile => !profile.status || profile.status.toUpperCase() === 'APPROVED'),
    [techniciansQuery.data],
  );

  const stats = useMemo(
    () => ({
      pending: jobs.filter(job => job.status === 'Pending').length,
      inProgress: jobs.filter(job => job.status === 'In Progress').length,
      completed: jobs.filter(job => job.status === 'Completed').length,
    }),
    [jobs],
  );

  const runAction = useJobAction();
  const createJob = useCreateJob();

  // Live detail for the open modal — stays fresh after every invalidation and
  // supplies created_by_name + the maintenance_events timeline.
  const { data: selectedDetail } = useJob(selectedJobId ?? undefined);
  const selectedJob = selectedJobId
    ? (selectedDetail?.job ?? jobs.find(job => job.id === selectedJobId) ?? null)
    : null;
  const selectedEvents = selectedDetail?.events ?? [];

  const performAction = (
    jobId: string,
    action: 'assign' | 'accept' | 'start' | 'complete' | 'escalate' | 'cancel',
    opts: { payload?: Record<string, unknown>; label?: string; success?: string; onDone?: () => void } = {},
  ) => {
    runAction.mutate(
      { id: jobId, action, payload: opts.payload, label: opts.label },
      {
        onSuccess: result => {
          opts.onDone?.();
          if (opts.success) setNotice({ kind: 'ok', text: result.queued ? `${opts.success} (queued — you're offline)` : opts.success });
        },
        onError: actionError => setNotice({ kind: 'err', text: actionError instanceof Error ? actionError.message : 'Action failed.' }),
      },
    );
  };

  const handleScheduleSubmit = () => {
    if (!newAcId.trim() || !newDesc.trim()) {
      setNotice({ kind: 'err', text: 'AC unit and description are required.' });
      return;
    }
    createJob.mutate(
      {
        ac_id: newAcId.trim(),
        maintenance_type: newType,
        priority: toApiJobPriority(newPriority),
        scheduled_date: newDate,
        problem_description: newDesc.trim(),
        ...(newAssignee ? { technician_id: newAssignee } : {}),
      },
      {
        onSuccess: () => {
          setNewDesc('');
          setShowAdd(false);
          setNotice({ kind: 'ok', text: 'Job scheduled.' });
        },
        onError: createError => setNotice({ kind: 'err', text: createError instanceof Error ? createError.message : 'Unable to schedule the job.' }),
      },
    );
  };

  const jobActionButtons = (job: MaintenanceJob) => (
    <>
      {job.status === 'Pending' && (
        <button disabled={runAction.isPending} onClick={() => { setAssignJobId(job.id); setAssignTechId(''); }} className="px-3 py-2 rounded-lg bg-amber-100 hover:bg-amber-200 text-amber-800 text-xs font-medium transition-colors cursor-pointer disabled:opacity-60">
          Assign…
        </button>
      )}
      {job.status === 'Accepted' && !job.acceptedAt && (
        <button disabled={runAction.isPending} onClick={() => performAction(job.id, 'accept', { label: 'Accept job', success: 'Job accepted.' })} className="px-3 py-2 rounded-lg bg-indigo-100 hover:bg-indigo-200 text-indigo-700 text-xs font-medium transition-colors cursor-pointer disabled:opacity-60">
          Accept
        </button>
      )}
      {job.status === 'Accepted' && job.acceptedAt && (
        <button disabled={runAction.isPending} onClick={() => performAction(job.id, 'start', { label: 'Start job', success: 'Job started.' })} className="px-3 py-2 rounded-lg bg-blue-100 hover:bg-blue-200 text-blue-700 text-xs font-medium transition-colors cursor-pointer disabled:opacity-60">
          Start Job
        </button>
      )}
      {job.status === 'In Progress' && (
        <button disabled={runAction.isPending} onClick={() => { setCompleteJobId(job.id); setCompleteWork(''); }} className="px-3 py-2 rounded-lg bg-emerald-100 hover:bg-emerald-200 text-emerald-700 text-xs font-medium transition-colors cursor-pointer disabled:opacity-60">
          Complete…
        </button>
      )}
    </>
  );

  return (
    <MaintenanceMovementShell movement={<MovementPanel />} maintenance={
    <div className="flex flex-col gap-6 pb-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Maintenance Management</h1>
          <p className="text-sm text-slate-500 mt-1">Track, assign & complete maintenance jobs for all AC units.</p>
        </div>
        <ProtectedAction permission="MANAGE_MAINTENANCE">
          <button onClick={() => setShowAdd(true)} className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium shadow-sm transition-colors">
            <Plus size={16} /> Schedule Job
          </button>
        </ProtectedAction>
      </div>

      {notice && (
        <div className={`rounded-xl border px-4 py-3 text-sm font-semibold flex items-center justify-between ${notice.kind === 'err' ? 'border-red-200 bg-red-50 text-red-700' : 'border-emerald-200 bg-emerald-50 text-emerald-700'}`}>
          <span>{notice.text}</span>
          <button onClick={() => setNotice(null)} className="ml-3 font-bold cursor-pointer" aria-label="Dismiss">×</button>
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {[
          { label: 'Pending', count: stats.pending, icon: Clock, color: 'text-amber-600', bg: 'bg-amber-50 border-amber-100' },
          { label: 'In Progress', count: stats.inProgress, icon: Wrench, color: 'text-blue-600', bg: 'bg-blue-50 border-blue-100' },
          { label: 'Completed', count: stats.completed, icon: CheckCircle2, color: 'text-emerald-600', bg: 'bg-emerald-50 border-emerald-100' },
        ].map(item => {
          const Icon = item.icon;
          return (
            <div key={item.label} className={`rounded-xl border p-4 flex items-center gap-4 ${item.bg}`}>
              <div className={`w-10 h-10 rounded-full flex items-center justify-center bg-white shadow-sm ${item.color}`}>
                <Icon size={20} />
              </div>
              <div>
                <p className={`text-2xl font-bold ${item.color}`}>{item.count}</p>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider">{item.label}</p>
              </div>
            </div>
          );
        })}
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <div className="relative flex-1 min-w-[200px]">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="text"
            placeholder="Search job ID, AC ID, technician..."
            className="w-full pl-10 pr-4 py-2.5 border border-slate-200 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="flex gap-2 flex-wrap">
          {['All', 'Pending', 'In Progress', 'Completed', 'Cancelled'].map(s => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-3 py-2 rounded-lg text-xs font-semibold border transition-all ${statusFilter === s ? 'bg-slate-900 text-white border-slate-900' : 'bg-white text-slate-600 border-slate-200 hover:border-slate-400'}`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Job Cards */}
      <div className="grid gap-4">
        {jobs.map((job, i) => (
          <motion.div
            key={job.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.06 }}
            className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 flex flex-col md:flex-row gap-4"
          >
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap mb-2">
                <span className="font-mono text-sm font-bold text-blue-700">{job.id}</span>
                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${STATUS_COLORS[job.status] ?? 'border-slate-200 bg-slate-50 text-slate-600'}`}>{job.status}</span>
                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${PRIORITY_COLORS[job.priority] ?? 'bg-slate-100 text-slate-600'}`}>{job.priority}</span>
                <span className="text-xs text-slate-400">• {job.type}</span>
              </div>
              <p className="font-medium text-slate-800 text-sm mb-1">{job.description}</p>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500 mt-2">
                <span>🔧 <strong>{job.acId}</strong></span>
                <span>📍 {job.acLocation}</span>
                <span>👷 {job.assignedTo}</span>
                <span>📅 {job.scheduledDate}</span>
              </div>
            </div>
            <div className="flex flex-row md:flex-col gap-2 flex-shrink-0">
              <button onClick={() => setSelectedJobId(job.id)} className="px-3 py-2 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-medium transition-colors flex items-center gap-1.5 cursor-pointer">
                <Eye size={13} /> View
              </button>
              <ProtectedAction permission="MANAGE_MAINTENANCE">
                {jobActionButtons(job)}
              </ProtectedAction>
            </div>
          </motion.div>
        ))}

        {jobs.length === 0 && (
          <div className="text-center py-16 text-slate-400 bg-white rounded-xl border border-slate-200">
            <Wrench size={36} className="mx-auto mb-3 opacity-30" />
            <p className="font-medium">{isLoading ? 'Loading jobs…' : 'No maintenance jobs found.'}</p>
          </div>
        )}
      </div>

      {/* Job Detail Modal */}
      {selectedJob && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden">
            <div className="p-5 bg-slate-50 border-b border-slate-100 flex items-center justify-between">
              <div>
                <h3 className="font-bold text-lg">{selectedJob.id} – {selectedJob.type}</h3>
                <span className={`inline-block mt-1 px-2 py-0.5 text-xs font-semibold rounded-full border ${STATUS_COLORS[selectedJob.status] ?? 'border-slate-200 bg-slate-50 text-slate-600'}`}>{selectedJob.status}</span>
              </div>
              <button onClick={() => setSelectedJobId(null)} className="p-2 rounded-full hover:bg-slate-200 cursor-pointer"><X size={18} /></button>
            </div>
            <div className="p-6 space-y-4 text-sm">
              <p className="text-slate-700">{selectedJob.description}</p>
              <div className="grid grid-cols-2 gap-3">
                {[
                  ['AC Unit', selectedJob.acId], ['Location', selectedJob.acLocation],
                  ['Priority', selectedJob.priority], ['Assigned To', selectedJob.assignedTo],
                  ['Reported By', selectedJob.reportedBy], ['Scheduled', selectedJob.scheduledDate],
                  ...(selectedJob.completedDate ? [['Completed', selectedJob.completedDate]] : []),
                ].map(([k, v]) => (
                  <div key={k}>
                    <p className="text-xs text-slate-400 uppercase font-semibold tracking-wide">{k}</p>
                    <p className="font-medium text-slate-800 mt-0.5">{v}</p>
                  </div>
                ))}
              </div>
              {selectedJob.notes && (
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                  <p className="text-xs text-slate-400 uppercase font-semibold tracking-wide mb-1">Notes</p>
                  <p className="text-slate-700">{selectedJob.notes}</p>
                </div>
              )}
              {selectedEvents.length > 0 && (
                <div>
                  <p className="text-xs text-slate-400 uppercase font-semibold tracking-wide mb-2">Timeline</p>
                  <div className="space-y-2">
                    {selectedEvents.map(event => (
                      <div key={event.id} className="border-l-2 border-blue-200 pl-3">
                        <p className="text-sm font-medium text-slate-800">{event.event}{event.to_status ? ` · ${event.to_status}` : ''}</p>
                        {event.note && <p className="text-sm text-slate-600">{event.note}</p>}
                        <p className="text-xs text-slate-400">{new Date(event.created_at).toLocaleString()}{event.actor_name ? ` · ${event.actor_name}` : ''}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <div className="p-5 border-t border-slate-100 flex justify-end gap-3">
              <button onClick={() => setSelectedJobId(null)} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium cursor-pointer">Close</button>
              <ProtectedAction permission="MANAGE_MAINTENANCE">
                {jobActionButtons(selectedJob)}
              </ProtectedAction>
            </div>
          </motion.div>
        </div>
      )}

      {/* Assign Job Modal (SCHEDULED → ASSIGNED) */}
      {assignJobId && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-sm overflow-hidden">
            <div className="p-5 bg-slate-50 border-b border-slate-100 flex items-center justify-between">
              <h3 className="font-bold text-lg">Assign job</h3>
              <button onClick={() => setAssignJobId(null)} className="p-2 rounded-full hover:bg-slate-200 cursor-pointer"><X size={18} /></button>
            </div>
            <div className="p-6 space-y-4 text-sm">
              <p className="text-slate-500">Choose the technician who will work <span className="font-mono font-semibold text-slate-800">{assignJobId}</span>.</p>
              <div>
                <label className="text-xs font-semibold text-slate-500 uppercase">Technician</label>
                <select value={assignTechId} onChange={e => setAssignTechId(e.target.value)} className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-blue-500 bg-white">
                  <option value="">{techniciansQuery.isLoading ? 'Loading technicians…' : 'Select a technician'}</option>
                  {technicians.map(technician => (
                    <option key={technician.id} value={technician.id}>{technician.name || technician.email}</option>
                  ))}
                </select>
              </div>
              {runAction.isError && <p className="text-xs font-semibold text-red-600">{runAction.error instanceof Error ? runAction.error.message : 'Unable to assign the job.'}</p>}
            </div>
            <div className="p-5 border-t border-slate-100 flex justify-end gap-3">
              <button onClick={() => setAssignJobId(null)} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium cursor-pointer">Cancel</button>
              <button
                disabled={!assignTechId || runAction.isPending}
                onClick={() => performAction(assignJobId, 'assign', {
                  payload: { technician_id: assignTechId },
                  label: 'Assign job',
                  success: 'Job assigned.',
                  onDone: () => { setAssignJobId(null); setAssignTechId(''); },
                })}
                className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-60 cursor-pointer"
              >
                Assign
              </button>
            </div>
          </motion.div>
        </div>
      )}

      {/* Complete Job Modal (IN_PROGRESS → COMPLETED, work_performed required) */}
      {completeJobId && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-md overflow-hidden">
            <div className="p-5 bg-slate-50 border-b border-slate-100 flex items-center justify-between">
              <h3 className="font-bold text-lg">Complete job</h3>
              <button onClick={() => setCompleteJobId(null)} className="p-2 rounded-full hover:bg-slate-200 cursor-pointer"><X size={18} /></button>
            </div>
            <div className="p-6 space-y-4 text-sm">
              <p className="text-slate-500">The server records the work performed as part of the audit trail.</p>
              <div>
                <label className="text-xs font-semibold text-slate-500 uppercase">Work performed</label>
                <textarea value={completeWork} onChange={e => setCompleteWork(e.target.value)} rows={4} placeholder="What was done?" className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-blue-500" />
              </div>
              {runAction.isError && <p className="text-xs font-semibold text-red-600">{runAction.error instanceof Error ? runAction.error.message : 'Unable to complete the job.'}</p>}
            </div>
            <div className="p-5 border-t border-slate-100 flex justify-end gap-3">
              <button onClick={() => setCompleteJobId(null)} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium cursor-pointer">Cancel</button>
              <button
                disabled={!completeWork.trim() || runAction.isPending}
                onClick={() => performAction(completeJobId, 'complete', {
                  payload: { work_performed: completeWork.trim() },
                  label: 'Complete job',
                  success: 'Job completed.',
                  onDone: () => { setCompleteJobId(null); setCompleteWork(''); },
                })}
                className="px-4 py-2 bg-emerald-600 text-white rounded-lg text-sm font-medium hover:bg-emerald-700 disabled:opacity-60 cursor-pointer"
              >
                Mark Complete
              </button>
            </div>
          </motion.div>
        </div>
      )}

      {/* Add Job Modal */}
      {showAdd && (
        <div className="fixed inset-0 bg-black/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <motion.div initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="bg-white rounded-2xl shadow-2xl w-full max-w-lg overflow-hidden">
            <div className="p-5 bg-slate-50 border-b border-slate-100 flex items-center justify-between">
              <h3 className="font-bold text-lg">Schedule Maintenance Job</h3>
              <button onClick={() => setShowAdd(false)} className="p-2 rounded-full hover:bg-slate-200 cursor-pointer"><X size={18} /></button>
            </div>
            <div className="p-6 space-y-4">
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <label className="text-xs font-semibold text-slate-500 uppercase">AC Unit</label>
                  <input value={newAcId} onChange={e => setNewAcId(e.target.value)} className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none" placeholder="AC-127" />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-500 uppercase">Job Type</label>
                  <select value={newType} onChange={e => setNewType(e.target.value as MaintenanceJob['type'])} className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none bg-white">
                    <option value="Preventive">Preventive</option>
                    <option value="Repair">Repair</option>
                    <option value="Emergency">Emergency</option>
                    <option value="Inspection">Inspection</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-500 uppercase">Priority</label>
                  <select value={newPriority} onChange={e => setNewPriority(e.target.value as MaintenanceJob['priority'])} className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none bg-white">
                    <option value="Low">Low</option>
                    <option value="Medium">Medium</option>
                    <option value="High">High</option>
                    <option value="Critical">Critical</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-500 uppercase">Assign To</label>
                  <select value={newAssignee} onChange={e => setNewAssignee(e.target.value)} className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none bg-white">
                    <option value="">Assign later (schedule)</option>
                    {technicians.map(technician => (
                      <option key={technician.id} value={technician.id}>{technician.name || technician.email}</option>
                    ))}
                  </select>
                </div>
                <div className="col-span-2">
                  <label className="text-xs font-semibold text-slate-500 uppercase">Scheduled Date</label>
                  <input type="date" value={newDate} onChange={e => setNewDate(e.target.value)} className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none" />
                </div>
                <div className="col-span-2">
                  <label className="text-xs font-semibold text-slate-500 uppercase">Description</label>
                  <textarea value={newDesc} onChange={e => setNewDesc(e.target.value)} className="mt-1 w-full border border-slate-200 rounded-lg px-3 py-2 focus:ring-2 focus:ring-blue-500 outline-none" rows={2} placeholder="Describe the maintenance task..." />
                </div>
              </div>
              {createJob.isError && (
                <p className="text-xs font-semibold text-red-600">{createJob.error instanceof Error ? createJob.error.message : 'Unable to schedule the job.'}</p>
              )}
            </div>
            <div className="p-5 border-t border-slate-100 flex justify-end gap-3">
              <button onClick={() => setShowAdd(false)} className="px-4 py-2 bg-slate-100 text-slate-700 rounded-lg text-sm font-medium cursor-pointer">Cancel</button>
              <button onClick={handleScheduleSubmit} disabled={createJob.isPending} className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 flex items-center gap-1.5 cursor-pointer disabled:opacity-60"><Plus size={14} /> {createJob.isPending ? 'Scheduling…' : 'Schedule Job'}</button>
            </div>
          </motion.div>
        </div>
      )}
    </div>
    } />
  );
}
