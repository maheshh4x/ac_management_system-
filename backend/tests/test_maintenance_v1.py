"""Phase 4: work-order lifecycle endpoints (list/detail/create/actions/comments),
PATCH content-only rules, list filters and the maintenance trend report.

Every test seeds short-lived profile rows (created_by / actor_id / author_id /
notification recipients all reference profiles) and removes them — plus every
row it created — afterwards: profiles.role is authoritative over the JWT
claim, and leaving rows behind would flip the role-matrix tests.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import (  # noqa: E402
    TEST_SECRET,
    TEST_SUPABASE_URL,
    TEST_USER_ID,
    AuthClient,
    make_token,
)

TECH_A = "33333333-3333-3333-3333-333333333331"
TECH_B = "33333333-3333-3333-3333-333333333332"
VIEWER_U = "33333333-3333-3333-3333-333333333333"
AC_PREFIX = "P4-AC-%"
PROFILE_IDS = (TEST_USER_ID, TECH_A, TECH_B, VIEWER_U)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _p4_profiles(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """Profiles for every actor these tests use + full cleanup afterwards."""
    seed = (
        (TEST_USER_ID, "tester@acms.test", "Test User", "SUPER_ADMIN"),
        (TECH_A, "tech-a@acms.test", "Tech A", "TECHNICIAN"),
        (TECH_B, "tech-b@acms.test", "Tech B", "TECHNICIAN"),
        (VIEWER_U, "viewer-u@acms.test", "Viewer U", "VIEWER"),
    )
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for pid, email, name, role in seed:
                cur.execute(
                    "INSERT INTO profiles (id, email, name, role, status, is_active) "
                    "VALUES (%s, %s, %s, %s, 'APPROVED', TRUE) "
                    "ON CONFLICT (id) DO NOTHING",
                    (pid, email, name, role),
                )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Break FK references first: audits/notifications may point at
            # work orders, so they go before the work orders themselves.
            cur.execute(
                "DELETE FROM audit_logs "
                " WHERE user_id = ANY(%s::uuid[])"
                "    OR entity_id IN (SELECT id::text FROM maintenance_work_orders"
                "                      WHERE ac_id LIKE %s)",
                (list(PROFILE_IDS[1:]), AC_PREFIX),
            )
            cur.execute(
                "DELETE FROM notifications "
                " WHERE user_id = ANY(%s::uuid[])"
                "    OR entity_id IN (SELECT id::text FROM maintenance_work_orders"
                "                      WHERE ac_id LIKE %s)",
                (list(PROFILE_IDS[1:]), AC_PREFIX),
            )
            cur.execute(
                "DELETE FROM maintenance_work_orders WHERE ac_id LIKE %s"
                "    OR technician_id = ANY(%s::uuid[])"
                "    OR created_by = ANY(%s::uuid[])",
                (AC_PREFIX, list(PROFILE_IDS[1:]), list(PROFILE_IDS)),
            )
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (AC_PREFIX,))
            cur.execute("DELETE FROM profiles WHERE id = ANY(%s::uuid[])", (list(PROFILE_IDS),))
        conn.commit()
    finally:
        conn.close()


def _authed_client(monkeypatch, role, sub):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    app = create_app()
    app.config["TESTING"] = True
    return AuthClient(app.test_client(), make_token(role, sub=sub))


@pytest.fixture()
def admin(db_admin_client):
    return db_admin_client


@pytest.fixture()
def tech_a(monkeypatch, db):  # noqa: ARG001
    return _authed_client(monkeypatch, "TECHNICIAN", TECH_A)


@pytest.fixture()
def tech_b(monkeypatch, db):  # noqa: ARG001
    return _authed_client(monkeypatch, "TECHNICIAN", TECH_B)


@pytest.fixture()
def viewer(monkeypatch, db):  # noqa: ARG001
    return _authed_client(monkeypatch, "VIEWER", VIEWER_U)


# ── helpers ──────────────────────────────────────────────────────────────────


def _new_ac(suffix: str) -> str:
    """A throwaway AC row (cleaned up by the fixture teardown)."""
    ac_id = f"P4-AC-{suffix}-{uuid.uuid4().hex[:6]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, room_id) VALUES (%s, 'ROOM-102')", (ac_id,))
        conn.commit()
    finally:
        conn.close()
    return ac_id


def _create(client, ac_id, **overrides):
    body = {
        "ac_id": ac_id,
        "maintenance_type": "Preventive Maintenance",
        "priority": "MEDIUM",
        "scheduled_date": date.today().isoformat(),
        "problem_description": "quarterly service",
    }
    body.update(overrides)
    return client.post("/api/v1/maintenance", json=body)


def _fetch(sql, params=()):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _events(wo_id):
    return [
        row[0]
        for row in _fetch(
            "SELECT event FROM maintenance_events WHERE work_order_id = %s"
            " ORDER BY created_at ASC, id ASC",
            (wo_id,),
        )
    ]


def _error_code(resp):
    return resp.get_json()["error"]["code"]


# ── create / list / detail ───────────────────────────────────────────────────


def test_create_list_detail_roundtrip(admin):
    ac_id = _new_ac("RT")
    r = _create(admin, ac_id)
    assert r.status_code == 201, r.get_json()
    data = r.get_json()["data"]
    wo_id = data["id"]
    assert data["status"] == "SCHEDULED"

    r = admin.get(f"/api/v1/maintenance?status=SCHEDULED&ac_id={ac_id}")
    body = r.get_json()
    assert r.status_code == 200
    assert body["pagination"]["total"] == 1
    assert body["data"][0]["id"] == wo_id
    # joined location/technician display fields
    assert body["data"][0]["room_name"] == "CSE Computer Lab 1"

    r = admin.get(f"/api/v1/maintenance/{wo_id}")
    assert r.status_code == 200
    detail = r.get_json()["data"]
    assert detail["id"] == wo_id
    assert [e["event"] for e in detail["events"]] == ["CREATED"]
    assert detail["comments"] == []
    assert detail["created_by_name"] == "Test User"

    r = admin.get(f"/api/v1/maintenance/{uuid.uuid4()}")
    assert r.status_code == 404
    assert _error_code(r) == "WORK_ORDER_NOT_FOUND"


def test_create_with_technician_goes_to_assigned(admin):
    ac_id = _new_ac("ASG")
    r = _create(admin, ac_id, technician_id=TECH_A, priority="HIGH")
    assert r.status_code == 201, r.get_json()
    data = r.get_json()["data"]
    wo_id = data["id"]
    assert data["status"] == "ASSIGNED"

    rows = _fetch(
        "SELECT type FROM notifications WHERE user_id = %s AND entity_id = %s",
        (TECH_A, wo_id),
    )
    assert [row[0] for row in rows] == ["JOB_ASSIGNED"]

    detail = admin.get(f"/api/v1/maintenance/{wo_id}").get_json()["data"]
    assert [e["event"] for e in detail["events"]] == ["CREATED", "ASSIGNED"]
    assert detail["technician_name"] == "Tech A"
    assert detail["priority"] == "HIGH"


def test_technician_sees_only_own_jobs(admin, tech_a):
    mine = _create(admin, _new_ac("SCA"), technician_id=TECH_A).get_json()["data"]
    other = _create(admin, _new_ac("SCB"), technician_id=TECH_B).get_json()["data"]

    r = tech_a.get("/api/v1/maintenance?page_size=200")
    assert r.status_code == 200
    ids = {item["id"] for item in r.get_json()["data"]}
    assert mine["id"] in ids
    assert other["id"] not in ids

    r = tech_a.get("/api/v1/maintenance?assigned_to=me")
    ids = {item["id"] for item in r.get_json()["data"]}
    assert mine["id"] in ids
    assert other["id"] not in ids


def test_viewer_cannot_create_or_view_reports(viewer):
    r = viewer.post("/api/v1/maintenance", json={"ac_id": "X", "maintenance_type": "y"})
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"
    r = viewer.get("/api/v1/reports/maintenance-trend")
    assert r.status_code == 403


def test_create_validation_errors(admin):
    ac_id = _new_ac("VAL")

    r = _create(admin, ac_id, status="COMPLETED")
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_STATUS"

    r = _create(admin, ac_id, status="ASSIGNED")  # no technician_id
    assert r.status_code == 400
    assert _error_code(r) == "TECHNICIAN_REQUIRED"

    r = _create(admin, "NO-SUCH-AC-XYZ")
    assert r.status_code == 404
    assert _error_code(r) == "ASSET_NOT_FOUND"

    r = _create(admin, ac_id, priority="URGENT")
    assert r.status_code == 400
    assert _error_code(r) == "VALIDATION_ERROR"

    r = _create(admin, ac_id, technician_id=VIEWER_U)
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_TECHNICIAN"

    r = _create(admin, ac_id, technician_id=str(uuid.uuid4()))
    assert r.status_code == 404
    assert _error_code(r) == "TECHNICIAN_NOT_FOUND"


# ── lifecycle ────────────────────────────────────────────────────────────────


def test_full_lifecycle_flow(admin, tech_a):
    ac_id = _new_ac("LC")
    wo = _create(admin, ac_id, technician_id=TECH_A).get_json()["data"]
    wo_id = wo["id"]

    r = tech_a.post(f"/api/v1/maintenance/{wo_id}/accept", json={})
    assert r.status_code == 200, r.get_json()
    r = tech_a.post(f"/api/v1/maintenance/{wo_id}/start", json={})
    assert r.status_code == 200, r.get_json()
    r = tech_a.post(
        f"/api/v1/maintenance/{wo_id}/complete",
        json={"work_performed": "cleaned filters", "labor_hours": 2, "parts_used": "filter"},
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["status"] == "COMPLETED"
    assert data["completed_by"] == TECH_A

    detail = admin.get(f"/api/v1/maintenance/{wo_id}").get_json()["data"]
    assert [e["event"] for e in detail["events"]] == [
        "CREATED",
        "ASSIGNED",
        "ACCEPTED",
        "STARTED",
        "COMPLETED",
    ]

    # AC service dates updated in the same transaction (Preventive → 90 days)
    rows = _fetch(
        "SELECT last_maintenance_date, next_service_date FROM acs WHERE ac_id = %s",
        (ac_id,),
    )
    # The service dates are written by the database clock (session TZ = UTC),
    # not by the host's local clock — comparing them with date.today() breaks
    # for the hours between local midnight and UTC midnight.
    today = datetime.now(timezone.utc).date()
    assert rows[0][0] == today
    assert rows[0][1] == today + timedelta(days=90)

    # the creator (not the acting technician) is notified of each step
    rows = _fetch(
        "SELECT type FROM notifications WHERE user_id = %s AND entity_id = %s" " ORDER BY created_at",
        (TEST_USER_ID, wo_id),
    )
    assert [row[0] for row in rows] == ["JOB_ACCEPTED", "JOB_STARTED", "JOB_COMPLETED"]

    # one audit row per mutation
    rows = _fetch("SELECT action FROM audit_logs WHERE entity_id = %s", (wo_id,))
    actions = {row[0] for row in rows}
    assert {"CREATE_MAINTENANCE", "ACCEPT_JOB", "START_JOB", "COMPLETE_JOB"} <= actions


def test_illegal_transitions(admin, tech_a):
    wo = _create(admin, _new_ac("TR"), technician_id=TECH_A).get_json()["data"]
    wo_id = wo["id"]

    r = tech_a.post(f"/api/v1/maintenance/{wo_id}/start", json={})
    assert r.status_code == 409
    assert _error_code(r) == "NOT_ACCEPTED"

    assert tech_a.post(f"/api/v1/maintenance/{wo_id}/accept", json={}).status_code == 200

    r = tech_a.post(f"/api/v1/maintenance/{wo_id}/accept", json={})
    assert r.status_code == 409
    assert _error_code(r) == "ALREADY_ACCEPTED"

    r = tech_a.post(f"/api/v1/maintenance/{wo_id}/complete", json={"work_performed": "x"})
    assert r.status_code == 409
    assert _error_code(r) == "INVALID_TRANSITION"

    # assigning without a technician must be a 400, not a 404 (compute first)
    r = admin.post(f"/api/v1/maintenance/{wo_id}/assign", json={})
    assert r.status_code == 400
    assert _error_code(r) == "TECHNICIAN_REQUIRED"


def test_technician_b_cannot_touch_a_job(admin, tech_a, tech_b):
    wo = _create(admin, _new_ac("FB"), technician_id=TECH_A).get_json()["data"]
    wo_id = wo["id"]

    r = tech_b.post(f"/api/v1/maintenance/{wo_id}/accept", json={})
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"
    r = tech_b.post(f"/api/v1/maintenance/{wo_id}/start", json={})
    assert r.status_code == 403

    # technicians lack ASSIGN_MAINTENANCE at the endpoint gate as well
    r = tech_a.post(f"/api/v1/maintenance/{wo_id}/assign", json={"technician_id": TECH_B})
    assert r.status_code == 403
    r = tech_a.post(f"/api/v1/maintenance/{wo_id}/cancel", json={})
    assert r.status_code == 403


def test_escalate_and_cancel(admin, tech_a):
    wo = _create(admin, _new_ac("ES"), technician_id=TECH_A, priority="HIGH").get_json()["data"]
    wo_id = wo["id"]

    r = admin.post(f"/api/v1/maintenance/{wo_id}/escalate", json={})
    assert r.status_code == 400
    assert _error_code(r) == "REASON_REQUIRED"

    r = admin.post(f"/api/v1/maintenance/{wo_id}/escalate", json={"reason": "complaint from HOD"})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["priority"] == "CRITICAL"
    assert data["escalation_reason"] == "complaint from HOD"

    r = admin.post(f"/api/v1/maintenance/{wo_id}/cancel", json={"reason": "duplicate request"})
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "CANCELLED"

    # terminal states are frozen
    r = admin.post(f"/api/v1/maintenance/{wo_id}/assign", json={"technician_id": TECH_A})
    assert r.status_code == 409
    assert _error_code(r) == "TERMINAL_STATE"


# ── PATCH (content only) ─────────────────────────────────────────────────────


def test_patch_rejects_status_and_updates_content(admin):
    wo = _create(admin, _new_ac("PT")).get_json()["data"]
    wo_id = wo["id"]

    r = admin.patch(f"/api/v1/maintenance/{wo_id}", json={"status": "COMPLETED"})
    assert r.status_code == 400
    assert _error_code(r) == "STATUS_ACTION_REQUIRED"

    r = admin.patch(
        f"/api/v1/maintenance/{wo_id}",
        json={"remarks": "checked by manager", "priority": "LOW"},
    )
    assert r.status_code == 200, r.get_json()

    detail = admin.get(f"/api/v1/maintenance/{wo_id}").get_json()["data"]
    assert detail["remarks"] == "checked by manager"
    assert detail["priority"] == "LOW"

    r = admin.patch(f"/api/v1/maintenance/{wo_id}", json={"priority": "URGENT"})
    assert r.status_code == 400
    assert _error_code(r) == "VALIDATION_ERROR"

    r = admin.patch(f"/api/v1/maintenance/{uuid.uuid4()}", json={"remarks": "x"})
    assert r.status_code == 404
    assert _error_code(r) == "NOT_FOUND"


# ── comments ─────────────────────────────────────────────────────────────────


def test_comments_roundtrip(admin):
    wo = _create(admin, _new_ac("CM")).get_json()["data"]
    wo_id = wo["id"]

    r = admin.post(
        f"/api/v1/maintenance/{wo_id}/comments",
        json={"body": "part ordered", "photos": ["https://x.co/p1.png"]},
    )
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["data"]["body"] == "part ordered"

    detail = admin.get(f"/api/v1/maintenance/{wo_id}").get_json()["data"]
    assert len(detail["comments"]) == 1
    assert detail["comments"][0]["author_name"] == "Test User"
    assert detail["comments"][0]["photos"] == ["https://x.co/p1.png"]

    r = admin.post(f"/api/v1/maintenance/{wo_id}/comments", json={"body": ""})
    assert r.status_code == 400

    r = admin.post(
        f"/api/v1/maintenance/{wo_id}/comments",
        json={"body": "x", "photos": ["p"] * 11},
    )
    assert r.status_code == 400

    r = admin.post(f"/api/v1/maintenance/{uuid.uuid4()}/comments", json={"body": "hi"})
    assert r.status_code == 404
    assert _error_code(r) == "WORK_ORDER_NOT_FOUND"

    rows = _fetch("SELECT action FROM audit_logs WHERE entity_id = %s", (wo_id,))
    assert "ADD_JOB_COMMENT" in {row[0] for row in rows}


# ── list filters ─────────────────────────────────────────────────────────────


def test_list_filters(admin, tech_a):
    ac_id = _new_ac("FL")
    wo = _create(admin, ac_id, technician_id=TECH_A, priority="CRITICAL").get_json()["data"]

    r = admin.get(f"/api/v1/maintenance?priority=CRITICAL&ac_id={ac_id}")
    assert r.status_code == 200
    assert [item["id"] for item in r.get_json()["data"]] == [wo["id"]]

    r = admin.get(f"/api/v1/maintenance?q=quarterly&ac_id={ac_id}")
    assert r.status_code == 200
    assert [item["id"] for item in r.get_json()["data"]] == [wo["id"]]

    r = admin.get(f"/api/v1/maintenance?from={date.today().isoformat()}&ac_id={ac_id}")
    assert r.status_code == 200
    assert [item["id"] for item in r.get_json()["data"]] == [wo["id"]]

    r = admin.get("/api/v1/maintenance?status=BOGUS")
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_STATUS"

    r = admin.get("/api/v1/maintenance?priority=BOGUS")
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_PRIORITY"

    r = admin.get("/api/v1/maintenance?from=not-a-date")
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_DATE"

    r = admin.get("/api/v1/maintenance?page=abc")
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_PAGINATION"


# ── reports ──────────────────────────────────────────────────────────────────


def test_reports_maintenance_trend(admin, tech_a):
    _create(
        admin,
        _new_ac("RPT"),
        technician_id=TECH_A,
        priority="CRITICAL",
        maintenance_type="Emergency callout",
    )

    r = admin.get("/api/v1/reports/maintenance-trend")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]

    # window: current month + 5 before it, oldest first, zero-filled shape.
    # The endpoint derives "current month" from the database clock (UTC), so
    # the expectation must come from UTC too — see the note in the lifecycle
    # test above; date.today() diverges for the first hours of every day.
    today = datetime.now(timezone.utc).date()
    expected = []
    y, m = today.year, today.month
    for _ in range(6):
        expected.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    expected.reverse()

    trend = data["monthlyTrend"]
    assert len(trend) >= 6
    assert [entry["fullMonth"] for entry in trend[:6]] == expected
    keys = {"month", "fullMonth", "total", "Preventive", "Repair", "Emergency", "Inspection"}
    for entry in trend:
        assert keys <= set(entry)

    current = trend[5]
    assert current["total"] >= 1
    assert current["Emergency"] >= 1  # "Emergency callout" normalizes

    dist = data["priorityDistribution"]
    assert [d["name"] for d in dist] == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    assert next(d for d in dist if d["name"] == "CRITICAL")["value"] >= 1

    stats = data["stats"]
    assert stats["total"] >= 1
    assert stats["scheduled"] + stats["assigned"] + stats["in_progress"] >= 1
    assert isinstance(stats["mttrDays"], (int, float))

    # technicians lack VIEW_REPORTS
    r = tech_a.get("/api/v1/reports/maintenance-trend")
    assert r.status_code == 403
    # anonymous callers get 401 before any data access (raw client: no token;
    # conftest's `client` fixture would also un-set JWT_SECRET for the whole
    # test, breaking the authed clients above)
    r = admin._client.get("/api/v1/reports/maintenance-trend")
    assert r.status_code == 401
