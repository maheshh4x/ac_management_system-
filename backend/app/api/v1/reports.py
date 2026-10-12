"""Reporting endpoints (contract line 66).

Five ``GET /api/v1/reports/*`` endpoints, all ``?from&to&block`` + ``?format=csv``
(Phase 7): ``assets-by-block``, ``assets-by-type``, ``capacity``,
``maintenance-trend``, ``due-for-maintenance``. All require ``VIEW_REPORTS``
for JSON and CSV alike (decision: no separate EXPORT_DATA gate — both
permissions are held by exactly the same roles).

Asset reports (``assets-by-block``/``assets-by-type``/``capacity``) are
asset-inventory snapshots over active assets: their ``?from&to`` filters
``installation_date`` (assets installed in that range);
``due-for-maintenance`` filters ``next_service_date`` (``from`` defaults to
today — pass an earlier date to include past-due units). ``?block`` always
scopes to one block (id/name substring, same semantics as ``GET /assets``).

``maintenance-trend``: ``monthlyTrend`` is a window of calendar months
(default: last 6 including the current one), zero-filled, bucketed by
``COALESCE(scheduled_date, completed_at::date)``; months with data outside
the window are appended after the window (ascending) — ``?from&to`` override
the window (≤ 24 months, else 400 INVALID_RANGE) and filter every query in
the report (trend rows, stats, priority). ``priorityDistribution`` is four
named buckets (zero-count included; the FE charts filter ``value > 0``).
``stats`` carries status counts + month-to-date / overdue / upcoming / MTTR
— the FE adapter maps ``scheduled+assigned → pending``,
``in_progress → inProgress``.

The ``build_*_query`` helpers are imported by ``scripts/seed_perf.py`` so the
EXPLAIN ANALYZE numbers recorded in ``docs/perf.md`` describe exactly the SQL
the API runs (no drift between measurement and shipping queries).
"""

from __future__ import annotations

import logging
from datetime import date

from flask import Blueprint, g, request
from psycopg2.extras import RealDictCursor

from app.auth.jwt import require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, success
from app.middleware.rate_limit import rate_limit
from app.services import location_sql

reports_v1_bp = Blueprint("reports_v1", __name__)

logger = logging.getLogger(__name__)

MONTH_NAMES = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
PRIORITY_ORDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
MAX_TREND_MONTHS = 24
DUE_JSON_LIMIT = 500
DUE_CSV_LIMIT = 5000

# Effective-location join chains (§7, shared with every other reader):
# asset_locations confirmed ids first, pre-014 room-mirror fallback after.
ASSET_JOINS = location_sql.ASSET_JOINS
WO_JOINS = location_sql.WO_JOINS

# Effective block label with the historical 'Unassigned' bucket.
_BLOCK_UNASSIGNED_SQL = location_sql.label_or("block", "'Unassigned'")


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS present", (f"public.{name}",))
    row = cur.fetchone()
    return (row["present"] if isinstance(row, dict) else row[0]) is not None


def _type_bucket(maintenance_type: str | None) -> str | None:
    """maintenance_type → chart series (loose contains-match, like the old
    client-side derivation). Types outside these four still count toward the
    month ``total`` but are not broken out."""
    t = (maintenance_type or "").strip().lower()
    if "prevent" in t:
        return "Preventive"
    if "repair" in t:
        return "Repair"
    if "emergenc" in t:
        return "Emergency"
    if "inspect" in t:
        return "Inspection"
    return None


def _blank_month(full_month: str) -> dict:
    return {
        "month": MONTH_NAMES[int(full_month[5:7]) - 1],
        "fullMonth": full_month,
        "total": 0,
        "Preventive": 0,
        "Repair": 0,
        "Emergency": 0,
        "Inspection": 0,
    }


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# ── shared /reports/* plumbing (Phase 7) ──────────────────────────────────────


