/**
 * QR API wrapper (Phase 6) — replaces the old USE_MOCK_QR in-memory
 * database and the dead super-admin mock QR fetches.
 *
 * QR codes are generated on demand by the backend and never stored: these
 * calls only stream images/PDFs (`apiBlob`) or small JSON payloads (`api`)
 * from the Flask API. The payload itself is the immutable
 * {site}/a/{asset_uuid} URL — regenerating a label re-issues the image but
 * never changes what a sticker decodes to.
 */
import { api, apiBlob } from "@/lib/api/client";

export type QrImageFormat = "png" | "svg";

export interface QrRegenerateResult {
  ac_id: string;
  asset_uuid: string | null;
  asset_code: string | null;
  /** Per-asset label version (migration 010); bumps on every regenerate. */
  qr_version: number;
  /** Unchanged by regenerate — old stickers keep resolving to this URL. */
  payload: string;
}

/** QR image for an asset — GET /api/v1/assets/<ref>/qr (VIEW_ASSET). */
export async function fetchQrImage(
  ref: string,
  format: QrImageFormat = "png",
  size?: number,
): Promise<Blob> {
  const params = new URLSearchParams({ format });
  if (size) params.set("size", String(size));
  return apiBlob(`/api/v1/assets/${encodeURIComponent(ref)}/qr?${params.toString()}`);
}

/** The raw string the QR encodes — GET /api/v1/assets/<ref>/qr?format=payload. */
export async function fetchQrPayload(ref: string): Promise<string> {
  const data = await api<{ payload: string }>(
    `/api/v1/assets/${encodeURIComponent(ref)}/qr?format=payload`,
  );
  return data.payload;
}

/**
 * Re-issue the label — POST /api/v1/assets/<ref>/qr/regenerate (EDIT_ASSET).
 * Bumps qr_version and audits; the payload URL does not change, so
 * stickers already printed keep working.
 */
export async function regenerateQr(ref: string): Promise<QrRegenerateResult> {
  return api<QrRegenerateResult>(`/api/v1/assets/${encodeURIComponent(ref)}/qr/regenerate`, {
    method: "POST",
  });
}

/**
 * Printable A4 label sheet (3 × 8 per page) — GET /api/v1/assets/qr/batch
 * (EXPORT_DATA). Omit `refs` for every active asset (server caps at 96
 * labels / four sheets).
 */
export async function fetchQrBatchPdf(refs: string[] = []): Promise<Blob> {
  const query = refs.length ? `?ids=${refs.map(encodeURIComponent).join(",")}` : "";
  return apiBlob(`/api/v1/assets/qr/batch${query}`);
}

/** Re-exported for existing importers — the helper now lives in `@/lib/utils`
 * because Phase 7 report/audit CSV downloads need it too. */
export { downloadBlob } from "@/lib/utils";
