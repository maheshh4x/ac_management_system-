"""Movement workflow: request → approve/reject → complete (Phase 5).

Rules (plan phase-5 backend task 3):
  create    CREATE_MOVEMENT; the asset must be ACTIVE (not decommissioned);
            the destination room must differ from its current room; at most
            ONE open (PENDING/APPROVED) movement per asset — enforced by the
            partial unique index from 009, so a race loser gets 409
            MOVEMENT_OPEN instead of a duplicate.
  decide    APPROVE_MOVEMENT; approver ≠ requester (403 SELF_APPROVE); a
            FACILITY_MANAGER with assigned_blocks set may only decide
            requests for assets located in those blocks; reject requires a
            reason (400 REASON_REQUIRED).
  complete  COMPLETE_MOVEMENT; only from APPROVED; a technician may only
            complete their own request; the asset row is locked
            (SELECT … FOR UPDATE) and acs.room_id + asset_location_history +
            the COMPLETED flag are written in ONE transaction. A concurrent
            second complete waits on the lock, re-reads the status and gets
            409 INVALID_STATE — the room and the history can never disagree.
  cancel    the requester, while PENDING.

List joins asset (code/make/model), from/to room → floor → block names and
requester/approver/completer names; filters mine=1, ac_id, status, from, to;
comments are embedded (movement_comments, 009).

Notifications are best-effort AFTER commit: approvers on a new request,
the requester on approve/reject/complete. audit() records every transition.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from uuid import UUID

from flask import Blueprint, g, request
from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor
from pydantic import ValidationError

from app.auth.jwt import require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, paginated, success
from app.middleware.rate_limit import rate_limit
from app.schemas.v1_schemas import MovementCommentCreate, MovementCreate
from app.services.audit_service import audit
from app.services.notify import notify, notify_movement_decision

movements_v1_bp = Blueprint("movements_v1", __name__)

logger = logging.getLogger(__name__)

STATUSES = {"PENDING", "APPROVED", "REJECTED", "CANCELLED", "COMPLETED"}

# Legacy data may predate 009 — a read degrades gracefully, writes say so.
_SELECT_HEAD = """
    SELECT m.id, m.ac_id, m.asset_uuid, m.status, m.reason, m.rejection_reason,
           m.from_room_id, m.to_room_id,
           m.requested_by, m.requested_at,
           m.approved_by, m.approved_at,
           m.completed_by, m.completed_at, m.cancelled_at,
           a.asset_code, a.ac_make_company AS make, a.ac_model_number AS model,
           fr.room_name AS from_room_name, ff.floor_name AS from_floor_name,
           fb.block_name AS from_block_name,
           tr.room_name AS to_room_name, tf.floor_name AS to_floor_name,
           tb.block_name AS to_block_name,
           rp.name AS requested_by_name, rp.email AS requested_by_email,
           rp.role AS requested_by_role,
           ap.name AS approved_by_name,
           cp.name AS completed_by_name,
"""

_SELECT_COMMENTS = """           (SELECT COALESCE(json_agg(json_build_object(
                       'id', c.id, 'author_id', c.author_id,
                       'author_name', COALESCE(p.name, p.email),
                       'body', c.body, 'created_at', c.created_at)
                       ORDER BY c.created_at ASC), '[]'::json)
              FROM movement_comments c
              LEFT JOIN profiles p ON p.id = c.author_id
             WHERE c.movement_id = m.id) AS comments
"""

_SELECT_FROM = """      FROM asset_movements m
      LEFT JOIN acs a ON a.ac_id = m.ac_id
      LEFT JOIN rooms fr ON fr.room_id = m.from_room_id
      LEFT JOIN floors ff ON ff.floor_id = fr.floor_id
      LEFT JOIN blocks fb ON fb.block_id = ff.block_id
      LEFT JOIN rooms tr ON tr.room_id = m.to_room_id
      LEFT JOIN floors tf ON tf.floor_id = tr.floor_id
      LEFT JOIN blocks tb ON tb.block_id = tf.block_id
      LEFT JOIN profiles rp ON rp.id = m.requested_by
      LEFT JOIN profiles ap ON ap.id = m.approved_by
      LEFT JOIN profiles cp ON cp.id = m.completed_by
