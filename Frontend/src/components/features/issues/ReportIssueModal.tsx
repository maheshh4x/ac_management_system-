'use client';

/**
 * Report-an-issue form (Features B/C): shared by the asset detail page and
 * the technician asset page. Any authenticated role may file; every report
 * starts PENDING_APPROVAL and becomes a technician job only through Super
 * Admin approval — the modal states this explicitly so nobody mistakes a
 * report for a dispatched job.
 *
 * Duplicate-submit protection: one idempotency key per modal instance plus
 * the submit button disables while the mutation is pending, so double-clicks
 * and retries return the original report.
 */
import React, { useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { AlertCircle, CheckCircle2, Loader2, X } from 'lucide-react';

import { ApiError } from '@/lib/api/client';
import {
  REPORT_CATEGORIES,
  reportStatusLabel,
  useCreateIssueReport,
  type ReportCategory,
} from '@/lib/api/issue-reports';

const PRIORITIES = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const;

function newIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `form-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function ReportIssueModal({
  acId,
  allowAssetEntry = false,
  onClose,
  onSubmitted,
}: {
  /** Prepopulated asset (business ac_id). Required unless allowAssetEntry. */
  acId?: string;
  /** Global entry point: let the reporter type an asset id. */
  allowAssetEntry?: boolean;
  onClose: () => void;
  onSubmitted?: (reportCode: string) => void;
}) {
  const createReport = useCreateIssueReport();
  const [assetInput, setAssetInput] = useState(acId ?? '');
  const [category, setCategory] = useState<ReportCategory>('AC_NOT_COOLING');
  const [description, setDescription] = useState('');
  const [priority, setPriority] = useState<string>('MEDIUM');
  const [locationText, setLocationText] = useState('');
  const [formError, setFormError] = useState('');
  const [confirmation, setConfirmation] = useState<{ code: string } | null>(null);
  // One key per modal instance: a retry after a timeout returns the same
  // report instead of filing a second one.
  const idempotencyKey = useRef(newIdempotencyKey());

  const submit = () => {
    setFormError('');
    const trimmed = description.trim();
    if (!trimmed) {
      setFormError('Please describe the issue.');
      return;
    }
    const asset = (allowAssetEntry ? assetInput : acId ?? '').trim();
    if (!asset) {
      setFormError('An asset is required for this report.');
      return;
    }
    createReport.mutate(
      {
        ac_id: asset,
        category,
        description: trimmed,
        priority,
        location_text: locationText.trim() || undefined,
        idempotency_key: idempotencyKey.current,
      },
      {
        onSuccess: (report) => {
          setConfirmation({ code: report.report_code });
          onSubmitted?.(report.report_code);
        },
        onError: (err) => {
          if (err instanceof ApiError) {
            if (err.code === 'ASSET_NOT_FOUND') {
              setFormError('No asset matches that identifier. Check the asset ID and try again.');
              return;
            }
            setFormError(err.message || 'Unable to file the report. Your input is preserved — try again.');
            return;
          }
          setFormError('Unable to file the report. Your input is preserved — try again.');
        },
      },
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4" role="dialog" aria-modal="true" aria-label="Report an issue">
      <motion.div
        initial={{ scale: 0.97, y: 8 }}
        animate={{ scale: 1, y: 0 }}
        className="max-h-[94vh] w-full max-w-lg overflow-y-auto rounded-2xl bg-white p-6 shadow-xl"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-slate-900">Report an issue</h2>
            <p className="mt-0.5 text-xs text-slate-500">
              Filed as {reportStatusLabel('PENDING_APPROVAL')} — a Super Admin reviews it before any technician job is created.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700">
            <X size={18} />
          </button>
        </div>

        {confirmation ? (
          <div className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
            <p className="flex items-center gap-2 font-bold">
              <CheckCircle2 size={16} /> Report filed
            </p>
            <p className="mt-1">
              Reference <span className="font-mono font-bold">{confirmation.code}</span> · Status:{' '}
              {reportStatusLabel('PENDING_APPROVAL')}. You can track it under My Reports.
            </p>
            <button onClick={onClose} className="mt-3 min-h-11 rounded-xl bg-emerald-600 px-4 text-sm font-bold text-white hover:bg-emerald-700">
              Done
            </button>
          </div>
        ) : (
          <div className="mt-5 space-y-4">
            {allowAssetEntry && (
              <label className="block text-sm font-semibold text-slate-700">
                Asset ID
                <input
                  value={assetInput}
                  onChange={(e) => setAssetInput(e.target.value)}
                  placeholder="e.g. AC-1024 or asset code"
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm font-normal outline-none focus:ring-2 focus:ring-blue-400"
                />
              </label>
            )}
            <label className="block text-sm font-semibold text-slate-700">
              Issue category
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value as ReportCategory)}
                className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-normal outline-none focus:ring-2 focus:ring-blue-400"
              >
                {REPORT_CATEGORIES.map((c) => (
                  <option key={c.value} value={c.value}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-sm font-semibold text-slate-700">
              Description <span className="text-red-500">*</span>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={4}
                maxLength={2000}
                placeholder="What is wrong, when did it start, what have you observed?"
                className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm font-normal outline-none focus:ring-2 focus:ring-blue-400"
              />
            </label>
            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm font-semibold text-slate-700">
                Priority
                <select
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm font-normal outline-none focus:ring-2 focus:ring-blue-400"
                >
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm font-semibold text-slate-700">
                Location note <span className="font-normal text-slate-400">(optional)</span>
                <input
                  value={locationText}
                  onChange={(e) => setLocationText(e.target.value)}
                  maxLength={500}
                  placeholder="e.g. near the window side"
                  className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2.5 text-sm font-normal outline-none focus:ring-2 focus:ring-blue-400"
                />
              </label>
            </div>
            <p className="text-xs text-slate-500">
              Your priority suggestion is recorded but does not escalate the job by itself. The recorded asset
              location is snapshotted automatically; the note above adds detail.
            </p>
            {formError && (
              <p className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 p-3 text-sm font-medium text-red-700">
                <AlertCircle size={16} className="mt-0.5 shrink-0" /> {formError}
              </p>
            )}
            <div className="flex gap-2">
              <button
                onClick={submit}
                disabled={createReport.isPending}
                className="flex min-h-11 flex-1 items-center justify-center gap-2 rounded-xl bg-blue-600 px-4 text-sm font-bold text-white hover:bg-blue-700 disabled:opacity-60"
              >
                {createReport.isPending && <Loader2 size={16} className="animate-spin" />}
                {createReport.isPending ? 'Filing…' : 'File report'}
              </button>
              <button
                onClick={onClose}
                className="min-h-11 rounded-xl border border-slate-200 bg-white px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </motion.div>
    </div>
  );
}
