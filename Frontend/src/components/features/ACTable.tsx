// src/components/features/ACTable.tsx
'use client';

import React, { useMemo } from 'react';
import { motion } from 'framer-motion';
import {
  CheckCircle2, AlertOctagon, Wrench, PowerOff, Eye, Edit3, Trash2, MapPin,
  ChevronLeft, ChevronRight, ArrowUpDown, ShieldCheck, Airplay, Save, X
} from 'lucide-react';
import type { ExtendedAC, InlineDraft } from '@/lib/types/asset';
import { ProtectedAction } from '@/components/auth/ProtectedRoute';
import Link from 'next/link';
import { useAuth } from '@/contexts/AuthContext';
import { useState } from 'react';
import { canEditInventory } from '@/lib/permissions';

export type { InlineDraft };

export interface TablePagination {
  page: number;
  pageSize: number;
  total: number;
  totalPages: number;
}

interface ACTableProps {
  acList: ExtendedAC[];
  loading?: boolean;
  onEdit?: (ac: ExtendedAC) => void;
  onDeactivate?: (ac: ExtendedAC) => void;
  /** Server-driven paging (Phase 3 task 2). Falls back to local slicing. */
  pagination?: TablePagination;
  onPageChange?: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  /** Whitelisted backend sort key ('ac_id', '-capacity', ...). */
  sort?: string;
  onSortChange?: (sort: string) => void;
  /** Persist an inline edit; receives the ac and the UI-shaped draft. */
  onInlineSave?: (ac: ExtendedAC, draft: InlineDraft) => Promise<void>;
}

const STATUS_BADGES: Record<string, { bg: string; text: string; icon: React.ReactNode }> = {
  Working: {
    bg: 'bg-emerald-50 text-emerald-700 border border-emerald-200',
    text: 'Working',
    icon: <CheckCircle2 size={12} className="inline mr-1" />
  },
  Fault: {
    bg: 'bg-red-50 text-red-700 border border-red-200',
    text: 'Faulty',
    icon: <AlertOctagon size={12} className="inline mr-1" />
  },
  Maintenance: {
    bg: 'bg-amber-50 text-amber-700 border border-amber-200',
    text: 'Maintenance',
    icon: <Wrench size={12} className="inline mr-1" />
  },
  Offline: {
    bg: 'bg-slate-50 text-slate-600 border border-slate-200',
    text: 'Offline',
    icon: <PowerOff size={12} className="inline mr-1" />
  }
};

/** Header sort key -> backend whitelist key (SORT_KEYS in assets.py). */
const SORTABLE: Record<string, string> = {
  id: 'ac_id',
  make: 'name',
  status: 'status',
  buildingName: 'block',
  capacity: 'capacity',
  nextService: 'next_service',
};

type SortField = 'id' | 'make' | 'type' | 'capacity' | 'status' | 'buildingName' | 'lastMaintenanceDate' | 'nextService';

