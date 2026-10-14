"""Universal issue reporting with Super Admin approval (Features B/C).

Any authenticated role may file a report; every report starts
PENDING_APPROVAL and becomes a technician work order only through Super
Admin approval. Approval runs in one transaction — the conditional status
flip plus the work-order insert — so concurrent approvals, retries and
double-clicks can never create duplicate jobs:

* the status flip is conditional (``WHERE status='PENDING_APPROVAL'``), so
  exactly one approval wins; losers see the decided row and get the same
  work-order id back;
* the work order carries ``client_mutation_id='report:<report-id>'`` which
  is UNIQUE, so even a retried insert inside the window collapses to the
  existing job instead of a duplicate;
* a failed work-order insert rolls the whole transaction back, leaving the
  report PENDING_APPROVAL — never approved-without-a-job.

Reporter identity always comes from the verified session, never the body.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from flask import Blueprint, g, request
from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor
from pydantic import ValidationError

from app.auth.jwt import require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, success
from app.middleware.rate_limit import rate_limit
from app.schemas.v1_schemas import IssueReportCreate, IssueReportReject
from app.services import location_sql
from app.services.audit_service import audit
from app.services.notify import notify

issue_reports_v1_bp = Blueprint("issue_reports_v1", __name__)

logger = logging.getLogger(__name__)

# Report category -> work-order maintenance_type (free text; the frontend
# buckets by prefix, so keep the Preventive prefix for preventive reports).
CATEGORY_MAINTENANCE_TYPE = {
    "PREVENTIVE_MAINTENANCE": "Preventive",
    "OUTDOOR_UNIT": "Outdoor unit repair",
    "INDOOR_UNIT": "Indoor unit repair",
    "AC_NOT_COOLING": "Repair",
    "AC_NOT_STARTING": "Repair",
    "WATER_LEAKAGE": "Repair",
    "NOISE_VIBRATION": "Repair",
    "ELECTRICAL_POWER": "Repair",
    "OTHER": "Repair",
}

CATEGORY_LABELS = {
    "AC_NOT_COOLING": "AC not cooling",
    "AC_NOT_STARTING": "AC not starting",
    "WATER_LEAKAGE": "Water leakage",
    "NOISE_VIBRATION": "Unusual noise or vibration",
    "ELECTRICAL_POWER": "Electrical or power concern",
    "OUTDOOR_UNIT": "Outdoor unit problem",
    "INDOOR_UNIT": "Indoor unit problem",
    "PREVENTIVE_MAINTENANCE": "Preventive maintenance",
    "OTHER": "Other",
}


def _now():
    return datetime.now(timezone.utc)


def _serialize(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _require_profile(user: dict):
    if not user.get("profile"):
        return error("PROFILE_NOT_FOUND", "Your user profile has not been provisioned yet.", 409)
    return None


def _find_asset(cur, ref: str):
    """Triple-ref asset lookup (asset_uuid | asset_code | ac_id)."""
    cur.execute(
        "SELECT ac_id, asset_uuid FROM acs WHERE asset_uuid::text = %s OR asset_code = %s OR ac_id = %s LIMIT 1",
        (ref, ref, ref),
    )
    return cur.fetchone()


def _location_snapshot(cur, ac_id: str) -> str | None:
    """Effective location text at submit time — historical context that never
    changes even if the asset is moved later."""
    try:
        cur.execute(
            f"SELECT {location_sql.label('college')} AS college,"
            f" {location_sql.label('block')} AS block,"
            f" {location_sql.label('floor')} AS floor,"
            f" {location_sql.label('room')} AS room"
            f" FROM acs a {location_sql.LOCATION_JOIN} {location_sql.EFFECTIVE_JOINS}"
            " WHERE a.ac_id = %s LIMIT 1",
            (ac_id,),
        )
        row = cur.fetchone()
    except Exception:  # noqa: BLE001 — snapshot is context, never load-bearing
        return None
    if not row:
        return None
    parts = [row["college"], row["block"], row["floor"], row["room"]]
    text = " / ".join(p for p in parts if p)
    return text or None


def _report_visible_to(cur, report_id: str, user: dict):
    """Owner-or-approver fetch: unrelated reports are 404, indistinguishable
    from missing (same IDOR pattern as the work-order endpoints)."""
    from app.auth.permissions import has_permission

    cur.execute("SELECT * FROM maintenance_reports WHERE id = %s", (report_id,))
    row = cur.fetchone()
    if row is None:
        return None
    if has_permission(user.get("role"), "APPROVE_REPORTS"):
        return row
    actor_id = str((user.get("profile") or {}).get("id") or user.get("id") or "")
    if row["reporter_id"] is not None and str(row["reporter_id"]) == actor_id:
        return row
    return None


def _paginate(cur, select_sql: str, base_sql: str, params: list, page: int, page_size: int):
    cur.execute(f"SELECT COUNT(*) AS n {base_sql}", params)
    total = cur.fetchone()["n"]
    cur.execute(
        f"{select_sql} {base_sql} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
        params + [page_size, (page - 1) * page_size],
    )
    return total, [_serialize(dict(r)) for r in cur.fetchall()]


def _page_args():
    try:
        page = max(1, int((request.args.get("page") or "1")))
    except ValueError:
        return None, error("INVALID_PAGINATION", "page must be a positive integer.", 400)
    try:
        page_size = min(100, max(1, int((request.args.get("page_size") or "20"))))
    except ValueError:
        return None, error("INVALID_PAGINATION", "page_size must be a positive integer.", 400)
    return (page, page_size), None


def _valid_report_id(report_id: str):
    """UUID-shaped ids only — anything else is 404 (indistinguishable from
    missing, and never a database error)."""
    try:
        return str(uuid.UUID(str(report_id)))
    except (ValueError, AttributeError, TypeError):
        return None


@issue_reports_v1_bp.route("/api/v1/issue-reports", methods=["POST"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(60, 60, "issue-report-create")
def create_issue_report():
    """File an issue report. All four roles may submit; the report starts
    PENDING_APPROVAL and is never a work order at this point."""
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return error("VALIDATION_ERROR", "Request body must be a JSON object.", 400)
    try:
        payload = IssueReportCreate(**raw)
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid report payload.", 400, exc.errors(include_context=False))

    user = g.current_user or {}
    profile_err = _require_profile(user)
    if profile_err:
        return profile_err
    actor_id = str(user["profile"]["id"])

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            asset = _find_asset(cur, payload.ac_id.strip())
            if asset is None:
                cur.close()
                return error("ASSET_NOT_FOUND", "Asset not found.", 404)
            ac_id = asset["ac_id"]
            snapshot = _location_snapshot(cur, ac_id)

            # Idempotent retry: the same form instance (same key) returns the
            # original report instead of filing a duplicate.
            if payload.idempotency_key:
                cur.execute(
                    "SELECT * FROM maintenance_reports WHERE idempotency_key = %s",
                    (payload.idempotency_key,),
                )
                existing = cur.fetchone()
                if existing:
                    cur.close()
                    data = _serialize(dict(existing))
                    data["deduplicated"] = True
                    return success(data)

            report_code = None
            for _ in range(3):
                candidate = "IR-" + uuid.uuid4().hex[:8].upper()
                cur.execute("SELECT 1 FROM maintenance_reports WHERE report_code = %s", (candidate,))
                if cur.fetchone() is None:
                    report_code = candidate
                    break
            if report_code is None:
                cur.close()
                return error("REPORT_CREATE_FAILED", "Unable to file the report.", 500)

            try:
                cur.execute(
                    """INSERT INTO maintenance_reports
                       (report_code, ac_id, reporter_id, category, description,
                        priority, location_text, asset_location_snapshot,
                        idempotency_key)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                    (
                        report_code,
                        ac_id,
                        actor_id,
                        payload.category,
                        payload.description,
                        payload.priority,
                        payload.location_text,
                        snapshot,
                        payload.idempotency_key,
                    ),
                )
                row = _serialize(dict(cur.fetchone()))
            except pg_errors.UniqueViolation:
                # Lost a race on the idempotency key: fetch the winner.
                conn.rollback()
                cur.execute(
                    "SELECT * FROM maintenance_reports WHERE idempotency_key = %s",
                    (payload.idempotency_key,),
                )
                winner = cur.fetchone()
                cur.close()
                if winner:
                    data = _serialize(dict(winner))
                    data["deduplicated"] = True
                    return success(data)
                return error("REPORT_CREATE_FAILED", "Unable to file the report.", 500)
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("issue report create failed request_id=%s", getattr(g, "request_id", "-"))
        return error("REPORT_CREATE_FAILED", "Unable to file the report.", 500)

    audit("SUBMIT_ISSUE_REPORT", "issue_report", row["id"], None, {"report_code": report_code, "ac_id": ac_id})
    try:
        notify(
            actor_id,
            "REPORT_SUBMITTED",
            f"Issue report {report_code} received",
            "Your report is pending Super Admin approval.",
            "issue_report",
            row["id"],
        )
    except Exception:  # noqa: BLE001 — notify() is already best-effort
        pass
    return success(row, 201)


