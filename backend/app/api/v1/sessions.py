"""AC usage sessions: QR ON/OFF session recording (record-only, no approvals).

Rules:
  start   RECORD_USAGE_SESSION; the asset must exist and be usable (not
          decommissioned/inactive); a FACILITY_MANAGER with assigned_blocks
          set may only record for assets located in those blocks; at most
          ONE open (ended_at IS NULL) session per asset — enforced by the
          partial unique index from 019, so a racing second start loses
          with 409 SESSION_ALREADY_OPEN instead of a duplicate.
  stop    RECORD_USAGE_SESSION; closes the asset's open session (SELECT …
          FOR UPDATE serializes concurrent stops); 409 NO_OPEN_SESSION when
          there is nothing to close. Stopping is allowed on a
          decommissioned asset so open books can always be closed.
  list    VIEW_ASSET; recent sessions with durations for the Energy tab.

Every transition commits its business write first and calls audit() after
(record-only, never blocking — the same policy as movements/maintenance).
Authz-relevant denials (unknown asset is 404; unavailable asset and
out-of-scope facility are audited as SESSION_*_DENIED); client state
conflicts (already open / none open) return clear 409s without audit rows.
A QR code only identifies the asset: all permission checks run here on the
backend regardless of which UI initiated the call.
"""

from __future__ import annotations

import logging

from flask import Blueprint, g, request
from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor
from pydantic import ValidationError

from app.auth.jwt import get_current_user, require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, success
from app.middleware.rate_limit import rate_limit
from app.schemas.v1_schemas import UsageSessionNote
from app.services import location_sql
from app.services.audit_service import audit

sessions_v1_bp = Blueprint("sessions_v1", __name__)

logger = logging.getLogger(__name__)


def _resolve_asset(cur, ref: str):
    """Triple-ref lookup (asset_uuid / ac_id / asset_code), like assets._find_asset."""
    cur.execute(
        "SELECT ac_id, asset_uuid, lifecycle_status, is_active FROM acs"
        " WHERE asset_uuid::text = %s OR ac_id = %s OR asset_code = %s LIMIT 1",
        (ref, ref, ref),
    )
    return cur.fetchone()


def _asset_block_id(cur, ac_id: str):
    """Canonical block of an asset via confirmed location, else room chain."""
    cur.execute(
        "SELECT COALESCE(al.block_id, fb.block_id) AS block_id"
        " FROM acs a"
        " LEFT JOIN asset_locations al ON al.ac_id = a.ac_id"
        " LEFT JOIN rooms r ON r.room_id = COALESCE(al.room_id, a.room_id)"
        " LEFT JOIN floors f ON f.floor_id = COALESCE(al.floor_id, r.floor_id)"
        " LEFT JOIN blocks fb ON fb.block_id = COALESCE(al.block_id, f.block_id)"
        " WHERE a.ac_id = %s",
        (ac_id,),
    )
    row = cur.fetchone()
    return row["block_id"] if row else None


def _check_manager_scope(cur, user: dict, asset: dict):
    """FACILITY_MANAGER with assigned_blocks set is restricted to those blocks.

    Returns an error response when out of scope, else None. Assets with no
    located block are allowed through: there is no facility to scope them to
    (documented; the session still records who/where for review).
    """
    if user.get("role") != "FACILITY_MANAGER":
        return None
    assigned = (user.get("profile") or {}).get("assigned_blocks") or []
    if not assigned:
        return None
    block_id = _asset_block_id(cur, asset["ac_id"])
    if block_id is not None and block_id not in set(assigned):
        audit(
            "SESSION_START_DENIED",
            "asset",
            asset["ac_id"],
            None,
            {"reason": "facility_out_of_scope", "block_id": block_id},
        )
        return error(
            "ASSET_OUT_OF_SCOPE",
            "This asset is outside your assigned facilities.",
            403,
        )
    return None


def _serialize(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "ac_id": row["ac_id"],
        "started_by": str(row["started_by"]) if row.get("started_by") else None,
        "started_at": row["started_at"].isoformat() if row.get("started_at") else None,
        "ended_at": row["ended_at"].isoformat() if row.get("ended_at") else None,
        "duration_seconds": (
            int(row["duration_seconds"]) if row.get("duration_seconds") is not None else None
        ),
        "start_note": row.get("start_note"),
        "end_note": row.get("end_note"),
    }