"""

_LIST_SELECT = _SELECT_HEAD + _SELECT_COMMENTS + _SELECT_FROM
# Pre-009 database: same projection without the comments subselect.
_LIST_SELECT_NO_COMMENTS = _SELECT_HEAD + "           NULL AS comments\n" + _SELECT_FROM


def _serialize(d: dict) -> dict:
    """JSON-safe row: dates → ISO strings, UUID → str, Decimal → float."""
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


def _location(d: dict, prefix: str) -> str | None:
    """Composed "Block / Floor / Room" display string (legacy UI format)."""
    names = [d.get(f"{prefix}_{part}_name") for part in ("block", "floor", "room")]
    joined = " / ".join(n for n in names if n)
    return joined or None


def _tables(cur) -> tuple[bool, bool]:
    """(asset_movements, movement_comments) present on this database."""
    cur.execute(
        "SELECT to_regclass('public.asset_movements') AS movements,"
        "       to_regclass('public.movement_comments') AS comments"
    )
    row = cur.fetchone()
    vals = list(row.values()) if isinstance(row, dict) else list(row)
    return vals[0] is not None, vals[1] is not None


def _require_profile(user: dict):
    """requested_by / approved_by / completed_by / author_id all reference
    profiles — reject cleanly instead of blowing up on a 500 FK violation."""
    if not user.get("profile"):
        return error(
            "PROFILE_NOT_FOUND",
            "Your user profile has not been provisioned yet.",
            409,
        )
    return None


def _actor() -> dict:
    return getattr(g, "current_user", None) or {}


def _profile_blocks(user: dict) -> list[str] | None:
    blocks = (user.get("profile") or {}).get("assigned_blocks")
    return list(blocks) if blocks else None


def _notify_approvers(mv_id: str, ac_id: str, requester_name: str, requester_id: str | None) -> None:
    """Best-effort AFTER commit: every active approver hears about the request."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM profiles"
            " WHERE role IN ('SUPER_ADMIN', 'FACILITY_MANAGER')"
            "   AND status = 'APPROVED' AND is_active"
            "   AND id <> %s",
            (requester_id or "00000000-0000-0000-0000-000000000000",),
        )
        recipients = [row[0] for row in cur.fetchall()]
        cur.close()
        for uid in recipients:
            notify(
                uid,
                "MOVEMENT_REQUESTED",
                "New movement request",
                f"{requester_name} requested to move {ac_id} to another room.",
                "asset_movement",
                mv_id,
                conn=conn,
            )
        conn.commit()
    except Exception:
        logger.warning(
            "movement approver notification failed request_id=%s",
            getattr(g, "request_id", "-"),
            exc_info=True,
        )
    finally:
        if conn is not None:
            conn.close()


# ── GET /movements (list) ────────────────────────────────────────────────────


