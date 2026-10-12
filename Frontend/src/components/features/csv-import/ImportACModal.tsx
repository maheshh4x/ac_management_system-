// src/components/features/csv-import/ImportACModal.tsx
'use client';

/**
 * Phase 3 task 4 — import is now server-driven:
 *   1. upload the file (≤10 MB, .xlsx/.csv, magic-byte checked),
 *   2. POST /api/v1/assets/import?dry_run=true → validation report
 *      (nothing is written),
 *   3. user confirms → same endpoint without dry_run commits in ONE
 *      transaction with per-row savepoints → final counts.
 * The backend owns parsing/mapping/duplicates; this modal renders the
 * report and the counts. Client-side parsers (excel-parser,
 * csv-cleaner-validator, xlsx) are gone.
 */
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  Upload, FileSpreadsheet, Download, AlertTriangle, X, ArrowRight,
  Info, Check, AlertOctagon, Search, ChevronLeft, ChevronRight, Trash2,
  RefreshCw, CheckCircle2, CircleDashed,
} from 'lucide-react';
import {
  downloadImportTemplate,
  uploadAssetImport,
  describeImportFailure,
  isUncertainImportFailure,
  type ImportReport,
  type ImportReportRow,
} from '@/lib/api/imports';
import { ApiError } from '@/lib/api/client';

interface ImportACModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Called after a successful commit so pages can refetch. */
  onImported?: () => void;
}

type ImportStep = 'SELECT_FILE' | 'REVIEW' | 'PROCESSING' | 'RESULT';

const STEPS: { id: ImportStep; label: string }[] = [
  { id: 'SELECT_FILE', label: 'Upload File' },
  { id: 'REVIEW', label: 'Review Report' },
  { id: 'PROCESSING', label: 'Import' },
];

const ROWS_PER_PAGE = 50;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const ACCEPTED = ['.xlsx', '.csv'];

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

const ACTION_STYLES: Record<ImportReportRow['action'], string> = {
  create: 'bg-emerald-100 text-emerald-800',
  update: 'bg-blue-100 text-blue-800',
  duplicate: 'bg-amber-100 text-amber-800',
  invalid: 'bg-red-100 text-red-800',
  skip: 'bg-slate-100 text-slate-600',
};

