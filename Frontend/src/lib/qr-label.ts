/**
 * Branded QR label composition — client-only, dependency-free, deterministic.
 *
 * Pure model builder (`buildQrLabelModel`) + a thin canvas renderer
 * (`renderQrLabelPng`) over an ALREADY-FETCHED QR blob: constructing a label
 * never fires an API request and never generates a QR (the backend-minted
 * PNG/SVG stays the single QR source, so the payload cannot drift from the
 * stable `{FRONTEND_URL}/a/{asset_uuid}` identifier).
 *
 * All location strings arrive pre-resolved through the app's effective
 * location rule (canonical name -> id -> preserved source -> omitted);
 * blank levels are omitted from the label, never fabricated.
 */

/** Pre-resolved display strings for one asset (no resolution lives here). */
export interface QrLabelFields {
  /** Stable human identifier shown as "Asset ID" (raw ac_id). */
  assetId: string;
  /** Optional "make + model" subtitle. */
  make?: string | null;
  model?: string | null;
  college?: string | null;
  block?: string | null;
  department?: string | null;
  /** Floor/location text, where available. */
  floor?: string | null;
  /** Room/location text, where available. */
  room?: string | null;
}

export interface QrLabelRow {
  label: string;
  value: string;
}

export interface QrLabelModel {
  brand: string;
  assetId: string;
  subtitle: string | null;
  rows: QrLabelRow[];
  footer: string;
}

const FALLBACK = "—";

/** Drop blanks, keep everything else verbatim (wrapping happens at render). */
function clean(value: string | null | undefined): string | null {
  if (value == null) return null;
  const text = String(value).trim();
  return text && text !== FALLBACK ? text : null;
}

/**
 * Deterministic label model: branding + identifier + one row per available
 * dimension (College / Block / Department / Location) + scan instruction.
 * Floor and room share the Location row ("floor / room"), matching the
 * public page's joined display; a missing side simply shortens it.
 */
export function buildQrLabelModel(fields: QrLabelFields): QrLabelModel {
  const assetId = (fields.assetId ?? "").trim();
  const make = clean(fields.make);
  const model = clean(fields.model);
  const rows: QrLabelRow[] = [];
  const college = clean(fields.college);
  const block = clean(fields.block);
  const department = clean(fields.department);
  const location = [clean(fields.floor), clean(fields.room)].filter((p): p is string => p !== null);
  if (college) rows.push({ label: "College", value: college });
  if (block) rows.push({ label: "Block", value: block });
  if (department) rows.push({ label: "Department", value: department });
  if (location.length > 0) rows.push({ label: "Location", value: location.join(" / ") });
  return {
    brand: "NITTTR ACMS",
    assetId,
    subtitle: [make, model].filter((p): p is string => p !== null).join(" ") || null,
    rows,
    footer: "Scan to view asset details",
  };
}

// ── canvas renderer ─────────────────────────────────────────────────────────
// Print resolution: 3x CSS scale (≈900 px card). All geometry derives from
// the row count, so long names wrap instead of clipping.

const CARD_WIDTH = 900;
const PADDING = 56;
const QR_SIZE = 400;
const QR_QUIET = 28;
const GAP_AFTER_QR = 44;

const FONT_STACK = "Arial, Helvetica, sans-serif";

interface TextStyle {
  font: string;
  color: string;
  lineHeight: number;
}

const BRAND_STYLE: TextStyle = { font: `700 44px ${FONT_STACK}`, color: "#0f172a", lineHeight: 60 };
const TITLE_STYLE: TextStyle = { font: `700 52px ${FONT_STACK}`, color: "#0f172a", lineHeight: 68 };
const SUBTITLE_STYLE: TextStyle = { font: `400 34px ${FONT_STACK}`, color: "#475569", lineHeight: 46 };
const ROW_LABEL_STYLE: TextStyle = { font: `600 30px ${FONT_STACK}`, color: "#64748b", lineHeight: 40 };
const ROW_VALUE_STYLE: TextStyle = { font: `600 36px ${FONT_STACK}`, color: "#0f172a", lineHeight: 48 };
const FOOTER_STYLE: TextStyle = { font: `400 30px ${FONT_STACK}`, color: "#64748b", lineHeight: 42 };

/** Wrap to at most two lines; overlong text ellipsizes instead of clipping. */
export function wrapLabelText(
  ctx: { measureText: (text: string) => { width: number } },
  text: string,
  maxWidth: number,
): string[] {
  const words = text.split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let current = "";
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (ctx.measureText(candidate).width <= maxWidth || !current) {
      current = candidate;
    } else {
      lines.push(current);
      current = word;
      if (lines.length === 1) {
        // Second line gets the remainder, ellipsized if still too long.
        let rest = current;
        while (rest && ctx.measureText(`${rest}…`).width > maxWidth) {
          rest = rest.slice(0, -1);
        }
        return [lines[0], rest ? `${rest}…` : ""].filter(Boolean);
      }
    }
  }
  if (current) {
    if (lines.length === 0 && ctx.measureText(current).width > maxWidth) {
      // One unbreakable token wider than the column: ellipsize it.
      let rest = current;
      while (rest && ctx.measureText(`${rest}…`).width > maxWidth) {
        rest = rest.slice(0, -1);
      }
      return rest ? [`${rest}…`] : [];
    }
    lines.push(current);
  }
  return lines.slice(0, 2);
}

interface LabelCanvas {
  width: number;
  height: number;
  getContext: (kind: "2d") => LabelContext | null;
  toBlob: (callback: (blob: Blob | null) => void, type?: string) => void;
}

