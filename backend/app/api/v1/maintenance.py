"""Work-order maintenance endpoints (Plan #15, #20; Phase 4 lifecycle).

HTTP concerns only: auth/permission gates, payload validation, envelopes and
post-commit side effects (audit + notifications). Every state transition goes
through the table-driven state machine in :mod:`app.services.work_orders`,
including the replayed mutations of ``POST /maintenance/sync``.

Side-effect pattern: timeline events / AC date updates are written inside the
business transaction; ``audit()`` and ``notify()`` run AFTER commit (notify
never commits on its own — a bare ``close()`` rolls back — so notifications
are emitted on their own connection and committed explicitly).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from flask import Blueprint, g, request
from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor
from pydantic import ValidationError

from app.auth.jwt import require_auth
from app.auth.permissions import has_permission, require_permission
from app.config.database import get_db_connection
from app.core import error, paginated, success
from app.core.clock import utcnow
from app.middleware.rate_limit import rate_limit
from app.schemas.v1_schemas import (
    MaintenanceCommentCreate,
    MaintenanceCreate,
    MaintenanceUpdate,
)
from app.services import location_sql
from app.services.audit_service import audit
from app.services.notify import notify
from app.services.work_orders import (
    NOTIFY_TYPES,
    PRIORITY_LADDER,
    STATUSES,
    WorkOrderError,
    apply_transition,
    notification_body,
    resolve_recipients,
    validate_assignee,
)

maintenance_v1_bp = Blueprint("maintenance_v1", __name__)

logger = logging.getLogger(__name__)

ACTIONS = ("assign", "accept", "start", "complete", "escalate", "cancel")

# audit_logs.action is verb-first (CREATE_MAINTENANCE / UPDATE_MAINTENANCE).
ACTION_AUDIT = {
    "assign": "ASSIGN_JOB",
    "accept": "ACCEPT_JOB",
    "start": "START_JOB",
    "complete": "COMPLETE_JOB",
    "cancel": "CANCEL_JOB",
    "escalate": "ESCALATE_JOB",
}

ACTION_TITLES = {
    "assign": "New maintenance job assigned",
    "accept": "Maintenance job accepted",
    "start": "Maintenance job started",
    "complete": "Maintenance job completed",
    "cancel": "Maintenance job cancelled",
    "escalate": "Maintenance job escalated",
}

# Shared SELECT for list + detail: work order joined to asset/location/owner.
# Location labels are effective (§7): canonical name first, preserved source
# for levels the resolver has not confirmed yet.
_WO_SELECT = (
    "SELECT w.*, a.asset_code, a.ac_make_company, a.ac_model_number,"
    " a.ac_serial_number, a.ac_type, a.ac_capacity_ton,"
    f" {location_sql.label('block')} AS block_name,"
    f" {location_sql.label('floor')} AS floor_name,"
    f" {location_sql.label('room')} AS room_name,"
    " tp.name AS technician_name, tp.email AS technician_email"
)
_WO_JOINS = (
    " FROM maintenance_work_orders w"
    " LEFT JOIN acs a ON a.ac_id = w.ac_id"
    " LEFT JOIN asset_locations al ON al.ac_id = a.ac_id"
    " LEFT JOIN rooms r ON r.room_id = COALESCE(al.room_id, a.room_id)"
    " LEFT JOIN floors f ON f.floor_id = COALESCE(al.floor_id, r.floor_id)"
    " LEFT JOIN blocks b ON b.block_id = COALESCE(al.block_id, f.block_id)"
    " LEFT JOIN profiles tp ON tp.id = w.technician_id"
)


def _now() -> datetime:
    # Monotonic within the process: the timeline sorts on created_at and the
    # row id is a random UUID, so equal stamps would order arbitrarily.
    return utcnow()


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) AS present", (f"public.{name}",))
    row = cur.fetchone()
    return (row["present"] if isinstance(row, dict) else row[0]) is not None


def _serialize(d: dict) -> dict:
    """JSON-safe row: dates → ISO strings, UUID/Decimal → str/float."""
    out = {}
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, UUID):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _require_profile(user: dict):
    """Timeline/audit FKs (created_by / actor_id / author_id) reference
    profiles — reject cleanly instead of blowing up on a 500 FK violation."""
    if not user.get("profile"):
        return error(
            "PROFILE_NOT_FOUND",
            "Your user profile has not been provisioned yet.",
            409,
        )
    return None


def _notify_rows(recipients, ntype: str, title: str, body: str, entity_id: str) -> None:
    """Best-effort notifications AFTER the business commit.

    ``notify()`` never commits on its own (a bare ``close()`` rolls back), so
    one dedicated connection writes every row and commits explicitly.
    """
    if not recipients:
        return
    conn = None
    try:
        conn = get_db_connection()
        for uid in recipients:
            notify(uid, ntype, title, body, "maintenance_work_order", entity_id, conn=conn)
        conn.commit()
    except Exception:
        # Best-effort: the transition itself is already committed, so a
        # notification failure must never surface as a request error.
        logger.warning(
            "notification write failed request_id=%s", getattr(g, "request_id", "-"), exc_info=True
        )
    finally:
        if conn is not None:
            conn.close()


def _emit_side_effects(action: str, updated: dict, planned, recipients) -> None:
    """Post-commit: audit row + notifications for one applied transition."""
    audit(
        ACTION_AUDIT[action],
        "maintenance",
        str(updated.get("id")),
        {"status": planned.from_status},
        {"status": planned.to_status, **planned.updates},
    )
    _notify_rows(
        recipients,
        NOTIFY_TYPES[action],
        ACTION_TITLES[action],
        notification_body(updated, action, planned),
        str(updated.get("id")),
    )


def _json_object():
    """Request body as a dict, or None when it is not a JSON object."""
    raw = request.get_json(silent=True)
    if raw is None:
        return {}
    return raw if isinstance(raw, dict) else None


# ── GET /maintenance (list) ───────────────────────────────────────────────────


def _own_jobs_clause(user: dict, alias: str = "") -> tuple[str, list] | None:
    """Detail-level twin of the technician filter in ``list_work_orders``.

    The list hides jobs that are neither assigned to nor created by the
    calling technician; without the same clause on the by-id endpoints a
    technician could still reach a colleague's job by guessing its id — to
    read it (GET), to rewrite its content (PATCH) or to comment on it.
    Returns ``NULL`` for roles that are not object-scoped (managers and
    viewers see every job the list shows them).
    """
    if (user.get("role") or "") != "TECHNICIAN":
        return None
    actor_id = str(user.get("id") or "")
    col = f"{alias}." if alias else ""
    return (
        f"({col}technician_id::text = %s OR {col}created_by::text = %s)",
        [actor_id, actor_id],
    )


@maintenance_v1_bp.route("/api/v1/maintenance", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(120, 60, "maint-list")
def list_work_orders():
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(200, max(1, int(request.args.get("page_size", 50))))
    except ValueError:
        return error("INVALID_PAGINATION", "page and page_size must be integers.", 400)

    user = g.current_user or {}
    role = user.get("role")
    actor_id = str(user.get("id") or "")

    status_param = (request.args.get("status") or "").strip()
    priority_param = (request.args.get("priority") or "").strip()
    ac_id = (request.args.get("ac_id") or "").strip()
    assigned_to = (request.args.get("assigned_to") or "").strip()
    q = (request.args.get("q") or "").strip()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    where, params = [], []

    if status_param:
        statuses = [s.strip().upper() for s in status_param.split(",") if s.strip()]
        bad = [s for s in statuses if s not in STATUSES]
        if bad:
            return error("INVALID_STATUS", f"Unknown status: {', '.join(bad)}.", 400)
        where.append("w.status = ANY(%s)")
        params.append(statuses)

    if priority_param:
        priorities = [p.strip().upper() for p in priority_param.split(",") if p.strip()]
        bad = [p for p in priorities if p not in PRIORITY_LADDER]
        if bad:
            return error("INVALID_PRIORITY", f"Unknown priority: {', '.join(bad)}.", 400)
        where.append("w.priority = ANY(%s)")
        params.append(priorities)

    if ac_id:
        where.append("w.ac_id = %s")
        params.append(ac_id)

    if assigned_to:
        where.append("w.technician_id::text = %s")
        params.append(actor_id if assigned_to == "me" else assigned_to)

    if date_from:
        try:
            where.append("w.scheduled_date >= %s")
            params.append(date.fromisoformat(date_from))
        except ValueError:
            return error("INVALID_DATE", "from must be an ISO date (YYYY-MM-DD).", 400)

    if date_to:
        try:
            where.append("w.scheduled_date <= %s")
            params.append(date.fromisoformat(date_to))
        except ValueError:
            return error("INVALID_DATE", "to must be an ISO date (YYYY-MM-DD).", 400)

    if q:
        like = f"%{q}%"
        where.append(
            "(w.problem_description ILIKE %s OR w.maintenance_type ILIKE %s"
            " OR w.remarks ILIKE %s OR w.ac_id ILIKE %s"
            " OR a.asset_code ILIKE %s OR a.ac_serial_number ILIKE %s)"
        )
        params.extend([like] * 6)

    # Technicians only ever see their own jobs (assigned or self-created).
    if role == "TECHNICIAN":
        where.append("(w.technician_id::text = %s OR w.created_by::text = %s)")
        params.extend([actor_id, actor_id])

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if not _table_exists(cur, "maintenance_work_orders"):
                # Unmigrated DB: best-effort read from the legacy table.
                cur.execute("SELECT COUNT(*) AS total FROM maintenance")
                total = cur.fetchone()["total"]
                cur.execute(
                    "SELECT maintenance_id AS id, ac_id, maintenance_type,"
                    " maintenance_date AS scheduled_date, remarks FROM maintenance"
                    " ORDER BY maintenance_date DESC LIMIT %s OFFSET %s",
                    (page_size, (page - 1) * page_size),
                )
                items = [_serialize(dict(r)) for r in cur.fetchall()]
                cur.close()
                return paginated(items, page, page_size, total)
            cur.execute(
                f"SELECT COUNT(*) AS total{_WO_JOINS} {where_sql}",
                tuple(params),
            )
            total = cur.fetchone()["total"]
            cur.execute(
                f"{_WO_SELECT}{_WO_JOINS} {where_sql}"
                " ORDER BY w.created_at DESC, w.id ASC LIMIT %s OFFSET %s",
                tuple(params) + (page_size, (page - 1) * page_size),
            )
            items = [_serialize(dict(r)) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("maintenance list failed request_id=%s", getattr(g, "request_id", "-"))
        return error("MAINTENANCE_LIST_FAILED", "Unable to list maintenance.", 500)
    return paginated(items, page, page_size, total)


# ── GET /maintenance/<wo_id> (detail + timeline + comments) ───────────────────


@maintenance_v1_bp.route("/api/v1/maintenance/<wo_id>", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(120, 60, "maint-detail")
def get_work_order(wo_id):
    user = g.current_user or {}
    where, params = "w.id::text = %s", [wo_id]
    scoped = _own_jobs_clause(user, "w")
    if scoped:
        where += f" AND {scoped[0]}"
        params.extend(scoped[1])
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if not _table_exists(cur, "maintenance_work_orders"):
                cur.close()
                return error("NOT_SUPPORTED", "Work-order table not migrated yet.", 409)
            cur.execute(
                f"{_WO_SELECT}, cp.name AS created_by_name{_WO_JOINS}"
                " LEFT JOIN profiles cp ON cp.id = w.created_by"
                f" WHERE {where}",
                params,
            )
            row = cur.fetchone()
            if row is None:
                cur.close()
                return error("WORK_ORDER_NOT_FOUND", "Work order not found.", 404)
            data = _serialize(dict(row))

            events = []
            if _table_exists(cur, "maintenance_events"):
                cur.execute(
                    "SELECT e.id, e.event, e.from_status, e.to_status, e.note,"
                    " e.client_mutation_id, e.created_at, e.actor_id,"
                    " p.name AS actor_name"
                    " FROM maintenance_events e"
                    " LEFT JOIN profiles p ON p.id = e.actor_id"
                    " WHERE e.work_order_id = %s"
                    " ORDER BY e.created_at ASC, e.id ASC",
                    (wo_id,),
                )
                events = [_serialize(dict(r)) for r in cur.fetchall()]

            comments = []
            if _table_exists(cur, "maintenance_comments"):
                cur.execute(
                    "SELECT c.id, c.body, c.photos, c.created_at, c.author_id,"
                    " p.name AS author_name, p.role AS author_role"
                    " FROM maintenance_comments c"
                    " LEFT JOIN profiles p ON p.id = c.author_id"
                    " WHERE c.work_order_id = %s"
                    " ORDER BY c.created_at ASC, c.id ASC",
                    (wo_id,),
                )
                comments = [_serialize(dict(r)) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("maintenance detail failed request_id=%s", getattr(g, "request_id", "-"))
        return error("MAINTENANCE_DETAIL_FAILED", "Unable to load work order.", 500)
    data["events"] = events
    data["comments"] = comments
    return success(data)


# ── POST /maintenance (create; optional assignment) ───────────────────────────


@maintenance_v1_bp.route("/api/v1/maintenance", methods=["POST"])
@require_auth
@require_permission("MANAGE_MAINTENANCE")
@rate_limit(60, 60, "maint-create")
def create_work_order():
    body = _json_object()
    if body is None:
        return error("VALIDATION_ERROR", "Request body must be a JSON object.", 400)
    try:
        payload = MaintenanceCreate.model_validate(body)
    except ValidationError as exc:
        return error(
            "VALIDATION_ERROR", "Invalid maintenance payload.", 400, exc.errors(include_context=False)
        )

    user = g.current_user or {}
    actor_id = str(user.get("id") or "")
    actor_role = user.get("role")
    profile_err = _require_profile(user)
    if profile_err:
        return profile_err

    technician_id = (payload.technician_id or "").strip() or None
    client_status = (payload.status or "").strip().upper() or "SCHEDULED"
    if client_status not in ("SCHEDULED", "ASSIGNED"):
        return error("INVALID_STATUS", "status must be SCHEDULED or ASSIGNED at creation.", 400)
    if client_status == "ASSIGNED" and not technician_id:
        return error(
            "TECHNICIAN_REQUIRED",
            "technician_id is required when status is ASSIGNED.",
            400,
        )
    if technician_id and technician_id != actor_id and not has_permission(actor_role, "ASSIGN_MAINTENANCE"):
        return error("FORBIDDEN", "You may only assign jobs to yourself.", 403)

    status = "ASSIGNED" if technician_id else "SCHEDULED"
    scheduled = payload.scheduled_date or payload.maintenance_date
    new_id = None
    created = None
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if not _table_exists(cur, "maintenance_work_orders"):
                # Unmigrated DB: legacy insert (assignment needs lifecycle cols).
                if technician_id:
                    cur.close()
                    return error("NOT_SUPPORTED", "Work-order table not migrated yet.", 409)
                cur.execute(
                    "INSERT INTO maintenance (ac_id, maintenance_type, maintenance_date, remarks)"
                    " VALUES (%s,%s,COALESCE(%s,CURRENT_DATE),%s) RETURNING maintenance_id",
                    (
                        payload.ac_id,
                        payload.maintenance_type,
                        scheduled.isoformat() if scheduled else None,
                        payload.remarks or payload.problem_description,
                    ),
                )
                new_id = cur.fetchone()["maintenance_id"]
                cur.close()
                conn.commit()
                audit(
                    "CREATE_MAINTENANCE",
                    "maintenance",
                    new_id,
                    None,
                    payload.model_dump(mode="json"),
                )
                return success({"id": new_id}, 201)

            cur.execute("SELECT ac_id FROM acs WHERE ac_id = %s", (payload.ac_id,))
            if cur.fetchone() is None:
                cur.close()
                return error("ASSET_NOT_FOUND", "Asset not found.", 404)
            if technician_id:
                try:
                    validate_assignee(cur, technician_id)
                except WorkOrderError as exc:
                    cur.close()
                    return error(exc.code, exc.message, exc.http)
            cur.execute(
                """INSERT INTO maintenance_work_orders
                   (ac_id, maintenance_type, priority, status, scheduled_date,
                    problem_description, remarks, technician_id, assigned_at,
                    created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (
                    payload.ac_id,
                    payload.maintenance_type,
                    payload.priority,
                    status,
                    scheduled.isoformat() if scheduled else None,
                    payload.problem_description,
                    payload.remarks,
                    technician_id,
                    _now() if technician_id else None,
                    actor_id,
                ),
            )
            created = dict(cur.fetchone())
            new_id = str(created["id"])
            cur.execute(
                """INSERT INTO maintenance_events
                   (work_order_id, actor_id, event, from_status, to_status,
                    note, created_at)
                   VALUES (%s,%s,'CREATED',NULL,%s,%s,%s)""",
                (new_id, actor_id, "SCHEDULED", None, _now()),
            )
            if technician_id:
                cur.execute(
                    """INSERT INTO maintenance_events
                       (work_order_id, actor_id, event, from_status, to_status,
                        note, created_at)
                       VALUES (%s,%s,'ASSIGNED','SCHEDULED','ASSIGNED',%s,%s)""",
                    (
                        new_id,
                        actor_id,
                        f"Assigned to technician {technician_id}",
                        _now(),
                    ),
                )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("maintenance create failed request_id=%s", getattr(g, "request_id", "-"))
        return error("MAINTENANCE_CREATE_FAILED", "Unable to create maintenance.", 500)

    audit(
        "CREATE_MAINTENANCE",
        "maintenance",
        new_id,
        None,
        {**payload.model_dump(mode="json"), "status": status},
    )
    if technician_id and technician_id != actor_id:
        _notify_rows(
            [technician_id],
            NOTIFY_TYPES["assign"],
            ACTION_TITLES["assign"],
            notification_body(created, "assign", None),
            new_id,
        )
    return success({"id": new_id, "status": status}, 201)