@issue_reports_v1_bp.route("/api/v1/issue-reports/mine", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def my_issue_reports():
    """The reporter's own reports with current statuses."""
    user = g.current_user or {}
    profile_err = _require_profile(user)
    if profile_err:
        return profile_err
    actor_id = str(user["profile"]["id"])
    paging, err = _page_args()
    if err:
        return err
    page, page_size = paging
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        total, rows = _paginate(
            cur,
            "SELECT *",
            "FROM maintenance_reports WHERE reporter_id = %s",
            [actor_id],
            page,
            page_size,
        )
        cur.close()
    finally:
        conn.close()
    return success({"reports": rows, "pagination": {"page": page, "page_size": page_size, "total": total}})


@issue_reports_v1_bp.route("/api/v1/issue-reports", methods=["GET"])
@require_auth
@require_permission("APPROVE_REPORTS")
def all_issue_reports():
    """Super Admin tracking: every report including decided ones, newest
    first — so an approved report stays visible after it leaves the pending
    queue. Optional ?status=PENDING_APPROVAL|APPROVED|REJECTED filter."""
    status = (request.args.get("status") or "").strip().upper() or None
    if status is not None and status not in ("PENDING_APPROVAL", "APPROVED", "REJECTED"):
        return error("VALIDATION_ERROR", "status must be PENDING_APPROVAL, APPROVED or REJECTED.", 400)
    paging, err = _page_args()
    if err:
        return err
    page, page_size = paging
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        params: list = []
        where = ""
        if status is not None:
            where = "WHERE r.status = %s"
            params.append(status)
        cur.execute(f"SELECT COUNT(*) AS n FROM maintenance_reports r {where}", params)
        total = cur.fetchone()["n"]
        cur.execute(
            "SELECT r.*, a.asset_code, p.name AS reporter_name,"
            " w.status AS work_order_status"
            " FROM maintenance_reports r"
            " LEFT JOIN acs a ON a.ac_id = r.ac_id"
            " LEFT JOIN profiles p ON p.id = r.reporter_id"
            " LEFT JOIN maintenance_work_orders w ON w.id = r.work_order_id "
            + (where + " " if where else "")
            + "ORDER BY r.created_at DESC, r.id DESC LIMIT %s OFFSET %s",
            params + [page_size, (page - 1) * page_size],
        )
        rows = [_serialize(dict(r)) for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return success({"reports": rows, "pagination": {"page": page, "page_size": page_size, "total": total}})


@issue_reports_v1_bp.route("/api/v1/issue-reports/pending", methods=["GET"])
@require_auth
@require_permission("APPROVE_REPORTS")
def pending_issue_reports():
    """Super Admin review queue: pending reports oldest first."""
    paging, err = _page_args()
    if err:
        return err
    page, page_size = paging
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT COUNT(*) AS n FROM maintenance_reports WHERE status = 'PENDING_APPROVAL'")
        total = cur.fetchone()["n"]
        cur.execute(
            """SELECT r.*, a.asset_code, p.name AS reporter_name
               FROM maintenance_reports r
               LEFT JOIN acs a ON a.ac_id = r.ac_id
               LEFT JOIN profiles p ON p.id = r.reporter_id
               WHERE r.status = 'PENDING_APPROVAL'
               ORDER BY r.created_at ASC, r.id ASC LIMIT %s OFFSET %s""",
            (page_size, (page - 1) * page_size),
        )
        rows = [_serialize(dict(r)) for r in cur.fetchall()]
        cur.close()
    finally:
        conn.close()
    return success({"reports": rows, "pagination": {"page": page, "page_size": page_size, "total": total}})


@issue_reports_v1_bp.route("/api/v1/issue-reports/<report_id>", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def get_issue_report(report_id):
    """One report with its decision history and linked work order, if the
    caller owns it or holds APPROVE_REPORTS."""
    rid = _valid_report_id(report_id)
    if rid is None:
        return error("REPORT_NOT_FOUND", "Report not found.", 404)
    user = g.current_user or {}
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        row = _report_visible_to(cur, rid, user)
        if row is None:
            cur.close()
            return error("REPORT_NOT_FOUND", "Report not found.", 404)
        data = _serialize(dict(row))
        work_order = None
        if data.get("work_order_id"):
            cur.execute(
                "SELECT id, ac_id, maintenance_type, priority, status, technician_id,"
                " scheduled_date, created_at FROM maintenance_work_orders WHERE id = %s",
                (str(data["work_order_id"]),),
            )
            wo = cur.fetchone()
            work_order = _serialize(dict(wo)) if wo else None
        data["work_order"] = work_order
        decider = None
        if data.get("decided_by"):
            cur.execute("SELECT id, name, role FROM profiles WHERE id = %s", (str(data["decided_by"]),))
            dec = cur.fetchone()
            decider = _serialize(dict(dec)) if dec else None
        data["decided_by_profile"] = decider
        cur.close()
    finally:
        conn.close()
    return success(data)


@issue_reports_v1_bp.route("/api/v1/issue-reports/<report_id>/approve", methods=["POST"])
@require_auth
@require_permission("APPROVE_REPORTS")
@rate_limit(60, 60, "issue-report-approve")
def approve_issue_report(report_id):
    """Approve a pending report and create exactly one SCHEDULED work order,
    atomically. Replays return the existing job (200, already_processed)."""
    rid = _valid_report_id(report_id)
    if rid is None:
        return error("REPORT_NOT_FOUND", "Report not found.", 404)
    user = g.current_user or {}
    profile_err = _require_profile(user)
    if profile_err:
        return profile_err
    actor_id = str(user["profile"]["id"])

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # Conditional flip: exactly one concurrent approval can win.
            cur.execute(
                """UPDATE maintenance_reports
                   SET status = 'APPROVED', decided_by = %s, decided_at = %s,
                       rejection_reason = NULL, updated_at = %s
                   WHERE id = %s AND status = 'PENDING_APPROVAL' RETURNING *""",
                (actor_id, _now(), _now(), rid),
            )
            flipped = cur.fetchone()
            if flipped is None:
                cur.execute("SELECT * FROM maintenance_reports WHERE id = %s", (rid,))
                decided = cur.fetchone()
                cur.close()
                if decided is None:
                    return error("REPORT_NOT_FOUND", "Report not found.", 404)
                decided = _serialize(dict(decided))
                return success(
                    {
                        "id": decided.get("work_order_id"),
                        "report_id": decided["id"],
                        "report_code": decided["report_code"],
                        "status": "SCHEDULED" if decided.get("work_order_id") else decided["status"],
                        "already_processed": True,
                    }
                )
            report = dict(flipped)
            mutation_id = f"report:{report['id']}"
            try:
                cur.execute(
                    """INSERT INTO maintenance_work_orders
                       (ac_id, maintenance_type, priority, status, scheduled_date,
                        problem_description, remarks, created_by, client_mutation_id)
                       VALUES (%s,%s,%s,'SCHEDULED',NULL,%s,%s,%s,%s) RETURNING *""",
                    (
                        report["ac_id"],
                        CATEGORY_MAINTENANCE_TYPE.get(report["category"], "Repair"),
                        report["priority"],
                        f"[{CATEGORY_LABELS.get(report['category'], report['category'])}]"
                        f" Reported {report['report_code']}: {report['description']}",
                        (report["location_text"] or None),
                        actor_id,
                        mutation_id,
                    ),
                )
                created = dict(cur.fetchone())
            except pg_errors.UniqueViolation:
                # A retried insert inside the window: adopt the existing job.
                conn.rollback()
                cur.execute(
                    "SELECT * FROM maintenance_work_orders WHERE client_mutation_id = %s",
                    (mutation_id,),
                )
                existing_wo = cur.fetchone()
                cur.close()
                if existing_wo is None:
                    return error("REPORT_APPROVE_FAILED", "Unable to approve the report.", 500)
                # Re-apply the decision + link in a fresh transaction.
                conn2 = get_db_connection()
                try:
                    cur2 = conn2.cursor(cursor_factory=RealDictCursor)
                    cur2.execute(
                        """UPDATE maintenance_reports SET status='APPROVED', decided_by=%s,
                           decided_at=%s, work_order_id=%s, updated_at=%s
                           WHERE id=%s AND status='PENDING_APPROVAL'""",
                        (actor_id, _now(), str(existing_wo["id"]), _now(), rid),
                    )
                    conn2.commit()
                    cur2.close()
                finally:
                    conn2.close()
                wo_id = str(existing_wo["id"])
                audit(
                    "APPROVE_ISSUE_REPORT",
                    "issue_report",
                    rid,
                    {"status": "PENDING_APPROVAL"},
                    {"status": "APPROVED", "work_order_id": wo_id, "deduplicated": True},
                )
                return success(
                    {
                        "id": wo_id,
                        "report_id": rid,
                        "report_code": report["report_code"],
                        "status": "SCHEDULED",
                        "already_processed": True,
                    }
                )
            wo_id = str(created["id"])
            cur.execute(
                """INSERT INTO maintenance_events
                   (work_order_id, actor_id, event, from_status, to_status, note, created_at)
                   VALUES (%s,%s,'CREATED',NULL,'SCHEDULED',%s,%s)""",
                (wo_id, actor_id, f"Created from approved issue report {report['report_code']}", _now()),
            )
            cur.execute(
                "UPDATE maintenance_reports SET work_order_id = %s, updated_at = %s WHERE id = %s",
                (wo_id, _now(), rid),
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("issue report approve failed request_id=%s", getattr(g, "request_id", "-"))
        return error("REPORT_APPROVE_FAILED", "Unable to approve the report.", 500)

    audit(
        "APPROVE_ISSUE_REPORT",
        "issue_report",
        rid,
        {"status": "PENDING_APPROVAL"},
        {"status": "APPROVED", "work_order_id": wo_id},
    )
    # Reporter learns the outcome; managers learn a job entered the queue.
    try:
        if report.get("reporter_id"):
            notify(
                str(report["reporter_id"]),
                "REPORT_APPROVED",
                f"Issue report {report['report_code']} approved",
                "A technician job has been created from your report.",
                "issue_report",
                rid,
            )
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM profiles WHERE role IN ('SUPER_ADMIN','FACILITY_MANAGER')"
                " AND is_active IS NOT FALSE LIMIT 50"
            )
            for (mid,) in cur.fetchall():
                if str(mid) == actor_id:
                    continue
                notify(
                    str(mid),
                    "REPORT_JOB_QUEUED",
                    f"New job from report {report['report_code']}",
                    "An approved issue report created an unassigned job.",
                    "maintenance_work_order",
                    wo_id,
                    conn=conn,
                )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — notifications are auxiliary
        logger.warning("report approval notifications failed", exc_info=True)
    return success(
        {
            "id": wo_id,
            "report_id": rid,
            "report_code": report["report_code"],
            "status": "SCHEDULED",
            "already_processed": False,
        }
    )


@issue_reports_v1_bp.route("/api/v1/issue-reports/<report_id>/reject", methods=["POST"])
@require_auth
@require_permission("APPROVE_REPORTS")
@rate_limit(60, 60, "issue-report-reject")
def reject_issue_report(report_id):
    """Reject a pending report with a recorded reason. Rejected reports keep
    their history and never produce a work order."""
    rid = _valid_report_id(report_id)
    if rid is None:
        return error("REPORT_NOT_FOUND", "Report not found.", 404)
    raw = request.get_json(silent=True)
    if not isinstance(raw, dict):
        return error("VALIDATION_ERROR", "Request body must be a JSON object.", 400)
    try:
        payload = IssueReportReject(**raw)
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "A rejection reason is required.", 400, exc.errors(include_context=False))

    user = g.current_user or {}
    profile_err = _require_profile(user)
    if profile_err:
        return profile_err
    actor_id = str(user["profile"]["id"])

    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            """UPDATE maintenance_reports
               SET status = 'REJECTED', decided_by = %s, decided_at = %s,
                   rejection_reason = %s, updated_at = %s
               WHERE id = %s AND status = 'PENDING_APPROVAL' RETURNING *""",
            (actor_id, _now(), payload.reason, _now(), rid),
        )
        rejected = cur.fetchone()
        if rejected is None:
            cur.execute("SELECT status FROM maintenance_reports WHERE id = %s", (rid,))
            decided = cur.fetchone()
            cur.close()
            if decided is None:
                return error("REPORT_NOT_FOUND", "Report not found.", 404)
            return error("REPORT_ALREADY_DECIDED", "This report has already been decided.", 409)
        report = _serialize(dict(rejected))
        cur.close()
        conn.commit()
    finally:
        conn.close()

    audit(
        "REJECT_ISSUE_REPORT",
        "issue_report",
        rid,
        {"status": "PENDING_APPROVAL"},
        {"status": "REJECTED", "reason": payload.reason},
    )
    try:
        if report.get("reporter_id"):
            notify(
                str(report["reporter_id"]),
                "REPORT_REJECTED",
                f"Issue report {report['report_code']} was not approved",
                f"Reason: {payload.reason}",
                "issue_report",
                rid,
            )
    except Exception:  # noqa: BLE001
        pass
    return success(report)
