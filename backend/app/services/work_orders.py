"""Table-driven work-order state machine (Phase 4, plan "maintenance jobs").

Two layers:

* ``compute_changes(wo, action, actor_id, actor_role, payload) -> Planned`` —
  pure (no I/O): validates the action against the current row and returns the
  column updates, AC date updates and the timeline event to persist. Raises
  :class:`WorkOrderError(code, message, http)` on any rule violation.
* ``apply_transition(conn, ...)`` — DB layer: locks the row (``FOR UPDATE``),
  calls the pure layer, writes the row update + ``maintenance_events`` row in
  the same transaction, and returns the updated row.

State machine (status column):

    SCHEDULED →(assign)→ ASSIGNED →(accept)→ ASSIGNED* →(start)→ IN_PROGRESS
        →(complete)→ COMPLETED        (terminal)
    SCHEDULED/ASSIGNED/IN_PROGRESS →(cancel)→ CANCELLED   (terminal)
    ASSIGNED/IN_PROGRESS →(escalate)→ same status, priority+1

Authorization is evaluated here as well as at the endpoint (defence in depth,
and ``POST /maintenance/sync`` only checks MANAGE_MAINTENANCE once for the
batch — each mutation must still enforce assign/cancel = ASSIGN_MAINTENANCE
and owner-or-manager for accept/start/complete/escalate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from psycopg2.extras import RealDictCursor

from app.auth.permissions import has_permission
from app.core.clock import utcnow

STATUSES = ("SCHEDULED", "ASSIGNED", "IN_PROGRESS", "COMPLETED", "CANCELLED")
TERMINAL_STATUSES = frozenset({"COMPLETED", "CANCELLED"})
PRIORITY_LADDER = ("LOW", "MEDIUM", "HIGH", "CRITICAL")

# notification types per action (see acms-plan 03-frontend-contract? no —
# Phase 4 decision log): assign→JOB_ASSIGNED, accept→JOB_ACCEPTED, …
NOTIFY_TYPES = {
    "assign": "JOB_ASSIGNED",
    "accept": "JOB_ACCEPTED",
    "start": "JOB_STARTED",
    "complete": "JOB_COMPLETED",
    "cancel": "JOB_CANCELLED",
    "escalate": "JOB_ESCALATED",
}

# Service interval for acs.next_service_date, keyed by maintenance_type
# (case-insensitive prefix match), default 180 days.
SERVICE_INTERVALS: tuple[tuple[str, int], ...] = (
    ("preventive", 90),
    ("routine", 90),
    ("service", 90),
    ("emergency", 90),
    ("inspection", 180),
    ("repair", 180),
    ("installation", 365),
    ("gas refill", 120),
)
DEFAULT_SERVICE_INTERVAL_DAYS = 180


class WorkOrderError(Exception):
    """Domain error: carries an API error code, message and HTTP status."""

    def __init__(self, code: str, message: str, http: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http = http


@dataclass
class Planned:
    """Everything one transition writes (pure; no DB handles)."""

    action: str
    from_status: str
    to_status: str
    updates: dict[str, Any] = field(default_factory=dict)
    acs_updates: dict[str, Any] = field(default_factory=dict)
    event: str = ""
    note: str | None = None


def _now() -> datetime:
    # Monotonic within the process: the timeline sorts on created_at and the
    # row id is a random UUID, so equal stamps would order arbitrarily.
    return utcnow()


def _is_manager(actor_role: str | None) -> bool:
    # assign/cancel gate = ASSIGN_MAINTENANCE (admin/manager only).
    return has_permission(actor_role, "ASSIGN_MAINTENANCE")


def _owner_or_manager(wo: dict, actor_id: str, actor_role: str | None) -> None:
    if _is_manager(actor_role):
        return
    tech = wo.get("technician_id")
    if tech is not None and str(tech) == str(actor_id):
        return
    raise WorkOrderError(
        "FORBIDDEN",
        "You can only act on jobs assigned to you.",
        403,
    )


def service_interval_days(maintenance_type: str | None) -> int:
    """Map a maintenance_type to the next-service interval in days."""
    t = (maintenance_type or "").strip().lower()
    for prefix, days in SERVICE_INTERVALS:
        if t.startswith(prefix) or prefix in t:
            return days
    return DEFAULT_SERVICE_INTERVAL_DAYS


def compute_changes(
    wo: dict,
    action: str,
    actor_id: str,
    actor_role: str | None,
    payload: dict | None = None,
) -> Planned:
    """Pure transition validator. ``wo`` is the current work-order row."""
    payload = payload or {}
    if action not in NOTIFY_TYPES:
        raise WorkOrderError("UNSUPPORTED_ACTION", f"Unknown action '{action}'.", 400)

    status = wo.get("status") or "SCHEDULED"
    if status in TERMINAL_STATUSES:
        raise WorkOrderError(
            "TERMINAL_STATE",
            f"Job is {status}; no further transitions are allowed.",
            409,
        )

    now = _now()

    if action == "assign":
        if not _is_manager(actor_role):
            raise WorkOrderError("FORBIDDEN", "Only managers may assign jobs.", 403)
        if status not in ("SCHEDULED", "ASSIGNED"):
            raise WorkOrderError(
                "INVALID_TRANSITION",
                f"Cannot assign a job in status {status}.",
                409,
            )
        technician_id = (payload.get("technician_id") or "").strip()
        if not technician_id:
            raise WorkOrderError("TECHNICIAN_REQUIRED", "technician_id is required.", 400)
        return Planned(
            action=action,
            from_status=status,
            to_status="ASSIGNED",
            updates={
                "status": "ASSIGNED",
                "technician_id": technician_id,
                "assigned_at": now,
                # A (re)assignment must be re-accepted by the new owner.
                "accepted_at": None,
            },
            event="ASSIGNED",
            note=f"Assigned to technician {technician_id}",
        )

    if action == "accept":
        if status != "ASSIGNED":
            raise WorkOrderError(
                "INVALID_TRANSITION",
                f"Cannot accept a job in status {status}.",
                409,
            )
        _owner_or_manager(wo, actor_id, actor_role)
        if wo.get("accepted_at") is not None:
            raise WorkOrderError("ALREADY_ACCEPTED", "Job has already been accepted.", 409)
        return Planned(
            action=action,
            from_status=status,
            to_status=status,
            updates={"accepted_at": now},
            event="ACCEPTED",
        )

    if action == "start":
        if status != "ASSIGNED":
            raise WorkOrderError(
                "INVALID_TRANSITION",
                f"Cannot start a job in status {status}.",
                409,
            )
        _owner_or_manager(wo, actor_id, actor_role)
        if wo.get("accepted_at") is None:
            raise WorkOrderError("NOT_ACCEPTED", "Accept the job before starting it.", 409)
        return Planned(
            action=action,
            from_status=status,
            to_status="IN_PROGRESS",
            updates={"status": "IN_PROGRESS", "started_at": now},
            event="STARTED",
        )

    if action == "complete":
        if status != "IN_PROGRESS":
            raise WorkOrderError(
                "INVALID_TRANSITION",
                f"Cannot complete a job in status {status}.",
                409,
            )
        _owner_or_manager(wo, actor_id, actor_role)
        work_performed = (payload.get("work_performed") or "").strip()
        if not work_performed:
            raise WorkOrderError(
                "WORK_PERFORMED_REQUIRED",
                "work_performed is required to complete a job.",
                400,
            )
        updates: dict[str, Any] = {
            "status": "COMPLETED",
            "completed_at": now,
            "completed_by": actor_id,
            "work_performed": work_performed,
        }
        for col in ("parts_used", "remarks"):
            val = payload.get(col)
            if val is not None and str(val).strip():
                updates[col] = str(val).strip()
        if payload.get("labor_hours") is not None:
            try:
                labor = float(payload["labor_hours"])
            except (TypeError, ValueError):
                raise WorkOrderError("INVALID_LABOR_HOURS", "labor_hours must be a number.", 400) from None
            if labor < 0:
                raise WorkOrderError(
                    "INVALID_LABOR_HOURS",
                    "labor_hours must not be negative.",
                    400,
                )
            updates["labor_hours"] = labor

        acs_updates: dict[str, Any] = {}
        if wo.get("ac_id"):
            today = now.date()
            acs_updates = {
                "last_maintenance_date": today,
                "next_service_date": today
                + timedelta(days=service_interval_days(wo.get("maintenance_type"))),
            }
        return Planned(
            action=action,
            from_status=status,
            to_status="COMPLETED",
            updates=updates,
            acs_updates=acs_updates,
            event="COMPLETED",
        )

    if action == "cancel":
        if status not in ("SCHEDULED", "ASSIGNED", "IN_PROGRESS"):
            raise WorkOrderError(
                "INVALID_TRANSITION",
                f"Cannot cancel a job in status {status}.",
                409,
            )
        if not _is_manager(actor_role):
            raise WorkOrderError("FORBIDDEN", "Only managers may cancel jobs.", 403)
        reason = (payload.get("reason") or "").strip() or None
        return Planned(
            action=action,
            from_status=status,
            to_status="CANCELLED",
            updates={"status": "CANCELLED"},
            event="CANCELLED",
            note=reason,
        )

    # action == "escalate"
    if status not in ("ASSIGNED", "IN_PROGRESS"):
        raise WorkOrderError(
            "INVALID_TRANSITION",
            f"Cannot escalate a job in status {status}.",
            409,
        )
    _owner_or_manager(wo, actor_id, actor_role)
    reason = (payload.get("reason") or "").strip()
    if not reason:
        raise WorkOrderError("REASON_REQUIRED", "An escalation reason is required.", 400)
    priority = wo.get("priority") or "MEDIUM"
    idx = PRIORITY_LADDER.index(priority) if priority in PRIORITY_LADDER else 1
    new_priority = PRIORITY_LADDER[min(idx + 1, len(PRIORITY_LADDER) - 1)]
    return Planned(
        action=action,
        from_status=status,
        to_status=status,
        updates={
            "priority": new_priority,
            "escalated_at": now,
            "escalation_reason": reason,
        },
        event="ESCALATED",
        note=reason,
    )


def validate_assignee(cur, technician_id: str) -> dict:
    """assign payload target must be an existing, active, non-VIEWER profile."""
    cur.execute(
        "SELECT id, role, is_active FROM profiles WHERE id::text = %s",
        (technician_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise WorkOrderError("TECHNICIAN_NOT_FOUND", "Technician profile not found.", 404)
    profile = dict(row)
    if not profile.get("is_active", True):
        raise WorkOrderError("INVALID_TECHNICIAN", "Technician profile is inactive.", 400)
    if profile.get("role") == "VIEWER":
        raise WorkOrderError("INVALID_TECHNICIAN", "VIEWER profiles cannot be assigned jobs.", 400)
    return profile


def apply_transition(
    conn,
    wo_id: str,
    action: str,
    actor_id: str,
    actor_role: str | None,
    payload: dict | None = None,
    client_mutation_id: str | None = None,
) -> tuple[dict, Planned]:
    """Lock the row, validate, and write the transition + timeline event.

    Returns ``(updated_row, planned)``. The caller commits (or rolls back) —
    side effects (audit, notifications) must be emitted after commit.
    """
    payload = payload or {}
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT * FROM maintenance_work_orders WHERE id::text = %s FOR UPDATE",
            (wo_id,),
        )
        wo = cur.fetchone()
        if wo is None:
            raise WorkOrderError("WORK_ORDER_NOT_FOUND", "Work order not found.", 404)
        wo = dict(wo)

        # compute_changes first: a missing technician_id must surface as
        # TECHNICIAN_REQUIRED (400), not TECHNICIAN_NOT_FOUND (404).
        planned = compute_changes(wo, action, actor_id, actor_role, payload)

        if action == "assign":
            validate_assignee(cur, str(planned.updates.get("technician_id") or ""))

        set_bits = [f"{col} = %s" for col in planned.updates]
        vals = list(planned.updates.values())
        if planned.to_status != planned.from_status or planned.updates:
            set_bits.append("updated_at = %s")
            vals.append(_now())
        cur.execute(
            f"UPDATE maintenance_work_orders SET {', '.join(set_bits)}" f" WHERE id::text = %s",
            vals + [wo_id],
        )
        if client_mutation_id:
            cur.execute(
                "UPDATE maintenance_work_orders SET client_mutation_id = %s" " WHERE id::text = %s",
                (client_mutation_id, wo_id),
            )
        if planned.acs_updates:
            set_acs = ", ".join(f"{col} = %s" for col in planned.acs_updates)
            cur.execute(
                f"UPDATE acs SET {set_acs} WHERE ac_id = %s",
                list(planned.acs_updates.values()) + [wo["ac_id"]],
            )
        cur.execute(
            """INSERT INTO maintenance_events
               (work_order_id, actor_id, event, from_status, to_status, note,
                client_mutation_id, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                wo["id"],
                actor_id,
                planned.event,
                planned.from_status,
                planned.to_status,
                planned.note,
                client_mutation_id,
                # explicit clock: NOW() is the transaction timestamp, which
                # would make every event of a sync batch share one time and
                # scramble the timeline order.
                _now(),
            ),
        )
        cur.execute(
            "SELECT * FROM maintenance_work_orders WHERE id::text = %s",
            (wo_id,),
        )
        updated = dict(cur.fetchone())
    finally:
        cur.close()
    return updated, planned