def _is_retired(asset: dict) -> bool:
    # Mirrors the list-endpoint convention COALESCE(is_active, TRUE): only an
    # explicit FALSE (or a terminal lifecycle) counts as unavailable.
    if asset.get("is_active") is False:
        return True
    return str(asset.get("lifecycle_status") or "").upper() in ("DECOMMISSIONED", "RETIRED")


@sessions_v1_bp.route("/api/v1/assets/<ref>/sessions/start", methods=["POST"])
@require_auth
@require_permission("RECORD_USAGE_SESSION")
@rate_limit(60, 60, "sess-start")
def start_session(ref):
    user = get_current_user()
    try:
        payload = UsageSessionNote.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid request payload.", 400, exc.errors())

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        asset = _resolve_asset(cur, ref)
        if asset is None:
            return error("ASSET_NOT_FOUND", "No asset matches this reference.", 404)
        if _is_retired(asset):
            audit(
                "SESSION_START_DENIED",
                "asset",
                asset["ac_id"],
                None,
                {"reason": "asset_unavailable"},
            )
            return error(
                "ASSET_UNAVAILABLE",
                "Sessions cannot be started on a decommissioned or inactive asset.",
                409,
            )
        scoped = _check_manager_scope(cur, user, asset)
        if scoped is not None:
            return scoped
        try:
            cur.execute(
                "INSERT INTO ac_usage_sessions (ac_id, started_by, start_note)"
                " VALUES (%s, %s, %s)"
                " RETURNING id, ac_id, started_by, started_at, ended_at,"
                " NULL::int AS duration_seconds, start_note, end_note",
                (asset["ac_id"], user.get("id"), payload.note),
            )
            row = cur.fetchone()
        except pg_errors.UniqueViolation:
            conn.rollback()
            cur.execute(
                "SELECT id FROM ac_usage_sessions WHERE ac_id = %s AND ended_at IS NULL LIMIT 1",
                (asset["ac_id"],),
            )
            open_row = cur.fetchone()
            return error(
                "SESSION_ALREADY_OPEN",
                "This asset already has an open usage session.",
                409,
                {"session_id": str(open_row["id"]) if open_row else None},
            )
        conn.commit()
    except Exception:  # noqa: BLE001
        logger.exception("session start failed request_id=%s", getattr(g, "request_id", "-"))
        return error("SESSION_START_FAILED", "Unable to start the usage session.", 500)
    finally:
        if conn is not None:
            conn.close()

    audit(
        "SESSION_STARTED",
        "ac_usage_session",
        str(row["id"]),
        None,
        {"ac_id": asset["ac_id"], "started_by": str(user.get("id"))},
    )
    return success(_serialize(row), 201)


@sessions_v1_bp.route("/api/v1/assets/<ref>/sessions/stop", methods=["POST"])
@require_auth
@require_permission("RECORD_USAGE_SESSION")
@rate_limit(60, 60, "sess-stop")
def stop_session(ref):
    user = get_current_user()
    try:
        payload = UsageSessionNote.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid request payload.", 400, exc.errors())

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        asset = _resolve_asset(cur, ref)
        if asset is None:
            return error("ASSET_NOT_FOUND", "No asset matches this reference.", 404)
        scoped = _check_manager_scope(cur, user, asset)
        if scoped is not None:
            return scoped
        # Lock the open row: a concurrent stop waits here, then sees the
        # closed state below instead of closing twice.
        cur.execute(
            "SELECT id, started_at FROM ac_usage_sessions"
            " WHERE ac_id = %s AND ended_at IS NULL LIMIT 1 FOR UPDATE",
            (asset["ac_id"],),
        )
        open_row = cur.fetchone()
        if open_row is None:
            return error(
                "NO_OPEN_SESSION",
                "This asset has no open usage session to stop.",
                409,
            )
        cur.execute(
            "UPDATE ac_usage_sessions SET ended_at = NOW(), end_note = %s, updated_at = NOW()"
            " WHERE id = %s"
            " RETURNING id, ac_id, started_by, started_at, ended_at,"
            " EXTRACT(EPOCH FROM (ended_at - started_at))::int AS duration_seconds,"
            " start_note, end_note",
            (payload.note, open_row["id"]),
        )
        row = cur.fetchone()
        conn.commit()
    except Exception:  # noqa: BLE001
        logger.exception("session stop failed request_id=%s", getattr(g, "request_id", "-"))
        return error("SESSION_STOP_FAILED", "Unable to stop the usage session.", 500)
    finally:
        if conn is not None:
            conn.close()

    audit(
        "SESSION_STOPPED",
        "ac_usage_session",
        str(row["id"]),
        {"ended_at": None},
        {"ended_at": row["ended_at"].isoformat(), "duration_seconds": int(row["duration_seconds"])},
    )
    return success(_serialize(row))


