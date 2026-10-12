"""GET /api/v1/assets — paginated, filtered, DB-side search (Plan #19-#22).

Phase 3: normalised status filtering (operational_status), sort/floor/room/
lifecycle/location_status/due_before filters, /assets/facets, and an
enriched detail payload (histories, attributes, full location chain,
qr_version). Legacy /api/acs keeps working until task 8 removes it.
"""

from __future__ import annotations

from flask import Blueprint, request
from psycopg2.extras import RealDictCursor

from app.auth.jwt import require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, paginated, success
from app.services import location_sql
from app.services.qr_service import QR_VERSION
from app.services.status_map import normalize_status_filter

assets_v1_bp = Blueprint("assets_v1", __name__)

ALLOWED_STATUSES = None  # free-text legacy statuses allowed; lifecycle validated on write

# Effective-location columns (§7): the confirmed chain from asset_locations
# with the pre-014 room-mirror fallback, the preserved source_* values and
# the per-field statuses — one shape for list, detail and CSV export.
BASE_SELECT = f"""
    a.ac_id, a.asset_uuid, a.asset_code,
    COALESCE(al.room_id, a.room_id) AS room_id,
    {location_sql.label("room")} AS room_name,
    {location_sql.room_type_label()} AS room_type,
    f.floor_id, {location_sql.label("floor")} AS floor_name, f.floor_number,
    b.block_id, {location_sql.label("block")} AS block_name,
    d.department_id, {location_sql.label("department")} AS department_name,
    c.college_id, {location_sql.label("college")} AS college_name,
    r.room_name AS canon_room_name, r.room_type AS canon_room_type,
    f.floor_name AS canon_floor_name, b.block_name AS canon_block_name,
    d.department_name AS canon_department_name, c.college_name AS canon_college_name,
    al.source_college_value, al.source_block_value, al.source_block_id_value,
    al.source_floor_value, al.source_floor_id_value,
    al.source_department_value, al.source_room_type_value,
    al.source_room_number_value, al.source_room_id_value,
    al.college_status, al.block_status, al.floor_status,
    al.department_status, al.room_status,
    al.manual_fields,
    a.ac_make_company, a.ac_model_number, a.ac_serial_number,
    a.ac_capacity_ton, a.ac_type, a.manufacturing_year, a.installation_year,
    a.operational_status, a.location_status,
    a.lifecycle_status, a.is_active, a.qr_version,
    a.ac_star_rating, a.power_rating_kw, a.power_factor,
    a.installation_date, a.last_maintenance_date, a.next_service_date,
    a.technician, a.remarks, a.created_at, a.updated_at
"""

# Whitelisted sort keys (Phase 3 task 3). Prefix "-" = descending.
SORT_KEYS = {
    "ac_id": "a.ac_id",
    "name": "a.ac_make_company",
    "status": "a.operational_status",
    "updated": "a.updated_at",
    "created": "a.created_at",
    "next_service": "a.next_service_date",
    "capacity": "a.ac_capacity_ton",
    "room": "r.room_name",
    "block": "b.block_name",
}


def _serialize(row: dict) -> dict:
    out = dict(row)
    for k, v in list(out.items()):
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, float) or type(v).__name__ == "Decimal":
            out[k] = float(v)
        elif isinstance(v, memoryview):
            out[k] = str(v)
    # asset_uuid may come back as UUID object
    if out.get("asset_uuid") is not None:
        out["asset_uuid"] = str(out["asset_uuid"])
    # Nested effective-location payload (§7): confirmed ids + preserved
    # source + per-field statuses for rows that carry location columns.
    loc = location_sql.location_object(out)
    if loc is not None:
        out["location"] = loc
    return out


def _sort_clause(raw: str | None) -> str:
    if not raw:
        return "a.ac_id ASC"
    desc = raw.startswith("-")
    key = raw[1:] if desc else raw
    column = SORT_KEYS.get(key)
    if column is None:
        return "a.ac_id ASC"  # unknown sort key: stable default, never 500
    return f"{column} {'DESC' if desc else 'ASC'}"