@movements_v1_bp.route("/api/v1/movements", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(120, 60, "mov-list")
def list_movements():
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(200, max(1, int(request.args.get("page_size", 50))))
    except ValueError:
        return error("INVALID_PAGINATION", "page and page_size must be integers.", 400)

    mine = (request.args.get("mine") or "").strip().lower() in {"1", "true", "yes"}
    ac_id = (request.args.get("ac_id") or "").strip()
    status_param = (request.args.get("status") or "").strip()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()

    statuses: list[str] = []
    if status_param:
        statuses = [s.strip().upper() for s in status_param.split(",") if s.strip()]
        bad = [s for s in statuses if s not in STATUSES]
        if bad:
            return error(
                "INVALID_STATUS",
                f"Unknown status: {', '.join(bad)}.",
                400,
                {"allowed": sorted(STATUSES)},
            )
    for label, value in (("from", date_from), ("to", date_to)):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                return error("INVALID_DATE", f"{label} must be an ISO date (YYYY-MM-DD).", 400)

    user = _actor()
    where, params = [], []
    if mine:
        where.append("m.requested_by = %s")
        params.append(str(user.get("id")))
    if ac_id:
        where.append("m.ac_id = %s")
        params.append(ac_id)
    if statuses:
        where.append("m.status = ANY(%s::text[])")
        params.append(statuses)
    if date_from:
        where.append("m.requested_at >= %s::date")
        params.append(date_from)
    if date_to:
        where.append("m.requested_at < (%s::date + INTERVAL '1 day')")
        params.append(date_to)
    w = ("WHERE " + " AND ".join(where)) if where else ""

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        movements_ok, comments_ok = _tables(cur)
        if not movements_ok:
            # Pre-002 database: nothing to list (legacy behaviour).
            return paginated([], page, page_size, 0)
        cur.execute(f"SELECT COUNT(*) AS total FROM asset_movements m {w}", tuple(params))
        total = cur.fetchone()["total"]
        select = _LIST_SELECT if comments_ok else _LIST_SELECT_NO_COMMENTS
        cur.execute(
            f"{select} {w}" " ORDER BY m.requested_at DESC, m.id DESC" " LIMIT %s OFFSET %s",
            tuple(params) + (page_size, (page - 1) * page_size),
        )
        items = []
        for r in cur.fetchall():
            d = _serialize(dict(r))
            d["from_location"] = _location(d, "from")
            d["to_location"] = _location(d, "to")
            items.append(d)
        cur.close()
    except Exception:
        logger.exception("movement list failed request_id=%s", getattr(g, "request_id", "-"))
        return error("MOVEMENT_LIST_FAILED", "Unable to list movements.", 500)
    finally:
        if conn is not None:
            conn.close()
    return paginated(items, page, page_size, total)


# ── POST /movements (create) ─────────────────────────────────────────────────


@movements_v1_bp.route("/api/v1/movements", methods=["POST"])
@require_auth
@require_permission("CREATE_MOVEMENT")
@rate_limit(60, 60, "mov-create")
def create_movement():
    try:
        payload = MovementCreate(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid movement payload.", 400, exc.errors(include_context=False))
    ac_id = payload.ac_id or payload.asset_id
    if not ac_id:
        return error("VALIDATION_ERROR", "ac_id is required.", 400)
    user = _actor()
    guard = _require_profile(user)
    if guard is not None:
        return guard

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        movements_ok, _ = _tables(cur)
        if not movements_ok:
            return error("NOT_SUPPORTED", "Movement table not migrated yet. Apply 009 migration.", 409)
        cur.execute(
            "SELECT room_id, is_active, lifecycle_status FROM acs WHERE ac_id = %s",
            (ac_id,),
        )
        asset = cur.fetchone()
        if not asset:
            return error("ASSET_NOT_FOUND", "Asset not found.", 404)
        if asset["is_active"] is False or asset["lifecycle_status"] == "DECOMMISSIONED":
            return error(
                "ASSET_DECOMMISSIONED",
                "Asset is decommissioned or inactive; it cannot be moved.",
                409,
            )
        cur.execute("SELECT 1 FROM rooms WHERE room_id = %s", (payload.to_room_id,))
        if cur.fetchone() is None:
            return error(
                "INVALID_ROOM",
                "to_room_id does not reference an existing room.",
                400,
            )
        if asset["room_id"] and asset["room_id"] == payload.to_room_id:
            return error(
                "SAME_ROOM",
                "The asset is already in that room.",
                400,
                {"room_id": payload.to_room_id},
            )
        # Fast path for the 009 invariant; the unique index closes the race.
        cur.execute(
            "SELECT id, status FROM asset_movements"
            " WHERE ac_id = %s AND status IN ('PENDING', 'APPROVED')",
            (ac_id,),
        )
        open_mv = cur.fetchone()
        if open_mv:
            return error(
                "MOVEMENT_OPEN",
                "An open movement request already exists for this asset.",
                409,
                {"movement_id": str(open_mv["id"]), "status": open_mv["status"]},
            )
        cur.execute(
            "INSERT INTO asset_movements"
            " (ac_id, from_room_id, to_room_id, requested_by, reason, status)"
            " VALUES (%s, %s, %s, %s, %s, 'PENDING') RETURNING id",
            (
                ac_id,
                asset["room_id"],
                payload.to_room_id,
                str(user.get("id")),
                payload.reason,
            ),
        )
        new_id = str(cur.fetchone()["id"])
        conn.commit()
    except pg_errors.UniqueViolation:
        # Lost the race against a concurrent create: honour the invariant.
        if conn is not None:
            conn.rollback()
            try:
                cur = conn.cursor(cursor_factory=RealDictCursor)
                cur.execute(
                    "SELECT id, status FROM asset_movements"
                    " WHERE ac_id = %s AND status IN ('PENDING', 'APPROVED')",
                    (ac_id,),
                )
                open_mv = cur.fetchone()
            except Exception:
                open_mv = None
                logger.exception("movements open lookup failed after race")
        details = {"movement_id": str(open_mv["id"])} if open_mv else None
        return error(
            "MOVEMENT_OPEN",
            "An open movement request already exists for this asset.",
            409,
            details,
        )
    except Exception:
        logger.exception("movement create failed request_id=%s", getattr(g, "request_id", "-"))
        return error("MOVEMENT_CREATE_FAILED", "Unable to create movement request.", 500)
    finally:
        if conn is not None:
            conn.close()

    profile = user.get("profile") or {}
    requester_name = profile.get("name") or user.get("email") or "A user"
    audit(
        "CREATE_MOVEMENT",
        "movement",
        new_id,
        None,
        {"ac_id": ac_id, "to_room_id": payload.to_room_id},
    )
    _notify_approvers(new_id, ac_id, requester_name, str(user.get("id")))
    return success({"id": new_id, "status": "PENDING"}, 201)


# ── approve / reject (decision) ──────────────────────────────────────────────


def _decide(mv_id: str, action: str):
    """Approve or reject a PENDING request. Returns (response, status)."""
    body = request.get_json(silent=True) or {}
    rejection_reason = None
    if action == "reject":
        rejection_reason = ((body.get("rejection_reason") or body.get("reason")) or "").strip()
        if not rejection_reason:
            return error("REASON_REQUIRED", "A rejection reason is required.", 400)

    user = _actor()
    actor_id = str(user.get("id"))
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT m.id, m.status, m.requested_by, m.from_room_id,"
            "       fb.block_id AS from_block_id"
            "  FROM asset_movements m"
            "  LEFT JOIN rooms fr ON fr.room_id = m.from_room_id"
            "  LEFT JOIN floors ff ON ff.floor_id = fr.floor_id"
            "  LEFT JOIN blocks fb ON fb.block_id = ff.block_id"
            " WHERE m.id::text = %s"
            " FOR UPDATE OF m",
            (mv_id,),
        )
        mv = cur.fetchone()
        if not mv:
            return error("NOT_FOUND", "Movement not found.", 404)
        if mv["status"] != "PENDING":
            return error(
                "INVALID_STATE",
                f"Movement is {mv['status']}; only a PENDING request can be decided.",
                409,
                {"status": mv["status"]},
            )
        if str(mv["requested_by"] or "") == actor_id:
            return error(
                "SELF_APPROVE",
                "You cannot decide your own movement request.",
                403,
            )
        blocks = _profile_blocks(user)
        if user.get("role") == "FACILITY_MANAGER" and blocks:
            from_block = mv["from_block_id"]
            if from_block is None or from_block not in set(blocks):
                return error(
                    "FORBIDDEN",
                    "You can only decide movement requests for assets in your assigned blocks.",
                    403,
                    {"assigned_blocks": blocks},
                )
        if action == "approve":
            cur.execute(
                "UPDATE asset_movements"
                " SET status='APPROVED', approved_by=%s, approved_at=NOW(),"
                "     updated_at=NOW()"
                " WHERE id::text=%s",
                (actor_id, mv_id),
            )
            new_status = "APPROVED"
        else:
            cur.execute(
                "UPDATE asset_movements"
                " SET status='REJECTED', approved_by=%s, approved_at=NOW(),"
                "     rejection_reason=%s, updated_at=NOW()"
                " WHERE id::text=%s",
                (actor_id, rejection_reason, mv_id),
            )
            new_status = "REJECTED"
        conn.commit()
    except Exception:
        logger.exception("movement %s failed request_id=%s", action, getattr(g, "request_id", "-"))
        return error("MOVEMENT_TRANSITION_FAILED", f"Unable to {action} movement.", 500)
    finally:
        if conn is not None:
            conn.close()

    audit(
        "APPROVED_MOVEMENT" if action == "approve" else "REJECTED_MOVEMENT",
        "movement",
        mv_id,
        {"status": "PENDING"},
        {"status": new_status},
    )
    requester = mv["requested_by"]
    if requester is not None:
        notify_movement_decision(str(requester), mv_id, "APPROVED" if action == "approve" else "REJECTED")
    return success({"id": mv_id, "status": new_status})


@movements_v1_bp.route("/api/v1/movements/<mv_id>/approve", methods=["POST"])
@require_auth
@require_permission("APPROVE_MOVEMENT")
@rate_limit(120, 60, "mov-decide")
def approve(mv_id):
    return _decide(mv_id, "approve")


@movements_v1_bp.route("/api/v1/movements/<mv_id>/reject", methods=["POST"])
@require_auth
@require_permission("APPROVE_MOVEMENT")
@rate_limit(120, 60, "mov-decide")
def reject(mv_id):
    return _decide(mv_id, "reject")


# ── POST /movements/<id>/complete ────────────────────────────────────────────


@movements_v1_bp.route("/api/v1/movements/<mv_id>/complete", methods=["POST"])
@require_auth
@require_permission("COMPLETE_MOVEMENT")
@rate_limit(120, 60, "mov-complete")
def complete(mv_id):
    user = _actor()
    actor_id = str(user.get("id"))
    role = user.get("role")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT id, ac_id, status, to_room_id, requested_by" "  FROM asset_movements WHERE id::text = %s",
            (mv_id,),
        )
        mv = cur.fetchone()
        if not mv:
            return error("NOT_FOUND", "Movement not found.", 404)
        if mv["status"] != "APPROVED":
            return error(
                "INVALID_STATE",
                f"Movement is {mv['status']}; only an APPROVED movement can be completed.",
                409,
                {"status": mv["status"]},
            )
        if role == "TECHNICIAN" and str(mv["requested_by"] or "") != actor_id:
            return error(
                "FORBIDDEN",
                "Only the technician who requested the movement can complete it.",
                403,
            )
        if not mv["ac_id"]:
            return error("ASSET_NOT_FOUND", "The asset no longer exists.", 404)

        # 1) Lock the asset row: concurrent completes and room changes for
        #    this asset serialize here (lock is held until commit/rollback).
        cur.execute(
            "SELECT room_id FROM acs WHERE ac_id = %s FOR UPDATE",
            (mv["ac_id"],),
        )
        asset = cur.fetchone()
        if not asset:
            return error("ASSET_NOT_FOUND", "The asset no longer exists.", 404)

        # 2) Re-read AFTER the lock: whoever held it committed/rolled back,
        #    so a double-complete loses here with 409 instead of duplicating
        #    the history row.
        cur.execute(
            "SELECT status FROM asset_movements WHERE id::text = %s",
            (mv_id,),
        )
        status_now = cur.fetchone()["status"]
        if status_now != "APPROVED":
            return error(
                "INVALID_STATE",
                f"Movement is {status_now}; it was decided while completing.",
                409,
                {"status": status_now},
            )

        # 3) One transaction: history row + room change + COMPLETED flag.
        cur.execute(
            "INSERT INTO asset_location_history"
            " (ac_id, from_room_id, to_room_id, movement_id)"
            " VALUES (%s, %s, %s, %s)",
            (mv["ac_id"], asset["room_id"], mv["to_room_id"], mv_id),
        )
        cur.execute(
            "UPDATE acs SET room_id=%s, updated_at=NOW() WHERE ac_id=%s",
            (mv["to_room_id"], mv["ac_id"]),
        )
        cur.execute(
            "UPDATE asset_movements SET status='COMPLETED', completed_by=%s,"
            " completed_at=NOW(), updated_at=NOW()"
            " WHERE id::text=%s",
            (actor_id, mv_id),
        )
        conn.commit()
    except Exception:
        logger.exception("movement complete failed request_id=%s", getattr(g, "request_id", "-"))
        return error("MOVEMENT_TRANSITION_FAILED", "Unable to complete movement.", 500)
    finally:
        if conn is not None:
            conn.close()

    audit(
        "COMPLETE_MOVEMENT",
        "movement",
        mv_id,
        {"status": "APPROVED"},
        {"status": "COMPLETED", "room_id": mv["to_room_id"]},
    )
    requester = mv["requested_by"]
    if requester is not None and str(requester) != actor_id:
        notify_movement_decision(str(requester), mv_id, "COMPLETED")
    return success({"id": mv_id, "status": "COMPLETED", "room_id": mv["to_room_id"]})