def resolve_recipients(conn, wo: dict, action: str, actor_id: str) -> list[str]:
    """Who gets notified about ``action`` on ``wo`` (never the actor)."""

    def managers_except() -> list[str]:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT id FROM profiles"
                " WHERE role IN ('SUPER_ADMIN','FACILITY_MANAGER') AND is_active"
                " AND id::text <> %s",
                (str(actor_id),),
            )
            return [str(r[0]) for r in cur.fetchall()]
        finally:
            cur.close()

    actor = str(actor_id)

    def others(*candidates: Any) -> list[str]:
        out = []
        for c in candidates:
            if c is None:
                continue
            cid = str(c)
            if cid != actor and cid not in out:
                out.append(cid)
        return out

    if action == "assign":
        return others(wo.get("technician_id"))
    if action == "cancel":
        cand = others(wo.get("technician_id"), wo.get("created_by"))
        return cand or managers_except()
    if action == "escalate":
        return managers_except()
    # accept / start / complete → creator (if not the actor), else managers
    cand = others(wo.get("created_by"))
    return cand or managers_except()


def notification_body(wo: dict, action: str, planned: Planned) -> str:
    kind = wo.get("maintenance_type") or "maintenance"
    ac = wo.get("ac_id") or "asset"
    if action == "assign":
        return f"{kind} job on {ac} has been assigned to you."
    if action == "accept":
        return f"{kind} job on {ac} was accepted."
    if action == "start":
        return f"{kind} job on {ac} has started."
    if action == "complete":
        return f"{kind} job on {ac} was completed."
    if action == "cancel":
        return f"{kind} job on {ac} was cancelled." + (f" Reason: {planned.note}" if planned.note else "")
    return f"{kind} job on {ac} escalated to {planned.updates.get('priority')}." f" Reason: {planned.note}"