def _like_literal(value: str) -> str:
    """Escape LIKE metacharacters so the value is matched verbatim.

    ``status_map.normalize_status_filter`` resolves unknown tokens to a verbatim
    match against ``operational_status`` (the free-text column this used to
    probe was dropped in 013), and the default LIKE escape character
    is a backslash — so a caller passing a trailing ``\\`` produced
    ``ILIKE '\\'`` -> SQLSTATE 22025 ("LIKE pattern must not end with escape
    character") -> an unhandled 500 on ``/assets`` and ``/dashboard/summary``
    (Phase 8 finding). Escaping also stops ``%``/``_`` from silently turning
    an exact status match into a wildcard one.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def clean_filter(value: str | None) -> str:
    """Normalise one filter argument: blank and the UI's literal "All"
    sentinel both mean "no filter" (shared with the /reports/* builders).

    Left in place, the sentinel became a real predicate —
    ``operational_status ILIKE 'All'`` or ``block_name ILIKE '%All%'`` —
    so a dashboard opened with its default "All" filters returned zero rows
    even though the table held assets. Only the exact sentinel is dropped;
    every other value (e.g. a block legitimately containing the word) is
    preserved verbatim.
    """
    text = (value or "").strip()
    return "" if text == "All" else text


def _build_filters(args) -> tuple[list[str], list]:
    """Shared WHERE builder for list + facets (identical semantics)."""
    where: list[str] = []
    params: list = []

    def eq(column: str, value: str | None):
        if value:
            where.append(f"{column} = %s")
            params.append(value)

    def like(column: str, value: str | None):
        if value:
            where.append(f"{column} ILIKE %s")
            params.append(f"%{value}%")

    status = clean_filter(args.get("status"))
    if status:
        canonical = normalize_status_filter(status)
        if canonical:
            where.append("a.operational_status = %s")
            params.append(canonical)
        else:
            # Unknown token: exact, case-insensitive match against the
            # constrained vocabulary (the free-text column this probed was
            # dropped in 013) — still the raw, escaped value as a bound param.
            where.append("a.operational_status ILIKE %s")
            params.append(_like_literal(status))

    block = clean_filter(args.get("block"))
    if block:
        clause, block_params = location_sql.filter_match("block", block)
        where.append(clause)
        params += block_params
    department = clean_filter(args.get("department"))
    if department:
        clause, dept_params = location_sql.filter_match("department", department)
        where.append(clause)
        params += dept_params

    like("a.ac_type", clean_filter(args.get("asset_type")))
    like("a.ac_make_company", clean_filter(args.get("manufacturer")))
    floor = clean_filter(args.get("floor"))
    if floor:
        clause, floor_params = location_sql.filter_match("floor", floor)
        where.append(clause)
        params += floor_params
    room = clean_filter(args.get("room"))
    if room:
        clause, room_params = location_sql.filter_match("room", room)
        where.append(clause)
        params += room_params
    eq("a.lifecycle_status", clean_filter(args.get("lifecycle")).upper() or None)
    eq("a.location_status", clean_filter(args.get("location_status")).upper() or None)

    # `All` here would otherwise reach a date comparison (SQLSTATE 22007).
    due_before = clean_filter(args.get("due_before"))
    if due_before:
        where.append("a.next_service_date <= %s")
        params.append(due_before)

    search = clean_filter(args.get("search"))
    if search:
        # Global search over identity + EFFECTIVE location (§10): canonical
        # names/ids and every preserved source value, through the same
        # centralized expressions the readers use — no second implementation.
        pattern = f"%{search}%"
        where.append(
            "(a.ac_id ILIKE %s OR a.asset_code ILIKE %s OR a.ac_serial_number ILIKE %s"
            " OR a.ac_make_company ILIKE %s OR a.ac_model_number ILIKE %s"
            f" OR {location_sql.label('block')} ILIKE %s"
            f" OR {location_sql.label('floor')} ILIKE %s"
            f" OR {location_sql.label('department')} ILIKE %s"
            f" OR r.room_name ILIKE %s OR {location_sql.ROOM_ID_SQL} ILIKE %s"
            " OR al.source_block_value ILIKE %s OR al.source_floor_value ILIKE %s"
            " OR al.source_department_value ILIKE %s OR al.source_room_number_value ILIKE %s"
            " OR al.source_room_type_value ILIKE %s)"
        )
        params += [pattern] * 15

    # Explicit lifecycle browsing includes decommissioned rows; otherwise the
    # historic active-only default applies.
    lifecycle = clean_filter(args.get("lifecycle"))
    if not lifecycle:
        where.insert(0, "COALESCE(a.is_active, TRUE) = TRUE")
    if not where:
        where.append("TRUE")
    return where, params


# One join shape for every asset-centric reader (§7): asset_locations for
# the confirmed ids, COALESCE chain back to the pre-014 mirror. Permission
# scoping expressions built against b.* therefore bind canonical ids only.
JOINS = location_sql.ASSET_JOINS


@assets_v1_bp.route("/api/v1/assets", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def list_assets():
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(200, max(1, int(request.args.get("page_size", 50))))
    except ValueError:
        return error("INVALID_PAGINATION", "page and page_size must be integers.", 400)

    where, params = _build_filters(request.args)
    where_sql = "WHERE " + " AND ".join(where)
    order_sql = _sort_clause(request.args.get("sort"))
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(f"SELECT COUNT(*) AS total {JOINS} {where_sql}", tuple(params))
            total = cur.fetchone()["total"]
            offset = (page - 1) * page_size
            cur.execute(
                f"SELECT {BASE_SELECT} {JOINS} {where_sql} ORDER BY {order_sql} LIMIT %s OFFSET %s",
                tuple(params) + (page_size, offset),
            )
            items = [_serialize(r) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:
        return error("ASSET_LIST_FAILED", "Unable to list assets.", 500)
    return paginated(items, page, page_size, total)


@assets_v1_bp.route("/api/v1/assets/facets", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def asset_facets():
    """Distinct values + counts for the filter bar (server-driven).

    One statement for all seven facets: the individual GROUP BY legs are
    combined with UNION ALL (each leg keeps its original expression, WHERE,
    ORDER BY and LIMIT 100, so the returned values are byte-identical), and
    the outer ORDER BY restores the per-facet ``count DESC, value`` order.
    Against a remote database this is one round trip instead of seven —
    the endpoint measured 8 statements / ~891 ms in production logs before
    the consolidation.
    """
    facets = {
        "status": "a.operational_status",
        "asset_type": "a.ac_type",
        "manufacturer": "a.ac_make_company",
        "lifecycle": "a.lifecycle_status",  # spans ALL rows (incl. inactive)
        "location_status": "a.location_status",
        # Location facets use the effective label (§7): canonical name/id,
        # then the preserved source text, so unresolved rows surface as
        # their imported value instead of an empty bucket.
        "block": location_sql.label_or("block", "''"),  # resolved via JOINS
        "department": location_sql.label_or("department", "''"),
    }
    # Location facets additionally carry the stable canonical id (None for
    # source-only values) and group by (id, value): two distinct canonical
    # entities that share a display name are never silently merged, and the
    # dashboard can filter by canonical id instead of display text.
    location_facets = ("block", "department")
    active_where = "WHERE COALESCE(a.is_active, TRUE) = TRUE"
    out: dict[str, list[dict]] = {key: [] for key in facets}
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            legs: list[str] = []
            for key, column in facets.items():
                if column.startswith("a."):
                    # lifecycle explicitly browses every row; the other
                    # facets list active assets only (unchanged semantics).
                    # NULL id keeps the UNION ALL shape identical to the
                    # location legs (facet, value, id, count).
                    where_sql = "" if key == "lifecycle" else active_where
                    legs.append(
                        f"(SELECT '{key}' AS facet, COALESCE({column}, '') AS value,"
                        f" NULL::text AS id,"
                        f" COUNT(*) AS count FROM acs a {where_sql}"
                        " GROUP BY 2 ORDER BY count DESC, value LIMIT 100)"
                    )
                else:
                    id_expr = location_sql.id_sql(key)
                    legs.append(
                        f"(SELECT '{key}' AS facet, COALESCE({column}, '') AS value,"
                        f" {id_expr} AS id,"
                        f" COUNT(*) AS count {JOINS} {active_where}"
                        " GROUP BY 2, 3 ORDER BY count DESC, value LIMIT 100)"
                    )
            cur.execute(" UNION ALL ".join(legs) + " ORDER BY facet, count DESC, value")
            for row in cur.fetchall():
                entry: dict = {"value": row["value"], "count": row["count"]}
                if row["facet"] in location_facets:
                    entry["id"] = row["id"]
                out[row["facet"]].append(entry)
            cur.close()
        finally:
            conn.close()
    except Exception:
        return error("ASSET_FACETS_FAILED", "Unable to load facets.", 500)
    return success(out)


def _find_asset(cur, ref: str):
    """Resolve by asset_uuid, asset_code, or legacy ac_id — single-row lookup."""
    cur.execute(
        f"SELECT {BASE_SELECT} {JOINS} "
        "WHERE a.asset_uuid::text = %s OR a.ac_id = %s OR a.asset_code = %s LIMIT 1",
        (ref, ref, ref),
    )
    return cur.fetchone()


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


@assets_v1_bp.route("/api/v1/assets/<ref>", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def get_asset(ref):
    """Enriched detail (Phase 3 task 4): full location chain, work-order +
    legacy maintenance history, movement history, room-change ledger
    (asset_location_history, Phase 5), extensible attributes and the QR
    payload version — one query per section, no N+1."""
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            row = _find_asset(cur, ref)
            maintenance_history: list[dict] = []
            movement_history: list[dict] = []
            location_history: list[dict] = []
            attributes: list[dict] = []
            if row:
                ac_id = row["ac_id"]
                asset_uuid = row["asset_uuid"]
                # Section 1 — work orders (with technician display names).
                # Rows migrated from the legacy table carry
                # legacy_maintenance_id and are rendered by section 1b below,
                # so exclude them here or history would show them twice.
                try:
                    cur.execute(
                        "SELECT w.id, w.ac_id, w.maintenance_type, w.priority, w.status,"
                        " w.scheduled_date, w.started_at, w.completed_at, w.problem_description,"
                        " w.work_performed, w.parts_used, w.labor_hours, w.remarks,"
                        " w.created_at, p.name AS technician_name"
                        " FROM maintenance_work_orders w"
                        " LEFT JOIN profiles p ON p.id = w.technician_id"
                        " WHERE (w.ac_id = %s OR w.asset_uuid = %s)"
                        " AND w.legacy_maintenance_id IS NULL"
                        " ORDER BY COALESCE(w.completed_at, w.scheduled_date, w.created_at) DESC LIMIT 50",
                        (ac_id, asset_uuid),
                    )
                    maintenance_history = [_serialize(dict(r)) for r in cur.fetchall()]
                except Exception:
                    maintenance_history = []
                # Section 1b — migrated legacy maintenance rows.
                try:
                    cur.execute(
                        "SELECT maintenance_id AS id, maintenance_type, maintenance_date,"
                        " remarks, NULL AS priority, 'COMPLETED' AS status, maintenance_date AS scheduled_date"
                        " FROM maintenance WHERE ac_id = %s ORDER BY maintenance_date DESC LIMIT 50",
                        (ac_id,),
                    )
                    maintenance_history += [_serialize(dict(r)) for r in cur.fetchall()]
                except Exception:
                    pass
                # Section 2 — movements with joined names (no per-row lookups).
                try:
                    cur.execute(
                        "SELECT m.id, m.ac_id, m.status, m.reason, m.rejection_reason,"
                        " m.requested_at, m.approved_at, m.completed_at,"
                        " fr.room_name AS from_room_name, tr.room_name AS to_room_name,"
                        " rp.name AS requested_by_name, ap.name AS approved_by_name"
                        " FROM asset_movements m"
                        " LEFT JOIN rooms fr ON fr.room_id = m.from_room_id"
                        " LEFT JOIN rooms tr ON tr.room_id = m.to_room_id"
                        " LEFT JOIN profiles rp ON rp.id = m.requested_by"
                        " LEFT JOIN profiles ap ON ap.id = m.approved_by"
                        " WHERE m.ac_id = %s OR m.asset_uuid = %s"
                        " ORDER BY m.requested_at DESC LIMIT 50",
                        (ac_id, asset_uuid),
                    )
                    movement_history = [_serialize(dict(r)) for r in cur.fetchall()]
                except Exception:
                    movement_history = []
                # Section 2b — completed room changes (Phase 5, written
                # atomically with acs.room_id on completion). Pre-009
                # databases have no such table yet: degrade to empty.
                try:
                    cur.execute(
                        "SELECT h.id, h.movement_id, h.at, h.from_room_id, h.to_room_id,"
                        " fr.room_name AS from_room_name, ff.floor_name AS from_floor_name,"
                        " fb.block_name AS from_block_name,"
                        " tr.room_name AS to_room_name, tf.floor_name AS to_floor_name,"
                        " tb.block_name AS to_block_name"
                        " FROM asset_location_history h"
                        " LEFT JOIN rooms fr ON fr.room_id = h.from_room_id"
                        " LEFT JOIN floors ff ON ff.floor_id = fr.floor_id"
                        " LEFT JOIN blocks fb ON fb.block_id = ff.block_id"
                        " LEFT JOIN rooms tr ON tr.room_id = h.to_room_id"
                        " LEFT JOIN floors tf ON tf.floor_id = tr.floor_id"
                        " LEFT JOIN blocks tb ON tb.block_id = tf.block_id"
                        " WHERE h.ac_id = %s ORDER BY h.at DESC, h.id DESC LIMIT 50",
                        (ac_id,),
                    )
                    location_history = [_serialize(dict(r)) for r in cur.fetchall()]
                except Exception:
                    location_history = []
                # Section 3 — extensible attributes from the flexible import.
                try:
                    cur.execute(
                        "SELECT attribute_name, attribute_value, attribute_type"
                        " FROM asset_attributes WHERE asset_uuid = %s ORDER BY attribute_name LIMIT 200",
                        (asset_uuid,),
                    )
                    attributes = [dict(r) for r in cur.fetchall()]
                except Exception:
                    attributes = []
            cur.close()
        finally:
            conn.close()
    except Exception:
        return error("ASSET_LOOKUP_FAILED", "Unable to look up asset.", 500)
    if not row:
        return error("ASSET_NOT_FOUND", "Asset not found.", 404)
    data = _serialize(row)
    data["maintenance_history"] = maintenance_history
    data["movement_history"] = movement_history
    data["location_history"] = location_history
    data["attributes"] = attributes
    # Per-asset label version (migration 010) — bumped by
    # POST /api/v1/assets/<ref>/qr/regenerate; QR_VERSION backstops NULL.
    data["qr_version"] = row.get("qr_version") or QR_VERSION
    return success(data)


@assets_v1_bp.route("/api/v1/assets/export", methods=["GET"])
@require_auth
@require_permission("EXPORT_DATA")
def export_assets():
    """Streaming CSV export (Phase 3 task 7). Honors the same filter
    arguments as the list endpoint; row by row via fetchmany — never loads
    the full result set into memory."""
    import csv as csv_mod
    import io as io_mod
    from datetime import date as date_cls

    from flask import Response

    fmt = (request.args.get("format") or "csv").strip().lower()
    if fmt != "csv":
        return error("UNSUPPORTED_FORMAT", "Only format=csv is supported.", 400)

    where, params = _build_filters(request.args)
    where_sql = "WHERE " + " AND ".join(where)
    order_sql = _sort_clause(request.args.get("sort"))

    def rows():
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(f"SELECT {BASE_SELECT} {JOINS} {where_sql} ORDER BY {order_sql}", tuple(params))
            columns = [d[0] for d in cur.description]
            buffer = io_mod.StringIO()
            # BOM so Excel opens the UTF-8 file with correct encoding.
            buffer.write("\ufeff")
            writer = csv_mod.writer(buffer)
            writer.writerow(columns)
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate()
            while True:
                batch = cur.fetchmany(500)
                if not batch:
                    break
                for record in batch:
                    writer.writerow(
                        [_iso(record[c]) if hasattr(record[c], "isoformat") else record[c] for c in columns]
                    )
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate()
            cur.close()
        finally:
            conn.close()

    filename = f"ac-assets-{date_cls.today().isoformat()}.csv"
    return Response(
        rows(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
