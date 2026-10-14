"""Users, approval queue, notifications (Phase 1.3).

Identity model (decision D3): Supabase Auth owns credentials; the
``profiles`` table owns role + approval status. New signups land as
role=VIEWER, status=PENDING (DB trigger or the register-profile fallback
below, both idempotent). Approver rules:

  * SUPER_ADMIN may approve/reject/patch anyone (except approving self).
  * FACILITY_MANAGER may approve only TECHNICIAN/VIEWER requests; when both
    sides have assigned_blocks, the sets must overlap.
  * Nobody can approve themselves; the last SUPER_ADMIN cannot be demoted
    or deactivated; a user cannot change their own role/is_active.

All mutations are audited and notify the affected user.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timezone

from flask import Blueprint, g, request
from psycopg2.extras import RealDictCursor
from pydantic import ValidationError

from app.auth.jwt import get_current_user, require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, paginated, success
from app.schemas.v1_schemas import (
    PROFILE_STATUSES,
    ApprovalDecision,
    ProfilePatch,
    RegisterProfile,
    RejectionDecision,
    UserAdminPatch,
)
from app.services import notify as notify_mod
from app.services.audit_service import audit

logger = logging.getLogger(__name__)

users_v1_bp = Blueprint("users_v1", __name__)

_PROFILE_COLUMNS = (
    "id, email, name, role, status, requested_role, phone, address, staff_id,"
    " designation, assigned_blocks, years_experience, certification_no,"
    " reporting_manager_id, college_id, department_id, is_active,"
    " approved_by, approved_at, rejection_reason, created_at, updated_at"
)

_PROFILE_KEYS = [c.strip() for c in _PROFILE_COLUMNS.split(",")]

# What a manager may grant (no MANAGE_USERS): subordinate roles only.
_MANAGER_GRANTABLE_ROLES = {"TECHNICIAN", "VIEWER"}


def _serialize_profile(row: dict) -> dict:
    out = dict(row)
    for k, v in list(out.items()):
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif type(v).__name__ == "UUID":
            out[k] = str(v)
        elif isinstance(v, (list, tuple)):
            out[k] = list(v)
    return out


def _fetch_profile(cur, profile_id: str):
    cur.execute(f"SELECT {_PROFILE_COLUMNS} FROM profiles WHERE id = %s", (str(profile_id),))
    row = cur.fetchone()
    if row is None:
        return None
    return _serialize_profile(dict(row))


def evaluate_approval(actor_role, actor_blocks, actor_id, target) -> tuple[bool, str | None]:
    """Pure approver-scoping rules. Returns (allowed, error_code)."""
    if str(actor_id) == str(target.get("id")):
        return False, "SELF_APPROVAL"
    if actor_role == "SUPER_ADMIN":
        return True, None
    if actor_role == "FACILITY_MANAGER":
        requested = target.get("requested_role") or "VIEWER"
        if requested not in _MANAGER_GRANTABLE_ROLES:
            return False, "ROLE_OUT_OF_SCOPE"
        if actor_blocks and target.get("assigned_blocks"):
            if not set(actor_blocks) & set(target["assigned_blocks"]):
                return False, "BLOCK_OUT_OF_SCOPE"
        return True, None
    return False, "FORBIDDEN"


def _me_payload(user: dict) -> dict:
    profile = user.get("profile") or {}
    out = _serialize_profile(profile) if profile else {}
    out.setdefault("id", user.get("id"))
    out.setdefault("email", user.get("email"))
    out.setdefault("role", user.get("role"))
    out.setdefault("status", profile.get("status") or "APPROVED")
    return {k: out.get(k) for k in _PROFILE_KEYS if k in out or out.get(k) is not None} or out


# ── current user ────────────────────────────────────────────────────────────


@users_v1_bp.route("/api/v1/me", methods=["GET"])
@require_auth
def get_me():
    return success(_me_payload(get_current_user()))


@users_v1_bp.route("/api/v1/me", methods=["PATCH"])
@require_auth
def patch_me():
    user = get_current_user()
    if user.get("role") == "VIEWER":
        return error("FORBIDDEN", "You do not have permission to perform this action.", 403)
    try:
        payload = ProfilePatch.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid request payload.", 400, exc.errors())

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return error("VALIDATION_ERROR", "No updatable fields supplied.", 400)

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values())
    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"UPDATE profiles SET {set_clause}, updated_at = NOW() WHERE id = %s"
                f" RETURNING {_PROFILE_COLUMNS}",
                (*values, user["id"]),
            )
            row = cur.fetchone()
        if row is None:
            return error("NOT_FOUND", "Profile not found.", 404)
        audit("PROFILE_UPDATE", "profile", user["id"], new_values=fields)
        return success(_serialize_profile(dict(row)))
    except Exception:  # noqa: BLE001
        logger.exception("profile update failed request_id=%s", getattr(g, "request_id", "-"))
        return error("PROFILE_UPDATE_FAILED", "Unable to update profile.", 500)


# ── signup self-registration (fallback when the auth trigger is absent) ─────


@users_v1_bp.route("/api/v1/auth/register-profile", methods=["POST"])
@require_auth
def register_profile():
    """Idempotent: creates the profiles row for the JWT subject if missing.

    Automatic onboarding: a verified identity (Supabase Auth owns credentials
    and only verified sessions reach here) is granted the least-privileged
    role VIEWER with status APPROVED immediately — no routine Super Admin
    approval step. The client can only *request* a non-privileged role, never
    grant one. A legacy PENDING row that was never reviewed (no approver, no
    approval timestamp) is completed the same way when its owner calls this
    endpoint; REJECTED rows and reviewed rows are returned untouched.
    """
    user = get_current_user()
    try:
        payload = RegisterProfile.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid request payload.", 400, exc.errors())

    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            row = _fetch_profile(cur, user["id"])
            if row is not None:
                if (
                    row.get("status") == "PENDING"
                    and row.get("approved_by") is None
                    and row.get("approved_at") is None
                ):
                    # Compatibility: accounts created before automatic
                    # onboarding (DB trigger / older register-profile) that no
                    # approver ever reviewed are completed on next contact.
                    cur.execute(
                        """UPDATE profiles SET status = 'APPROVED', approved_at = NOW(),
                               updated_at = NOW()
                            WHERE id = %s""",
                        (user["id"],),
                    )
                    row = _fetch_profile(cur, user["id"])
                    audit(
                        "USER_APPROVED",
                        "profile",
                        user["id"],
                        old_values={"status": "PENDING"},
                        new_values={"status": "APPROVED", "mode": "automatic-onboarding"},
                    )
                return success(row)
            cur.execute(
                """INSERT INTO profiles
                       (id, email, name, role, status, approved_at, requested_role,
                        staff_id, phone, designation, reporting_manager_id)
                   VALUES (%s, %s, %s, 'VIEWER', 'APPROVED', NOW(), %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (
                    user["id"],
                    user.get("email"),
                    payload.name,
                    payload.requested_role,
                    payload.staff_id,
                    payload.phone,
                    payload.designation,
                    str(payload.reporting_manager_id) if payload.reporting_manager_id else None,
                ),
            )
            row = _fetch_profile(cur, user["id"])
        audit(
            "PROFILE_REGISTERED",
            "profile",
            user["id"],
            None,
            {"role": "VIEWER", "status": "APPROVED", "mode": "automatic-onboarding"},
        )
        return success(row, 201)
    except Exception:  # noqa: BLE001
        logger.exception("register-profile failed request_id=%s", getattr(g, "request_id", "-"))
        return error("PROFILE_REGISTER_FAILED", "Unable to register profile.", 500)