def _report_args() -> tuple[dict | None, tuple | None]:
    """Parse the shared ``?from&to&block&format`` args of every /reports/*
    endpoint. Returns ``(args, None)`` on success or ``(None, error_response)``
    so the handler can return the second element directly.
    """
    # Local import (same pattern as system.py): the UI's "All" sentinel must
    # never become a block scope — `block_name ILIKE '%All%'` silently emptied
    # reports the same way it emptied the dashboard.
    from app.api.v1.assets import clean_filter

    fmt = (request.args.get("format") or "").strip().lower()
    if fmt not in ("", "json", "csv"):
        return None, error("UNSUPPORTED_FORMAT", "Only format=csv or format=json is supported.", 400)
    out: dict = {"from": "", "to": "", "block": "", "format": fmt or "json"}
    for key in ("from", "to"):
        raw = (request.args.get(key) or "").strip()
        if raw:
            try:
                date.fromisoformat(raw)
            except ValueError:
                return None, error("INVALID_DATE", f"{key} must be YYYY-MM-DD.", 400)
        out[key] = raw
    if out["from"] and out["to"] and out["from"] > out["to"]:
        return None, error("INVALID_DATE", "from must be on or before to.", 400)
    out["block"] = clean_filter(request.args.get("block"))
    return out, None


def _csv_response(rows: list[dict], columns: list[str], filename: str):
    """UTF-8 CSV download with BOM (Excel-safe), like ``GET /assets?format=csv``."""
    import csv as csv_mod
    import io as io_mod

    from flask import Response

    buffer = io_mod.StringIO()
    buffer.write("\ufeff")  # BOM so Excel opens the UTF-8 file correctly
    writer = csv_mod.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({c: ("" if row.get(c) is None else row.get(c)) for c in columns})
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _run_report_query(sql: str, params: list) -> tuple[list[dict] | None, tuple | None]:
    """Execute one report query; returns JSON-safe rows or an error response."""
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("report query failed request_id=%s", getattr(g, "request_id", "-"))
        return None, error("REPORT_FAILED", "Unable to build report.", 500)
    for row in rows:
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
            elif isinstance(v, float) or type(v).__name__ == "Decimal":
                row[k] = float(v)
            elif type(v).__name__ == "UUID":
                row[k] = str(v)
    return rows, None


def _asset_report_where(args: dict) -> tuple[list[str], list]:
    """Active-asset scope plus ``?block`` and ``?from/?to`` on installation_date."""
    where = ["COALESCE(a.is_active, TRUE) = TRUE"]
    params: list = []
    if args["block"]:
        clause, block_params = location_sql.filter_match("block", args["block"])
        where.append(clause)
        params += block_params
    if args["from"]:
        where.append("a.installation_date >= %s")
        params.append(args["from"])
    if args["to"]:
        where.append("a.installation_date <= %s")
        params.append(args["to"])
    return where, params


def _wo_scope(args: dict) -> tuple[list[str], list]:
    """Work-order filters shared by every maintenance-trend query: the
    ``?from``/``?to`` bucket-date range and the ``?block`` location scope."""
    conds: list[str] = []
    params: list = []
    if args["from"]:
        conds.append("COALESCE(w.scheduled_date, w.completed_at::date) >= %s")
        params.append(args["from"])
    if args["to"]:
        conds.append("COALESCE(w.scheduled_date, w.completed_at::date) <= %s")
        params.append(args["to"])
    if args["block"]:
        clause, block_params = location_sql.filter_match("block", args["block"])
        conds.append(clause)
        params += block_params
    return conds, params