# ── POST /maintenance/<wo_id>/<action> (lifecycle) ────────────────────────────


def _run_action(wo_id: str, action: str):
    raw = _json_object()
    if raw is None:
        return error("VALIDATION_ERROR", "Request body must be a JSON object.", 400)

    user = g.current_user or {}
    profile_err = _require_profile(user)
    if profile_err:
        return profile_err
    actor_id = str(user.get("id") or "")
    actor_role = user.get("role")

    try:
        conn = get_db_connection()
        try:
            updated, planned = apply_transition(conn, wo_id, action, actor_id, actor_role, raw)
            recipients = resolve_recipients(conn, updated, action, actor_id)
            conn.commit()
        except WorkOrderError as exc:
            conn.rollback()
            return error(exc.code, exc.message, exc.http)
        finally:
            conn.close()
    except Exception:
        logger.exception("maintenance %s failed request_id=%s", action, getattr(g, "request_id", "-"))
        return error("MAINTENANCE_ACTION_FAILED", f"Unable to {action} the job.", 500)

    _emit_side_effects(action, updated, planned, recipients)
    return success(_serialize(updated))


@maintenance_v1_bp.route("/api/v1/maintenance/<wo_id>/assign", methods=["POST"])
@require_auth
@require_permission("ASSIGN_MAINTENANCE")
@rate_limit(120, 60, "maint-assign")
def assign_work_order(wo_id):
    return _run_action(wo_id, "assign")


