// src/components/features/assets/AssetFormModal.tsx
'use client';

/**
 * Phase 3 task 3 — create/edit form for AC assets.
 * - zod schema mirrors AssetCreate/AssetUpdate (same ranges as the DB CHECKs)
 * - saves through the react-query mutations (optimistic + rollback)
 * - server field errors (VALIDATION_ERROR details, SERIAL_EXISTS, ...)
 *   are rendered inline under the offending input.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, AlertTriangle, Save, Plus } from 'lucide-react';
import { z } from 'zod';

import { ApiError } from '@/lib/api/client';
import { useCreateAsset, useUpdateAsset } from '@/lib/api/assets';
import { useCampusTree } from '@/lib/api/campus';
import { toApiStatus } from '@/lib/adapters/asset';
import type { AcStatus } from '@/lib/types/campus';
import type { ExtendedAC } from '@/lib/types/asset';

interface AssetFormModalProps {
  isOpen: boolean;
  onClose: () => void;
  mode: 'create' | 'edit';
  /** Row being edited (edit mode). */
  asset?: ExtendedAC | null;
  /** Called after a successful save (refetch caches). */
  onSaved?: () => void;
}

// All inputs are strings; numeric/date conversions happen at submit time.
const assetFormSchema = z.object({
  ac_id: z.string().trim().min(1, 'AC ID is required').max(128, 'Max 128 characters'),
  room_id: z.string(),
  ac_make_company: z.string().max(200, 'Max 200 characters'),
  ac_model_number: z.string().max(200, 'Max 200 characters'),
  ac_serial_number: z.string().max(200, 'Max 200 characters'),
  ac_capacity_ton: z
    .string()
    .refine(v => v.trim() === '' || (Number.isFinite(Number(v)) && Number(v) > 0), 'Capacity must be a positive number'),
  ac_type: z.string().max(100, 'Max 100 characters'),
  current_status: z.string(),
  installation_date: z.string(),
  last_maintenance_date: z.string(),
  manufacturing_year: z
    .string()
    .refine(v => v.trim() === '' || (Number.isInteger(Number(v)) && Number(v) >= 1900 && Number(v) <= 2100), 'Year must be 1900–2100'),
  installation_year: z
    .string()
    .refine(v => v.trim() === '' || (Number.isInteger(Number(v)) && Number(v) >= 1900 && Number(v) <= 2100), 'Year must be 1900–2100'),
  ac_star_rating: z
    .string()
    .refine(v => v.trim() === '' || (Number.isInteger(Number(v)) && Number(v) >= 1 && Number(v) <= 5), 'Star rating must be 1–5'),
  power_rating_kw: z
    .string()
    .refine(v => v.trim() === '' || (Number.isFinite(Number(v)) && Number(v) >= 0), 'Power rating must be ≥ 0'),
  power_factor: z
    .string()
    .refine(v => v.trim() === '' || (Number.isFinite(Number(v)) && Number(v) >= 0 && Number(v) <= 1), 'Power factor must be 0–1'),
  next_service_date: z.string(),
  technician: z.string().max(200, 'Max 200 characters'),
  remarks: z.string().max(2000, 'Max 2000 characters'),
});

type FormValues = z.infer<typeof assetFormSchema>;

const STATUS_OPTIONS: AcStatus[] = ['Working', 'Fault', 'Maintenance', 'Offline', 'Unknown'];
const TYPE_OPTIONS = ['Split', 'Window', 'Cassette', 'Ductable', 'Central'];

const EMPTY: FormValues = {
  ac_id: '',
  room_id: '',
  ac_make_company: '',
  ac_model_number: '',
  ac_serial_number: '',
  ac_capacity_ton: '',
  ac_type: '',
  current_status: 'Working',
  installation_date: '',
  last_maintenance_date: '',
  manufacturing_year: '',
  installation_year: '',
  ac_star_rating: '',
  power_rating_kw: '',
  power_factor: '',
  next_service_date: '',
  technician: '',
  remarks: '',
};

