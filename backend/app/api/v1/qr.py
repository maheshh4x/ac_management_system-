"""QR endpoints (Plan #9, #10, Phase 6): on-demand generation, never stored.

The payload is always ``{FRONTEND_URL}/a/{asset_uuid}`` — stable, and never
the internal ``ac_id`` — so a printed sticker survives every relabel and
relocation. There is no QR table: images and label sheets are minted per
request. ``POST .../qr/regenerate`` only bumps the per-asset label counter
(``acs.qr_version``) and audits the re-issue; the encoded URL is unchanged,
so stickers already in the field keep resolving.
"""

from __future__ import annotations

import io

from flask import Blueprint, Response, request
from psycopg2.extras import RealDictCursor

from app.auth.jwt import require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, success
from app.middleware.rate_limit import rate_limit
from app.services import location_sql, qr_service
from app.services.audit_service import audit

qr_v1_bp = Blueprint("qr_v1", __name__)

# One A4 sheet holds 3 x 8 = 24 labels; cap a single PDF at four sheets.
_MAX_BATCH_LABELS = 96

# Effective joins (§7): confirmed ids first, pre-014 mirror fallback after.
_BATCH_SELECT = (
    "SELECT a.asset_uuid, a.asset_code, a.ac_make_company, a.ac_model_number,"
    f" {location_sql.label('block')} AS block_name,"
    f" {location_sql.label('floor')} AS floor_name,"
    f" {location_sql.label('room')} AS room_name"
    f" {location_sql.ASSET_JOINS}"
)


def _asset_payload(row) -> str:
    return qr_service.qr_payload(
        asset_uuid=str(row["asset_uuid"]) if row.get("asset_uuid") else None,
        asset_code=row.get("asset_code"),
    )


@qr_v1_bp.route("/api/v1/assets/<ref>/qr", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def asset_qr(ref):
    """QR image (PNG/SVG), its raw payload, or an exact-size PNG.

    Query params: ``format`` = png | svg | payload (default png),
    ``size`` = requested PNG edge length in pixels (128-2048).
    """
    fmt = (request.args.get("format") or "png").lower()
    size = None
    raw_size = request.args.get("size")
    if raw_size:
        try:
            size = int(raw_size)
        except ValueError:
            return error("INVALID_SIZE", "size must be an integer pixel value.", 400)
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT ac_id, asset_uuid, asset_code FROM acs"
                " WHERE ac_id=%s OR asset_code=%s OR asset_uuid::text=%s LIMIT 1",
                (ref, ref, ref),
            )
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
    except Exception:
        return error("QR_LOOKUP_FAILED", "Unable to resolve asset for QR.", 500)
    if not row:
        return error("ASSET_NOT_FOUND", "Asset not found.", 404)
    payload = _asset_payload(row)
    if fmt == "svg":
        return Response(qr_service.qr_svg_text(payload), mimetype="image/svg+xml")
    if fmt == "payload":
        return success({"payload": payload})
    return Response(qr_service.qr_png_bytes(payload, size=size), mimetype="image/png")


@qr_v1_bp.route("/api/v1/qr/resolve", methods=["POST"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(30, 60, "qr-resolve")
def qr_resolve():
    """Resolve a scanned payload (URL or bare ref) to its asset.

    Absolute URLs are strictly parsed: a host that is neither this site's
    FRONTEND_URL host nor the API's own host is rejected (FOREIGN_HOST)
    before any lookup runs. The ref lookup is triple-key
    (asset_uuid / asset_code / ac_id); unknown refs are 404.
    """
    body = request.get_json(silent=True) or {}
    raw = body.get("payload") or body.get("qr") or body.get("text") or ""
    allowed = {h for h in (qr_service.frontend_host(), (request.host or "").lower()) if h}
    try:
        parsed = qr_service.parse_qr_payload(raw, allowed_hosts=allowed)
    except qr_service.ForeignHostError:
        return error("FOREIGN_HOST", "This QR code was issued by a different site.", 400)
    except ValueError:
        return error("INVALID_QR", "Unrecognized QR payload.", 400)
    ref = parsed.get("asset_uuid") or parsed.get("asset_code")
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT ac_id, asset_code, asset_uuid, ac_make_company, operational_status, room_id"
                " FROM acs WHERE ac_id=%s OR asset_code=%s OR asset_uuid::text=%s LIMIT 1",
                (ref, ref, ref),
            )
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
    except Exception:
        return error("QR_LOOKUP_FAILED", "Unable to resolve QR.", 500)
    if not row:
        return error("ASSET_NOT_FOUND", "No asset matches this QR.", 404)
    data = dict(row)
    if data.get("asset_uuid"):
        data["asset_uuid"] = str(data["asset_uuid"])
    return success(data)


@qr_v1_bp.route("/api/v1/assets/<ref>/qr/regenerate", methods=["POST"])
@require_auth
@require_permission("EDIT_ASSET")
@rate_limit(30, 60, "qr-regen")
def regenerate_qr(ref):
    """Re-issue an asset's QR label.

    Bumps ``acs.qr_version`` and audits REGENERATE_QR. The payload URL
    depends only on the immutable asset_uuid, so it is byte-identical
    before and after — old stickers keep working; this only records that a
    fresh label was issued (and re-arms downloads/printing).
    """
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "UPDATE acs SET qr_version = qr_version + 1"
                " WHERE ac_id=%s OR asset_code=%s OR asset_uuid::text=%s"
                " RETURNING ac_id, asset_uuid, asset_code, qr_version",
                (ref, ref, ref),
            )
            row = cur.fetchone()
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        return error("QR_REGENERATE_FAILED", "Unable to regenerate the QR label.", 500)
    if not row:
        return error("ASSET_NOT_FOUND", "Asset not found.", 404)
    audit(
        "REGENERATE_QR",
        "asset",
        row["ac_id"],
        {"qr_version": row["qr_version"] - 1},
        {"qr_version": row["qr_version"]},
    )
    return success(
        {
            "ac_id": row["ac_id"],
            "asset_uuid": str(row["asset_uuid"]) if row.get("asset_uuid") else None,
            "asset_code": row.get("asset_code"),
            "qr_version": row["qr_version"],
            "payload": _asset_payload(row),
        }
    )