@maintenance_v1_bp.route("/api/v1/maintenance/<wo_id>/accept", methods=["POST"])
@require_auth
@require_permission("MANAGE_MAINTENANCE")
@rate_limit(120, 60, "maint-accept")
def accept_work_order(wo_id):
    return _run_action(wo_id, "accept")


@maintenance_v1_bp.route("/api/v1/maintenance/<wo_id>/start", methods=["POST"])
@require_auth
@require_permission("MANAGE_MAINTENANCE")
@rate_limit(120, 60, "maint-start")
def start_work_order(wo_id):
    return _run_action(wo_id, "start")


@maintenance_v1_bp.route("/api/v1/maintenance/<wo_id>/complete", methods=["POST"])
@require_auth
@require_permission("MANAGE_MAINTENANCE")
@rate_limit(120, 60, "maint-complete")
def complete_work_order(wo_id):
    return _run_action(wo_id, "complete")


@maintenance_v1_bp.route("/api/v1/maintenance/<wo_id>/escalate", methods=["POST"])
@require_auth
@require_permission("MANAGE_MAINTENANCE")
@rate_limit(120, 60, "maint-escalate")
def escalate_work_order(wo_id):
    return _run_action(wo_id, "escalate")


@maintenance_v1_bp.route("/api/v1/maintenance/<wo_id>/cancel", methods=["POST"])
@require_auth
@require_permission("ASSIGN_MAINTENANCE")
@rate_limit(120, 60, "maint-cancel")
def cancel_work_order(wo_id):
    return _run_action(wo_id, "cancel")


