"""Best-effort notification writer (Phase 1.3).

Never breaks the business request: a lost notification is logged loudly but
must not roll back the audited action. Phase 4 (job assignment) and Phase 5
(movement decisions) call the same ``notify`` entry point — the hooks below
are thin conveniences so those phases have a stable call signature.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def notify(
    user_id: str,
    type: str,
    title: str,
    body: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    conn=None,
) -> None:
    """Insert one notification row. Uses the caller's connection when given
    (same transaction as the business write); otherwise opens its own."""
    try:
        from app.config.database import get_db_connection

        owns_conn = conn is None
        conn = conn or get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO notifications
                       (user_id, type, title, body, entity_type, entity_id)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    str(user_id),
                    type,
                    title,
                    body,
                    entity_type,
                    str(entity_id) if entity_id is not None else None,
                ),
            )
            cur.close()
            if owns_conn:
                # Pool connections roll back on a bare close() — a standalone
                # notification must commit its own tiny transaction.
                conn.commit()
        finally:
            if owns_conn:
                conn.close()
        # Phase 7: allow-listed types are mirrored to email — flag-gated
        # (EMAIL_ENABLED, default off) and best-effort, so this runs only
        # after the row is on its way to storage and can never fail the
        # business request. The send opens its own SMTP connection and is
        # deliberately outside the caller's transaction.
        try:
            from app.services.email import send_notification_email

            send_notification_email(user_id, type, title, body, entity_type, entity_id)
        except Exception:  # noqa: BLE001 - email is auxiliary by design
            logger.warning("email mirror failed user_id=%s type=%s", user_id, type, exc_info=True)
    except Exception:  # noqa: BLE001 - notifications are auxiliary
        logger.warning(
            "notification write failed user_id=%s type=%s title=%s",
            user_id,
            type,
            title,
            exc_info=True,
        )


# ── Hooks for later phases (stable call signatures) ─────────────────────────


def notify_account_approved(user_id: str, role: str, conn=None) -> None:
    notify(
        user_id,
        "ACCOUNT_APPROVED",
        "Account approved",
        f"Your account has been approved with role {role}.",
        "profile",
        user_id,
        conn,
    )


def notify_account_rejected(user_id: str, reason: str | None, conn=None) -> None:
    notify(
        user_id, "ACCOUNT_REJECTED", "Account registration rejected", reason or None, "profile", user_id, conn
    )


def notify_job_assigned(
    technician_id: str, work_order_id: str, summary: str | None = None, conn=None
) -> None:
    """Phase 4 hook: called when a work order is assigned to a technician."""
    notify(
        technician_id,
        "JOB_ASSIGNED",
        "New maintenance job assigned",
        summary,
        "maintenance_work_order",
        work_order_id,
        conn,
    )


def notify_movement_decision(requester_id: str, movement_id: str, decision: str, conn=None) -> None:
    """Phase 5 hook: called when a movement request is approved/rejected."""
    notify(
        requester_id,
        f"MOVEMENT_{decision}",
        f"Movement request {decision.lower()}",
        None,
        "asset_movement",
        movement_id,
        conn,
    )