@sessions_v1_bp.route("/api/v1/assets/<ref>/sessions", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def list_sessions(ref):
    try:
        limit = min(100, max(1, int(request.args.get("limit", 20))))
    except ValueError:
        return error("INVALID_PAGINATION", "limit must be an integer.", 400)
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            asset = _resolve_asset(cur, ref)
            if asset is None:
                cur.close()
                return error("ASSET_NOT_FOUND", "No asset matches this reference.", 404)
            cur.execute(
                "SELECT id, ac_id, started_by, started_at, ended_at,"
                " EXTRACT(EPOCH FROM (COALESCE(ended_at, NOW()) - started_at))::int"
                " AS duration_seconds, start_note, end_note"
                " FROM ac_usage_sessions WHERE ac_id = %s"
                " ORDER BY started_at DESC LIMIT %s",
                (asset["ac_id"], limit),
            )
            rows = [_serialize(dict(r)) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("sessions list failed request_id=%s", getattr(g, "request_id", "-"))
        return error("SESSIONS_LIST_FAILED", "Unable to list usage sessions.", 500)
    return success(rows)


# ── GET /api/v1/sessions (fleet view for the Energy dashboard) ───────────────


def asset_role_scope(user: dict) -> tuple[list[str], list, str]:
    """Shared role-scope fragment for session-backed reads.

    Returns ``(extra_where, extra_params, scope_label)`` using the exact
    expressions from ``dashboard_summary`` (technician mine-blocks,
    manager assigned-blocks). Extracted so the energy endpoints apply the
    identical slice — keep the SQL in sync if the dashboard changes.
    """
    role = (user.get("role") or "").upper()
    assigned = list((user.get("profile") or {}).get("assigned_blocks") or [])
    technician_blocks_sql = (
        "b.block_id IN ("
        "SELECT b2.block_id FROM maintenance_work_orders w2"
        " JOIN acs a2 ON a2.ac_id = w2.ac_id"
        " LEFT JOIN asset_locations al2 ON al2.ac_id = a2.ac_id"
        " LEFT JOIN rooms r2 ON r2.room_id = COALESCE(al2.room_id, a2.room_id)"
        " LEFT JOIN floors f2 ON f2.floor_id = COALESCE(al2.floor_id, r2.floor_id)"
        " LEFT JOIN blocks b2 ON b2.block_id = COALESCE(al2.block_id, f2.block_id)"
        " WHERE w2.technician_id = %s AND w2.status <> 'CANCELLED'"
        " AND b2.block_id IS NOT NULL)"
    )
    if role == "TECHNICIAN":
        return [technician_blocks_sql], [user.get("id")], "mine"
    if role == "FACILITY_MANAGER" and assigned:
        return ["b.block_id = ANY(%s)"], [assigned], "blocks"
    return [], [], "all"


@sessions_v1_bp.route("/api/v1/sessions", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def list_all_sessions():
    """Fleet-wide usage sessions for energy monitoring (Energy dashboard).

    Accepts the same asset filters as GET /assets (block, department,
    manufacturer, …) plus ``state`` (open|closed|all), ``from``/``to``
    (YYYY-MM-DD bounds on started_at) and ``limit`` (default 200, max 1000).
    Role scoping mirrors dashboard_summary: technicians see blocks holding
    their non-cancelled work orders ("mine"), managers with assigned_blocks
    see those blocks, everyone else sees the filtered slice. Read-only: no
    audit row is written for listing (app-wide convention).
    """
    from datetime import date

    from app.api.v1.assets import InvalidFilter, _build_filters

    user = get_current_user()

    state = (request.args.get("state") or "all").strip().lower()
    if state not in ("open", "closed", "all"):
        return error("INVALID_STATE", "state must be open, closed or all.", 400)
    start_from = (request.args.get("from") or "").strip() or None
    start_to = (request.args.get("to") or "").strip() or None
    try:
        parsed_from = date.fromisoformat(start_from) if start_from else None
        parsed_to = date.fromisoformat(start_to) if start_to else None
    except ValueError:
        return error("INVALID_DATE", "from/to must be YYYY-MM-DD dates.", 400)
    if parsed_from and parsed_to and parsed_from > parsed_to:
        return error("INVALID_RANGE", "from must not be after to.", 400)
    try:
        limit = min(1000, max(1, int(request.args.get("limit", 200))))
    except ValueError:
        return error("INVALID_PAGINATION", "limit must be an integer.", 400)

    try:
        where, params = _build_filters(request.args)
    except InvalidFilter as exc:
        return error("INVALID_FILTER", str(exc), 400)

    scope_where, scope_params, scope_label = asset_role_scope(user)
    where.extend(scope_where)
    params.extend(scope_params)
    if state == "open":
        where.append("s.ended_at IS NULL")
    elif state == "closed":
        where.append("s.ended_at IS NOT NULL")
    if parsed_from:
        where.append("s.started_at >= %s")
        params.append(parsed_from.isoformat())
    if parsed_to:
        where.append("s.started_at < (%s::date + INTERVAL '1 day')")
        params.append(parsed_to.isoformat())
    where_sql = " AND ".join(where)

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT COUNT(*) AS total,"
                " COUNT(*) FILTER (WHERE s.ended_at IS NULL) AS open_count"
                " FROM ac_usage_sessions s"
                f" JOIN acs a ON a.ac_id = s.ac_id {location_sql.LOCATION_JOIN}"
                f" {location_sql.EFFECTIVE_JOINS} WHERE {where_sql}",
                tuple(params),
            )
            count_row = cur.fetchone()
            total = count_row["total"]
            open_count = count_row["open_count"]
            cur.execute(
                "SELECT s.id, s.ac_id, s.started_by, s.started_at, s.ended_at,"
                " EXTRACT(EPOCH FROM (COALESCE(s.ended_at, NOW()) - s.started_at))::int"
                " AS duration_seconds, s.start_note, s.end_note,"
                " a.asset_code, a.ac_make_company AS manufacturer, a.ac_model_number AS model,"
                " a.ac_capacity_ton AS capacity_ton, a.ac_type,"
                " a.power_rating_kw AS power_rating_kw,"
                f" {location_sql.label('block')} AS block,"
                f" {location_sql.label('floor')} AS floor,"
                f" {location_sql.label('room')} AS room,"
                f" {location_sql.label('department')} AS department"
                " FROM ac_usage_sessions s"
                f" JOIN acs a ON a.ac_id = s.ac_id {location_sql.LOCATION_JOIN}"
                f" {location_sql.EFFECTIVE_JOINS} WHERE {where_sql}"
                " ORDER BY s.started_at DESC LIMIT %s",
                tuple(params) + (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            for row in rows:
                row["id"] = str(row["id"])
                if row.get("started_by") is not None:
                    row["started_by"] = str(row["started_by"])
                if row.get("capacity_ton") is not None:
                    row["capacity_ton"] = float(row["capacity_ton"])
                if row.get("power_rating_kw") is not None:
                    row["power_rating_kw"] = float(row["power_rating_kw"])
            cur.close()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("fleet sessions list failed request_id=%s", getattr(g, "request_id", "-"))
        return error("SESSIONS_LIST_FAILED", "Unable to list usage sessions.", 500)
    return success({"sessions": rows, "total": total, "open_count": open_count, "scope": scope_label})
