'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertCircle, CheckCircle2, Download, Loader2, Printer, QrCode, RefreshCw } from 'lucide-react';
import { ApiError } from '@/lib/api/client';
import {
  downloadBlob,
  fetchQrBatchPdf,
  fetchQrImage,
  fetchQrPayload,
  regenerateQr,
} from '@/lib/qr-service';
import { downloadQrLabel, type QrLabelFields } from '@/lib/qr-label';
import { effectiveLocationText } from '@/lib/adapters/asset';
import type { ExtendedAC } from '@/lib/types/asset';
import { useAuth } from '@/contexts/AuthContext';

interface AcQrActionsProps {
  acId: string;
  /** Refs to include on the printable A4 sheet (defaults to this asset). */
  batchIds?: string[];
  /** Already-loaded asset for the branded label (no extra request is made
      to build it — fields come from this object + the preview blob). */
  asset?: ExtendedAC | null;
}

type Busy = 'label' | 'png' | 'svg' | 'print' | 'regen' | null;

function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 403) return 'You do not have permission for this action.';
    if (err.status === 404) return 'AC not found.';
    if (err.code === 'NETWORK_ERROR') return 'Could not reach the server.';
    return err.message;
  }
  return 'Something went wrong. Please try again.';
}

/**
 * QR panel for one asset (SUPER_ADMIN). QR images/PDFs are minted on
 * demand by the backend (GET /assets/<ref>/qr, POST .../qr/regenerate,
 * GET /assets/qr/batch) — there is no mock status store any more: the
 * code always exists, and regenerating only re-issues the label.
 */
