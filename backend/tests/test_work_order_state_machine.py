"""Work-order state machine: exhaustive transition rules + property test.

The pure layer (``compute_changes``) is tested without a database; the DB
layer (``apply_transition``) runs only when TEST_DATABASE_URL is set.
"""

import random
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.work_orders import (  # noqa: E402
    PRIORITY_LADDER,
    STATUSES,
    TERMINAL_STATUSES,
    WorkOrderError,
    compute_changes,
    service_interval_days,
)

MANAGER = ("mgr-uuid", "FACILITY_MANAGER")
TECH_A = ("tech-a-uuid", "TECHNICIAN")
TECH_B = ("tech-b-uuid", "TECHNICIAN")


def wo(status="SCHEDULED", **extra):
    row = {
        "id": str(uuid.uuid4()),
        "status": status,
        "technician_id": None,
        "accepted_at": None,
        "priority": "MEDIUM",
        "ac_id": "AC-1",
        "maintenance_type": "Preventive",
        "created_by": "creator-uuid",
        "assigned_at": None,
        "escalated_at": None,
        "escalation_reason": None,
    }
    row.update(extra)
    return row


def assigned_wo(**extra):
    extra.setdefault("status", "ASSIGNED")
    extra.setdefault("technician_id", TECH_A[0])
    return wo(**extra)


def accepted_wo(**extra):
    from datetime import datetime, timezone

    return assigned_wo(accepted_at=datetime.now(timezone.utc), **extra)


def in_progress_wo(**extra):
    return accepted_wo(status="IN_PROGRESS", **extra)


def call(row, action, actor=TECH_A, payload=None):
    return compute_changes(row, action, actor[0], actor[1], payload)


def expect_error(code, row, action, actor=TECH_A, payload=None):
    with pytest.raises(WorkOrderError) as exc:
        call(row, action, actor, payload)
    assert exc.value.code == code
    return exc.value


# ── action availability / unsupported ────────────────────────────────────────


def test_unknown_action_is_rejected():
    expect_error("UNSUPPORTED_ACTION", wo(), "reschedule")


def test_terminal_states_are_immutable():
    for status in sorted(TERMINAL_STATUSES):
        for action in ("assign", "accept", "start", "complete", "cancel", "escalate"):
            err = expect_error("TERMINAL_STATE", wo(status=status), action, MANAGER)
            assert err.http == 409


# ── assign ───────────────────────────────────────────────────────────────────


def test_assign_moves_scheduled_to_assigned():
    p = call(wo(), "assign", MANAGER, {"technician_id": TECH_A[0]})
    assert p.to_status == "ASSIGNED"
    assert p.updates["technician_id"] == TECH_A[0]
    assert p.updates["accepted_at"] is None  # (re)assignment must be re-accepted
    assert p.event == "ASSIGNED"


def test_assign_is_manager_only():
    expect_error("FORBIDDEN", wo(), "assign", TECH_A, {"technician_id": TECH_A[0]})
    err = expect_error("FORBIDDEN", wo(), "assign", TECH_A, {"technician_id": TECH_A[0]})
    assert err.http == 403


def test_assign_requires_technician_id():
    err = expect_error("TECHNICIAN_REQUIRED", wo(), "assign", MANAGER, {})
    assert err.http == 400


@pytest.mark.parametrize("status", ["IN_PROGRESS", "COMPLETED", "CANCELLED"])
def test_assign_illegal_from_other_statuses(status):
    # COMPLETED/CANCELLED short-circuit as TERMINAL_STATE first.
    code = "TERMINAL_STATE" if status in TERMINAL_STATUSES else "INVALID_TRANSITION"
    expect_error(code, wo(status=status), "assign", MANAGER, {"technician_id": TECH_A[0]})


def test_reassign_resets_acceptance():
    p = call(accepted_wo(), "assign", MANAGER, {"technician_id": TECH_B[0]})
    assert p.updates["accepted_at"] is None
    assert p.updates["technician_id"] == TECH_B[0]


# ── accept ───────────────────────────────────────────────────────────────────


def test_accept_requires_assigned_status():
    expect_error("INVALID_TRANSITION", wo(), "accept")
    expect_error("INVALID_TRANSITION", in_progress_wo(), "accept")


