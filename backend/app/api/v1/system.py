"""Health/readiness, aggregated dashboard, audit read (Plan #35, #44).

Phase 1.2: GET endpoints are authenticated — only /health, /ready and
/api/v1/public/assets/<ref> are anonymous (see app.auth.legacy_guard).

Phase 7: dashboard summary is role-scoped (viewer counts-only, technician
my-jobs/my-blocks, manager block-scoped via profile.assigned_blocks, admin
all) and /audit gained filters, actor join, cursor pagination and PII
redaction of old_values/new_values.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import logging
import uuid as uuid_mod
from datetime import date, datetime

from flask import Blueprint, Response, request
from psycopg2.extras import RealDictCursor

from app.auth.jwt import get_current_user, require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, paginated, success
from app.services import location_sql
from app.services.audit_service import redact_values

logger = logging.getLogger(__name__)

system_v1_bp = Blueprint("system_v1", __name__)

# CSV export cap for GET /api/v1/audit?format=csv (openapi: AUDIT_CSV_LIMIT).
AUDIT_CSV_LIMIT = 5000


@system_v1_bp.route("/health", methods=["GET"])
def health():
    return success({"status": "ok", "service": "ACMS Backend"})


@system_v1_bp.route("/ready", methods=["GET"])
def ready():
    checks = {}
    ok = True
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT 1")
            cur.fetchone()
            checks["database"] = "ok"
            # Migration head check: the latest recorded migration should match
            # the latest numbered file on disk.
            cur.execute("SELECT to_regclass('public.schema_migrations') AS present")
            if cur.fetchone()["present"] is None:
                checks["migrations"] = "not tracked (run python -m app.migrate up)"
            else:
                cur.execute("SELECT MAX(version) AS head FROM schema_migrations")
                applied_head = cur.fetchone()["head"]
                from app.migrate import _migration_files

                files = _migration_files()
                expected_head = files[-1].name if files else None
                if applied_head == expected_head:
                    checks["migrations"] = "ok"
                else:
                    checks["migrations"] = f"pending (applied={applied_head}, latest={expected_head})"
                    ok = False
            cur.close()
        finally:
            conn.close()
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
        ok = False
    return success({"ready": ok, "checks": checks}, 200 if ok else 503)


_OPENAPI_CACHE: str | None = None


@system_v1_bp.route("/api/v1/openapi.json", methods=["GET"])
def openapi_json():
    """Serve the hand-maintained OpenAPI contract (Phase 1.0). The contract
    file is validated against url_map in tests/test_openapi.py."""
    global _OPENAPI_CACHE
    from pathlib import Path

    import yaml

    spec_path = Path(__file__).resolve().parents[3] / "openapi.yaml"
    try:
        if _OPENAPI_CACHE is None:
            _OPENAPI_CACHE = json.dumps(yaml.safe_load(spec_path.read_text(encoding="utf-8")), default=str)
        from flask import current_app

        return current_app.response_class(_OPENAPI_CACHE, mimetype="application/json")
    except FileNotFoundError:
        logger_msg = "openapi.yaml not found next to backend root"
        return error("OPENAPI_MISSING", logger_msg, 500)


@system_v1_bp.route("/api/v1/dashboard/summary", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def dashboard_summary():
    """DB-side aggregation (Plan #35) — no full-dataset browser computation.

    Accepts the same filter parameters as ``GET /api/v1/assets`` (search,
    status, block, department, asset_type, manufacturer, floor, room,
    lifecycle, location_status, due_before) so the stat cards, charts and
    the asset table always describe the same slice of data.

    Phase 7 role scoping (single response shape for every role):

    * SUPER_ADMIN and unscoped FACILITY_MANAGER — ``scope: "all"``.
    * FACILITY_MANAGER with ``profile.assigned_blocks`` — every count and
      grouping restricted to those blocks (same semantics as movements).
    * TECHNICIAN — ``scope: "mine"``: counts restricted to blocks where they
      hold work orders, plus a ``my_jobs`` section (their own job counts) and
      an MTTR over their completed jobs.
    * VIEWER — ``scope: "counts"``: aggregates only; the ``overdue`` detail
      list is empty and ``my_jobs`` is null (no row-level detail).

    Additions per phase-7 plan: ``kpis.due_7d``, ``kpis.mttr_hours`` (mean
    time to repair, hours, from completed work orders) and
    ``capacity_by_block``. MTTR is computed within the caller's scope when a
    scope applies.
    """
    # Local import: assets.py is a sibling module of the v1 package.
    from app.api.v1.assets import JOINS, _build_filters

    user = get_current_user() or {}
    role = (user.get("role") or "VIEWER").upper()
    assigned = list((user.get("profile") or {}).get("assigned_blocks") or [])
    technician = role == "TECHNICIAN"
    include_detail = role != "VIEWER"  # viewers: counts only, no row detail
    # Any caller may downgrade further (?include_detail=0) to the counts-only
    # payload; a viewer can never upgrade past their role.
    if (request.args.get("include_detail") or "").strip().lower() in {"0", "false", "no"}:
        include_detail = False

    where, params = _build_filters(request.args)

    # Role scope — appended AFTER the request filters so both share one
    # where_sql across KPIs, groupings, overdue rows and MTTR.
    # Scoping binds canonical ids only (§7): the block comes from the
    # effective chain (asset_locations first), so a roomless asset with a
    # confirmed block still counts for the technician/manager who owns it.
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
    if technician:
        where.append(technician_blocks_sql)
        params.append(user.get("id"))
        scope_label = "mine"
    elif role == "FACILITY_MANAGER" and assigned:
        where.append("b.block_id = ANY(%s)")
        params.append(assigned)
        scope_label = "blocks"
    else:
        scope_label = "counts" if not include_detail else "all"
    where_sql = " AND ".join(where)

    # Nine key fields, mirroring the frontend's data-completeness card. Each
    # term is a SUM(...) aggregate so the expression is valid in this
    # grouped query (raw column refs would need GROUP BY).
    completeness = (
        "(SUM(CASE WHEN COALESCE(a.ac_make_company, '') <> '' THEN 1 ELSE 0 END))"
        " + (SUM(CASE WHEN COALESCE(a.ac_model_number, '') <> '' THEN 1 ELSE 0 END))"
        " + (SUM(CASE WHEN a.ac_capacity_ton IS NOT NULL THEN 1 ELSE 0 END))"
        " + (SUM(CASE WHEN COALESCE(a.ac_serial_number, '') <> '' THEN 1 ELSE 0 END))"
        " + (SUM(CASE WHEN COALESCE(a.ac_type, '') <> '' THEN 1 ELSE 0 END))"
        " + (SUM(CASE WHEN a.installation_year IS NOT NULL THEN 1 ELSE 0 END))"
        " + (SUM(CASE WHEN a.ac_star_rating IS NOT NULL THEN 1 ELSE 0 END))"
        " + (SUM(CASE WHEN b.block_id IS NOT NULL THEN 1 ELSE 0 END))"
        " + (SUM(CASE WHEN d.department_id IS NOT NULL THEN 1 ELSE 0 END))"
    )

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                f"""
                SELECT COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE a.operational_status = 'WORKING') AS working,
                       COUNT(*) FILTER (WHERE a.operational_status = 'MAINTENANCE') AS under_maint,
                       COUNT(*) FILTER (WHERE a.operational_status = 'FAULT') AS faulty,
                       COUNT(*) FILTER (WHERE a.operational_status = 'OFFLINE') AS offline,
                       COUNT(*) FILTER (WHERE a.operational_status = 'UNKNOWN') AS unknown,
                       COALESCE(SUM(a.ac_capacity_ton), 0)::float AS capacity,
                       COUNT(DISTINCT d.department_id) AS departments,
                       COUNT(DISTINCT r.room_id) AS rooms,
                       COUNT(*) FILTER (WHERE a.next_service_date >= CURRENT_DATE) AS due_soon,
                       COUNT(*) FILTER (WHERE a.next_service_date BETWEEN CURRENT_DATE AND CURRENT_DATE + 7) AS due_7d,
                       COUNT(*) FILTER (WHERE a.next_service_date < CURRENT_DATE) AS overdue,
                       COALESCE(ROUND(100.0 * ({completeness}) / NULLIF(9.0 * COUNT(*), 0)), 0)::int
                           AS completeness_pct
                {JOINS}
                WHERE {where_sql}
                """,
                params,
            )
            kpis = dict(cur.fetchone())
            # MTTR (hours) from completed work orders, restricted to the
            # caller's scope when a scope applies (technician: own jobs).
            kpis["mttr_hours"] = 0.0
            try:
                mttr_where = [
                    "w.status = 'COMPLETED'",
                    "w.started_at IS NOT NULL",
                    "w.completed_at IS NOT NULL",
                ]
                mttr_params: list = []
                if technician:
                    mttr_where.append("w.technician_id = %s")
                    mttr_params.append(user.get("id"))
                elif role == "FACILITY_MANAGER" and assigned:
                    mttr_where.append(
                        "EXISTS (SELECT 1 FROM maintenance_work_orders w2"
                        " JOIN acs a2 ON a2.ac_id = w2.ac_id"
                        " LEFT JOIN asset_locations al2 ON al2.ac_id = a2.ac_id"
                        " LEFT JOIN rooms r2 ON r2.room_id = COALESCE(al2.room_id, a2.room_id)"
                        " LEFT JOIN floors f2 ON f2.floor_id = COALESCE(al2.floor_id, r2.floor_id)"
                        " LEFT JOIN blocks b2 ON b2.block_id = COALESCE(al2.block_id, f2.block_id)"
                        " WHERE w2.id = w.id AND b2.block_id = ANY(%s))"
                    )
                    mttr_params.append(assigned)
                cur.execute(
                    "SELECT COALESCE(AVG(EXTRACT(EPOCH FROM (w.completed_at - w.started_at)) / 3600.0), 0)::float AS mttr"
                    " FROM maintenance_work_orders w WHERE " + " AND ".join(mttr_where),
                    mttr_params,
                )
                kpis["mttr_hours"] = float(cur.fetchone()["mttr"] or 0.0)
            except Exception:
                logger.debug("mttr unavailable (work-order table not migrated?)", exc_info=True)
            # The six dimension groupings share one scan of the same slice:
            # a single UNION ALL round trip instead of six sequential ones
            # (each leg keeps its original GROUP BY/ORDER BY/LIMIT — only
            # the transport changes; values are re-keyed back below).
            # Effective block label (§7): canonical name/id, then the
            # preserved source text, then the historical 'Unassigned'.
            # Block legs also carry the canonical block id (bid) and group
            # by (label, id): two distinct canonical blocks that share a
            # display name are never silently merged; source-only rows keep
            # bid NULL under their imported text.
            block_k_sql = location_sql.label_or("block", "'Unassigned'")
            block_id_sql = "b.block_id"
            dim_legs = [
                # (section, k, v, n, bid) — v carries the numeric capacity legs.
                # Ordinals: section=1, k=2, v=3, n=4, bid=5.
                f"(SELECT 1 AS section, a.operational_status AS k,"
                f" NULL::float8 AS v, COUNT(*) AS n, NULL::text AS bid {JOINS} WHERE {where_sql}"
                " GROUP BY 2 ORDER BY n DESC LIMIT 20)",
                f"(SELECT 2 AS section, COALESCE(a.ac_make_company, 'Unknown') AS k,"
                f" NULL::float8 AS v, COUNT(*) AS n, NULL::text AS bid {JOINS} WHERE {where_sql}"
                " GROUP BY 2 ORDER BY n DESC LIMIT 20)",
                f"(SELECT 3 AS section, COALESCE(a.ac_type, 'Other') AS k,"
                f" NULL::float8 AS v, COUNT(*) AS n, NULL::text AS bid {JOINS} WHERE {where_sql}"
                " GROUP BY 2 ORDER BY n DESC LIMIT 20)",
                f"(SELECT 4 AS section, {block_k_sql} AS k,"
                f" NULL::float8 AS v, COUNT(*) AS n, {block_id_sql} AS bid {JOINS} WHERE {where_sql}"
                " GROUP BY 2, 5 ORDER BY n DESC LIMIT 20)",
                f"(SELECT 5 AS section, NULL::text AS k, a.ac_capacity_ton::float AS v,"
                f" COUNT(*) AS n, NULL::text AS bid {JOINS} WHERE {where_sql}"
                " GROUP BY 3 ORDER BY v NULLS LAST LIMIT 20)",
                f"(SELECT 6 AS section, {block_k_sql} AS k,"
                f" COALESCE(SUM(a.ac_capacity_ton), 0)::float AS v, COUNT(*) AS n,"
                f" {block_id_sql} AS bid"
                f" {JOINS} WHERE {where_sql}"
                " GROUP BY 2, 5 ORDER BY v DESC LIMIT 20)",
            ]
            cur.execute(" UNION ALL ".join(dim_legs), tuple(params) * 6)
            sections: dict[int, list[dict]] = {i: [] for i in range(1, 7)}
            for row in cur.fetchall():
                sections[row["section"]].append(dict(row))
            # Re-key + re-sort exactly as the six standalone queries did
            # (branch ORDER BY already emitted this order; the explicit
            # sorts keep the response deterministic even if the executor
            # interleaves UNION branches). Ties keep arrival order.
            by_status = [
                {"status": r["k"], "count": r["n"]} for r in sorted(sections[1], key=lambda r: -r["n"])
            ]
            by_mfr = [
                {"manufacturer": r["k"], "count": r["n"]} for r in sorted(sections[2], key=lambda r: -r["n"])
            ]
            by_type = [{"type": r["k"], "count": r["n"]} for r in sorted(sections[3], key=lambda r: -r["n"])]
            by_block = [
                {"block": r["k"], "block_id": r["bid"], "count": r["n"]}
                for r in sorted(sections[4], key=lambda r: -r["n"])
            ]
            by_capacity = [
                {"capacity_ton": r["v"], "count": r["n"]}
                for r in sorted(sections[5], key=lambda r: (r["v"] is None, r["v"] or 0))
            ]
            capacity_by_block = [
                {"block": r["k"], "block_id": r["bid"], "capacity_ton": r["v"], "count": r["n"]}
                for r in sorted(sections[6], key=lambda r: -r["v"])
            ]
            overdue_rows = []
            if include_detail:
                try:
                    cur.execute(
                        f"SELECT a.ac_id, a.next_service_date {JOINS} WHERE {where_sql}"
                        f" AND a.next_service_date < CURRENT_DATE"
                        f" ORDER BY a.next_service_date LIMIT 50",
                        params,
                    )
                    overdue_rows = [dict(r) for r in cur.fetchall()]
                    for o in overdue_rows:
                        for k, v in list(o.items()):
                            if hasattr(v, "isoformat"):
                                o[k] = v.isoformat()
                except Exception:
                    overdue_rows = []
            # Technician focus: their own job counts (open = not terminal).
            my_jobs = None
            if technician and include_detail:
                try:
                    # `status` can never be 'ESCALATED' (CHECK in migration
                    # 002): escalate() keeps the status and records
                    # `escalated_at` (008), so count that column — the old
                    # status filter reported 0 escalations forever.
                    cur.execute(
                        "SELECT COUNT(*) FILTER (WHERE status IN"
                        "  ('SCHEDULED','ASSIGNED','IN_PROGRESS')) AS open,"
                        " COUNT(*) FILTER (WHERE status = 'IN_PROGRESS') AS in_progress,"
                        " COUNT(*) FILTER (WHERE escalated_at IS NOT NULL) AS escalated,"
                        " COUNT(*) FILTER (WHERE status NOT IN ('COMPLETED','CANCELLED')"
                        "  AND scheduled_date < CURRENT_DATE) AS overdue,"
                        " COUNT(*) FILTER (WHERE status = 'COMPLETED') AS completed"
                        " FROM maintenance_work_orders WHERE technician_id = %s",
                        (user.get("id"),),
                    )
                    my_jobs = dict(cur.fetchone())
                except Exception:
                    logger.debug("my_jobs unavailable", exc_info=True)
                    my_jobs = None
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("dashboard summary failed")
        return error("DASHBOARD_FAILED", "Unable to build dashboard summary.", 500)
    return success(
        {
            "kpis": kpis,
            "scope": scope_label,
            "by_status": by_status,
            "by_manufacturer": by_mfr,
            "by_type": by_type,
            "by_block": by_block,
            "by_capacity": by_capacity,
            "capacity_by_block": capacity_by_block,
            "overdue": overdue_rows,
            "my_jobs": my_jobs,
        }
    )


@system_v1_bp.route("/api/v1/audit", methods=["GET"])
@require_auth
@require_permission("VIEW_AUDIT")
def list_audit():
    """Audit trail read (Phase 7 task 3).

    Filters: ``entity_type`` (exact), ``user`` (actor profile id), ``action``
    (case-insensitive contains), ``q`` (action / entity id / actor name),
    ``from`` / ``to`` (created_at, YYYY-MM-DD inclusive). Actor name and role
    are joined from ``profiles``.

    Pagination: cursor keyset by default — pass the previous response's
    ``pagination.next_cursor`` back as ``cursor`` for stable pages while new
    rows arrive. Classic ``page``/``page_size`` offset paging still works
    when ``cursor`` is absent.

    ``old_values``/``new_values`` are redacted at read time as defence in
    depth (the writer already redacts); rows are append-only and retained
    indefinitely (>= 1 year — see migrations/011_audit_readonly.sql).

    ``?format=csv`` exports the same filtered rows as a UTF-8 CSV download
    (additionally requires ``VIEW_REPORTS`` — checked in-code, same roles as
    ``VIEW_AUDIT``; capped at ``AUDIT_CSV_LIMIT`` rows).
    """
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(200, max(1, int(request.args.get("page_size", 50))))
    except ValueError:
        return error("INVALID_PAGINATION", "page and page_size must be integers.", 400)

    fmt = (request.args.get("format") or "").strip().lower()
    if fmt not in ("", "json", "csv"):
        return error("UNSUPPORTED_FORMAT", "Only format=csv or format=json is supported.", 400)
    if fmt == "csv":
        from app.auth.permissions import has_permission

        if not has_permission((get_current_user() or {}).get("role"), "VIEW_REPORTS"):
            return error("FORBIDDEN", "CSV export requires VIEW_REPORTS.", 403)

    where: list[str] = []
    params: list = []

    entity_type = (request.args.get("entity_type") or "").strip()
    if entity_type:
        where.append("LOWER(l.entity_type) = LOWER(%s)")
        params.append(entity_type)
    actor = (request.args.get("user") or request.args.get("actor") or "").strip()
    if actor:
        try:
            uuid_mod.UUID(actor)
        except ValueError:
            return error("INVALID_FILTER", "user must be a UUID.", 400)
        where.append("l.user_id = %s")
        params.append(actor)
    action = (request.args.get("action") or "").strip()
    if action:
        where.append("l.action ILIKE %s")
        params.append(f"%{action}%")
    query = (request.args.get("q") or "").strip()
    if query:
        where.append("(l.action ILIKE %s OR l.entity_id ILIKE %s" " OR COALESCE(p.name, '') ILIKE %s)")
        params += [f"%{query}%"] * 3
    for label, arg in (("from", "from"), ("to", "to")):
        raw = (request.args.get(arg) or "").strip()
        if raw:
            try:
                date.fromisoformat(raw)
            except ValueError:
                return error("INVALID_DATE", f"{label} must be YYYY-MM-DD.", 400)
            if arg == "from":
                where.append("l.created_at >= %s::date")
            else:
                where.append("l.created_at < %s::date + INTERVAL '1 day'")
            params.append(raw)

    # Cursor: opaque keyset token over (created_at DESC, id DESC). Bound
    # only on the page fetch so the COUNT (stable total across pages) and
    # the fetch can carry different parameters.
    cursor = (request.args.get("cursor") or "").strip()
    cursor_params: list = []
    if cursor:
        try:
            ts_raw, id_raw = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            cursor_ts = datetime.fromisoformat(ts_raw)
            cursor_id = uuid_mod.UUID(id_raw)
        except (ValueError, TypeError, UnicodeDecodeError):
            return error("INVALID_CURSOR", "cursor is not a valid pagination token.", 400)
        cursor_params = [cursor_ts.isoformat(), str(cursor_id)]

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    join = " FROM audit_logs l LEFT JOIN profiles p ON p.id = l.user_id "
    select = (
        "SELECT l.id, l.user_id, l.action, l.entity_type, l.entity_id,"
        " l.old_values, l.new_values, l.ip_address, l.user_agent, l.created_at,"
        " p.name AS actor_name, p.role AS actor_role"
    )
    order = " ORDER BY l.created_at DESC, l.id DESC "

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT to_regclass('public.audit_logs') AS present")
            if cur.fetchone()["present"] is None:
                cur.close()
                return paginated([], page, page_size, 0, next_cursor=None)
            cur.execute("SELECT COUNT(*) AS total" + join + where_sql, tuple(params))
            total = cur.fetchone()["total"]
            page_conditions = list(where)
            if cursor:
                page_conditions.append("(l.created_at, l.id) < (%s::timestamptz, %s::uuid)")
            page_sql = ("WHERE " + " AND ".join(page_conditions)) if page_conditions else ""

            export_rows: list[dict] = []
            if fmt == "csv":
                # Export path: same filters, no keyset/page — one capped
                # download (cursor params are meaningless here).
                cur.execute(
                    select + join + where_sql + order + "LIMIT %s",
                    tuple(params) + (AUDIT_CSV_LIMIT,),
                )
                export_rows = [dict(r) for r in cur.fetchall()]
            else:
                # Page fetch: filters + cursor keyset (fetch one extra row so
                # we know whether another page exists). With no cursor this is
                # classic offset paging, so ``page`` must reach the SQL —
                # echoing it in the envelope while always returning page 1
                # silently lied to every offset client (Phase 8 finding).
                limit_sql = "LIMIT %s"
                tail: tuple = (page_size + 1,)
                if not cursor:
                    limit_sql += " OFFSET %s"
                    tail = (page_size + 1, (page - 1) * page_size)
                cur.execute(
                    select + join + page_sql + order + limit_sql,
                    tuple(params + cursor_params) + tail,
                )
                rows = [dict(r) for r in cur.fetchall()]
                has_more = len(rows) > page_size
                rows = rows[:page_size]
                next_cursor = None
                if has_more and rows:
                    last = rows[-1]
                    token = f"{last['created_at'].isoformat()}|{last['id']}"
                    next_cursor = base64.urlsafe_b64encode(token.encode()).decode()
                items = []
                for d in rows:
                    for k, v in list(d.items()):
                        if hasattr(v, "isoformat"):
                            d[k] = v.isoformat()
                        elif type(v).__name__ == "UUID":
                            d[k] = str(v)
                    d["id"] = str(d.get("id"))
                    # PII rule: password-like fields never leave the server.
                    d["old_values"] = redact_values(d.get("old_values"))
                    d["new_values"] = redact_values(d.get("new_values"))
                    items.append(d)
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("audit list failed")
        return error("AUDIT_LIST_FAILED", "Unable to list audit logs.", 500)
    if fmt == "csv":
        return _audit_csv_response(export_rows)
    return paginated(items, page, page_size, total, next_cursor=next_cursor)


_AUDIT_CSV_COLUMNS = (
    "created_at",
    "action",
    "entity_type",
    "entity_id",
    "actor_name",
    "actor_role",
    "user_id",
    "ip_address",
    "old_values",
    "new_values",
)


def _audit_csv_response(rows: list[dict]):
    """UTF-8 (BOM) CSV download of the filtered audit rows."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_AUDIT_CSV_COLUMNS)
    for d in rows:
        line = []
        for col in _AUDIT_CSV_COLUMNS:
            v = d.get(col)
            if hasattr(v, "isoformat"):
                v = v.isoformat()
            elif type(v).__name__ == "UUID":
                v = str(v)
            elif col in ("old_values", "new_values"):
                v = json.dumps(redact_values(v), ensure_ascii=False, default=str)
            line.append("" if v is None else v)
        writer.writerow(line)
    resp = Response(buf.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = 'attachment; filename="audit_log.csv"'
    return resp