# ── POST /movements/<id>/cancel ──────────────────────────────────────────────


@movements_v1_bp.route("/api/v1/movements/<mv_id>/cancel", methods=["POST"])
@require_auth
@rate_limit(120, 60, "mov-decide")
def cancel(mv_id):
    user = _actor()
    actor_id = str(user.get("id"))
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(
            "SELECT id, status, requested_by FROM asset_movements WHERE id::text = %s",
            (mv_id,),
        )
        mv = cur.fetchone()
        if not mv:
            return error("NOT_FOUND", "Movement not found.", 404)
        if str(mv["requested_by"] or "") != actor_id:
            return error(
                "FORBIDDEN",
                "Only the requester can cancel a movement request.",
                403,
            )
        if mv["status"] != "PENDING":
            return error(
                "INVALID_STATE",
                f"Movement is {mv['status']}; only a PENDING request can be cancelled.",
                409,
                {"status": mv["status"]},
            )
        cur.execute(
            "UPDATE asset_movements"
            " SET status='CANCELLED', cancelled_at=NOW(), updated_at=NOW()"
            " WHERE id::text=%s",
            (mv_id,),
        )
        conn.commit()
    except Exception:
        logger.exception("movement cancel failed request_id=%s", getattr(g, "request_id", "-"))
        return error("MOVEMENT_TRANSITION_FAILED", "Unable to cancel movement.", 500)
    finally:
        if conn is not None:
            conn.close()

    audit("CANCELLED_MOVEMENT", "movement", mv_id, {"status": "PENDING"}, {"status": "CANCELLED"})
    return success({"id": mv_id, "status": "CANCELLED"})


