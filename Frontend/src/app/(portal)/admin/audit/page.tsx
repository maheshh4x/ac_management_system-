'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { motion } from 'framer-motion';

import {
  type AuditFilters,
  type AuditLogRow,
  fetchAuditCsv,
  useAuditLogs,
} from '@/lib/api/audit';
import { useUsers } from '@/lib/api/users';
import { downloadBlob } from '@/lib/utils';
import {
  AlertTriangle,
  Airplay,
  Download,
  FileLock,
  FileText,
  Layers,
  Loader2,
  LogIn,
  Map,
  MapPin,
  RefreshCw,
  Search,
  User,
  Wrench,
} from 'lucide-react';

const PAGE_SIZE = 25;

/**
 * Entity types actually written by `app/services/audit_service.py::audit()`
 * (see the `audit("<ACTION>", "<entity_type>", ...)` call sites). Styling is
 * keyed on entity_type rather than action so new actions stay legible without
 * a CSS change.
 */
const ENTITY_TYPES = [
  'asset',
  'block',
  'floor',
  'room',
  'department',
  'maintenance',
  'movement',
  'profile',
] as const;

const ENTITY_STYLES: Record<string, { color: string; bg: string; icon: React.ReactNode; label: string }> = {
  asset: { color: 'text-blue-700', bg: 'bg-blue-100', icon: <Airplay size={14} />, label: 'Asset' },
  block: { color: 'text-indigo-700', bg: 'bg-indigo-100', icon: <MapPin size={14} />, label: 'Block' },
  floor: { color: 'text-violet-700', bg: 'bg-violet-100', icon: <Layers size={14} />, label: 'Floor' },
  room: { color: 'text-teal-700', bg: 'bg-teal-100', icon: <Map size={14} />, label: 'Room' },
  department: {
    color: 'text-cyan-700',
    bg: 'bg-cyan-100',
    icon: <FileText size={14} />,
    label: 'Department',
  },
  maintenance: { color: 'text-amber-700', bg: 'bg-amber-100', icon: <Wrench size={14} />, label: 'Maintenance' },
  movement: { color: 'text-emerald-700', bg: 'bg-emerald-100', icon: <RefreshCw size={14} />, label: 'Movement' },
  profile: { color: 'text-purple-700', bg: 'bg-purple-100', icon: <User size={14} />, label: 'Profile' },
};

const DEFAULT_STYLE = {
  color: 'text-slate-700',
  bg: 'bg-slate-100',
  icon: <LogIn size={14} />,
  label: 'Other',
};

function entityStyle(entityType: string | null) {
  return (entityType && ENTITY_STYLES[entityType]) || DEFAULT_STYLE;
}

/** `old_values`/`new_values` arrive as a JSONB object or an already-redacted string. */
function asRecord(value: AuditLogRow['old_values']): Record<string, unknown> | null {
  if (value === null || value === undefined) return null;
  if (typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value === 'string') {
    try {
      const parsed: unknown = JSON.parse(value);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        return parsed as Record<string, unknown>;
      }
    } catch {
      return null;
    }
  }
  return null;
}

function fmtValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
}

/** Compact `field: before → after` summary of one audited change. */
function describeChange(row: AuditLogRow): string {
  const before = asRecord(row.old_values);
  const after = asRecord(row.new_values);
  if (!before && !after) return '—';
  const keys = Array.from(new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})]));
  if (keys.length === 0) return '—';

  const shown = keys.slice(0, 3).map((key) => {
    const from = before?.[key];
    const to = after?.[key];
    if (from === undefined) return `${key}: ${fmtValue(to)}`;
    if (to === undefined) return `${key}: removed`;
    return `${key}: ${fmtValue(from)} → ${fmtValue(to)}`;
  });
  const rest = keys.length - shown.length;
  return `${shown.join('; ')}${rest > 0 ? ` … +${rest} more` : ''}`;
}