# ── approval queue ──────────────────────────────────────────────────────────


@users_v1_bp.route("/api/v1/approvals", methods=["GET"])
@require_auth
@require_permission("APPROVE_USERS", "MANAGE_USERS")
def list_approvals():
    status = (request.args.get("status") or "PENDING").upper()
    if status not in PROFILE_STATUSES:
        return error("VALIDATION_ERROR", "status must be PENDING, APPROVED or REJECTED.", 400)
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(200, max(1, int(request.args.get("page_size", 50))))
    except ValueError:
        return error("INVALID_PAGINATION", "page and page_size must be integers.", 400)

    user = get_current_user()
    actor_blocks = (user.get("profile") or {}).get("assigned_blocks")
    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            where = "WHERE status = %s"
            params: list = [status]
            if user.get("role") == "FACILITY_MANAGER":
                where += " AND COALESCE(requested_role, 'VIEWER') IN ('TECHNICIAN','VIEWER')"
                if actor_blocks:
                    where += " AND (assigned_blocks IS NULL OR assigned_blocks && %s)"
                    params.append(list(actor_blocks))
            cur.execute(f"SELECT COUNT(*) AS total FROM profiles {where}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT {_PROFILE_COLUMNS} FROM profiles {where}"
                " ORDER BY created_at ASC LIMIT %s OFFSET %s",
                (*params, page_size, (page - 1) * page_size),
            )
            items = [_serialize_profile(dict(r)) for r in cur.fetchall()]
        return paginated(items, page, page_size, total)
    except Exception:  # noqa: BLE001
        logger.exception("approvals list failed request_id=%s", getattr(g, "request_id", "-"))
        return error("APPROVALS_LIST_FAILED", "Unable to list approvals.", 500)