export function AcQrActions({ acId, batchIds, asset }: AcQrActionsProps) {
  const { user } = useAuth();
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [payload, setPayload] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<Busy>(null);
  const [toastMessage, setToastMessage] = useState<{ text: string; type: 'error' | 'success' } | null>(null);
  const previewRef = useRef<string | null>(null);
  // The mount-time preview blob, reused for the branded label so Download
  // QR fires zero additional API requests.
  const previewBlobRef = useRef<Blob | null>(null);

  const showToast = useCallback((text: string, type: 'error' | 'success') => {
    setToastMessage({ text, type });
    window.setTimeout(() => setToastMessage(null), 5000);
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      try {
        const [image, url] = await Promise.all([fetchQrImage(acId, 'png'), fetchQrPayload(acId)]);
        if (!active) return;
        if (previewRef.current) URL.revokeObjectURL(previewRef.current);
        const objectUrl = URL.createObjectURL(image);
        previewBlobRef.current = image;
        previewRef.current = objectUrl;
        setPreviewUrl(objectUrl);
        setPayload(url);
      } catch (err) {
        if (active) showToast(describeError(err), 'error');
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
      if (previewRef.current) {
        URL.revokeObjectURL(previewRef.current);
        previewRef.current = null;
      }
    };
  }, [acId, showToast]);

  const handleDownload = async (format: 'png' | 'svg') => {
    if (busy) return;
    setBusy(format);
    try {
      const blob = await fetchQrImage(acId, format);
      downloadBlob(blob, `QR_${acId}.${format}`);
      showToast(format === 'png' ? 'QR image downloaded.' : 'QR image downloaded (SVG).', 'success');
    } catch (err) {
      showToast(describeError(err), 'error');
    } finally {
      setBusy(null);
    }
  };

  /** Primary Download QR: the branded printable label (college / block /
      department / location / asset ID + the stable QR), composed locally
      from the already-loaded asset and preview blob — zero new requests. */
  const labelFields: QrLabelFields | null = asset
    ? {
        assetId: asset.visualId ?? asset.acId ?? asset.id,
        make: asset.make,
        model: asset.model,
        college: effectiveLocationText(asset, 'college'),
        block: effectiveLocationText(asset, 'block'),
        department: effectiveLocationText(asset, 'department'),
        floor: effectiveLocationText(asset, 'floor'),
        room: effectiveLocationText(asset, 'room'),
      }
    : null;

  const handleDownloadLabel = async () => {
    if (busy) return;
    setBusy('label');
    try {
      const blob = previewBlobRef.current ?? (await fetchQrImage(acId, 'png'));
      if (labelFields) {
        await downloadQrLabel(labelFields, blob, `QR_${acId}_label.png`);
        showToast('Branded QR label downloaded.', 'success');
      } else {
        downloadBlob(blob, `QR_${acId}.png`);
        showToast('QR image downloaded.', 'success');
      }
    } catch (err) {
      showToast(describeError(err), 'error');
    } finally {
      setBusy(null);
    }
  };

  const handlePrintSheet = async () => {
    if (busy) return;
    setBusy('print');
    try {
      const pdf = await fetchQrBatchPdf(batchIds ?? [acId]);
      downloadBlob(pdf, 'qr-labels.pdf');
      showToast('Label sheet downloaded — A4, 3 × 8 labels per page.', 'success');
    } catch (err) {
      showToast(describeError(err), 'error');
    } finally {
      setBusy(null);
    }
  };

  const handleRegenerate = async () => {
    if (busy) return;
    const confirmed = window.confirm(
      "Re-issue this asset's QR label? The encoded URL never changes, so stickers already printed keep scanning — this only records a fresh label.",
    );
    if (!confirmed) return;
    setBusy('regen');
    try {
      const result = await regenerateQr(acId);
      showToast(`Label re-issued (version ${result.qr_version}). Existing stickers still work.`, 'success');
    } catch (err) {
      showToast(describeError(err), 'error');
    } finally {
      setBusy(null);
    }
  };

  // SUPER_ADMIN-only surface (existing UI gate, kept).
  if (user?.role !== 'SUPER_ADMIN') {
    return null;
  }

  return (
    <div className="mt-4 flex flex-col gap-3 pt-3 border-t border-slate-100 relative">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Device QR Management</h4>
        {loading && <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />}
      </div>

      <AnimatePresence>
        {toastMessage && (
          <motion.div
            initial={{ opacity: 0, y: -5 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className={`px-3 py-2 text-xs font-semibold rounded-lg flex items-center gap-1.5 ${toastMessage.type === 'error' ? 'bg-red-50 text-red-600 border border-red-200' : 'bg-emerald-50 text-emerald-600 border border-emerald-200'}`}
          >
            {toastMessage.type === 'error' ? <AlertCircle size={14} /> : <CheckCircle2 size={14} />}
            {toastMessage.text}
          </motion.div>
        )}
      </AnimatePresence>

      {!loading && !previewUrl && (
        <div className="flex items-center gap-1 text-xs text-red-500 py-1">
          <AlertCircle size={12} /> Failed to load the QR code.
        </div>
      )}

      {!loading && previewUrl && (
        <div className="flex flex-col gap-2 relative">
          <div className="flex items-start gap-3">
            <Image
              src={previewUrl}
              alt={`QR code for ${acId}`}
              unoptimized
              width={96}
              height={96}
              className="h-24 w-24 rounded-lg border border-slate-200 bg-white p-1"
            />
            <div className="min-w-0 flex-1">
              <p className="text-[10px] font-bold uppercase text-slate-400">Scanning opens</p>
              <p className="mt-0.5 break-all text-[11px] leading-relaxed text-slate-500">{payload}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleDownloadLabel}
              disabled={busy !== null}
              title="Download branded QR label (college, block, department, location, asset ID)"
              className="flex flex-1 items-center justify-center gap-2 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg text-sm font-semibold shadow-sm transition-colors disabled:opacity-50"
            >
              {busy === 'label' ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
              Download QR
            </button>
            <button
              onClick={() => handleDownload('png')}
              disabled={busy !== null}
              className="flex items-center justify-center gap-1.5 px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50"
              title="Download raw QR image"
            >
              {busy === 'png' ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
              PNG
            </button>
            <button
              onClick={() => handleDownload('svg')}
              disabled={busy !== null}
              className="flex items-center justify-center gap-1.5 px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50"
              title="Download scalable SVG"
            >
              {busy === 'svg' ? <Loader2 size={16} className="animate-spin" /> : <Download size={16} />}
              SVG
            </button>
            <button
              onClick={handlePrintSheet}
              disabled={busy !== null}
              className="flex items-center justify-center gap-1.5 px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50"
              title="Download printable A4 label sheet (3 × 8)"
            >
              {busy === 'print' ? <Loader2 size={16} className="animate-spin" /> : <Printer size={16} />}
              Print
            </button>
            <button
              onClick={handleRegenerate}
              disabled={busy !== null}
              className="flex items-center justify-center p-2 bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-300 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50"
              title="Regenerate label (the code itself never changes)"
            >
              {busy === 'regen' ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
            </button>
          </div>
          <span className="text-[10px] text-slate-400 font-medium px-1 flex items-center gap-1">
            <QrCode size={11} /> Stable code — printed stickers keep working after a re-issue.
          </span>
        </div>
      )}
    </div>
  );
}