# ── POST /maintenance/<wo_id>/comments ────────────────────────────────────────


@maintenance_v1_bp.route("/api/v1/maintenance/<wo_id>/comments", methods=["POST"])
@require_auth
@require_permission("MANAGE_MAINTENANCE")
@rate_limit(120, 60, "maint-comments")
def add_comment(wo_id):
    body = _json_object()
    if body is None:
        return error("VALIDATION_ERROR", "Request body must be a JSON object.", 400)
    try:
        payload = MaintenanceCommentCreate.model_validate(body)
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid comment payload.", 400, exc.errors(include_context=False))

    user = g.current_user or {}
    profile_err = _require_profile(user)
    if profile_err:
        return profile_err
    actor_id = str(user.get("id") or "")

    row = None
    where, params = "id::text = %s", [wo_id]
    scoped = _own_jobs_clause(user)
    if scoped:
        where += f" AND {scoped[0]}"
        params.extend(scoped[1])
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if not _table_exists(cur, "maintenance_comments"):
                cur.close()
                return error("NOT_SUPPORTED", "Work-order table not migrated yet.", 409)
            cur.execute(
                f"SELECT id FROM maintenance_work_orders WHERE {where}",
                params,
            )
            if cur.fetchone() is None:
                cur.close()
                return error("WORK_ORDER_NOT_FOUND", "Work order not found.", 404)
            cur.execute(
                """INSERT INTO maintenance_comments
                   (work_order_id, author_id, body, photos)
                   VALUES (%s,%s,%s,%s)
                   RETURNING id, body, photos, created_at, author_id""",
                (wo_id, actor_id, payload.body, payload.photos),
            )
            row = dict(cur.fetchone())
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("maintenance comment failed request_id=%s", getattr(g, "request_id", "-"))
        return error("COMMENT_FAILED", "Unable to add comment.", 500)
    audit("ADD_JOB_COMMENT", "maintenance", wo_id, None, {"comment_id": str(row["id"])})
    return success(_serialize(row), 201)