@qr_v1_bp.route("/api/v1/assets/qr/batch", methods=["GET"])
@require_auth
@require_permission("EXPORT_DATA")
@rate_limit(10, 60, "qr-batch")
def qr_batch():
    """Printable label sheet PDF — A4, 3 columns x 8 rows per page.

    ``?ids=a,b,c`` limits the sheet to those refs (uuid / code / ac_id);
    omitted = every active asset. Either way the PDF is capped at 96
    labels (four sheets). SUPER_ADMIN / FACILITY_MANAGER only.
    """
    refs = [r.strip() for r in (request.args.get("ids") or "").split(",") if r.strip()]
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if refs:
                cur.execute(
                    f"{_BATCH_SELECT}"
                    " WHERE a.ac_id = ANY(%s) OR a.asset_code = ANY(%s)"
                    " OR a.asset_uuid::text = ANY(%s)"
                    " ORDER BY a.asset_code LIMIT %s",
                    (refs, refs, refs, _MAX_BATCH_LABELS),
                )
            else:
                cur.execute(
                    f"{_BATCH_SELECT}" " WHERE a.is_active = TRUE" " ORDER BY a.asset_code LIMIT %s",
                    (_MAX_BATCH_LABELS,),
                )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:
        return error("QR_BATCH_FAILED", "Unable to build the label sheet.", 500)
    if not rows:
        return error("NO_ASSETS", "No assets matched for printing.", 404)
    pdf = _label_sheet_pdf(rows)
    return Response(
        pdf,
        mimetype="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="qr-labels.pdf"'},
    )


def _label_sheet_pdf(rows: list[dict]) -> bytes:
    """Render labels onto A4 sheet(s): 3 x 8 cells, dashed cut lines.

    Each cell: QR of the immutable payload + asset code + make/model +
    location, so a printed sticker identifies the unit even offline.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as pdf_canvas

    cols, rows_per_page = 3, 8
    margin = 18.0
    page_w, page_h = A4
    cell_w = (page_w - 2 * margin) / cols
    cell_h = (page_h - 2 * margin) / rows_per_page
    buf = io.BytesIO()
    canvas = pdf_canvas.Canvas(buf, pagesize=A4)
    for i, row in enumerate(rows):
        if i and i % (cols * rows_per_page) == 0:
            canvas.showPage()
        col = i % cols
        line = (i // cols) % rows_per_page
        x = margin + col * cell_w
        y = page_h - margin - (line + 1) * cell_h

        canvas.setDash(2, 2)
        canvas.setStrokeColorRGB(0.75, 0.78, 0.82)
        canvas.rect(x + 2, y + 2, cell_w - 4, cell_h - 4)
        canvas.setDash()

        payload = _asset_payload(row)
        img = ImageReader(io.BytesIO(qr_service.qr_png_bytes(payload)))
        qr_px = min(cell_h * 0.6, cell_w * 0.5)
        canvas.drawImage(
            img,
            x + (cell_w - qr_px) / 2,
            y + cell_h - qr_px - 5,
            width=qr_px,
            height=qr_px,
            mask="auto",
        )

        text_y = y + cell_h - qr_px - 14
        canvas.setFillColorRGB(0.1, 0.1, 0.12)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawCentredString(x + cell_w / 2, text_y, row.get("asset_code") or "")
        canvas.setFont("Helvetica", 6.5)
        model = " ".join(p for p in (row.get("ac_make_company"), row.get("ac_model_number")) if p)
        canvas.drawCentredString(x + cell_w / 2, text_y - 9, model[:40])
        location = " > ".join(
            p for p in (row.get("block_name"), row.get("floor_name"), row.get("room_name")) if p
        )
        canvas.setFont("Helvetica", 6)
        canvas.drawCentredString(x + cell_w / 2, text_y - 17, location[:52])
    canvas.showPage()
    canvas.save()
    return buf.getvalue()