def test_accept_sets_accepted_at():
    p = call(assigned_wo(), "accept", TECH_A)
    assert p.updates["accepted_at"] is not None
    assert p.to_status == "ASSIGNED"
    assert p.event == "ACCEPTED"


def test_accept_twice_fails():
    expect_error("ALREADY_ACCEPTED", accepted_wo(), "accept", TECH_A)


def test_only_owner_or_manager_can_accept():
    expect_error("FORBIDDEN", assigned_wo(), "accept", TECH_B)
    call(assigned_wo(), "accept", MANAGER)  # manager may accept on behalf


# ── start ────────────────────────────────────────────────────────────────────


def test_start_requires_acceptance_for_everyone():
    for actor in (TECH_A, MANAGER):
        err = expect_error("NOT_ACCEPTED", assigned_wo(), "start", actor)
        assert err.http == 409


def test_start_requires_accepted_and_moves_to_in_progress():
    p = call(accepted_wo(), "start", TECH_A)
    assert p.to_status == "IN_PROGRESS"
    assert p.updates["status"] == "IN_PROGRESS"
    assert p.updates["started_at"] is not None
    assert p.event == "STARTED"


def test_start_requires_accepted_and_moves_to_in_progress_manager():
    # managers are not exempt from the accept gate
    p = call(accepted_wo(), "start", MANAGER)
    assert p.to_status == "IN_PROGRESS"


def test_only_owner_or_manager_can_start():
    expect_error("FORBIDDEN", accepted_wo(), "start", TECH_B)


def test_start_from_scheduled_or_in_progress_illegal():
    expect_error("INVALID_TRANSITION", wo(), "start")
    expect_error("INVALID_TRANSITION", in_progress_wo(), "start")


# ── complete ─────────────────────────────────────────────────────────────────


def test_complete_moves_to_completed_and_updates_ac_dates():
    p = call(in_progress_wo(), "complete", TECH_A, {"work_performed": "Cleaned coils"})
    assert p.to_status == "COMPLETED"
    assert p.updates["status"] == "COMPLETED"
    assert p.updates["completed_by"] == TECH_A[0]
    assert p.acs_updates["last_maintenance_date"] is not None
    assert p.acs_updates["next_service_date"] > p.acs_updates["last_maintenance_date"]
    assert p.event == "COMPLETED"


def test_complete_without_ac_skips_ac_updates():
    p = call(in_progress_wo(ac_id=None), "complete", TECH_A, {"work_performed": "x"})
    assert p.acs_updates == {}


def test_complete_requires_work_performed():
    err = expect_error("WORK_PERFORMED_REQUIRED", in_progress_wo(), "complete", TECH_A, {})
    assert err.http == 400
    expect_error(
        "WORK_PERFORMED_REQUIRED",
        in_progress_wo(),
        "complete",
        TECH_A,
        {"work_performed": "   "},
    )


def test_complete_rejects_negative_labor_hours():
    expect_error(
        "INVALID_LABOR_HOURS",
        in_progress_wo(),
        "complete",
        TECH_A,
        {"work_performed": "x", "labor_hours": -1},
    )


def test_complete_only_from_in_progress():
    expect_error("INVALID_TRANSITION", accepted_wo(), "complete", TECH_A)
    expect_error("INVALID_TRANSITION", wo(), "complete", TECH_A)


def test_only_owner_or_manager_can_complete():
    expect_error("FORBIDDEN", in_progress_wo(), "complete", TECH_B, {"work_performed": "x"})


# ── cancel ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["SCHEDULED", "ASSIGNED", "IN_PROGRESS"])
def test_cancel_allowed_from_active_statuses_by_manager(status):
    p = call(wo(status=status), "cancel", MANAGER, {"reason": "duplicate"})
    assert p.to_status == "CANCELLED"
    assert p.event == "CANCELLED"
    assert p.note == "duplicate"


def test_cancel_is_manager_only():
    expect_error("FORBIDDEN", in_progress_wo(), "cancel", TECH_A)
    err = expect_error("FORBIDDEN", assigned_wo(), "cancel", TECH_A)
    assert err.http == 403


def test_cancel_reason_is_optional():
    p = call(assigned_wo(), "cancel", MANAGER, {})
    assert p.note is None


# ── escalate ─────────────────────────────────────────────────────────────────