@users_v1_bp.route("/api/v1/approvals/<profile_id>/approve", methods=["POST"])
@require_auth
@require_permission("APPROVE_USERS", "MANAGE_USERS")
def approve_user(profile_id):
    user = get_current_user()
    try:
        payload = ApprovalDecision.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid request payload.", 400, exc.errors())

    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            target = _fetch_profile(cur, profile_id)
            if target is None:
                return error("NOT_FOUND", "Profile not found.", 404)

            actor_blocks = (user.get("profile") or {}).get("assigned_blocks")
            allowed, reason_code = evaluate_approval(user.get("role"), actor_blocks, user.get("id"), target)
            if not allowed:
                msg = (
                    "You cannot approve your own account."
                    if reason_code == "SELF_APPROVAL"
                    else "You can only approve technician or viewer accounts" " within your assigned blocks."
                )
                return error("FORBIDDEN", msg, 403)

            if user.get("role") == "FACILITY_MANAGER":
                # Managers can only ever grant the scoped subordinate roles.
                final_role = target.get("requested_role") or "VIEWER"
            else:
                final_role = payload.role or target.get("requested_role") or "VIEWER"

            if target.get("status") == "APPROVED" and final_role == target.get("role"):
                # Nothing to grant: already approved with the role that would
                # be granted. A different requested/payload role falls through
                # to the role-elevation update below (still audited).
                return error("ALREADY_APPROVED", "This account is already approved.", 409)

            cur.execute(
                """UPDATE profiles SET role = %s, status = 'APPROVED',
                       approved_by = %s, approved_at = NOW(), rejection_reason = NULL,
                       updated_at = NOW()
                   WHERE id = %s""",
                (final_role, user["id"], profile_id),
            )
            notify_mod.notify_account_approved(target["id"], final_role, conn)
        audit(
            "USER_APPROVED",
            "profile",
            profile_id,
            old_values={"status": target.get("status"), "role": target.get("role")},
            new_values={"status": "APPROVED", "role": final_role},
        )
        return success({"id": profile_id, "status": "APPROVED", "role": final_role})
    except Exception:  # noqa: BLE001
        logger.exception("approve failed request_id=%s", getattr(g, "request_id", "-"))
        return error("APPROVE_FAILED", "Unable to approve account.", 500)


@users_v1_bp.route("/api/v1/approvals/<profile_id>/reject", methods=["POST"])
@require_auth
@require_permission("APPROVE_USERS", "MANAGE_USERS")
def reject_user(profile_id):
    user = get_current_user()
    try:
        payload = RejectionDecision.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "A rejection reason is required.", 400, exc.errors())

    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            target = _fetch_profile(cur, profile_id)
            if target is None:
                return error("NOT_FOUND", "Profile not found.", 404)
            if str(target.get("id")) == str(user.get("id")):
                return error("FORBIDDEN", "You cannot reject your own account.", 403)
            actor_blocks = (user.get("profile") or {}).get("assigned_blocks")
            allowed, reason_code = evaluate_approval(user.get("role"), actor_blocks, user.get("id"), target)
            if not allowed and reason_code != "SELF_APPROVAL":
                return error(
                    "FORBIDDEN",
                    "You can only reject technician or viewer accounts" " within your assigned blocks.",
                    403,
                )
            cur.execute(
                """UPDATE profiles SET status = 'REJECTED', rejection_reason = %s,
                       updated_at = NOW()
                   WHERE id = %s""",
                (payload.reason, profile_id),
            )
            notify_mod.notify_account_rejected(target["id"], payload.reason, conn)
        audit(
            "USER_REJECTED",
            "profile",
            profile_id,
            old_values={"status": target.get("status")},
            new_values={"status": "REJECTED", "reason": payload.reason},
        )
        return success({"id": profile_id, "status": "REJECTED"})
    except Exception:  # noqa: BLE001
        logger.exception("reject failed request_id=%s", getattr(g, "request_id", "-"))
        return error("REJECT_FAILED", "Unable to reject account.", 500)


# ── user administration ─────────────────────────────────────────────────────


