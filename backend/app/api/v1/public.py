"""Anonymous public endpoints (Phase 1.2 allow-list, Phase 6 read model).

Only ``GET /api/v1/public/assets/<ref>`` exists — the QR landing target —
returning exactly the reduced field set the plan allows (identity, model,
location, status, last service) and never a serial number, technician,
remarks, attributes or history. The response carries
``Cache-Control: public, max-age=60`` so browsers/intermediaries may cache
it, and a strict per-IP rate limit protects the anonymous route.
"""

from __future__ import annotations

import logging

from flask import Blueprint, g
from psycopg2.extras import RealDictCursor

from app.config.database import get_db_connection
from app.core import error, success
from app.middleware.rate_limit import rate_limit
from app.services import location_sql
from app.services.visual_id import visual_id_for_row

logger = logging.getLogger(__name__)

public_v1_bp = Blueprint("public_v1", __name__)

# Exactly the fields the Phase 6 plan lists for the public landing page.
# ``capacity`` is cast to float so the JSON encoder never sees a Decimal.
# Location labels are the effective ones (§7): canonical name first, the
# preserved source text for levels the resolver could not confirm.
_PUBLIC_FIELDS = (
    "a.asset_code, a.ac_make_company AS make, a.ac_model_number AS model,"
    " a.ac_type AS type, a.ac_capacity_ton::float AS capacity,"
    " a.operational_status, a.last_maintenance_date,"
    f" {location_sql.label('block')} AS block,"
    f" {location_sql.label('floor')} AS floor,"
    f" {location_sql.label('room')} AS room"
)

# Helper columns fetched only so _lookup_asset can DERIVE the Visual Asset
# ID with the one centralized formatter; they are stripped from the payload
# again and never serialized. Aliases match visual_id_for_row's keys.
_PUBLIC_VISUAL_FIELDS = (
    " a.asset_uuid,"
    f" {location_sql.label('college')} AS college_name,"
    f" {location_sql.label('block')} AS block_name,"
    f" {location_sql.label('floor')} AS floor_name,"
    f" {location_sql.label('department')} AS department_name,"
    f" {location_sql.room_type_label()} AS room_type,"
    f" COALESCE(al.room_id, a.room_id) AS room_id,"
    " r.room_name AS canon_room_name, al.source_room_number_value"
)

_VISUAL_HELPER_KEYS = (
    "asset_uuid",
    "college_name",
    "block_name",
    "floor_name",
    "department_name",
    "room_type",
    "room_id",
    "canon_room_name",
    "source_room_number_value",
)


def _lookup_asset(cur, ref: str):
    cur.execute(
        f"""SELECT {_PUBLIC_FIELDS},{_PUBLIC_VISUAL_FIELDS}
            {location_sql.ASSET_JOINS}
            WHERE a.asset_uuid::text = %s OR a.asset_code = %s OR a.ac_id = %s
            LIMIT 1""",
        (ref, ref, ref),
    )
    row = cur.fetchone()
    if row is None:
        return None
    out = dict(row)
    # Derived Visual Asset ID (label-consistent with the printed QR label);
    # display-only — a failure yields null, never a 500 on the landing page.
    try:
        out["visual_id"] = visual_id_for_row(out)
    except Exception:  # noqa: BLE001
        out["visual_id"] = None
    for key in _VISUAL_HELPER_KEYS:
        out.pop(key, None)
    # Same JSON-safe conversion as the authenticated read model: DATE ->
    # ISO string (the encoder's default date rendering is not ISO).
    for key, value in list(out.items()):
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
    return out


@public_v1_bp.route("/api/v1/public/assets/<ref>", methods=["GET"])
@rate_limit(30, 60, "public-asset")
def get_public_asset(ref):
    """Reduced, cacheable QR landing payload (anonymous by design)."""
    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            asset = _lookup_asset(cur, ref)
    except Exception:  # noqa: BLE001
        logger.exception("public asset lookup failed request_id=%s", getattr(g, "request_id", "-"))
        return error("PUBLIC_ASSET_FAILED", "Unable to load asset.", 500)
    if asset is None:
        return error("NOT_FOUND", "Asset not found.", 404)
    resp, status = success(asset)
    resp.headers["Cache-Control"] = "public, max-age=60"
    return resp, status


get_public_asset._public_endpoint = True