export const ACTable: React.FC<ACTableProps> = ({
  acList,
  loading = false,
  onEdit,
  onDeactivate,
  pagination,
  onPageChange,
  onPageSizeChange,
  sort,
  onSortChange,
  onInlineSave
}) => {
  const { user } = useAuth();
  const [localSortField, setLocalSortField] = useState<SortField>('id');
  const [localSortAsc, setLocalSortAsc] = useState(true);
  const [localPage, setLocalPage] = useState(1);
  const [localPageSize, setLocalPageSize] = useState(10);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<InlineDraft>({});
  const [saving, setSaving] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const canEditAcInventory = canEditInventory(user?.role ?? null);

  const controlled = Boolean(onPageChange);
  const sortControlled = Boolean(onSortChange);

  const roleBase = user?.role === 'SUPER_ADMIN' ? '/admin'
    : user?.role === 'FACILITY_MANAGER' ? '/manager'
    : user?.role === 'TECHNICIAN' ? '/technician'
    : '/viewer';

  // Uncontrolled fallback: local sort + slice (small, non-paged usages).
  const sortValue = (ac: ExtendedAC): string => {
    switch (localSortField) {
      case 'buildingName':
        return ac.buildingName || ac.location.buildingId || '';
      case 'nextService':
        return ac.nextMaintenanceDate || '';
      case 'lastMaintenanceDate':
        return ac.lastMaintenanceDate || '';
      case 'capacity':
        return String(ac.capacityTon ?? '');
      case 'type':
        return ac.type || '';
      default: {
        const v = ac[localSortField];
        return typeof v === 'string' ? v : '';
      }
    }
  };

  const sortedList = useMemo(() => {
    if (sortControlled) return acList;
    return [...acList].sort((a, b) => {
      const valA = sortValue(a);
      const valB = sortValue(b);
      const cmp = valA.localeCompare(valB, undefined, { numeric: true });
      return localSortAsc ? cmp : -cmp;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [acList, localSortField, localSortAsc, sortControlled]);

  const pageSize = pagination?.pageSize ?? localPageSize;
  const totalPages = (pagination?.totalPages ?? Math.ceil(sortedList.length / pageSize)) || 1;
  const page = pagination?.page ?? localPage;
  const total = pagination?.total ?? sortedList.length;
  const paginatedList = useMemo(() => {
    if (controlled || sortControlled) return sortedList;
    const start = (page - 1) * pageSize;
    return sortedList.slice(start, start + pageSize);
  }, [sortedList, page, pageSize, controlled, sortControlled]);

  const currentSortKey = sort ?? (sortControlled ? '' : (localSortAsc ? localSortField : `-${localSortField}`));

  const handleSort = (field: SortField) => {
    const key = SORTABLE[field];
    if (!key) return;
    if (onSortChange) {
      const desc = currentSortKey === `-${key}`;
      onSortChange(desc ? key : `-${key}`);
      return;
    }
    if (localSortField === field) {
      setLocalSortAsc(!localSortAsc);
    } else {
      setLocalSortField(field);
      setLocalSortAsc(true);
    }
  };

  const sortArrow = (field: SortField) => {
    const key = SORTABLE[field];
    const active = key && (currentSortKey === key || currentSortKey === `-${key}`);
    const desc = key && currentSortKey === `-${key}`;
    return (
      <ArrowUpDown
        size={12}
        className={active ? (desc ? 'text-blue-500 rotate-180' : 'text-blue-500') : 'text-slate-400'}
      />
    );
  };

  const startInlineEdit = (ac: ExtendedAC) => {
    if (!canEditAcInventory) return;
    setInlineError(null);
    setEditingId(ac.id);
    setDraft({
      status: ac.status,
      technician: ac.technician ?? '',
      remarks: ac.remarks ?? '',
      lastMaintenanceDate: ac.lastMaintenanceDate ?? '',
      nextMaintenanceDate: ac.nextMaintenanceDate ?? '',
    });
  };

  const saveInlineEdit = async (ac: ExtendedAC) => {
    if (!canEditAcInventory || !onInlineSave) return;
    setSaving(true);
    setInlineError(null);
    try {
      await onInlineSave(ac, draft);
      setEditingId(null);
      setDraft({});
    } catch (err) {
      // Keep the editor open with the draft intact; surface the message.
      setInlineError(err instanceof Error ? err.message : 'Saving failed.');
    } finally {
      setSaving(false);
    }
  };

  const gotoPage = (next: number) => {
    if (onPageChange) onPageChange(next);
    else setLocalPage(next);
  };

  const changePageSize = (size: number) => {
    if (onPageSizeChange) onPageSizeChange(size);
    else {
      setLocalPageSize(size);
      setLocalPage(1);
    }
  };

  if (loading) {
    return (
      <div className="bg-white rounded-xl border border-slate-200 p-8 shadow-2xs text-center animate-pulse space-y-4">
        <div className="h-6 w-48 bg-slate-200 rounded mx-auto" />
        <div className="h-4 w-64 bg-slate-100 rounded mx-auto" />
        <div className="h-64 bg-slate-50 rounded-xl" />
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-slate-200 shadow-2xs overflow-hidden">
      {inlineError && (
        <div className="px-4 py-2 bg-red-50 border-b border-red-200 text-xs text-red-700 font-medium">
          {inlineError}
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm border-collapse">
          <thead className="bg-slate-50 border-b border-slate-200 text-xs font-semibold text-slate-500 uppercase tracking-wider">
            <tr>
              <th
                className="px-4 py-3.5 cursor-pointer hover:bg-slate-100 transition-colors select-none"
                onClick={() => handleSort('id')}
              >
                <div className="flex items-center gap-1">
                  AC ID {sortArrow('id')}
                </div>
              </th>
              <th
                className="px-4 py-3.5 cursor-pointer hover:bg-slate-100 transition-colors select-none"
                onClick={() => handleSort('make')}
              >
                <div className="flex items-center gap-1">
                  Make / Model {sortArrow('make')}
                </div>
              </th>
              <th className="px-4 py-3.5">Type</th>
              <th
                className="px-4 py-3.5 cursor-pointer hover:bg-slate-100 transition-colors select-none"
                onClick={() => handleSort('capacity')}
              >
                <div className="flex items-center gap-1">
                  Capacity {sortArrow('capacity')}
                </div>
              </th>
              <th
                className="px-4 py-3.5 cursor-pointer hover:bg-slate-100 transition-colors select-none"
                onClick={() => handleSort('status')}
              >
                <div className="flex items-center gap-1">
                  Status {sortArrow('status')}
                </div>
              </th>
              <th
                className="px-4 py-3.5 cursor-pointer hover:bg-slate-100 transition-colors select-none"
                onClick={() => handleSort('buildingName')}
              >
                <div className="flex items-center gap-1">
                  Location (Building / Room) {sortArrow('buildingName')}
                </div>
              </th>
              <th className="px-4 py-3.5">Last Maintenance</th>
              <th className="px-4 py-3.5">Next Service</th>
              <th className="px-4 py-3.5">Lifecycle</th>
              <th className="px-4 py-3.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 font-normal text-slate-700">
            {paginatedList.map((ac, idx) => {
              const statusBadge = STATUS_BADGES[ac.status] || STATUS_BADGES.Offline;
              const detailHref = `${roleBase}/assets/${encodeURIComponent(ac.id)}`;
              const decommissioned = (ac.lifecycleStatus ?? '').toUpperCase() === 'DECOMMISSIONED' || ac.isActive === false;
              return (
                <motion.tr
                  key={ac.id}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: idx * 0.02 }}
                  className="hover:bg-slate-50/80 transition-colors group"
                >
                  <td className="px-4 py-3.5 font-mono font-semibold whitespace-nowrap">
                    <Link
                      href={detailHref}
                      className="text-blue-700 hover:text-blue-900 hover:underline text-left cursor-pointer transition-colors"
                      title={`Click to view detail page for ${ac.visualId ?? ac.acId ?? ac.id}`}
                    >
                      {ac.visualId ?? ac.acId ?? ac.id}
                    </Link>
                  </td>
                  <td className="px-4 py-3.5">
                    <div className="font-semibold text-slate-900">{ac.make} {ac.model}</div>
                    <div className="text-[11px] text-slate-400 font-mono">SN: {ac.serialNumber || 'N/A'}</div>
                  </td>
                  <td className="px-4 py-3.5 whitespace-nowrap text-slate-600">
                    {ac.type}
                  </td>
                  <td className="px-4 py-3.5 whitespace-nowrap text-slate-600">
                    {ac.capacity}
                  </td>
                  <td className="px-4 py-3.5 whitespace-nowrap">
                    {editingId === ac.id && canEditAcInventory ? (
                      <select
                        value={draft.status ?? ac.status}
                        onChange={e => setDraft(prev => ({ ...prev, status: e.target.value as ExtendedAC['status'] }))}
                        className="border border-slate-200 rounded-lg px-2 py-1 text-xs font-semibold text-slate-700 focus:ring-2 focus:ring-blue-400 outline-none"
                      >
                        <option value="Working">Working</option>
                        <option value="Fault">Fault</option>
                        <option value="Maintenance">Maintenance</option>
                        <option value="Offline">Offline</option>
                      </select>
                    ) : (
                      <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-xs font-semibold border ${statusBadge.bg}`}>
                        {statusBadge.icon} {statusBadge.text}
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3.5 max-w-[200px]">
                    <div className="text-slate-800 font-medium truncate">
                      {ac.buildingName || ac.location.buildingId}
                    </div>
                    <div className="text-xs text-slate-400 flex items-center gap-1 truncate">
                      <MapPin size={11} className="text-slate-400 shrink-0" />
                      {ac.roomName || ac.location.roomId || 'General Room'}
                    </div>
                  </td>
                  <td className="px-4 py-3.5 whitespace-nowrap text-xs text-slate-600">
                    {editingId === ac.id && canEditAcInventory ? (
                      <input
                        value={draft.lastMaintenanceDate ?? ac.lastMaintenanceDate ?? ''}
                        onChange={e => setDraft(prev => ({ ...prev, lastMaintenanceDate: e.target.value }))}
                        className="border border-slate-200 rounded-md px-2 py-1 text-[11px] w-28 focus:ring-2 focus:ring-blue-400 outline-none"
                      />
                    ) : (
                      <>
                        <div>{ac.lastMaintenanceDate || 'Not Recorded'}</div>
                        <div className="text-slate-400 text-[11px]">{ac.lastMaintenanceType || 'Routine'}</div>
                      </>
                    )}
                  </td>
                  <td className="px-4 py-3.5 whitespace-nowrap text-xs text-slate-600">
                    {editingId === ac.id && canEditAcInventory ? (
                      <input
                        value={draft.nextMaintenanceDate ?? ac.nextMaintenanceDate ?? ''}
                        onChange={e => setDraft(prev => ({ ...prev, nextMaintenanceDate: e.target.value }))}
                        className="border border-slate-200 rounded-md px-2 py-1 text-[11px] w-24 focus:ring-2 focus:ring-blue-400 outline-none"
                      />
                    ) : (
                      <div>{ac.nextMaintenanceDate || 'Scheduled'}</div>
                    )}
                  </td>
                  <td className="px-4 py-3.5 whitespace-nowrap">
                    {decommissioned ? (
                      <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-600 border border-slate-200">
                        Decommissioned
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                        <ShieldCheck size={10} /> Active
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3.5 text-right whitespace-nowrap">
                    <div className="flex items-center justify-end gap-1 opacity-90 group-hover:opacity-100 transition-opacity">
                      {/* View detail page */}
                      <Link
                        href={detailHref}
                        className="p-1.5 rounded-lg hover:bg-blue-50 text-blue-600 transition-colors"
                        title="View detail page"
                      >
                        <Eye size={15} />
                      </Link>
                      {onEdit && (
                        <ProtectedAction permission="EDIT_AC">
                          <button
                            onClick={() => onEdit(ac)}
                            className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-600 transition-colors"
                            title="Edit Record"
                          >
                            <Edit3 size={15} />
                          </button>
                        </ProtectedAction>
                      )}

                      {canEditAcInventory && onInlineSave && (
                        editingId === ac.id ? (
                          <>
                            <button
                              onClick={() => saveInlineEdit(ac)}
                              disabled={saving}
                              className="p-1.5 rounded-lg hover:bg-emerald-50 text-emerald-600 transition-colors disabled:opacity-50"
                              title="Save changes"
                            >
                              <Save size={15} />
                            </button>
                            <button
                              onClick={() => {
                                setEditingId(null);
                                setDraft({});
                                setInlineError(null);
                              }}
                              className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-600 transition-colors"
                              title="Cancel edit"
                            >
                              <X size={15} />
                            </button>
                          </>
                        ) : (
                          <button
                            onClick={() => startInlineEdit(ac)}
                            className="p-1.5 rounded-lg hover:bg-slate-100 text-slate-600 transition-colors"
                            title="Edit inline"
                          >
                            <Edit3 size={15} />
                          </button>
                        )
                      )}
                      {onDeactivate && (
                        <ProtectedAction permission="DEACTIVATE_AC">
                          <button
                            onClick={() => onDeactivate(ac)}
                            className="p-1.5 rounded-lg hover:bg-red-50 text-red-500 transition-colors"
                            title="Deactivate"
                          >
                            <Trash2 size={15} />
                          </button>
                        </ProtectedAction>
                      )}
                      <Link
                        href={`/campus?acId=${ac.id}`}
                        className="p-1.5 rounded-lg hover:bg-emerald-50 text-emerald-600 transition-colors"
                        title="View on 3D Campus Map"
                      >
                        <MapPin size={15} />
                      </Link>
                    </div>
                  </td>
                </motion.tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {paginatedList.length === 0 && (
        <div className="text-center py-16 text-slate-400">
          <Airplay size={40} className="mx-auto mb-3 opacity-30" />
          <p className="font-semibold text-slate-600 text-base">No AC assets match your filters.</p>
          <p className="text-xs text-slate-400 mt-1">Try resetting your search query or building filters.</p>
        </div>
      )}

      {/* Pagination Controls */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4 p-4 border-t border-slate-100 bg-slate-50/50 text-xs text-slate-600">
        <div className="flex items-center gap-2">
          <span>Show</span>
          <select
            value={pageSize}
            onChange={(e) => changePageSize(Number(e.target.value))}
            className="px-2 py-1 border border-slate-200 rounded-md bg-white font-medium focus:ring-2 focus:ring-blue-500 outline-none"
          >
            <option value={10}>10 rows</option>
            <option value={25}>25 rows</option>
            <option value={50}>50 rows</option>
            <option value={100}>100 rows</option>
          </select>
          <span>of <strong className="text-slate-900">{total}</strong> total ACs</span>
        </div>

        <div className="flex items-center gap-2">
          <button
            disabled={page === 1}
            onClick={() => gotoPage(page - 1)}
            className="p-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 disabled:opacity-40 disabled:hover:bg-white transition-colors"
          >
            <ChevronLeft size={16} />
          </button>
          <span>
            Page <strong className="text-slate-900">{page}</strong> of <strong>{totalPages}</strong>
          </span>
          <button
            disabled={page >= totalPages}
            onClick={() => gotoPage(page + 1)}
            className="p-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-100 disabled:opacity-40 disabled:hover:bg-white transition-colors"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>
    </div>
  );
};
