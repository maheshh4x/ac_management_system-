// src/components/features/assets/ACDetailView.tsx
'use client';

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ArrowLeft, CheckCircle2, AlertOctagon, Wrench, PowerOff,
  MapPin, Calendar, User, Star, Zap, Building2, Tag,
  ClipboardList, Activity, Shield, FileText, Edit3, Save, X,
  ChevronRight, Clock, AlertTriangle, Info, RefreshCw, MoveRight, QrCode
} from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';
import { ApiError } from '@/lib/api/client';
import { useAssetDetail, useUpdateAsset } from '@/lib/api/assets';
import { detailEditToPayload, effectiveLocationText, locationLevelText, locationReviewLevels } from '@/lib/adapters/asset';
import type { ExtendedAC } from '@/lib/types/asset';
import { useJobs } from '@/lib/api/maintenance';
import { useAuth } from '@/contexts/AuthContext';
import { canEditInventory, hasPermission } from '@/lib/permissions';
import { AcQrActions } from '@/components/features/qr/AcQrActions';

interface InfoCardProps {
  label: string;
  value: string;
  icon?: React.ReactNode;
}

function InfoCard({ label, value, icon }: InfoCardProps) {
  return (
    <div className="bg-slate-50 rounded-xl p-3.5 border border-slate-100">
      <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase text-slate-400 mb-1">
        {icon && <span className="text-slate-400">{icon}</span>}
        {label}
      </div>
      <div className="text-sm font-semibold text-slate-800 truncate">{value || '—'}</div>
    </div>
  );
}

interface EditableFieldProps {
  label: string;
  fieldKey: keyof ExtendedAC;
  value: string;
  isEditing: boolean;
  canEdit: boolean;
  icon?: React.ReactNode;
  onChange: (key: keyof ExtendedAC, value: string) => void;
}

function EditableField({ label, fieldKey, value, isEditing, canEdit, icon, onChange }: EditableFieldProps) {
  return (
    <div className="bg-slate-50 rounded-xl p-3.5 border border-slate-100">
      <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase text-slate-400 mb-1">
        {icon && <span>{icon}</span>}
        {label}
      </div>
      {isEditing && canEdit ? (
        <input
          className="w-full text-sm font-semibold text-slate-800 bg-white border border-blue-300 rounded-lg px-2 py-1 outline-none focus:ring-2 focus:ring-blue-400"
          value={value}
          onChange={e => onChange(fieldKey, e.target.value)}
        />
      ) : (
        <div className="text-sm font-semibold text-slate-800 truncate">{value || '—'}</div>
      )}
    </div>
  );
}

// Lazy-load 3D viewer to avoid SSR issues
const AC3DViewer = dynamic(
  () => import('@/components/features/3d/AC3DViewer').then(m => ({ default: m.AC3DViewer })),
  { ssr: false, loading: () => (
    <div className="rounded-2xl bg-gradient-to-br from-slate-900 to-slate-800 border border-slate-700 flex items-center justify-center" style={{ height: 380 }}>
      <div className="text-slate-500 text-sm animate-pulse">Loading 3D Model…</div>
    </div>
  )}
);

// ── Status helpers ────────────────────────────────────────────────────────────
const STATUS_CONFIG: Record<string, { bg: string; text: string; icon: React.ReactNode; ring: string }> = {
  Working:     { bg: 'bg-emerald-100 text-emerald-800 border-emerald-200', text: 'Working',     icon: <CheckCircle2 size={14} />, ring: 'ring-emerald-400' },
  Fault:       { bg: 'bg-red-100 text-red-800 border-red-200',           text: 'Fault',        icon: <AlertOctagon size={14} />, ring: 'ring-red-400'     },
  Maintenance: { bg: 'bg-amber-100 text-amber-800 border-amber-200',     text: 'Maintenance',  icon: <Wrench size={14} />,       ring: 'ring-amber-400'  },
  Offline:     { bg: 'bg-slate-100 text-slate-600 border-slate-200',     text: 'Offline',      icon: <PowerOff size={14} />,     ring: 'ring-slate-400'  },
};