# ── POST /movements/<id>/comments ────────────────────────────────────────────


@movements_v1_bp.route("/api/v1/movements/<mv_id>/comments", methods=["POST"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(60, 60, "mov-comments")
def add_comment(mv_id):
    try:
        payload = MovementCommentCreate(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid comment payload.", 400, exc.errors(include_context=False))
    user = _actor()
    guard = _require_profile(user)
    if guard is not None:
        return guard
    author_id = str(user.get("id"))

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        movements_ok, comments_ok = _tables(cur)
        if not movements_ok or not comments_ok:
            return error("NOT_SUPPORTED", "Movement tables not migrated yet. Apply 009 migration.", 409)
        cur.execute(
            "SELECT id, status FROM asset_movements WHERE id::text = %s",
            (mv_id,),
        )
        mv = cur.fetchone()
        if not mv:
            return error("NOT_FOUND", "Movement not found.", 404)
        if mv["status"] != "PENDING":
            return error(
                "INVALID_STATE",
                f"Movement is {mv['status']}; comments are only accepted while the request is pending.",
                409,
                {"status": mv["status"]},
            )
        cur.execute(
            "INSERT INTO movement_comments (movement_id, author_id, body)"
            " VALUES (%s, %s, %s) RETURNING id, created_at",
            (mv_id, author_id, payload.body),
        )
        created = cur.fetchone()
        conn.commit()
    except Exception:
        logger.exception("movement comment failed request_id=%s", getattr(g, "request_id", "-"))
        return error("MOVEMENT_COMMENT_FAILED", "Unable to add comment.", 500)
    finally:
        if conn is not None:
            conn.close()

    profile = user.get("profile") or {}
    return success(
        {
            "id": str(created["id"]),
            "movement_id": mv_id,
            "author_id": author_id,
            "author_name": profile.get("name") or user.get("email"),
            "body": payload.body,
            "created_at": (
                created["created_at"].isoformat()
                if hasattr(created["created_at"], "isoformat")
                else created["created_at"]
            ),
        },
        201,
    )