def _shift_month(d: date, delta: int) -> date:
    idx = d.year * 12 + (d.month - 1) + delta
    return date(idx // 12, idx % 12 + 1, 1)


def _month_keys(start: date, end: date) -> list[str]:
    keys: list[str] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        keys.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            m, y = 1, y + 1
    return keys


def _trend_window(from_raw: str, to_raw: str) -> tuple[list[str] | None, tuple | None]:
    """Month keys (``YYYY-MM``, ascending) for the trend window.

    Default (no args): the current month and the five before it. ``?from``/
    ``?to`` override it (month of ``from`` .. month of ``to``); a single bound
    anchors the other side (``from`` → today, ``to`` → five months earlier).
    Returns ``(None, error_response)`` when the range exceeds
    ``MAX_TREND_MONTHS``.
    """
    today = date.today()
    if from_raw:
        start = date.fromisoformat(from_raw)
    elif to_raw:
        start = _shift_month(date.fromisoformat(to_raw), -5)
    else:
        start = _shift_month(today, -5)
    end = date.fromisoformat(to_raw) if to_raw else today
    if (end.year, end.month) < (start.year, start.month):
        end = start  # `from` in the future without `to`: one-month window
    span = (end.year - start.year) * 12 + (end.month - start.month) + 1
    if span > MAX_TREND_MONTHS:
        return None, error(
            "INVALID_RANGE",
            f"Date range spans more than {MAX_TREND_MONTHS} months.",
            400,
        )
    return _month_keys(start, end), None


# ── query builders (shared with scripts/seed_perf.py) ─────────────────────────


def build_assets_by_block_query(args: dict) -> tuple[str, list]:
    """SQL + params for ``/reports/assets-by-block``."""
    where, params = _asset_report_where(args)
    sql = (
        f"SELECT {_BLOCK_UNASSIGNED_SQL} AS block,"
        " COUNT(*) AS count, COALESCE(SUM(a.ac_capacity_ton), 0) AS capacity_ton"
        f" {ASSET_JOINS} WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY count DESC, block"
    )
    return sql, params


def build_assets_by_type_query(args: dict) -> tuple[str, list]:
    """SQL + params for ``/reports/assets-by-type``."""
    where, params = _asset_report_where(args)
    sql = (
        "SELECT COALESCE(NULLIF(a.ac_type, ''), 'Other') AS type, COUNT(*) AS count"
        f" {ASSET_JOINS} WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY count DESC, type"
    )
    return sql, params


def build_capacity_query(args: dict) -> tuple[str, list]:
    """SQL + params for ``/reports/capacity`` (units with a known capacity)."""
    where, params = _asset_report_where(args)
    where = [*where, "a.ac_capacity_ton IS NOT NULL"]
    sql = (
        "SELECT a.ac_capacity_ton::float AS capacity_ton, COUNT(*) AS count"
        f" {ASSET_JOINS} WHERE {' AND '.join(where)} GROUP BY 1 ORDER BY 1"
    )
    return sql, params


def build_due_for_maintenance_query(args: dict, limit: int = DUE_JSON_LIMIT) -> tuple[str, list]:
    """SQL + params for ``/reports/due-for-maintenance``, earliest due first."""
    where = ["COALESCE(a.is_active, TRUE) = TRUE", "a.next_service_date IS NOT NULL"]
    params: list = []
    if args["block"]:
        clause, block_params = location_sql.filter_match("block", args["block"])
        where.append(clause)
        params += block_params
    # `from` defaults to today: the report lists what is upcoming; pass an
    # earlier date to pull past-due units back in.
    where.append("a.next_service_date >= %s")
    params.append(args["from"] or date.today().isoformat())
    if args["to"]:
        where.append("a.next_service_date <= %s")
        params.append(args["to"])
    sql = (
        "SELECT a.ac_id, a.asset_code, a.ac_make_company AS manufacturer, a.ac_type,"
        f" {_BLOCK_UNASSIGNED_SQL} AS block,"
        f" {location_sql.label('floor')} AS floor,"
        f" {location_sql.label('room')} AS room, a.technician,"
        " a.next_service_date, a.operational_status AS status"
        f" {ASSET_JOINS} WHERE {' AND '.join(where)}"
        " ORDER BY a.next_service_date, a.ac_id LIMIT %s"
    )
    params.append(limit)
    return sql, params


def build_trend_rows_query(args: dict) -> tuple[str, list]:
    """SQL + params for the per-row monthly-trend fetch (one row per work order)."""
    conds, params = _wo_scope(args)
    where = ["COALESCE(w.scheduled_date, w.completed_at::date) IS NOT NULL", *conds]
    sql = (
        "SELECT COALESCE(w.scheduled_date, w.completed_at::date) AS bucket_date,"
        " w.maintenance_type"
        f" {WO_JOINS} WHERE {' AND '.join(where)}"
    )
    return sql, params


def build_trend_stats_query(args: dict) -> tuple[str, list]:
    """SQL + params for the trend report's aggregate stats block."""
    conds, params = _wo_scope(args)
    where_sql = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = (
        "SELECT"
        " COUNT(*) AS total,"
        " COUNT(*) FILTER (WHERE w.status = 'SCHEDULED') AS scheduled,"
        " COUNT(*) FILTER (WHERE w.status = 'ASSIGNED') AS assigned,"
        " COUNT(*) FILTER (WHERE w.status = 'IN_PROGRESS') AS in_progress,"
        " COUNT(*) FILTER (WHERE w.status = 'COMPLETED') AS completed,"
        " COUNT(*) FILTER (WHERE w.status = 'CANCELLED') AS cancelled,"
        " COUNT(*) FILTER (WHERE"
        "  (w.scheduled_date >= date_trunc('month', CURRENT_DATE)::date"
        "   AND w.scheduled_date < (date_trunc('month', CURRENT_DATE) + INTERVAL '1 month')::date)"
        "  OR (w.completed_at >= date_trunc('month', CURRENT_DATE)"
        "      AND w.completed_at < date_trunc('month', CURRENT_DATE) + INTERVAL '1 month')"
        " ) AS mtd_count,"
        " COUNT(*) FILTER (WHERE w.status IN ('SCHEDULED','ASSIGNED','IN_PROGRESS')"
        "  AND w.scheduled_date IS NOT NULL AND w.scheduled_date < CURRENT_DATE) AS overdue_count,"
        " COUNT(*) FILTER (WHERE w.status IN ('SCHEDULED','ASSIGNED','IN_PROGRESS')"
        "  AND w.scheduled_date IS NOT NULL AND w.scheduled_date >= CURRENT_DATE) AS upcoming_count,"
        " COALESCE(ROUND(AVG(GREATEST(1, w.completed_at::date - w.scheduled_date))"
        "  FILTER (WHERE w.status = 'COMPLETED' AND w.completed_at IS NOT NULL"
        "          AND w.scheduled_date IS NOT NULL)::numeric, 1), 0) AS mttr_days"
        f" {WO_JOINS}{where_sql}"
    )
    return sql, params


def build_trend_priority_query(args: dict) -> tuple[str, list]:
    """SQL + params for the trend report's priority distribution."""
    conds, params = _wo_scope(args)
    where_sql = (" WHERE " + " AND ".join(conds)) if conds else ""
    sql = (
        "SELECT COALESCE(NULLIF(w.priority, ''), 'MEDIUM') AS priority, COUNT(*) AS n"
        f" {WO_JOINS}{where_sql} GROUP BY 1"
    )
    return sql, params


# ── endpoints ─────────────────────────────────────────────────────────────────


@reports_v1_bp.route("/api/v1/reports/maintenance-trend", methods=["GET"])
@require_auth
@require_permission("VIEW_REPORTS")
@rate_limit(120, 60, "reports-trend")
def maintenance_trend():
    """Monthly trend + priority distribution + stats.

    ``?from&to`` set the window (≤ 24 months) and filter every query in the
    report; ``?block`` scopes by asset location; ``?format=csv`` streams the
    monthly series as a CSV download.
    """
    args, bad = _report_args()
    if bad:
        return bad
    window, bad = _trend_window(args["from"], args["to"])
    if bad:
        return bad

    trend = {key: _blank_month(key) for key in window}
    extras: dict[str, dict] = {}
    priority_counts = {p: 0 for p in PRIORITY_ORDER}
    stats: dict = {}

    rows_sql, rows_params = build_trend_rows_query(args)
    stats_sql, stats_params = build_trend_stats_query(args)
    prio_sql, prio_params = build_trend_priority_query(args)

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if not _table_exists(cur, "maintenance_work_orders"):
                cur.close()
                return error("NOT_SUPPORTED", "Work-order table not migrated yet.", 409)

            # ── monthly trend (per-row bucketing) ────────────────────────────
            cur.execute(rows_sql, rows_params)
            for row in cur.fetchall():
                key = _month_key(row["bucket_date"])
                entry = trend.get(key)
                if entry is None:
                    entry = extras.setdefault(key, _blank_month(key))
                entry["total"] += 1
                bucket = _type_bucket(row["maintenance_type"])
                if bucket is not None:
                    entry[bucket] += 1

            # ── aggregate stats ──────────────────────────────────────────────
            cur.execute(stats_sql, stats_params)
            stats = dict(cur.fetchone())

            # ── priority distribution ────────────────────────────────────────
            cur.execute(prio_sql, prio_params)
            for row in cur.fetchall():
                if row["priority"] in priority_counts:
                    priority_counts[row["priority"]] = row["n"]
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("maintenance trend failed request_id=%s", getattr(g, "request_id", "-"))
        return error("REPORT_FAILED", "Unable to build maintenance report.", 500)

    monthly = [trend[key] for key in window] + [extras[key] for key in sorted(extras)]
    if args["format"] == "csv":
        return _csv_response(
            monthly,
            ["month", "fullMonth", "total", "Preventive", "Repair", "Emergency", "Inspection"],
            f"maintenance-trend-{date.today().isoformat()}.csv",
        )
    return success(
        {
            "monthlyTrend": monthly,
            "priorityDistribution": [{"name": p, "value": priority_counts[p]} for p in PRIORITY_ORDER],
            "stats": {
                "total": stats.get("total") or 0,
                "scheduled": stats.get("scheduled") or 0,
                "assigned": stats.get("assigned") or 0,
                "in_progress": stats.get("in_progress") or 0,
                "completed": stats.get("completed") or 0,
                "cancelled": stats.get("cancelled") or 0,
                "mtdCount": stats.get("mtd_count") or 0,
                "mttrDays": float(stats.get("mttr_days") or 0),
                "overdueCount": stats.get("overdue_count") or 0,
                "upcomingCount": stats.get("upcoming_count") or 0,
            },
        }
    )


@reports_v1_bp.route("/api/v1/reports/assets-by-block", methods=["GET"])
@require_auth
@require_permission("VIEW_REPORTS")
@rate_limit(120, 60, "reports-block")
def assets_by_block():
    """Active assets grouped by block: ``{block, count, capacity_ton}`` rows."""
    args, bad = _report_args()
    if bad:
        return bad
    rows, bad = _run_report_query(*build_assets_by_block_query(args))
    if bad:
        return bad
    if args["format"] == "csv":
        return _csv_response(
            rows,
            ["block", "count", "capacity_ton"],
            f"assets-by-block-{date.today().isoformat()}.csv",
        )
    return success({"items": rows, "count": len(rows)})


@reports_v1_bp.route("/api/v1/reports/assets-by-type", methods=["GET"])
@require_auth
@require_permission("VIEW_REPORTS")
@rate_limit(120, 60, "reports-type")
def assets_by_type():
    """Active assets grouped by AC type: ``{type, count}`` rows."""
    args, bad = _report_args()
    if bad:
        return bad
    rows, bad = _run_report_query(*build_assets_by_type_query(args))
    if bad:
        return bad
    if args["format"] == "csv":
        return _csv_response(rows, ["type", "count"], f"assets-by-type-{date.today().isoformat()}.csv")
    return success({"items": rows, "count": len(rows)})


@reports_v1_bp.route("/api/v1/reports/capacity", methods=["GET"])
@require_auth
@require_permission("VIEW_REPORTS")
@rate_limit(120, 60, "reports-capacity")
def capacity_report():
    """Active assets grouped by capacity in tons: ``{capacity_ton, count}`` rows."""
    args, bad = _report_args()
    if bad:
        return bad
    rows, bad = _run_report_query(*build_capacity_query(args))
    if bad:
        return bad
    if args["format"] == "csv":
        return _csv_response(rows, ["capacity_ton", "count"], f"capacity-{date.today().isoformat()}.csv")
    return success({"items": rows, "count": len(rows)})


@reports_v1_bp.route("/api/v1/reports/due-for-maintenance", methods=["GET"])
@require_auth
@require_permission("VIEW_REPORTS")
@rate_limit(120, 60, "reports-due")
def due_for_maintenance():
    """Active assets whose ``next_service_date`` falls in ``?from`` (default:
    today) … ``?to``, earliest due first. JSON caps at ``DUE_JSON_LIMIT``
    rows, CSV at ``DUE_CSV_LIMIT`` (``limit`` echoed in the JSON payload)."""
    args, bad = _report_args()
    if bad:
        return bad
    limit = DUE_CSV_LIMIT if args["format"] == "csv" else DUE_JSON_LIMIT
    rows, bad = _run_report_query(*build_due_for_maintenance_query(args, limit=limit))
    if bad:
        return bad
    if args["format"] == "csv":
        return _csv_response(
            rows,
            [
                "ac_id",
                "asset_code",
                "manufacturer",
                "ac_type",
                "block",
                "floor",
                "room",
                "technician",
                "next_service_date",
                "status",
            ],
            f"due-for-maintenance-{date.today().isoformat()}.csv",
        )
    return success({"items": rows, "count": len(rows), "limit": limit})