const TABS = ['Overview', 'Maintenance History', 'Energy Usage', 'Warranty & Docs'];

interface ACDetailViewProps {
  acId: string;
  backHref?: string;
}

export function ACDetailView({ acId, backHref = '..' }: ACDetailViewProps) {
  const router = useRouter();
  const { user } = useAuth();
  const canEdit = canEditInventory(user?.role ?? null) || hasPermission(user?.role ?? null, 'EDIT_AC');

  // Enriched detail: full location chain, histories, attributes, qr_version.
  // The route param is the asset_uuid (ACTable links by uuid; the endpoint
  // also accepts asset_code / ac_id).
  const { data: ac, isLoading, isError, error: loadError, refetch } = useAssetDetail(acId);
  const updateAsset = useUpdateAsset();

  const [activeTab, setActiveTab] = useState(0);
  const [isEditing, setIsEditing] = useState(false);
  const [editedFields, setEditedFields] = useState<Partial<ExtendedAC>>({});
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  // Maintenance jobs for this unit (business key — the route param may be a
  // UUID while work orders filter on the AC-… asset code).
  const { jobs: maintenanceJobs } = useJobs({ acId: ac?.acId, pageSize: 200 }, Boolean(ac?.acId));

  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-slate-400">
        <div className="w-10 h-10 border-4 border-slate-200 border-t-blue-600 rounded-full animate-spin mb-4" />
        <p className="text-sm font-semibold text-slate-500">Loading asset…</p>
      </div>
    );
  }

  if (isError || !ac) {
    const notFound =
      loadError instanceof ApiError &&
      (loadError.status === 404 || loadError.code === 'ASSET_NOT_FOUND');
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] text-slate-400">
        <AlertTriangle size={48} className="mb-4 opacity-40" />
        <p className="text-lg font-semibold text-slate-600">
          {notFound ? 'AC Unit Not Found' : 'Failed to Load Asset'}
        </p>
        <p className="text-sm mt-1">
          {notFound ? (
            <>
              ID <span className="font-mono text-slate-500">{acId}</span> does not exist in the database.
            </>
          ) : (
            (loadError instanceof Error ? loadError.message : 'Unexpected error.')
          )}
        </p>
        <div className="mt-6 flex items-center gap-2">
          {!notFound && (
            <button
              onClick={() => refetch()}
              className="flex items-center gap-2 px-4 py-2 bg-white border border-slate-200 text-slate-700 rounded-xl text-sm font-semibold hover:bg-slate-50 transition-colors"
            >
              <RefreshCw size={15} /> Retry
            </button>
          )}
          <button onClick={() => (backHref ? router.push(backHref) : router.back())} className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-xl text-sm font-semibold hover:bg-blue-700 transition-colors">
            <ArrowLeft size={16} /> Go Back
          </button>
        </div>
      </div>
    );
  }

  const statusCfg = STATUS_CONFIG[ac.status] ?? STATUS_CONFIG.Offline;
  // Levels whose preserved source has no confirmed id (§13) — shown as a
  // pending-review strip inside the Location card; empty = fully settled.
  const reviewKeys = locationReviewLevels(ac.locationDetail);

  // ── Edit handlers ──────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!ac || Object.keys(editedFields).length === 0) return;

    setSaving(true);
    setSaveMsg(null);

    try {
      const payload = detailEditToPayload(editedFields);
      if (Object.keys(payload).length === 0) {
        throw new Error('Nothing to save.');
      }
      // Optimistic: useUpdateAsset previews the patch, rolls back on error.
      await updateAsset.mutateAsync({ acId: ac.acId ?? ac.id, payload });

      setIsEditing(false);
      setEditedFields({});
      setSaveMsg('Changes saved successfully.');
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : 'Save failed. Please try again.';
      setSaveMsg(message);
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(null), 4000);
    }
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
    setEditedFields({});
  };

  const field = (key: keyof ExtendedAC) =>
    key in editedFields ? (editedFields[key] as string) : (ac[key] as string | undefined) ?? '';

  const setField = (key: keyof ExtendedAC, value: string) =>
    setEditedFields(prev => ({ ...prev, [key]: value }));

  return (
    <div className="flex flex-col gap-6 pb-10">
      {/* Breadcrumb/back nav */}
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        className="flex items-center gap-2 text-sm text-slate-500"
      >
        <button onClick={() => (backHref ? router.push(backHref) : router.back())} className="flex items-center gap-1.5 hover:text-blue-600 transition-colors font-medium">
          <ArrowLeft size={15} /> Back
        </button>
        <ChevronRight size={14} className="text-slate-300" />
        <span className="text-slate-400">AC Assets</span>
        <ChevronRight size={14} className="text-slate-300" />
        <span className="text-slate-900 font-semibold" title={ac.id}>{ac.visualId ?? ac.acId ?? ac.id}</span>
      </motion.div>

      {/* Two-col hero layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* LEFT — 3D Model Viewer */}
        <motion.div initial={{ opacity: 0, x: -20 }} animate={{ opacity: 1, x: 0 }} transition={{ duration: 0.4 }}>
          <AC3DViewer modelUrl="/model/ac_model3.glb" height={400} />
        </motion.div>

        {/* RIGHT — Identity card */}
        <motion.div
          initial={{ opacity: 0, x: 20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.4, delay: 0.05 }}
          className="flex flex-col gap-4"
        >
          {/* Header card */}
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
            <div className="flex items-start justify-between gap-4 mb-4">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <h1 className="text-2xl font-bold text-slate-900 font-mono" title={`Technical ID: ${ac.id}`}>
                    {ac.visualId ?? ac.acId ?? ac.id}
                  </h1>
                  <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold border ${statusCfg.bg}`}>
                    {statusCfg.icon} {statusCfg.text}
                  </span>
                </div>
                <p className="text-slate-500 text-sm">{ac.make} {ac.model}</p>
                <p className="text-xs text-slate-400 font-mono mt-0.5">SN: {ac.serialNumber || 'N/A'}</p>
              </div>
              {ac.lifecycleStatus === 'DECOMMISSIONED' ? (
                <span className="px-2 py-1 rounded-lg bg-amber-50 text-amber-700 border border-amber-200 text-[10px] font-bold uppercase tracking-wide whitespace-nowrap">Decommissioned</span>
              ) : (
                <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg bg-emerald-50 text-emerald-700 border border-emerald-200 text-[10px] font-bold uppercase tracking-wide whitespace-nowrap">
                  <Shield size={10} /> Official
                </span>
              )}
            </div>

            {/* Quick stats row */}
            <div className="grid grid-cols-3 gap-3 text-center">
              <div className="bg-blue-50 rounded-xl p-3">
                <div className="text-lg font-bold text-blue-700">{ac.capacity || '—'}</div>
                <div className="text-[10px] text-blue-500 font-semibold uppercase">Capacity</div>
              </div>
              <div className="bg-purple-50 rounded-xl p-3">
                <div className="text-lg font-bold text-purple-700">{ac.type || '—'}</div>
                <div className="text-[10px] text-purple-500 font-semibold uppercase">Type</div>
              </div>
              <div className="bg-amber-50 rounded-xl p-3">
                <div className="text-lg font-bold text-amber-700">{ac.starRating ? `${ac.starRating}★` : '—'}</div>
                <div className="text-[10px] text-amber-500 font-semibold uppercase">Star Rating</div>
              </div>
            </div>
          </div>

          {/* Location card */}
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-5">
            <h3 className="text-xs font-bold uppercase text-slate-400 mb-3 flex items-center gap-1.5">
              <MapPin size={13} /> Location
            </h3>
            {reviewKeys.length > 0 && (
              <div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 p-3">
                <p className="flex items-center gap-1.5 text-[11px] font-bold uppercase text-amber-700">
                  <AlertTriangle size={12} /> Location needs review
                </p>
                <ul className="mt-2 space-y-1.5">
                  {reviewKeys.map(key => {
                    const lvl = ac.locationDetail?.levels[key];
                    const ambiguous = lvl?.status === 'AMBIGUOUS';
                    return (
                      <li key={key} className="flex items-baseline gap-2 text-[11px]">
                        <span className="w-20 shrink-0 font-bold uppercase text-amber-800">{key}</span>
                        <span className="flex-1 truncate font-mono text-amber-700" title={lvl?.source ?? ''}>
                          &ldquo;{locationLevelText(lvl)}&rdquo;
                        </span>
                        <span className={`shrink-0 rounded-full border px-1.5 py-0.5 text-[9px] font-bold ${ambiguous ? 'border-orange-200 bg-orange-100 text-orange-700' : 'border-amber-200 bg-amber-100 text-amber-800'}`}>
                          {lvl?.status}
                        </span>
                      </li>
                    );
                  })}
                </ul>
                <p className="mt-2 text-[10px] leading-relaxed text-amber-600">
                  The import supplied text that matched nothing yet — finish it from the campus Location review tab or
                  with a manual confirmation. The source text above is kept as-is.
                </p>
              </div>
            )}
            <div className="grid grid-cols-2 gap-2.5 text-xs">
              {/* One effective-location helper for every level (§7):
                  canonical name -> canonical id -> preserved source -> "—",
                  for ASSIGNED, PARTIAL and PENDING alike. A room_id of NULL
                  never hides the other available dimensions. */}
              <InfoCard label="Building" value={effectiveLocationText(ac, "block")} icon={<Building2 size={11} />} />
              <InfoCard label="Floor" value={effectiveLocationText(ac, "floor")} />
              <InfoCard label="Room" value={effectiveLocationText(ac, "room")} />
              <InfoCard label="Department" value={effectiveLocationText(ac, "department")} />
            </div>
          </div>

          {/* Asset QR (reuses AcQrActions: preview + branded download + sheet
              + regenerate; the panel self-gates to permitted roles and the
              backend enforces every action — the card only renders where the
              panel does, so nobody sees an empty card) */}
          {user?.role === 'SUPER_ADMIN' && (
          <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-5">
            <h3 className="text-xs font-bold uppercase text-slate-400 mb-3 flex items-center gap-1.5">
              <QrCode size={13} /> Asset QR
            </h3>
            <AcQrActions acId={ac.acId ?? ac.id} asset={ac} />
          </div>
          )}

          {/* Actions (Admin only) */}
          {canEdit && (
            <div className="flex gap-2">
              {!isEditing ? (
                <button
                  onClick={() => setIsEditing(true)}
                  className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-xl text-sm font-semibold transition-colors shadow-sm"
                >
                  <Edit3 size={14} /> Edit Unit
                </button>
              ) : (
                <>
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="flex items-center gap-2 px-4 py-2.5 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-sm font-semibold transition-colors shadow-sm disabled:opacity-60"
                  >
                    <Save size={14} /> {saving ? 'Saving…' : 'Save Changes'}
                  </button>
                  <button
                    onClick={handleCancelEdit}
                    className="flex items-center gap-2 px-4 py-2.5 bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 rounded-xl text-sm font-semibold transition-colors"
                  >
                    <X size={14} /> Cancel
                  </button>
                </>
              )}
              <Link
                href={`/campus?acId=${ac.id}`}
                className="flex items-center gap-2 px-4 py-2.5 bg-white border border-slate-200 hover:bg-slate-50 text-emerald-700 rounded-xl text-sm font-semibold transition-colors"
              >
                <MapPin size={14} /> Locate on 3D Twin Map
              </Link>
            </div>
          )}
          {!canEdit && (
            <Link
              href={`/campus?acId=${ac.id}`}
              className="inline-flex items-center gap-2 px-4 py-2.5 bg-white border border-slate-200 hover:bg-emerald-50 text-emerald-700 rounded-xl text-sm font-semibold transition-colors w-fit"
            >
              <MapPin size={14} /> Locate on 3D Twin Map
            </Link>
          )}

          {/* Save confirmation */}
          <AnimatePresence>
            {saveMsg && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="flex items-center gap-2 text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-2.5 text-sm font-semibold"
              >
                <CheckCircle2 size={15} /> {saveMsg}
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>

      {/* Tabs */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}>
        <div className="flex gap-1 bg-slate-100 rounded-xl p-1 w-fit">
          {TABS.map((tab, i) => (
            <button
              key={tab}
              onClick={() => setActiveTab(i)}
              className={`px-4 py-2 rounded-lg text-sm font-semibold transition-all ${
                activeTab === i
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        <div className="mt-4">
          {/* Tab 0: Overview */}
          {activeTab === 0 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              {/* Technical specs */}
              <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-5">
                <h3 className="text-xs font-bold uppercase text-slate-400 mb-4 flex items-center gap-1.5">
                  <Activity size={13} /> Technical Specifications
                </h3>
                <div className="grid grid-cols-2 gap-2.5 text-xs">
                  <EditableField label="Brand / Make" fieldKey="make" value={field('make')} isEditing={isEditing} canEdit={canEdit} icon={<Tag size={11} />} onChange={setField} />
                  <EditableField label="Model" fieldKey="model" value={field('model')} isEditing={isEditing} canEdit={canEdit} onChange={setField} />
                  <EditableField label="Serial Number" fieldKey="serialNumber" value={field('serialNumber')} isEditing={isEditing} canEdit={canEdit} onChange={setField} />
                  <InfoCard label="Capacity" value={ac.capacity || '—'} icon={<Zap size={11} />} />
                  <InfoCard label="Power Rating" value={ac.powerRating || 'Not recorded'} icon={<Zap size={11} />} />
                  <InfoCard label="Power Factor" value={ac.powerFactor || 'Not recorded'} />
                  <InfoCard label="Manufacturing Year" value={ac.manufacturingYear || 'Not recorded'} icon={<Calendar size={11} />} />
                  <InfoCard label="Installation Year" value={ac.installationYear || 'Not recorded'} icon={<Calendar size={11} />} />
                  {isEditing && canEdit ? (
                    <EditableField label="Status" fieldKey="status" value={field('status')} isEditing={isEditing} canEdit={canEdit} onChange={setField} />
                  ) : (
                    <div className="bg-slate-50 rounded-xl p-3.5 border border-slate-100">
                      <div className="text-[10px] font-bold uppercase text-slate-400 mb-1">Status</div>
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${statusCfg.bg}`}>
                        {statusCfg.icon} {ac.status}
                      </span>
                    </div>
                  )}
                  <EditableField label="Remarks" fieldKey="remarks" value={field('remarks')} isEditing={isEditing} canEdit={canEdit} onChange={setField} />
                </div>
              </div>

              {/* Maintenance info */}
              <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-5">
                <h3 className="text-xs font-bold uppercase text-slate-400 mb-4 flex items-center gap-1.5">
                  <Wrench size={13} /> Maintenance Information
                </h3>
                <div className="grid grid-cols-2 gap-2.5 text-xs">
                  <InfoCard label="Last Service Date" value={ac.lastMaintenanceDate || 'Not recorded'} icon={<Calendar size={11} />} />
                  <InfoCard label="Last Service Type" value={ac.lastMaintenanceType || 'Not recorded'} />
                  <InfoCard label="Next Service Date" value={ac.nextMaintenanceDate || 'Not scheduled'} icon={<Clock size={11} />} />
                  <EditableField label="Assigned Technician" fieldKey="technician" value={field('technician')} isEditing={isEditing} canEdit={canEdit} icon={<User size={11} />} onChange={setField} />
                </div>
                <div className="mt-4 p-3 bg-blue-50 rounded-xl border border-blue-100">
                  <div className="text-[10px] font-bold uppercase text-blue-400 mb-1 flex items-center gap-1"><ClipboardList size={11} /> Linked Maintenance Jobs</div>
                  <div className="text-lg font-bold text-blue-700">{maintenanceJobs.length}</div>
                  <div className="text-xs text-blue-500">jobs recorded for this unit</div>
                </div>
              </div>
            </motion.div>
          )}

          {/* Tab 1: Maintenance History */}
          {activeTab === 1 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <div className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                <div className="p-5 border-b border-slate-100 flex items-center justify-between">
                  <h3 className="font-bold text-slate-900 flex items-center gap-2"><ClipboardList size={16} /> Maintenance History for {ac.id}</h3>
                  <span className="text-xs bg-slate-100 text-slate-600 rounded-full px-3 py-1 font-semibold">{maintenanceJobs.length} jobs</span>
                </div>
                {maintenanceJobs.length === 0 ? (
                  <div className="text-center py-16 text-slate-400">
                    <Wrench size={40} className="mx-auto mb-3 opacity-30" />
                    <p className="font-semibold text-slate-600">No maintenance jobs recorded for this unit.</p>
                    <p className="text-xs mt-1">Jobs scheduled via the Maintenance module will appear here.</p>
                  </div>
                ) : (
                  <div className="divide-y divide-slate-100">
                    {maintenanceJobs.map(job => (
                      <div key={job.id} className="flex items-start gap-4 p-4 hover:bg-slate-50 transition-colors">
                        <div className="mt-0.5 p-2 rounded-lg bg-blue-50 text-blue-600"><Wrench size={14} /></div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="font-mono text-xs font-bold text-slate-500">{job.id}</span>
                            <span className="text-sm font-semibold text-slate-800">{job.type}</span>
                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                              job.status === 'Completed' ? 'bg-emerald-100 text-emerald-700' :
                              job.status === 'In Progress' ? 'bg-blue-100 text-blue-700' :
                              job.status === 'Pending' ? 'bg-amber-100 text-amber-700' :
                              'bg-slate-100 text-slate-600'
                            }`}>{job.status}</span>
                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                              job.priority === 'Critical' ? 'bg-red-100 text-red-700' :
                              job.priority === 'High' ? 'bg-orange-100 text-orange-700' :
                              'bg-slate-100 text-slate-600'
                            }`}>{job.priority}</span>
                          </div>
                          <p className="text-xs text-slate-500 mt-0.5 truncate">{job.description}</p>
                          <div className="flex items-center gap-3 mt-1 text-[11px] text-slate-400">
                            <span className="flex items-center gap-1"><Calendar size={10} /> {job.scheduledDate}</span>
                            <span className="flex items-center gap-1"><User size={10} /> {job.assignedTo}</span>
                            {job.completedDate && <span className="flex items-center gap-1 text-emerald-500"><CheckCircle2 size={10} /> Completed: {job.completedDate}</span>}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          )}

          {/* Tab 1: Maintenance History — plus Phase-5 location history
              (asset_location_history: rows written atomically with the
              room change on movement completion). */}
          {activeTab === 1 && ac.locationHistory.length > 0 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
              <div className="p-5 border-b border-slate-100 flex items-center justify-between">
                <h3 className="font-bold text-slate-900 flex items-center gap-2"><MapPin size={16} /> Location History</h3>
                <span className="text-xs bg-slate-100 text-slate-600 rounded-full px-3 py-1 font-semibold">{ac.locationHistory.length} moves</span>
              </div>
              <div className="divide-y divide-slate-100">
                {ac.locationHistory.map(row => {
                  const compose = (side: 'from' | 'to') =>
                    [row[`${side}_block_name`], row[`${side}_floor_name`], row[`${side}_room_name`]]
                      .filter(Boolean)
                      .join(' / ');
                  const at = typeof row.at === 'string' ? row.at : '';
                  return (
                    <div key={String(row.id)} className="flex items-center gap-3 p-4 hover:bg-slate-50 transition-colors text-sm">
                      <div className="mt-0.5 p-2 rounded-lg bg-blue-50 text-blue-600"><MoveRight size={14} /></div>
                      <div className="flex-1 min-w-0 flex flex-wrap items-center gap-2">
                        <span className="text-slate-700 font-medium">{compose('from') || 'Previous room'}</span>
                        <MoveRight size={14} className="text-blue-400" />
                        <span className="text-blue-700 font-medium">{compose('to') || '—'}</span>
                      </div>
                      <span className="text-xs text-slate-400 shrink-0">{at ? new Date(at).toLocaleString() : '—'}</span>
                    </div>
                  );
                })}
              </div>
            </motion.div>
          )}

          {/* Tab 2: Energy Usage */}
          {activeTab === 2 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
                <div className="flex items-start gap-4 mb-6">
                  <div className="p-3 bg-amber-50 rounded-xl text-amber-600"><Zap size={22} /></div>
                  <div>
                    <h3 className="font-bold text-slate-900">Energy Consumption</h3>
                    <p className="text-sm text-slate-500 mt-0.5">Live kWh energy telemetry for this unit</p>
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
                  <div className="bg-slate-50 rounded-xl border border-slate-100 p-4 text-center">
                    <div className="text-2xl font-bold text-slate-400">—</div>
                    <div className="text-xs text-slate-500 mt-1 font-semibold">Daily kWh</div>
                  </div>
                  <div className="bg-slate-50 rounded-xl border border-slate-100 p-4 text-center">
                    <div className="text-2xl font-bold text-slate-400">—</div>
                    <div className="text-xs text-slate-500 mt-1 font-semibold">Monthly kWh</div>
                  </div>
                  <div className="bg-slate-50 rounded-xl border border-slate-100 p-4 text-center">
                    <div className="text-2xl font-bold text-slate-400">—</div>
                    <div className="text-xs text-slate-500 mt-1 font-semibold">Estimated Monthly Cost</div>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-4 bg-blue-50 border border-blue-100 rounded-xl text-sm text-blue-700">
                  <Info size={16} className="mt-0.5 shrink-0" />
                  <div>
                    <span className="font-bold">IoT Smart Meter Required:</span> Granular energy consumption data (kWh per unit) requires IoT smart energy meters installed per AC circuit. This field will populate automatically once hardware telemetry is available. Estimated connected load based on rated power: <strong>{ac.powerRating || 'N/A'}</strong>.
                  </div>
                </div>
              </div>
            </motion.div>
          )}

          {/* Tab 3: Warranty & Docs */}
          {activeTab === 3 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
              <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6">
                <div className="flex items-start gap-4 mb-6">
                  <div className="p-3 bg-purple-50 rounded-xl text-purple-600"><Shield size={22} /></div>
                  <div>
                    <h3 className="font-bold text-slate-900">Warranty & Documentation</h3>
                    <p className="text-sm text-slate-500 mt-0.5">AMC, warranty expiry, and attached documents</p>
                  </div>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
                  <InfoCard label="Installation Date" value={ac.installationDate || ac.installationYear || 'Not recorded'} icon={<Calendar size={11} />} />
                  <InfoCard label="Star Rating" value={ac.starRating ? `${ac.starRating} Star` : 'Not recorded'} icon={<Star size={11} />} />
                  <div className="bg-slate-50 rounded-xl border border-slate-100 p-3.5">
                    <div className="text-[10px] font-bold uppercase text-slate-400 mb-1">AMC Expiry</div>
                    <div className="text-sm font-semibold text-slate-400">Not yet configured</div>
                  </div>
                  <div className="bg-slate-50 rounded-xl border border-slate-100 p-3.5">
                    <div className="text-[10px] font-bold uppercase text-slate-400 mb-1">Warranty Expiry</div>
                    <div className="text-sm font-semibold text-slate-400">Not yet configured</div>
                  </div>
                </div>
                <div className="flex items-start gap-3 p-4 bg-purple-50 border border-purple-100 rounded-xl text-sm text-purple-700">
                  <FileText size={16} className="mt-0.5 shrink-0" />
                  <div>
                    <span className="font-bold">Coming Soon:</span> AMC/Warranty expiry tracking and document uploads (PDF invoices, warranty cards, inspection certificates) will be scaffolded in the next update.
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </div>
      </motion.div>
    </div>
  );
}