interface LabelContext {
  fillStyle: string | CanvasGradient | CanvasPattern;
  strokeStyle: string | CanvasGradient | CanvasPattern;
  lineWidth: number;
  font: string;
  textAlign: CanvasTextAlign;
  textBaseline: CanvasTextBaseline;
  fillRect: (x: number, y: number, w: number, h: number) => void;
  strokeRect: (x: number, y: number, w: number, h: number) => void;
  fillText: (text: string, x: number, y: number) => void;
  measureText: (text: string) => { width: number };
  drawImage: (image: ImageBitmap, x: number, y: number, w: number, h: number) => void;
}

function drawRow(
  ctx: LabelContext,
  label: string,
  value: string,
  x: number,
  y: number,
  maxWidth: number,
): number {
  ctx.font = ROW_LABEL_STYLE.font;
  ctx.fillStyle = ROW_LABEL_STYLE.color;
  ctx.textAlign = "left";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(label, x, y);
  const valueX = x + 300;
  const wrapped = wrapLabelText(ctx, value, maxWidth - 300);
  ctx.font = ROW_VALUE_STYLE.font;
  ctx.fillStyle = ROW_VALUE_STYLE.color;
  let lineY = y;
  for (const line of wrapped) {
    ctx.fillText(line, valueX, lineY);
    lineY += ROW_VALUE_STYLE.lineHeight;
  }
  return Math.max(ROW_LABEL_STYLE.lineHeight, wrapped.length * ROW_VALUE_STYLE.lineHeight);
}

/**
 * Render the label to a PNG blob. The QR bitmap keeps a white quiet zone;
 * every text block is measured before it is drawn, so nothing clips.
 */
export async function renderQrLabelPng(model: QrLabelModel, qrBlob: Blob): Promise<Blob> {
  const bitmap = await createImageBitmap(qrBlob);
  const canvas = document.createElement("canvas") as unknown as LabelCanvas;
  const maxText = CARD_WIDTH - PADDING * 2;
  const measure = document.createElement("canvas").getContext("2d");
  if (!measure) throw new Error("Canvas 2D is not available in this browser.");

  // Measure dynamic blocks first so the card height is exact.
  const titleLines = wrapLabelText(measure, `Asset ID: ${model.assetId}`, maxText);
  const rowHeights: number[] = [];
  for (const row of model.rows) {
    const wrapped = wrapLabelText(measure, row.value, maxText - 300);
    rowHeights.push(Math.max(ROW_LABEL_STYLE.lineHeight, wrapped.length * ROW_VALUE_STYLE.lineHeight));
  }

  const headerH = BRAND_STYLE.lineHeight + 16;
  const titleH = titleLines.length * TITLE_STYLE.lineHeight + (model.subtitle ? SUBTITLE_STYLE.lineHeight + 8 : 8);
  const rowsH = rowHeights.reduce((sum, h) => sum + h + 14, 0);
  const footerH = FOOTER_STYLE.lineHeight + 8;
  const height =
    PADDING + headerH + 12 + titleH + 20 + rowsH + 20 + (QR_SIZE + QR_QUIET * 2) + GAP_AFTER_QR + footerH + PADDING;

  canvas.width = CARD_WIDTH;
  canvas.height = Math.ceil(height);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D is not available in this browser.");

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.strokeStyle = "#cbd5e1";
  ctx.lineWidth = 3;
  ctx.strokeRect(4, 4, canvas.width - 8, canvas.height - 8);

  let y = PADDING;
  const cx = CARD_WIDTH / 2;
  const left = PADDING;

  ctx.font = BRAND_STYLE.font;
  ctx.fillStyle = BRAND_STYLE.color;
  ctx.textAlign = "center";
  ctx.textBaseline = "alphabetic";
  ctx.fillText(model.brand, cx, y + 44);
  y += headerH + 12;

  ctx.font = TITLE_STYLE.font;
  ctx.fillStyle = TITLE_STYLE.color;
  for (const line of titleLines) {
    ctx.fillText(line, cx, y + 50);
    y += TITLE_STYLE.lineHeight;
  }
  if (model.subtitle) {
    ctx.font = SUBTITLE_STYLE.font;
    ctx.fillStyle = SUBTITLE_STYLE.color;
    ctx.fillText(model.subtitle, cx, y + 34);
    y += SUBTITLE_STYLE.lineHeight + 8;
  } else {
    y += 8;
  }
  y += 20;

  ctx.textAlign = "left";
  for (let i = 0; i < model.rows.length; i++) {
    const row = model.rows[i];
    const used = drawRow(ctx, row.label, row.value, left, y + 34, maxText);
    y += used + 14;
  }
  y += 20 - 14;

  // QR centered with a white quiet zone.
  const qrBox = QR_SIZE + QR_QUIET * 2;
  const qrX = (CARD_WIDTH - qrBox) / 2;
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(qrX, y, qrBox, qrBox);
  ctx.drawImage(bitmap, qrX + QR_QUIET, y + QR_QUIET, QR_SIZE, QR_SIZE);
  if (typeof bitmap.close === "function") bitmap.close();
  y += qrBox + GAP_AFTER_QR;

  ctx.font = FOOTER_STYLE.font;
  ctx.fillStyle = FOOTER_STYLE.color;
  ctx.textAlign = "center";
  ctx.fillText(model.footer, cx, y + 30);

  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/png"));
  if (!blob) throw new Error("Could not encode the QR label image.");
  return blob;
}

/** Render + trigger a file download (client-only DOM anchor). */
export async function downloadQrLabel(
  fields: QrLabelFields,
  qrBlob: Blob,
  filename: string,
): Promise<void> {
  const blob = await renderQrLabelPng(buildQrLabelModel(fields), qrBlob);
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