# ── POST /maintenance/sync (offline replay; server wins) ──────────────────────


@maintenance_v1_bp.route("/api/v1/maintenance/sync", methods=["POST"])
@require_auth
@require_permission("MANAGE_MAINTENANCE")
@rate_limit(60, 60, "maint-sync")
def sync_work_orders():
    body = _json_object()
    if body is None:
        return error("VALIDATION_ERROR", "Request body must be a JSON object.", 400)
    mutations = body.get("mutations")
    if not isinstance(mutations, list):
        return error("VALIDATION_ERROR", "mutations must be a list.", 400)
    if len(mutations) > 100:
        return error("TOO_MANY_MUTATIONS", "At most 100 mutations per sync request.", 400)

    user = g.current_user or {}
    profile_err = _require_profile(user)
    if profile_err:
        return profile_err
    actor_id = str(user.get("id") or "")
    actor_role = user.get("role")

    results: list[dict] = []
    applied: list[tuple] = []  # (action, updated, planned, recipients)
    conn = None
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            if not _table_exists(cur, "maintenance_work_orders"):
                cur.close()
                return error("NOT_SUPPORTED", "Work-order table not migrated yet.", 409)
            for idx, item in enumerate(mutations):
                entry: dict = {"index": idx}
                if not isinstance(item, dict):
                    results.append(
                        {
                            **entry,
                            "result": "rejected",
                            "code": "VALIDATION_ERROR",
                            "message": "Mutation must be a JSON object.",
                        }
                    )
                    continue
                cmid = str(item.get("client_mutation_id") or "").strip()
                action = str(item.get("action") or "").strip().lower()
                job_id = str(item.get("job_id") or "").strip()
                payload = item.get("payload") or {}
                entry.update(
                    {
                        "client_mutation_id": cmid,
                        "job_id": job_id,
                        "action": action,
                    }
                )
                if not cmid or not job_id or not action or not isinstance(payload, dict):
                    results.append(
                        {
                            **entry,
                            "result": "rejected",
                            "code": "VALIDATION_ERROR",
                            "message": "client_mutation_id, job_id and action are required.",
                        }
                    )
                    continue
                if action not in ACTIONS:
                    results.append(
                        {
                            **entry,
                            "result": "rejected",
                            "code": "UNSUPPORTED_ACTION",
                            "message": f"Unknown action '{action}'.",
                        }
                    )
                    continue
                sp = f"sync_sp_{idx}"
                cur.execute(f"SAVEPOINT {sp}")
                try:
                    cur.execute(
                        "SELECT 1 FROM maintenance_events WHERE client_mutation_id = %s",
                        (cmid,),
                    )
                    if cur.fetchone() is not None:
                        cur.execute(f"RELEASE SAVEPOINT {sp}")
                        results.append({**entry, "result": "duplicate"})
                        continue
                    updated, planned = apply_transition(
                        conn,
                        job_id,
                        action,
                        actor_id,
                        actor_role,
                        payload,
                        client_mutation_id=cmid,
                    )
                    recipients = resolve_recipients(conn, updated, action, actor_id)
                    cur.execute(f"RELEASE SAVEPOINT {sp}")
                    results.append({**entry, "result": "applied", "status": updated.get("status")})
                    applied.append((action, updated, planned, recipients))
                except WorkOrderError as exc:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    # A concurrent replay of the same mutation may have just
                    # committed its ledger row — that is a duplicate, not a
                    # domain rejection.
                    cur.execute(
                        "SELECT 1 FROM maintenance_events WHERE client_mutation_id = %s",
                        (cmid,),
                    )
                    if cur.fetchone() is not None:
                        results.append({**entry, "result": "duplicate"})
                    else:
                        results.append(
                            {
                                **entry,
                                "result": "rejected",
                                "code": exc.code,
                                "message": exc.message,
                            }
                        )
                except pg_errors.UniqueViolation:
                    # uq_me_client_mutation / uq_wo_client_mutation: the same
                    # mutation id already exists → duplicate by definition.
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    results.append({**entry, "result": "duplicate"})
                except Exception:
                    cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
                    logger.exception(
                        "sync mutation failed request_id=%s mutation=%s",
                        getattr(g, "request_id", "-"),
                        cmid,
                    )
                    results.append(
                        {
                            **entry,
                            "result": "rejected",
                            "code": "SYNC_FAILED",
                            "message": "Unexpected error applying mutation.",
                        }
                    )
            cur.close()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    except Exception:
        logger.exception("maintenance sync failed request_id=%s", getattr(g, "request_id", "-"))
        return error("SYNC_FAILED", "Unable to sync jobs.", 500)
    finally:
        if conn is not None:
            conn.close()

    for action, updated, planned, recipients in applied:
        _emit_side_effects(action, updated, planned, recipients)

    counts = {"applied": 0, "duplicate": 0, "rejected": 0}
    for r in results:
        counts[r["result"]] += 1
    return success({"results": results, **counts})