function fromAsset(asset: ExtendedAC): FormValues {
  return {
    ...EMPTY,
    ac_id: asset.acId ?? asset.id,
    room_id: asset.location.roomId ?? '',
    ac_make_company: asset.make,
    ac_model_number: asset.model,
    ac_serial_number: asset.serialNumber,
    ac_capacity_ton: asset.capacityTon != null ? String(asset.capacityTon) : '',
    ac_type: asset.type,
    current_status: asset.status,
    installation_date: asset.installationDate ?? '',
    last_maintenance_date: asset.lastMaintenanceDate ?? '',
    manufacturing_year: asset.manufacturingYear ?? '',
    installation_year: asset.installationYear ?? '',
    ac_star_rating: asset.starRating ?? '',
    power_rating_kw: asset.powerRating ? String(Number(asset.powerRating.replace(/[^0-9.]/g, '')) || '') : '',
    power_factor: asset.powerFactor ?? '',
    next_service_date: asset.nextMaintenanceDate ?? '',
    technician: asset.technician ?? '',
    remarks: asset.remarks ?? '',
  };
}

const str = (v: string) => (v.trim() === '' ? null : v.trim());
const num = (v: string) => (v.trim() === '' ? null : Number(v));
const int = (v: string) => (v.trim() === '' ? null : Math.trunc(Number(v)));
const date = (v: string) => (v.trim() === '' ? null : v.trim());

function buildPayload(values: FormValues, mode: 'create' | 'edit'): Record<string, unknown> {
  const payload: Record<string, unknown> = {
    room_id: str(values.room_id),
    ac_make_company: str(values.ac_make_company),
    ac_model_number: str(values.ac_model_number),
    ac_serial_number: str(values.ac_serial_number),
    ac_capacity_ton: num(values.ac_capacity_ton),
    ac_type: str(values.ac_type),
    current_status: toApiStatus(values.current_status as AcStatus),
    installation_date: date(values.installation_date),
    last_maintenance_date: date(values.last_maintenance_date),
    manufacturing_year: int(values.manufacturing_year),
    installation_year: int(values.installation_year),
    ac_star_rating: int(values.ac_star_rating),
    power_rating_kw: num(values.power_rating_kw),
    power_factor: num(values.power_factor),
    next_service_date: date(values.next_service_date),
    technician: str(values.technician),
    remarks: str(values.remarks),
  };
  if (mode === 'create') payload.ac_id = values.ac_id.trim();
  return payload;
}

/** Map ApiError shapes onto the form's input names. */
function serverFieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError)) return {};
  const out: Record<string, string> = {};
  if (err.code === 'VALIDATION_ERROR' && Array.isArray(err.details)) {
    for (const issue of err.details as { loc?: unknown[]; msg?: unknown }[]) {
      const field = Array.isArray(issue.loc) ? String(issue.loc[0] ?? '') : '';
      if (field && issue.msg) out[field] = String(issue.msg);
    }
  }
  if (err.code === 'ASSET_EXISTS') out.ac_id = err.message;
  if (err.code === 'SERIAL_EXISTS') out.ac_serial_number = err.message;
  if (err.code === 'INVALID_ROOM') out.room_id = err.message;
  return out;
}

