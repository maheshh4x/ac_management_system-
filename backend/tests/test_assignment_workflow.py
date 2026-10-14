"""Technician assignment workflow (SCHEDULED → ASSIGNED → My Jobs).

Covers the reported scenario end to end: an approved-but-unassigned work
order, the least-privilege technician picker, manager assignment, and the
assigned technician's job visibility. All against TEST_DATABASE_URL.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import auth, make_token  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="needs a live database — set TEST_DATABASE_URL"
)


def _code(r) -> str:
    return ((r.get_json() or {}).get("error") or {}).get("code", "<no error envelope>")


@pytest.fixture()
def actors(db):
    """Manager, two technicians, a viewer, an inactive tech, a pending tech."""
    ids = {
        "manager": str(uuid.uuid4()),
        "tech_a": str(uuid.uuid4()),
        "tech_b": str(uuid.uuid4()),
        "viewer": str(uuid.uuid4()),
        "inactive": str(uuid.uuid4()),
        "pending": str(uuid.uuid4()),
    }
    rows = [
        (ids["manager"], "FACILITY_MANAGER", True, "APPROVED"),
        (ids["tech_a"], "TECHNICIAN", True, "APPROVED"),
        (ids["tech_b"], "TECHNICIAN", True, "APPROVED"),
        (ids["viewer"], "VIEWER", True, "APPROVED"),
        (ids["inactive"], "TECHNICIAN", False, "APPROVED"),
        (ids["pending"], "TECHNICIAN", True, "PENDING"),
    ]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for pid, role, active, status in rows:
                cur.execute(
                    "INSERT INTO profiles (id, email, name, role, status, is_active)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (pid, f"{pid[:8]}@acms.test", f"Actor {role}", role, status, active),
                )
        conn.commit()
    finally:
        conn.close()
    yield ids
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE user_id = ANY(%s::uuid[])", (list(ids.values()),))
            cur.execute("DELETE FROM notifications WHERE user_id = ANY(%s::uuid[])", (list(ids.values()),))
            cur.execute(
                "DELETE FROM maintenance_work_orders WHERE created_by = ANY(%s::uuid[])"
                " OR technician_id = ANY(%s::uuid[])",
                (list(ids.values()), list(ids.values())),
            )
            cur.execute("DELETE FROM profiles WHERE id = ANY(%s::uuid[])", (list(ids.values()),))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def scheduled_wo(db, actors):
    ac_id = f"ASG-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, room_id) VALUES (%s, 'ROOM-102')", (ac_id,))
            cur.execute(
                """INSERT INTO maintenance_work_orders
                   (ac_id, maintenance_type, priority, status, problem_description, created_by)
                   VALUES (%s, 'Repair', 'MEDIUM', 'SCHEDULED', 'Assign me', %s) RETURNING id""",
                (ac_id, actors["manager"]),
            )
            wo_id = str(cur.fetchone()[0])
        conn.commit()
    finally:
        conn.close()
    yield {"id": wo_id, "ac_id": ac_id}
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM maintenance_work_orders WHERE id = %s", (wo_id,))
            cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
        conn.commit()
    finally:
        conn.close()


def _tok(role, sub):
    return auth(make_token(role, sub=sub))


def _wo(wo_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, technician_id, assigned_at, accepted_at"
                " FROM maintenance_work_orders WHERE id = %s",
                (wo_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


# ── technician picker ────────────────────────────────────────────────────────


def test_manager_lists_assignable_technicians(secret_client, actors, db):
    r = secret_client.get(
        "/api/v1/users/assignable-technicians", headers=_tok("FACILITY_MANAGER", actors["manager"])
    )
    assert r.status_code == 200, r.get_json()
    ids = {t["id"] for t in r.get_json()["data"]["technicians"]}
    assert actors["tech_a"] in ids and actors["tech_b"] in ids
    # Inactive, pending-approval and VIEWER profiles are never assignable.
    assert actors["inactive"] not in ids
    assert actors["pending"] not in ids
    assert actors["viewer"] not in ids
    assert actors["manager"] not in ids
    # Least privilege: id + display fields only, no roles/status dump.
    assert set(r.get_json()["data"]["technicians"][0]) == {"id", "name", "email"}


def test_picker_denied_for_technician_viewer_and_anonymous(secret_client, actors, db):
    assert secret_client.get(
        "/api/v1/users/assignable-technicians", headers=_tok("TECHNICIAN", actors["tech_a"])
    ).status_code == 403
    assert secret_client.get(
        "/api/v1/users/assignable-technicians", headers=_tok("VIEWER", actors["viewer"])
    ).status_code == 403
    assert secret_client.get("/api/v1/users/assignable-technicians").status_code == 401


# ── assignment ───────────────────────────────────────────────────────────────


def test_manager_assigns_unassigned_order(secret_client, actors, scheduled_wo, db):
    r = secret_client.post(
        f"/api/v1/maintenance/{scheduled_wo['id']}/assign",
        json={"technician_id": actors["tech_a"]},
        headers=_tok("FACILITY_MANAGER", actors["manager"]),
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["data"]
    assert body["status"] == "ASSIGNED" and body["technician_id"] == actors["tech_a"]

    status, tech_id, assigned_at, accepted_at = _wo(scheduled_wo["id"])
    assert status == "ASSIGNED" and str(tech_id) == actors["tech_a"]
    assert assigned_at is not None and accepted_at is None  # reassignment needs re-accept

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT event, from_status, to_status FROM maintenance_events WHERE work_order_id = %s",
                (scheduled_wo["id"],),
            )
            events = cur.fetchall()
            cur.execute(
                "SELECT action FROM audit_logs WHERE entity_type = 'maintenance'"
                " AND entity_id = %s AND action = 'ASSIGN_JOB'",
                (scheduled_wo["id"],),
            )
            audits = cur.fetchall()
            cur.execute(
                "SELECT type FROM notifications WHERE user_id = %s AND entity_id = %s",
                (actors["tech_a"], scheduled_wo["id"]),
            )
            notifs = cur.fetchall()
    finally:
        conn.close()
    assert ("ASSIGNED", "SCHEDULED", "ASSIGNED") in events
    assert len(audits) == 1
    assert ("JOB_ASSIGNED",) in notifs


def test_unauthorized_assignment_rejected(secret_client, actors, scheduled_wo, db):
    # Technician (no ASSIGN_MAINTENANCE) cannot assign, even to themselves.
    r = secret_client.post(
        f"/api/v1/maintenance/{scheduled_wo['id']}/assign",
        json={"technician_id": actors["tech_a"]},
        headers=_tok("TECHNICIAN", actors["tech_a"]),
    )
    assert r.status_code == 403
    # Viewer cannot assign either.
    r = secret_client.post(
        f"/api/v1/maintenance/{scheduled_wo['id']}/assign",
        json={"technician_id": actors["tech_a"]},
        headers=_tok("VIEWER", actors["viewer"]),
    )
    assert r.status_code == 403
    assert _wo(scheduled_wo["id"])[0] == "SCHEDULED"  # untouched


def test_invalid_technicians_rejected(secret_client, actors, scheduled_wo, db):
    mgr = _tok("FACILITY_MANAGER", actors["manager"])
    r = secret_client.post(
        f"/api/v1/maintenance/{scheduled_wo['id']}/assign",
        json={"technician_id": actors["viewer"]},
        headers=mgr,
    )
    assert r.status_code == 400 and _code(r) == "INVALID_TECHNICIAN"
    r = secret_client.post(
        f"/api/v1/maintenance/{scheduled_wo['id']}/assign",
        json={"technician_id": str(uuid.uuid4())},
        headers=mgr,
    )
    assert r.status_code == 404 and _code(r) == "TECHNICIAN_NOT_FOUND"
    r = secret_client.post(
        f"/api/v1/maintenance/{scheduled_wo['id']}/assign", json={}, headers=mgr
    )
    assert r.status_code == 400 and _code(r) == "TECHNICIAN_REQUIRED"
    assert _wo(scheduled_wo["id"])[0] == "SCHEDULED"  # untouched


# ── visibility ───────────────────────────────────────────────────────────────


def test_assigned_job_visible_to_owner_only(secret_client, actors, scheduled_wo, db):
    secret_client.post(
        f"/api/v1/maintenance/{scheduled_wo['id']}/assign",
        json={"technician_id": actors["tech_a"]},
        headers=_tok("FACILITY_MANAGER", actors["manager"]),
    )
    mine_a = secret_client.get(
        "/api/v1/maintenance", headers=_tok("TECHNICIAN", actors["tech_a"])
    ).get_json()["data"]
    assert any(str(j.get("id")) == scheduled_wo["id"] for j in mine_a)
    mine_b = secret_client.get(
        "/api/v1/maintenance", headers=_tok("TECHNICIAN", actors["tech_b"])
    ).get_json()["data"]
    assert all(str(j.get("id")) != scheduled_wo["id"] for j in mine_b)
    mgr = secret_client.get(
        "/api/v1/maintenance", headers=_tok("FACILITY_MANAGER", actors["manager"])
    ).get_json()["data"]
    assert any(str(j.get("id")) == scheduled_wo["id"] for j in mgr)