# ── PATCH /maintenance/<wo_id> (content only — status uses lifecycle) ─────────


@maintenance_v1_bp.route("/api/v1/maintenance/<wo_id>", methods=["PATCH"])
@require_auth
@require_permission("MANAGE_MAINTENANCE")
def update_work_order(wo_id):
    raw = _json_object()
    if raw is None:
        return error("VALIDATION_ERROR", "Request body must be a JSON object.", 400)
    if "status" in raw:
        return error(
            "STATUS_ACTION_REQUIRED",
            "Status changes go through the lifecycle endpoints"
            " (assign/accept/start/complete/escalate/cancel).",
            400,
        )
    try:
        payload = MaintenanceUpdate.model_validate(raw)
    except ValidationError as exc:
        return error(
            "VALIDATION_ERROR", "Invalid maintenance payload.", 400, exc.errors(include_context=False)
        )
    fields = {k: v for k, v in payload.model_dump(mode="json").items() if v is not None}
    if not fields:
        return error("NO_FIELDS", "No fields provided for update.", 400)
    # Same scope as GET: a technician may only rewrite a job the list shows
    # them. rowcount == 0 then reports 404, indistinguishable from a missing id.
    where, params = "id::text = %s", [wo_id]
    scoped = _own_jobs_clause(g.current_user or {})
    if scoped:
        where += f" AND {scoped[0]}"
        params.extend(scoped[1])
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            if not _table_exists(cur, "maintenance_work_orders"):
                cur.close()
                return error("NOT_SUPPORTED", "Work-order table not migrated yet.", 409)
            sets = ", ".join(f"{k} = %s" for k in fields)
            cur.execute(
                f"UPDATE maintenance_work_orders SET {sets}, updated_at = NOW() WHERE {where}",
                tuple(fields.values()) + tuple(params),
            )
            if cur.rowcount == 0:
                cur.close()
                return error("NOT_FOUND", "Maintenance record not found.", 404)
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("maintenance update failed request_id=%s", getattr(g, "request_id", "-"))
        return error("MAINTENANCE_UPDATE_FAILED", "Unable to update maintenance.", 500)
    audit("UPDATE_MAINTENANCE", "maintenance", wo_id, None, fields)
    return success({"id": wo_id})