export const AssetFormModal: React.FC<AssetFormModalProps> = ({ isOpen, onClose, mode, asset, onSaved }) => {
  const [values, setValues] = useState<FormValues>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const createAsset = useCreateAsset();
  const updateAsset = useUpdateAsset();
  const { data: campus } = useCampusTree(isOpen);

  // Reset the form whenever it opens (or the edited asset changes).
  useEffect(() => {
    if (!isOpen) return;
    setValues(mode === 'edit' && asset ? fromAsset(asset) : EMPTY);
    setErrors({});
    setServerErrors({});
    setFormError(null);
    setSaving(false);
  }, [isOpen, mode, asset]);

  const roomOptions = useMemo(() => {
    const out: { id: string; label: string }[] = [];
    for (const college of campus?.colleges ?? []) {
      for (const block of college.blocks) {
        for (const floor of block.floors) {
          for (const room of floor.rooms) {
            out.push({
              id: room.room_id,
              label: `${block.block_name} / ${floor.floor_name} / ${room.room_name}`,
            });
          }
        }
      }
    }
    return out.sort((a, b) => a.label.localeCompare(b.label));
  }, [campus]);

  const set = (key: keyof FormValues) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
    setValues(prev => ({ ...prev, [key]: e.target.value }));
    setErrors(prev => {
      if (!prev[key]) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
    setServerErrors(prev => {
      if (!prev[key]) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  };

  const handleSubmit = async (e: React.SyntheticEvent) => {
    e.preventDefault();
    setFormError(null);
    setServerErrors({});

    const parsed = assetFormSchema.safeParse(values);
    if (!parsed.success) {
      const fieldErrors: Record<string, string> = {};
      for (const issue of parsed.error.issues) {
        const field = String(issue.path[0] ?? '');
        if (field && !fieldErrors[field]) fieldErrors[field] = issue.message;
      }
      setErrors(fieldErrors);
      return;
    }
    setErrors({});
    setSaving(true);
    try {
      if (mode === 'create') {
        await createAsset.mutateAsync(buildPayload(parsed.data, 'create'));
      } else {
        const acId = asset?.acId ?? asset?.id;
        if (!acId) throw new Error('This asset has no usable identifier.');
        await updateAsset.mutateAsync({ acId, payload: buildPayload(parsed.data, 'edit') });
      }
      onSaved?.();
      onClose();
    } catch (err) {
      const mapped = serverFieldErrors(err);
      if (Object.keys(mapped).length > 0) {
        setServerErrors(mapped);
      } else {
        setFormError(err instanceof Error ? err.message : 'Saving failed.');
      }
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  const errorFor = (key: keyof FormValues) => errors[key] ?? serverErrors[key];

  const field = (
    key: keyof FormValues,
    label: string,
    props: React.InputHTMLAttributes<HTMLInputElement> & { span?: string } = {},
  ) => {
    const { span = 'sm:col-span-1', ...inputProps } = props;
    const err = errorFor(key);
    return (
      <div className={span}>
        <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 block mb-1">
          {label}
          {inputProps.required && <span className="text-red-500 ml-0.5">*</span>}
        </label>
        <input
          {...inputProps}
          value={values[key]}
          onChange={set(key)}
          className={`w-full text-sm px-3 py-2 border rounded-lg bg-white focus:ring-2 outline-none transition-colors ${
            err ? 'border-red-300 focus:ring-red-300' : 'border-slate-200 focus:ring-blue-400'
          }`}
        />
        {err && <p className="text-[11px] text-red-600 mt-1 font-medium">{err}</p>}
      </div>
    );
  };

  return (
    <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96 }}
        className="bg-white rounded-2xl shadow-2xl w-full max-w-3xl max-h-[94vh] flex flex-col overflow-hidden border border-slate-200"
      >
        <div className="p-4 sm:p-5 border-b border-slate-100 bg-slate-50 flex items-center justify-between shrink-0">
          <div>
            <h2 className="text-base sm:text-lg font-bold text-slate-900">
              {mode === 'create' ? 'Add AC Unit' : `Edit ${asset?.id ?? 'AC Unit'}`}
            </h2>
            <p className="text-xs text-slate-500">
              {mode === 'create'
                ? 'Creates a new asset record (CREATE_ASSET).'
                : 'Changes are applied immediately and rolled back if the server rejects them.'}
            </p>
          </div>
          <button onClick={onClose} className="p-2 rounded-full hover:bg-slate-200 text-slate-500 transition-colors" aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto min-h-0 p-5 space-y-4">
          <AnimatePresence>
            {formError && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: 'auto' }}
                exit={{ opacity: 0, height: 0 }}
                className="p-3 bg-red-50 border border-red-200 rounded-xl text-xs font-medium text-red-700 flex items-center gap-1.5"
              >
                <AlertTriangle size={13} /> {formError}
              </motion.div>
            )}
          </AnimatePresence>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {field('ac_id', 'AC ID', {
              required: mode === 'create',
              disabled: mode === 'edit',
              placeholder: 'e.g. AC-001',
            })}

            <div className="sm:col-span-2">
              <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 block mb-1">
                Room
              </label>
              <select
                value={values.room_id}
                onChange={set('room_id')}
                className={`w-full text-sm px-3 py-2 border rounded-lg bg-white focus:ring-2 outline-none ${
                  errorFor('room_id') ? 'border-red-300 focus:ring-red-300' : 'border-slate-200 focus:ring-blue-400'
                }`}
              >
                <option value="">— No room —</option>
                {roomOptions.map(r => (
                  <option key={r.id} value={r.id}>
                    {r.label}
                  </option>
                ))}
              </select>
              {errorFor('room_id') && <p className="text-[11px] text-red-600 mt-1 font-medium">{errorFor('room_id')}</p>}
            </div>

            {field('ac_make_company', 'Brand / Make', { placeholder: 'e.g. Daikin' })}
            {field('ac_model_number', 'Model', { placeholder: 'e.g. FTKM50' })}
            {field('ac_serial_number', 'Serial Number')}
            {field('ac_capacity_ton', 'Capacity (Ton)', { inputMode: 'decimal', placeholder: '1.5' })}

            <div className="sm:col-span-1">
              <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 block mb-1">Type</label>
              <select
                value={values.ac_type}
                onChange={set('ac_type')}
                className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg bg-white focus:ring-2 focus:ring-blue-400 outline-none"
              >
                <option value="">— Unknown —</option>
                {TYPE_OPTIONS.map(t => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
                {values.ac_type && !TYPE_OPTIONS.includes(values.ac_type) && (
                  <option value={values.ac_type}>{values.ac_type}</option>
                )}
              </select>
            </div>

            <div className="sm:col-span-1">
              <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 block mb-1">Status</label>
              <select
                value={values.current_status}
                onChange={set('current_status')}
                className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg bg-white focus:ring-2 focus:ring-blue-400 outline-none"
              >
                {STATUS_OPTIONS.map(s => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            {field('installation_date', 'Installation Date', { type: 'date' })}
            {field('last_maintenance_date', 'Last Maintenance', { type: 'date' })}
            {field('manufacturing_year', 'Manufacturing Year', { inputMode: 'numeric', placeholder: '2022' })}
            {field('installation_year', 'Installation Year', { inputMode: 'numeric', placeholder: '2023' })}
            {field('ac_star_rating', 'Star Rating (1–5)', { inputMode: 'numeric' })}
            {field('power_rating_kw', 'Power (kW)', { inputMode: 'decimal', placeholder: '1.5' })}
            {field('power_factor', 'Power Factor', { inputMode: 'decimal', placeholder: '0.95' })}
            {field('next_service_date', 'Next Service Date', { type: 'date' })}
            {field('technician', 'Technician', { placeholder: 'Assigned technician' })}

            <div className="sm:col-span-2">
              <label className="text-[11px] font-bold uppercase tracking-wider text-slate-500 block mb-1">Remarks</label>
              <textarea
                value={values.remarks}
                onChange={set('remarks')}
                rows={3}
                maxLength={2000}
                className={`w-full text-sm px-3 py-2 border rounded-lg bg-white focus:ring-2 outline-none resize-y ${
                  errorFor('remarks') ? 'border-red-300 focus:ring-red-300' : 'border-slate-200 focus:ring-blue-400'
                }`}
              />
              {errorFor('remarks') && <p className="text-[11px] text-red-600 mt-1 font-medium">{errorFor('remarks')}</p>}
            </div>
          </div>
        </form>

        <div className="p-4 border-t border-slate-100 bg-slate-50 flex items-center justify-end shrink-0 gap-3">
          <button
            onClick={onClose}
            type="button"
            className="px-4 py-2 bg-white border border-slate-200 text-slate-700 rounded-xl text-xs font-semibold hover:bg-slate-100 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving}
            className="flex items-center gap-1.5 px-5 py-2 bg-blue-600 text-white rounded-xl text-xs font-semibold hover:bg-blue-700 transition-colors shadow-sm disabled:opacity-50"
          >
            {saving ? (
              <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
            ) : mode === 'create' ? (
              <Plus size={14} />
            ) : (
              <Save size={14} />
            )}
            {mode === 'create' ? 'Create Asset' : 'Save Changes'}
          </button>
        </div>
      </motion.div>
    </div>
  );
};