export default function AuditLogsPage() {
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [entityFilter, setEntityFilter] = useState('All');
  const [actionFilter, setActionFilter] = useState('');
  const [actorFilter, setActorFilter] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [page, setPage] = useState(1);
  const [exportState, setExportState] = useState<'idle' | 'working' | 'error'>('idle');
  const [exportError, setExportError] = useState<string | null>(null);

  // Debounce free-text search so each keystroke is not a round trip.
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search.trim()), 350);
    return () => clearTimeout(timer);
  }, [search]);

  // Any filter change invalidates the current page number. Adjusted during
  // render rather than in an effect — see react.dev "You Might Not Need an
  // Effect": the previous value is compared and the state corrected before
  // committing, so there is no extra render pass.
  const filterSignature = [debouncedSearch, entityFilter, actionFilter, actorFilter, dateFrom, dateTo].join('|');
  const [prevFilterSignature, setPrevFilterSignature] = useState(filterSignature);
  if (prevFilterSignature !== filterSignature) {
    setPrevFilterSignature(filterSignature);
    setPage(1);
  }

  const filters: AuditFilters = useMemo(
    () => ({
      q: debouncedSearch || undefined,
      entityType: entityFilter === 'All' ? undefined : entityFilter,
      action: actionFilter || undefined,
      actor: actorFilter || undefined,
      from: dateFrom || undefined,
      to: dateTo || undefined,
      page,
      pageSize: PAGE_SIZE,
    }),
    [debouncedSearch, entityFilter, actionFilter, actorFilter, dateFrom, dateTo, page],
  );

  const logs = useAuditLogs(filters);
  // Actor dropdown — audit readers are always admins, so /users is available.
  const { data: users } = useUsers();

  const items = useMemo(() => logs.data?.items ?? [], [logs.data]);
  const pagination = logs.data?.pagination;
  const activeFilters = [
    debouncedSearch,
    entityFilter === 'All' ? '' : entityFilter,
    actionFilter,
    actorFilter,
    dateFrom,
    dateTo,
  ].filter(Boolean).length;

  const pageStats = useMemo(() => {
    const actors = new Set<string>();
    const actions = new Set<string>();
    items.forEach((row) => {
      if (row.actor_name) actors.add(row.actor_name);
      if (row.action) actions.add(row.action);
    });
    return { actors: actors.size, actions: actions.size };
  }, [items]);

  async function handleExportCsv() {
    setExportState('working');
    setExportError(null);
    try {
      const blob = await fetchAuditCsv(filters);
      downloadBlob(blob, `audit_log_${new Date().toISOString().slice(0, 10)}.csv`);
      setExportState('idle');
    } catch (error) {
      setExportState('error');
      setExportError(error instanceof Error ? error.message : 'Export failed.');
    }
  }

  const formatTimestamp = (ts: string) => {
    const d = new Date(ts);
    return Number.isNaN(d.getTime()) ? ts : d.toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' });
  };

  const actorOptions = (users?.items ?? []).filter((u) => u.id);

  return (
    <div className="flex flex-col gap-6 pb-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Audit Logs</h1>
          <p className="text-sm text-slate-500 mt-1">
            Complete audit trail of all user actions in the system.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <button
            onClick={handleExportCsv}
            disabled={exportState === 'working'}
            className="flex items-center gap-2 px-4 py-2.5 bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60"
          >
            {exportState === 'working' ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
            {exportState === 'working' ? 'Exporting…' : 'Export CSV'}
          </button>
          {exportError && <p className="text-xs text-red-600 max-w-[240px] text-right">{exportError}</p>}
        </div>
      </div>

      {/* Summary Tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[
          {
            label: 'Matching Events',
            value: pagination ? pagination.total : logs.isPending ? '—' : 0,
            hint: 'across all pages',
            color: 'text-blue-600',
            bg: 'bg-blue-50 border-blue-100',
          },
          {
            label: 'On This Page',
            value: items.length,
            hint: `page ${pagination?.page ?? page} of ${pagination?.total_pages ?? 1}`,
            color: 'text-emerald-600',
            bg: 'bg-emerald-50 border-emerald-100',
          },
          {
            label: 'Actors',
            value: pageStats.actors,
            hint: 'distinct on this page',
            color: 'text-amber-600',
            bg: 'bg-amber-50 border-amber-100',
          },
          {
            label: 'Actions',
            value: pageStats.actions,
            hint: 'distinct on this page',
            color: 'text-purple-600',
            bg: 'bg-purple-50 border-purple-100',
          },
        ].map((item) => (
          <div key={item.label} className={`rounded-xl border p-4 ${item.bg}`}>
            <p className={`text-2xl font-bold ${item.color}`}>{item.value}</p>
            <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mt-1">{item.label}</p>
            <p className="text-[11px] text-slate-400 mt-0.5">{item.hint}</p>
          </div>
        ))}
      </div>

      {/* Server-side filters */}
      <div className="flex flex-col gap-3">
        <div className="flex gap-3 flex-wrap">
          <div className="relative flex-1 min-w-[200px]">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="text"
              placeholder="Search action, entity ID, or actor name..."
              className="w-full pl-10 pr-4 py-2.5 border border-slate-200 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>

          <select
            value={actionFilter}
            onChange={(e) => setActionFilter(e.target.value)}
            aria-label="Filter by action"
            className="px-3 py-2.5 border border-slate-200 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none cursor-pointer"
          >
            <option value="">All actions</option>
            <optgroup label="Assets">
              {['CREATE_ASSET', 'UPDATE_ASSET', 'DECOMMISSION_ASSET', 'REGENERATE_QR'].map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </optgroup>
            <optgroup label="Campus structure">
              {['CREATE_BLOCK', 'UPDATE_BLOCK', 'DELETE_BLOCK', 'CREATE_FLOOR', 'UPDATE_FLOOR', 'DELETE_FLOOR', 'CREATE_ROOM', 'UPDATE_ROOM', 'DELETE_ROOM'].map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </optgroup>
            <optgroup label="Maintenance">
              {['CREATE_MAINTENANCE', 'UPDATE_MAINTENANCE', 'ADD_JOB_COMMENT'].map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </optgroup>
            <optgroup label="Movements">
              {['CREATE_MOVEMENT', 'APPROVED_MOVEMENT', 'REJECTED_MOVEMENT', 'COMPLETE_MOVEMENT', 'CANCELLED_MOVEMENT'].map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </optgroup>
            <optgroup label="Users">
              {['PROFILE_UPDATE', 'PROFILE_REGISTERED', 'USER_APPROVED', 'USER_REJECTED', 'USER_UPDATED'].map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </optgroup>
          </select>

          <select
            value={actorFilter}
            onChange={(e) => setActorFilter(e.target.value)}
            aria-label="Filter by actor"
            className="px-3 py-2.5 border border-slate-200 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none cursor-pointer"
          >
            <option value="">All actors</option>
            {actorOptions.map((u) => (
              <option key={u.id} value={u.id}>
                {u.name || u.email || u.id}
              </option>
            ))}
          </select>

          <input
            type="date"
            aria-label="From date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            className="px-3 py-2.5 border border-slate-200 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none"
          />
          <input
            type="date"
            aria-label="To date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            className="px-3 py-2.5 border border-slate-200 rounded-lg text-sm bg-white focus:ring-2 focus:ring-blue-500 outline-none"
          />
        </div>

        <div className="flex gap-2 flex-wrap items-center">
          {(['All', ...ENTITY_TYPES] as string[]).map((e) => (
            <button
              key={e}
              onClick={() => setEntityFilter(e)}
              className={`px-3 py-2 rounded-lg text-xs font-semibold border transition-all ${
                entityFilter === e
                  ? 'bg-slate-900 text-white border-slate-900'
                  : 'bg-white text-slate-600 border-slate-200 hover:border-slate-400'
              }`}
            >
              {e === 'All' ? 'All' : entityStyle(e).label}
            </button>
          ))}
          {activeFilters > 0 && (
            <button
              onClick={() => {
                setSearch('');
                setDebouncedSearch('');
                setEntityFilter('All');
                setActionFilter('');
                setActorFilter('');
                setDateFrom('');
                setDateTo('');
              }}
              className="px-2.5 py-1 text-xs text-blue-600 hover:text-blue-800 font-medium underline"
            >
              Clear {activeFilters} filter{activeFilters > 1 ? 's' : ''}
            </button>
          )}
        </div>
      </div>

      {/* Logs table */}
      <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
        {logs.isError && (
          <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
            <AlertTriangle size={32} className="text-red-500 mb-3" />
            <p className="text-sm font-semibold text-red-900 mb-1">Unable to load the audit trail</p>
            <p className="text-xs text-red-600 mb-4 max-w-md">
              {logs.error instanceof Error ? logs.error.message : 'The request failed.'}
            </p>
            <button
              onClick={() => void logs.refetch()}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-red-600 text-white rounded-lg text-xs font-medium hover:bg-red-700 transition-colors"
            >
              <RefreshCw size={13} /> Retry
            </button>
          </div>
        )}

        {!logs.isError && logs.isPending && (
          <div className="divide-y divide-slate-100">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-4 py-4 animate-pulse">
                <div className="h-3 w-24 bg-slate-200 rounded" />
                <div className="h-3 w-32 bg-slate-200 rounded" />
                <div className="h-3 w-28 bg-slate-200 rounded" />
                <div className="h-3 flex-1 bg-slate-100 rounded" />
              </div>
            ))}
          </div>
        )}

        {!logs.isError && !logs.isPending && items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] text-sm">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  {['Timestamp', 'Actor', 'Action', 'Entity', 'Changes', 'IP'].map((h) => (
                    <th key={h} className="text-left text-xs font-semibold text-slate-500 uppercase tracking-wider px-4 py-3">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {items.map((log, i) => {
                  const style = entityStyle(log.entity_type);
                  return (
                    <motion.tr
                      key={log.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: Math.min(i * 0.03, 0.3) }}
                      className="hover:bg-slate-50 transition-colors"
                    >
                      <td className="px-4 py-3.5 text-slate-500 text-xs whitespace-nowrap">
                        {formatTimestamp(log.created_at)}
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="font-medium text-slate-900">{log.actor_name || 'System'}</div>
                        <div className="text-xs text-slate-400">
                          {(log.actor_role || '—').replace(/_/g, ' ')}
                        </div>
                      </td>
                      <td className="px-4 py-3.5">
                        <span
                          className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold ${style.color} ${style.bg}`}
                        >
                          {style.icon} {log.action.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td className="px-4 py-3.5">
                        <div className="text-xs text-slate-600 font-medium">{style.label}</div>
                        <div className="text-xs text-slate-400 font-mono">{log.entity_id || '—'}</div>
                      </td>
                      <td className="px-4 py-3.5 text-slate-600 text-xs max-w-[260px]">
                        <span className="line-clamp-2" title={describeChange(log)}>
                          {describeChange(log)}
                        </span>
                      </td>
                      <td className="px-4 py-3.5 font-mono text-xs text-slate-400">{log.ip_address || '—'}</td>
                    </motion.tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {!logs.isError && !logs.isPending && items.length === 0 && (
          <div className="text-center py-16 text-slate-400">
            <FileLock size={36} className="mx-auto mb-3 opacity-30" />
            <p className="font-medium">No audit log entries found.</p>
            <p className="text-xs mt-1">Try widening the date range or clearing the filters.</p>
          </div>
        )}

        {/* Cursor-stable server pagination (offset mode: page X of Y) */}
        {!logs.isError && !logs.isPending && pagination && pagination.total_pages > 1 && (
          <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-4 py-3">
            <p className="text-xs text-slate-500">
              Page <span className="font-semibold text-slate-700">{pagination.page}</span> of{' '}
              <span className="font-semibold text-slate-700">{pagination.total_pages}</span> ·{' '}
              {pagination.total.toLocaleString('en-IN')} events
            </p>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1 || logs.isFetching}
                className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-200 text-slate-600 hover:border-slate-400 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Previous
              </button>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={page >= pagination.total_pages || logs.isFetching}
                className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-slate-200 text-slate-600 hover:border-slate-400 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