@users_v1_bp.route("/api/v1/users/assignable-technicians", methods=["GET"])
@require_auth
@require_permission("ASSIGN_MAINTENANCE")
def assignable_technicians():
    """Technician picker for the assignment workflow. The full user list
    needs MANAGE_USERS (SUPER_ADMIN only), which would leave FACILITY_MANAGER
    holders — who may assign jobs — with an empty dropdown. This narrow,
    least-privilege listing returns only active, approved TECHNICIAN profiles
    (id + display fields), nothing else."""
    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, name, email FROM profiles"
                " WHERE role = 'TECHNICIAN' AND is_active IS NOT FALSE AND status = 'APPROVED'"
                " ORDER BY COALESCE(NULLIF(name, ''), email) ASC LIMIT 200"
            )
            items = [
                {"id": str(r["id"]), "name": r["name"], "email": r["email"]} for r in cur.fetchall()
            ]
        return success({"technicians": items, "total": len(items)})
    except Exception:  # noqa: BLE001
        logger.exception("assignable technicians failed request_id=%s", getattr(g, "request_id", "-"))
        return error("TECHNICIANS_LIST_FAILED", "Unable to list technicians.", 500)


@users_v1_bp.route("/api/v1/users", methods=["GET"])
@require_auth
@require_permission("MANAGE_USERS")
def list_users():
    role = (request.args.get("role") or "").upper() or None
    status = (request.args.get("status") or "").upper() or None
    search = (request.args.get("search") or "").strip()
    if role and role not in {"SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER"}:
        return error("VALIDATION_ERROR", "Unknown role filter.", 400)
    if status and status not in PROFILE_STATUSES:
        return error("VALIDATION_ERROR", "Unknown status filter.", 400)
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(200, max(1, int(request.args.get("page_size", 50))))
    except ValueError:
        return error("INVALID_PAGINATION", "page and page_size must be integers.", 400)

    where, params = [], []
    if role:
        where.append("role = %s")
        params.append(role)
    if status:
        where.append("status = %s")
        params.append(status)
    if search:
        where.append("(name ILIKE %s OR email ILIKE %s OR staff_id ILIKE %s)")
        like = f"%{search}%"
        params.extend([like, like, like])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"SELECT COUNT(*) AS total FROM profiles {where_sql}", params)
            total = cur.fetchone()["total"]
            cur.execute(
                f"SELECT {_PROFILE_COLUMNS} FROM profiles {where_sql}"
                " ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (*params, page_size, (page - 1) * page_size),
            )
            items = [_serialize_profile(dict(r)) for r in cur.fetchall()]
        return paginated(items, page, page_size, total)
    except Exception:  # noqa: BLE001
        logger.exception("users list failed request_id=%s", getattr(g, "request_id", "-"))
        return error("USERS_LIST_FAILED", "Unable to list users.", 500)


@users_v1_bp.route("/api/v1/users/<profile_id>", methods=["PATCH"])
@require_auth
@require_permission("MANAGE_USERS")
def patch_user(profile_id):
    user = get_current_user()
    try:
        payload = UserAdminPatch.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid request payload.", 400, exc.errors())

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return error("VALIDATION_ERROR", "No updatable fields supplied.", 400)

    if str(user.get("id")) == str(profile_id) and ("role" in changes or "is_active" in changes):
        return error("FORBIDDEN", "You cannot change your own role or active state.", 403)

    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            target = _fetch_profile(cur, profile_id)
            if target is None:
                return error("NOT_FOUND", "Profile not found.", 404)

            demoting_admin = (
                changes.get("role") is not None and changes["role"] != "SUPER_ADMIN"
            ) or changes.get("is_active") is False
            if demoting_admin and target.get("role") == "SUPER_ADMIN":
                cur.execute(
                    "SELECT COUNT(*) AS n FROM profiles"
                    " WHERE role = 'SUPER_ADMIN' AND is_active AND status = 'APPROVED'"
                )
                if cur.fetchone()["n"] <= 1:
                    return error(
                        "LAST_SUPER_ADMIN",
                        "The last active super admin cannot be demoted or deactivated.",
                        409,
                    )

            set_clause = ", ".join(f"{k} = %s" for k in changes)
            values = [str(v) if type(v).__name__ == "UUID" else v for v in changes.values()]
            cur.execute(
                f"UPDATE profiles SET {set_clause}, updated_at = NOW() WHERE id = %s",
                (*values, profile_id),
            )
            if changes.get("role") and changes["role"] != target.get("role"):
                notify_mod.notify(
                    target["id"],
                    "ROLE_CHANGED",
                    "Your role was changed",
                    f"Your role is now {changes['role']}.",
                    "profile",
                    target["id"],
                    conn,
                )
        audit(
            "USER_UPDATED",
            "profile",
            profile_id,
            old_values={k: target.get(k) for k in changes},
            new_values=changes,
        )
        return success({"id": profile_id, **changes})
    except Exception:  # noqa: BLE001
        logger.exception("user patch failed request_id=%s", getattr(g, "request_id", "-"))
        return error("USER_UPDATE_FAILED", "Unable to update user.", 500)