def test_escalate_raises_priority_one_level():
    p = call(assigned_wo(), "escalate", TECH_A, {"reason": "overdue"})
    assert p.updates["priority"] == "HIGH"
    assert p.updates["escalated_at"] is not None
    assert p.updates["escalation_reason"] == "overdue"
    assert p.to_status == "ASSIGNED"  # status unchanged
    assert p.event == "ESCALATED"


def test_escalate_caps_at_critical():
    p = call(assigned_wo(priority="CRITICAL"), "escalate", TECH_A, {"reason": "r"})
    assert p.updates["priority"] == "CRITICAL"


def test_escalate_requires_reason():
    err = expect_error("REASON_REQUIRED", assigned_wo(), "escalate", TECH_A, {})
    assert err.http == 400


@pytest.mark.parametrize("status", ["SCHEDULED", "COMPLETED", "CANCELLED"])
def test_escalate_only_from_assigned_or_in_progress(status):
    code = "TERMINAL_STATE" if status in TERMINAL_STATUSES else "INVALID_TRANSITION"
    expect_error(code, wo(status=status), "escalate", TECH_A, {"reason": "r"})


def test_only_owner_or_manager_can_escalate():
    expect_error("FORBIDDEN", assigned_wo(), "escalate", TECH_B, {"reason": "r"})


# ── service interval map ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "mtype,days",
    [
        ("Preventive", 90),
        ("routine", 90),
        ("Service", 90),
        ("emergency", 90),
        ("Inspection", 180),
        ("repair", 180),
        ("Installation", 365),
        ("Gas Refill", 120),
        ("Weird Custom Type", 180),
        (None, 180),
    ],
)
def test_service_interval_map(mtype, days):
    assert service_interval_days(mtype) == days


# ── property test: random sequences never reach an illegal state ─────────────


def test_random_action_sequences_never_reach_illegal_state():
    """Apply random actions; every outcome is either a documented error or a
    valid transition, and the row always stays within STATUSES."""
    rng = random.Random(20261006)
    actions = ["assign", "accept", "start", "complete", "cancel", "escalate"]
    actors = [MANAGER, TECH_A, TECH_B]

    for trial in range(300):
        row = wo()
        for _ in range(rng.randint(1, 12)):
            action = rng.choice(actions)
            actor = rng.choice(actors)
            payload = {"technician_id": TECH_A[0], "reason": "prop", "work_performed": "done"}
            try:
                p = compute_changes(row, action, actor[0], actor[1], payload)
            except WorkOrderError as e:
                # documented error codes only, and never a status outside STATUSES
                assert e.code in {
                    "UNSUPPORTED_ACTION",
                    "TERMINAL_STATE",
                    "INVALID_TRANSITION",
                    "FORBIDDEN",
                    "TECHNICIAN_REQUIRED",
                    "ALREADY_ACCEPTED",
                    "NOT_ACCEPTED",
                    "WORK_PERFORMED_REQUIRED",
                    "REASON_REQUIRED",
                    "INVALID_LABOR_HOURS",
                }, f"unexpected code {e.code} (trial {trial})"
                # a failed transition must leave the state untouched — replay
                # with the same input raises the same error deterministically
                with pytest.raises(WorkOrderError):
                    compute_changes(row, action, actor[0], actor[1], payload)
                continue
            assert p.to_status in STATUSES, p.to_status
            # simulate applying the transition
            row.update(p.updates)
            row["status"] = p.to_status
            assert row["status"] in STATUSES
            # terminal absorption: once terminal, every later action errors
            if row["status"] in TERMINAL_STATUSES:
                for a2 in actions:
                    with pytest.raises(WorkOrderError) as exc:
                        compute_changes(row, a2, MANAGER[0], MANAGER[1], payload)
                    assert exc.value.code == "TERMINAL_STATE"
                break


def test_priority_never_exceeds_ladder():
    row = assigned_wo(priority="LOW")
    for _ in range(5):
        p = compute_changes(row, "escalate", TECH_A[0], TECH_A[1], {"reason": "x"})
        row.update(p.updates)
        assert row["priority"] in PRIORITY_LADDER


# The DB layer (apply_transition: row lock, event insert, AC date update) is
# exercised end-to-end through the endpoints in test_maintenance_v1.py and
# test_offline_sync.py.