export const ImportACModal: React.FC<ImportACModalProps> = ({ isOpen, onClose, onImported }) => {
  const [step, setStep] = useState<ImportStep>('SELECT_FILE');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [result, setResult] = useState<ImportReport | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  const [rowFilter, setRowFilter] = useState<'ALL' | ImportReportRow['action']>('ALL');
  const [rowSearch, setRowSearch] = useState('');
  const [rowPage, setRowPage] = useState(1);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const currentStepIndex = STEPS.findIndex(s => s.id === step);

  const reset = useCallback(() => {
    setStep('SELECT_FILE');
    setSelectedFile(null);
    setReport(null);
    setResult(null);
    setErrorMsg(null);
    setIsLoading(false);
    setRowFilter('ALL');
    setRowSearch('');
    setRowPage(1);
  }, []);

  // ── Step 1: pick a file → dry run ────────────────────────────────────────
  const runDryRun = useCallback(async (file: File) => {
    setErrorMsg(null);
    const lower = file.name.toLowerCase();
    if (!ACCEPTED.some(ext => lower.endsWith(ext))) {
      setErrorMsg('Unsupported file type. Please upload a .xlsx or .csv file.');
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setErrorMsg(`File is too large (${formatBytes(file.size)}). The limit is 10 MB.`);
      return;
    }
    setIsLoading(true);
    try {
      const dryReport = await uploadAssetImport(file, true);
      setSelectedFile(file);
      setReport(dryReport);
      setStep('REVIEW');
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : 'Unable to validate the file.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) void runDryRun(f);
    e.target.value = ''; // allow re-selecting the same file
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) void runDryRun(f);
  }, [runDryRun]);

  // ── Step 2 → 3: commit ───────────────────────────────────────────────────
  const handleCommit = async () => {
    if (!selectedFile) return;
    setStep('PROCESSING');
    setErrorMsg(null);
    try {
      const finalReport = await uploadAssetImport(selectedFile, false);
      setResult(finalReport);
      setStep('RESULT');
      onImported?.();
    } catch (err) {
      if (isUncertainImportFailure(err)) {
        // No response confirmed the outcome. Verify ONCE by re-sending the
        // same file: its content-derived Idempotency-Key makes the backend
        // replay the first attempt's report (or answer 409 if it is still
        // running) — so this can never import the same bytes twice.
        try {
          const verified = await uploadAssetImport(selectedFile, false);
          setResult(verified);
          setStep('RESULT');
          onImported?.();
          return;
        } catch (verifyErr) {
          setErrorMsg(
            verifyErr instanceof ApiError && verifyErr.code === 'IMPORT_IN_PROGRESS'
              ? describeImportFailure(verifyErr)
              : describeImportFailure(err),
          );
        }
      } else {
        setErrorMsg(describeImportFailure(err));
      }
      setStep('REVIEW');
    }
  };

  // ── Row table (from the report) ──────────────────────────────────────────
  const filteredRows = useMemo(() => {
    const rows = report?.rows ?? [];
    let out = rows;
    if (rowFilter !== 'ALL') out = out.filter(r => r.action === rowFilter);
    if (rowSearch.trim()) {
      const q = rowSearch.toLowerCase();
      out = out.filter(
        r =>
          String(r.ac_id ?? '').toLowerCase().includes(q) ||
          String(r.message ?? '').toLowerCase().includes(q) ||
          String(r.row).includes(q),
      );
    }
    return out;
  }, [report, rowFilter, rowSearch]);

  const rowTotalPages = Math.max(1, Math.ceil(filteredRows.length / ROWS_PER_PAGE));
  const pagedRows = filteredRows.slice((rowPage - 1) * ROWS_PER_PAGE, rowPage * ROWS_PER_PAGE);

  if (!isOpen) return null;

  const counts = report
    ? [
        { label: 'Physical rows', value: report.physical_rows, color: 'bg-slate-50 border-slate-200 text-slate-700' },
        { label: 'Blank rows ignored', value: report.blank_rows_ignored, color: 'bg-slate-50 border-slate-200 text-slate-500' },
        { label: 'Parsed records', value: report.total_rows, color: 'bg-indigo-50 border-indigo-200 text-indigo-700' },
        { label: 'Mapped columns', value: report.recognized_columns.length, color: 'bg-cyan-50 border-cyan-200 text-cyan-700' },
        { label: 'Unmapped columns', value: report.unknown_columns.length, color: 'bg-orange-50 border-orange-200 text-orange-700' },
        { label: 'New', value: report.created, color: 'bg-emerald-50 border-emerald-200 text-emerald-700' },
        { label: 'Updates', value: report.updated, color: 'bg-blue-50 border-blue-200 text-blue-700' },
        { label: 'Duplicates', value: report.duplicates, color: 'bg-amber-50 border-amber-200 text-amber-700' },
        { label: 'Invalid', value: report.invalid, color: 'bg-red-50 border-red-200 text-red-700' },
        { label: 'Pending Location', value: report.pending_location, color: 'bg-violet-50 border-violet-200 text-violet-700' },
      ]
    : [];

  return (
    <div className="fixed inset-0 bg-slate-950/70 backdrop-blur-sm z-50 flex items-center justify-center p-3 sm:p-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96 }}
        className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl max-h-[94vh] flex flex-col overflow-hidden border border-slate-200"
      >
        {/* Header */}
        <div className="p-4 sm:p-5 border-b border-slate-100 bg-slate-50 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-blue-100 text-blue-700 rounded-xl">
              <FileSpreadsheet size={22} />
            </div>
            <div>
              <h2 className="text-base sm:text-lg font-bold text-slate-900">Import AC Asset Data</h2>
              <p className="text-xs text-slate-500 hidden sm:block">
                Excel or CSV — validated first, imported only when you confirm.
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-2 rounded-full hover:bg-slate-200 text-slate-500 transition-colors" aria-label="Close">
            <X size={18} />
          </button>
        </div>

        {/* Step bar */}
        {!['PROCESSING', 'RESULT'].includes(step) && (
          <div className="px-5 pt-4 pb-0 shrink-0">
            <div className="flex items-center gap-1">
              {STEPS.map((s, i, arr) => {
                const stepIdx = STEPS.findIndex(x => x.id === s.id);
                const done = stepIdx < currentStepIndex;
                const active = s.id === step;
                return (
                  <React.Fragment key={s.id}>
                    <div
                      className={`flex items-center gap-1.5 text-[11px] font-semibold px-2 py-1 rounded-full transition-all ${
                        active ? 'bg-blue-600 text-white' : done ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-400'
                      }`}
                    >
                      {done ? <Check size={10} /> : null}
                      {s.label}
                    </div>
                    {i < arr.length - 1 && <div className={`flex-1 h-0.5 rounded ${done ? 'bg-blue-400' : 'bg-slate-200'}`} />}
                  </React.Fragment>
                );
              })}
            </div>
          </div>
        )}

        {/* Error alert */}
        <AnimatePresence>
          {errorMsg && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="mx-5 mt-3 p-3 bg-red-50 border border-red-200 rounded-xl text-xs font-medium text-red-700 flex items-center justify-between shrink-0"
            >
              <span className="flex items-center gap-1.5">
                <AlertTriangle size={13} />
                {errorMsg}
              </span>
              <button onClick={() => setErrorMsg(null)} className="p-1 ml-2">
                <X size={13} />
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Body */}
        <div className="p-5 flex-1 overflow-y-auto min-h-0">
          {/* ── STEP 1: SELECT FILE ── */}
          {step === 'SELECT_FILE' && (
            <div className="space-y-5">
              <div
                onDrop={handleDrop}
                onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
                onDragLeave={() => setIsDragging(false)}
                onClick={() => !isLoading && fileInputRef.current?.click()}
                className={`border-2 border-dashed rounded-2xl p-10 text-center cursor-pointer transition-all select-none ${
                  isDragging
                    ? 'border-blue-500 bg-blue-50 scale-[1.01]'
                    : 'border-slate-300 hover:border-blue-400 bg-slate-50/60 hover:bg-blue-50/20'
                }`}
              >
                <input
                  type="file"
                  accept=".xlsx,.csv"
                  ref={fileInputRef}
                  className="hidden"
                  onChange={handleFileInput}
                />
                <div
                  className={`w-16 h-16 mx-auto mb-4 rounded-2xl flex items-center justify-center shadow-sm transition-transform ${
                    isDragging ? 'bg-blue-600 text-white scale-110' : 'bg-white border border-slate-200 text-blue-600'
                  }`}
                >
                  {isLoading ? (
                    <div className="w-7 h-7 border-4 border-slate-200 border-t-blue-600 rounded-full animate-spin" />
                  ) : (
                    <Upload size={28} />
                  )}
                </div>
                <h3 className="font-bold text-slate-800 text-base mb-1">
                  {isDragging ? 'Drop your file here' : 'Drag & Drop Excel or CSV'}
                </h3>
                <p className="text-xs text-slate-500 mb-4">or click to choose a file from your computer · max 10 MB</p>
                <div className="flex items-center justify-center gap-2">
                  {ACCEPTED.map(ext => (
                    <span key={ext} className="px-3 py-1 bg-white border border-slate-200 rounded-full text-xs font-bold text-slate-600 shadow-sm">
                      {ext}
                    </span>
                  ))}
                </div>
              </div>

              <div className="flex items-center justify-between text-xs text-slate-500 px-1">
                <span className="flex items-center gap-1.5">
                  <Info size={12} />
                  No file ready? The template shows the canonical 22-column format.
                </span>
                <button
                  onClick={downloadImportTemplate}
                  className="flex items-center gap-1.5 text-blue-600 font-semibold hover:underline"
                >
                  <Download size={12} /> Download Template
                </button>
              </div>
            </div>
          )}

          {/* ── STEP 2: DRY-RUN REPORT ── */}
          {step === 'REVIEW' && report && (
            <div className="space-y-4">
              {/* File banner */}
              <div className="flex items-center gap-4 p-4 bg-slate-50 border border-slate-200 rounded-xl">
                <div className="p-3 bg-green-50 text-green-600 rounded-xl border border-green-100">
                  <FileSpreadsheet size={24} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-bold text-slate-900 text-sm truncate">{selectedFile?.name}</p>
                  <p className="text-xs text-slate-500">
                    {selectedFile ? formatBytes(selectedFile.size) : ''} · {report.physical_rows} physical rows ·{' '}
                    {report.blank_rows_ignored} blank ignored · {report.total_rows} parsed records ·{' '}
                    {report.canonical_format ? 'canonical format' : 'flexible format'}
                    {report.generated_identities > 0 ? ` · ${report.generated_identities} IDs generated` : ''}
                  </p>
                </div>
                <span className="flex items-center gap-1.5 text-[11px] font-semibold text-blue-700 bg-blue-50 border border-blue-200 px-2.5 py-1 rounded-full shrink-0">
                  <CircleDashed size={12} /> Validation only — nothing written yet
                </span>
                <button
                  onClick={reset}
                  className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-red-600 font-semibold transition-colors shrink-0"
                >
                  <Trash2 size={13} /> Change File
                </button>
              </div>

              {/* Count cards */}
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                {counts.map(c => (
                  <div key={c.label} className={`p-3 rounded-xl border text-center ${c.color}`}>
                    <span className="text-[10px] uppercase font-bold opacity-80 block">{c.label}</span>
                    <span className="text-xl font-bold">{c.value}</span>
                  </div>
                ))}
              </div>

              {/* Errors / warnings */}
              {report.errors.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-xl p-3 max-h-40 overflow-y-auto space-y-1">
                  <p className="text-xs font-bold text-red-800 flex items-center gap-1.5 mb-1">
                    <AlertOctagon size={13} /> {report.errors.length} error(s) — these rows will be skipped
                  </p>
                  {report.errors.slice(0, 50).map((e, i) => (
                    <p key={i} className="text-[11px] text-red-700">
                      Row {e.row}: {e.message}
                    </p>
                  ))}
                </div>
              )}
              {report.warnings.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-xl p-3 max-h-32 overflow-y-auto space-y-1">
                  <p className="text-xs font-bold text-amber-800 flex items-center gap-1.5 mb-1">
                    <AlertTriangle size={13} /> {report.warnings.length} warning(s)
                  </p>
                  {report.warnings.slice(0, 50).map((w, i) => (
                    <p key={i} className="text-[11px] text-amber-700">
                      {w.row ? `Row ${w.row}: ` : ''}
                      {w.message}
                    </p>
                  ))}
                </div>
              )}
              {(report.unknown_columns.length > 0 ||
                report.missing_fields.length > 0 ||
                report.recognized_columns.length > 0) && (
                <div className="bg-slate-50 border border-slate-200 rounded-xl p-3 text-[11px] text-slate-600 space-y-1">
                  {report.recognized_columns.length > 0 && (
                    <p>
                      <strong>Mapped columns ({report.recognized_columns.length}):</strong>{' '}
                      {report.recognized_columns.join(', ')}
                    </p>
                  )}
                  {report.missing_fields.length > 0 && (
                    <p>
                      <strong>Optional columns not present:</strong> {report.missing_fields.join(', ')}
                    </p>
                  )}
                  {report.unknown_columns.length > 0 && (
                    <p>
                      <strong>Unrecognised columns (stored as raw data):</strong> {report.unknown_columns.join(', ')}
                    </p>
                  )}
                </div>
              )}

              {/* Row table */}
              <div className="border border-slate-200 rounded-xl overflow-hidden">
                <div className="flex items-center gap-2 p-2.5 bg-slate-50 border-b border-slate-200">
                  <div className="flex-1 relative">
                    <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input
                      type="text"
                      value={rowSearch}
                      onChange={e => { setRowSearch(e.target.value); setRowPage(1); }}
                      placeholder="Search rows..."
                      className="w-full text-xs pl-8 pr-3 py-2 border border-slate-200 rounded-xl bg-white focus:ring-2 focus:ring-blue-400 outline-none"
                    />
                  </div>
                  {(['ALL', 'create', 'update', 'duplicate', 'invalid'] as const).map(f => (
                    <button
                      key={f}
                      onClick={() => { setRowFilter(f); setRowPage(1); }}
                      className={`px-2.5 py-1.5 rounded-lg text-[11px] font-semibold border transition-colors ${
                        rowFilter === f
                          ? 'bg-slate-900 text-white border-slate-900'
                          : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-100'
                      }`}
                    >
                      {f === 'ALL' ? 'All' : f}
                    </button>
                  ))}
                </div>
                <div className="overflow-x-auto max-h-[260px] overflow-y-auto">
                  <table className="w-full text-xs text-left min-w-[560px]">
                    <thead className="bg-slate-50 border-b border-slate-200 text-slate-500 uppercase font-semibold sticky top-0 z-10">
                      <tr>
                        <th className="p-2.5 font-bold">Row</th>
                        <th className="p-2.5 font-bold">Action</th>
                        <th className="p-2.5 font-bold">AC ID</th>
                        <th className="p-2.5 font-bold">Details</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {pagedRows.length === 0 ? (
                        <tr>
                          <td colSpan={4} className="p-6 text-center text-slate-400">
                            No rows match the current filter.
                          </td>
                        </tr>
                      ) : pagedRows.map(r => (
                        <tr key={`${r.row}-${r.ac_id ?? ''}`} className="hover:bg-slate-50">
                          <td className="p-2.5 font-mono text-slate-400">#{r.row}</td>
                          <td className="p-2.5 whitespace-nowrap">
                            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${ACTION_STYLES[r.action] ?? 'bg-slate-100 text-slate-600'}`}>
                              {r.action}
                            </span>
                          </td>
                          <td className="p-2.5 font-mono">{r.ac_id || <span className="text-slate-300">—</span>}</td>
                          <td className="p-2.5 max-w-[320px] truncate text-slate-600" title={r.message ?? ''}>
                            {r.message || <span className="text-slate-300">—</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {rowTotalPages > 1 && (
                  <div className="flex items-center justify-between p-2.5 border-t border-slate-100 bg-slate-50 text-xs">
                    <span className="text-slate-500">{filteredRows.length} rows</span>
                    <div className="flex items-center gap-1">
                      <button
                        disabled={rowPage === 1}
                        onClick={() => setRowPage(p => p - 1)}
                        className="p-1.5 rounded-lg border border-slate-200 hover:bg-white disabled:opacity-40"
                      >
                        <ChevronLeft size={13} />
                      </button>
                      <span className="px-3 py-1 font-semibold">{rowPage} / {rowTotalPages}</span>
                      <button
                        disabled={rowPage === rowTotalPages}
                        onClick={() => setRowPage(p => p + 1)}
                        className="p-1.5 rounded-lg border border-slate-200 hover:bg-white disabled:opacity-40"
                      >
                        <ChevronRight size={13} />
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ── STEP 3: PROCESSING ── */}
          {step === 'PROCESSING' && (
            <div className="py-16 text-center space-y-6">
              <div className="w-16 h-16 rounded-full border-4 border-slate-200 border-t-blue-600 animate-spin mx-auto" />
              <div>
                <h3 className="text-base font-bold text-slate-900 mb-1">Importing {selectedFile?.name}…</h3>
                <p className="text-xs text-slate-400">
                  One transaction with per-row savepoints — failed rows never break the rest.
                </p>
              </div>
            </div>
          )}

          {/* ── STEP 4: RESULT ── */}
          {step === 'RESULT' && result && (
            <div className="py-8 space-y-6 text-center">
              <div className="w-16 h-16 bg-emerald-100 text-emerald-600 rounded-full flex items-center justify-center mx-auto shadow-sm">
                <Check size={32} />
              </div>
              <div>
                <h3 className="text-xl font-bold text-slate-900">Import Complete!</h3>
                <p className="text-xs text-slate-500 mt-1 max-w-sm mx-auto">
                  {result.imported} of {result.total_rows} rows imported — committed in a single transaction.
                </p>
                {result.replayed && (
                  <p className="text-xs text-amber-600 mt-1 max-w-sm mx-auto">
                    An identical import had already completed — showing its original result (nothing was imported twice).
                  </p>
                )}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-left max-w-lg mx-auto">
                {[
                  { label: 'Created', value: result.created, color: 'text-emerald-600' },
                  { label: 'Updated', value: result.updated, color: 'text-blue-600' },
                  { label: 'Duplicates', value: result.duplicates, color: 'text-amber-600' },
                  { label: 'Invalid', value: result.invalid, color: 'text-red-600' },
                ].map(i => (
                  <div key={i.label} className="bg-slate-50 border border-slate-200 p-3 rounded-xl">
                    <span className="text-[10px] text-slate-400 uppercase font-bold block">{i.label}</span>
                    <span className={`text-2xl font-bold ${i.color}`}>{i.value}</span>
                  </div>
                ))}
              </div>
              {result.failed > 0 && (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-xl py-2 px-3 max-w-lg mx-auto">
                  {result.failed} row(s) failed and were rolled back individually — see the report below for reasons.
                </p>
              )}
              <p className="text-xs text-slate-400 flex items-center justify-center gap-1">
                <RefreshCw size={11} /> Dashboard, charts, and all portal views have been refreshed.
              </p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-slate-100 bg-slate-50 flex items-center justify-between shrink-0 gap-3">
          <div>
            {step === 'SELECT_FILE' && (
              <button
                onClick={onClose}
                className="px-4 py-2 bg-white border border-slate-200 text-slate-700 rounded-xl text-xs font-semibold hover:bg-slate-100 transition-colors"
              >
                Cancel
              </button>
            )}
            {step === 'REVIEW' && (
              <button
                onClick={reset}
                className="px-4 py-2 bg-white border border-slate-200 text-slate-700 rounded-xl text-xs font-semibold hover:bg-slate-100 transition-colors"
              >
                ← Start Over
              </button>
            )}
            {step === 'RESULT' && (
              <span className="text-xs text-slate-500 flex items-center gap-1.5">
                <CheckCircle2 size={13} className="text-emerald-600" /> Server-side report saved in the audit log.
              </span>
            )}
          </div>

          <div>
            {step === 'REVIEW' && report && (
              <button
                onClick={handleCommit}
                disabled={report.total_rows === 0 || isLoading}
                className="flex items-center gap-1.5 px-5 py-2 bg-emerald-600 text-white rounded-xl text-xs font-semibold hover:bg-emerald-700 transition-colors shadow-sm disabled:opacity-50"
              >
                Confirm & Import {report.imported > 0 ? `(${report.imported} rows)` : ''} <ArrowRight size={14} />
              </button>
            )}
            {step === 'RESULT' && (
              <button
                onClick={onClose}
                className="px-6 py-2 bg-blue-600 text-white rounded-xl text-xs font-semibold hover:bg-blue-700 transition-colors shadow-sm"
              >
                Done
              </button>
            )}
          </div>
        </div>
      </motion.div>
    </div>
  );
};