# ── notifications ───────────────────────────────────────────────────────────


@users_v1_bp.route("/api/v1/notifications", methods=["GET"])
@require_auth
def list_notifications():
    user = get_current_user()
    unread_only = (request.args.get("unread") or "").lower() in {"1", "true", "yes"}
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return error("INVALID_PAGINATION", "page and page_size must be integers.", 400)

    # Phase 7: ?since= (ISO date or datetime) lets the 60 s FE poll fetch only
    # what is newer than the last seen notification instead of the first page.
    since_raw = (request.args.get("since") or "").strip()
    since: datetime | None = None
    if since_raw:
        # Clients that skip URL-encoding turn "…+00:00" into "… 00:00"; a bare
        # space is also a legal date/time separator. Repair both before parsing.
        candidate = re.sub(r"(?<=\d) (?=\d{2}:\d{2}$)", "+", since_raw)
        candidate = candidate.replace(" ", "T")
        try:
            since = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        except ValueError:
            try:
                since = datetime.combine(date.fromisoformat(candidate), time.min)
            except ValueError:
                return error("INVALID_DATE", "since must be an ISO date or datetime.", 400)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    where = "WHERE user_id = %s"
    params: list = [user["id"]]
    if unread_only:
        where += " AND is_read = FALSE"
    if since is not None:
        where += " AND created_at > %s"
        params.append(since)
    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT COUNT(*) AS total,"
                " COUNT(*) FILTER (WHERE NOT is_read) AS unread"
                " FROM notifications WHERE user_id = %s",
                (user["id"],),
            )
            counts = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) AS total" f" FROM notifications {where}",
                tuple(params),
            )
            filtered_total = cur.fetchone()["total"]
            cur.execute(
                "SELECT id, type, title, body, entity_type, entity_id, is_read, created_at"
                f" FROM notifications {where} ORDER BY created_at DESC LIMIT %s OFFSET %s",
                (*params, page_size, (page - 1) * page_size),
            )
            items = []
            for r in cur.fetchall():
                d = dict(r)
                d["id"] = str(d["id"])
                if hasattr(d.get("created_at"), "isoformat"):
                    d["created_at"] = d["created_at"].isoformat()
                items.append(d)
        return success(
            {
                "items": items,
                "unread_count": counts["unread"],
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": filtered_total,
                    "total_pages": max(1, -(-filtered_total // page_size)),
                },
            }
        )
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "UndefinedTable":
            return success(
                {
                    "items": [],
                    "unread_count": 0,
                    "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 1},
                }
            )
        logger.exception("notifications list failed request_id=%s", getattr(g, "request_id", "-"))
        return error("NOTIFICATIONS_FAILED", "Unable to list notifications.", 500)


@users_v1_bp.route("/api/v1/notifications/<notification_id>/read", methods=["POST"])
@require_auth
def mark_notification_read(notification_id):
    user = get_current_user()
    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET is_read = TRUE" " WHERE id = %s AND user_id = %s",
                (notification_id, user["id"]),
            )
            if cur.rowcount == 0:
                return error("NOT_FOUND", "Notification not found.", 404)
        return success({"id": notification_id, "is_read": True})
    except Exception:  # noqa: BLE001
        logger.exception("notification read failed request_id=%s", getattr(g, "request_id", "-"))
        return error("NOTIFICATIONS_FAILED", "Unable to update notification.", 500)


@users_v1_bp.route("/api/v1/notifications/read-all", methods=["POST"])
@require_auth
def mark_all_notifications_read():
    user = get_current_user()
    try:
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE notifications SET is_read = TRUE WHERE user_id = %s AND NOT is_read",
                (user["id"],),
            )
            updated = cur.rowcount
        return success({"updated": updated})
    except Exception:  # noqa: BLE001
        logger.exception("notifications read-all failed request_id=%s", getattr(g, "request_id", "-"))
        return error("NOTIFICATIONS_FAILED", "Unable to update notifications.", 500)
