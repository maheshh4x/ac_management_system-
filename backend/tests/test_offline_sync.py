"""Phase 4: offline mutation sync (POST /api/v1/maintenance/sync).

Covers the plan's done-when: a job created online, worked offline and synced
back replays idempotently (same client_mutation_id → duplicate, DB untouched),
per-item results carry domain errors, and authorization is re-checked inside
the state machine for every replayed mutation (server wins).
"""

from __future__ import annotations

import sys
import uuid
from datetime import date
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

TECH_A = "44444444-4444-4444-4444-444444444441"
TECH_B = "44444444-4444-4444-4444-444444444442"
VIEWER_U = "44444444-4444-4444-4444-444444444443"
AC_PREFIX = "P4SYNC-AC-%"
PROFILE_IDS = (TEST_USER_ID, TECH_A, TECH_B, VIEWER_U)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _p4_sync_profiles(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    seed = (
        (TEST_USER_ID, "tester@acms.test", "Test User", "SUPER_ADMIN"),
        (TECH_A, "sync-tech-a@acms.test", "Sync Tech A", "TECHNICIAN"),
        (TECH_B, "sync-tech-b@acms.test", "Sync Tech B", "TECHNICIAN"),
        (VIEWER_U, "sync-viewer@acms.test", "Sync Viewer", "VIEWER"),
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
    ac_id = f"P4SYNC-AC-{suffix}-{uuid.uuid4().hex[:6]}"
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
        "maintenance_type": "Repair",
        "priority": "MEDIUM",
        "scheduled_date": date.today().isoformat(),
        "problem_description": "offline sync test job",
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


def _wo_row(wo_id):
    return _fetch(
        "SELECT status, accepted_at, client_mutation_id" " FROM maintenance_work_orders WHERE id = %s",
        (wo_id,),
    )


def _mutation(cmid, action, job_id, payload=None):
    return {
        "client_mutation_id": cmid,
        "action": action,
        "job_id": job_id,
        "payload": payload or {},
    }


def _error_code(resp):
    return resp.get_json()["error"]["code"]


# ── replay idempotency ───────────────────────────────────────────────────────


def test_sync_replay_is_duplicate_and_db_unchanged(admin, tech_a):
    wo = _create(admin, _new_ac("RP"), technician_id=TECH_A).get_json()["data"]
    wo_id = wo["id"]
    batch = {"mutations": [_mutation(str(uuid.uuid4()), "accept", wo_id)]}

    r = tech_a.post("/api/v1/maintenance/sync", json=batch)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["applied"] == 1
    assert data["duplicate"] == 0
    assert data["rejected"] == 0
    assert data["results"][0]["result"] == "applied"

    row_before = _wo_row(wo_id)
    events_before = _events(wo_id)
    assert row_before[0][0] == "ASSIGNED"  # accept keeps the status
    assert row_before[0][1] is not None  # accepted_at set

    # network hiccup replay of the exact same batch
    r = tech_a.post("/api/v1/maintenance/sync", json=batch)
    data = r.get_json()["data"]
    assert data["applied"] == 0
    assert data["duplicate"] == 1
    assert data["rejected"] == 0
    assert data["results"][0]["result"] == "duplicate"

    assert _wo_row(wo_id) == row_before  # row byte-identical
    assert _events(wo_id) == events_before  # no duplicate timeline entries


def test_sync_partial_batch_reports_per_item(admin, tech_a):
    wo = _create(admin, _new_ac("PB"), technician_id=TECH_A).get_json()["data"]
    wo_id = wo["id"]
    terminal = _create(admin, _new_ac("PB2")).get_json()["data"]
    assert admin.post(f"/api/v1/maintenance/{terminal['id']}/cancel", json={}).status_code == 200

    r = tech_a.post(
        "/api/v1/maintenance/sync",
        json={
            "mutations": [
                _mutation(str(uuid.uuid4()), "accept", wo_id),
                _mutation(str(uuid.uuid4()), "accept", str(uuid.uuid4())),
                _mutation(str(uuid.uuid4()), "teleport", wo_id),
                "not-an-object",
                {"client_mutation_id": "", "action": "accept", "job_id": wo_id},
                _mutation(str(uuid.uuid4()), "accept", terminal["id"]),
            ]
        },
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    results = data["results"]
    assert [item["result"] for item in results] == [
        "applied",
        "rejected",
        "rejected",
        "rejected",
        "rejected",
        "rejected",
    ]
    assert results[1]["code"] == "WORK_ORDER_NOT_FOUND"
    assert results[2]["code"] == "UNSUPPORTED_ACTION"
    assert results[3]["code"] == "VALIDATION_ERROR"
    assert results[4]["code"] == "VALIDATION_ERROR"
    assert results[5]["code"] == "TERMINAL_STATE"
    assert data["applied"] == 1
    assert data["rejected"] == 5

    # one bad item must not roll back the good one
    assert _wo_row(wo_id)[0][1] is not None


# ── authorization on replay ──────────────────────────────────────────────────


def test_sync_authz_rejected_per_item_and_per_request(admin, tech_a, tech_b, viewer):
    wo = _create(admin, _new_ac("AZ"), technician_id=TECH_A).get_json()["data"]
    wo_id = wo["id"]

    # tech B has MANAGE_MAINTENANCE but is neither owner nor manager
    r = tech_b.post(
        "/api/v1/maintenance/sync",
        json={"mutations": [_mutation(str(uuid.uuid4()), "accept", wo_id)]},
    )
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["results"][0]["result"] == "rejected"
    assert data["results"][0]["code"] == "FORBIDDEN"

    rows = _fetch(
        "SELECT status, accepted_at FROM maintenance_work_orders WHERE id = %s",
        (wo_id,),
    )
    assert rows[0][0] == "ASSIGNED"
    assert rows[0][1] is None
    assert len(_events(wo_id)) == 2  # CREATED + ASSIGNED only

    # viewers lack MANAGE_MAINTENANCE → the whole batch is refused
    r = viewer.post("/api/v1/maintenance/sync", json={"mutations": []})
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"


# ── payload validation ───────────────────────────────────────────────────────


def test_sync_payload_validation(admin, tech_a):  # noqa: ARG001
    r = tech_a.post("/api/v1/maintenance/sync", json={})
    assert r.status_code == 400
    assert _error_code(r) == "VALIDATION_ERROR"

    r = tech_a.post("/api/v1/maintenance/sync", json={"mutations": "nope"})
    assert r.status_code == 400
    assert _error_code(r) == "VALIDATION_ERROR"

    r = tech_a.post("/api/v1/maintenance/sync", json={"mutations": [{}] * 101})
    assert r.status_code == 400
    assert _error_code(r) == "TOO_MANY_MUTATIONS"

    r = tech_a.post("/api/v1/maintenance/sync", json={"mutations": []})
    assert r.status_code == 200
    assert r.get_json()["data"] == {
        "results": [],
        "applied": 0,
        "duplicate": 0,
        "rejected": 0,
    }


# ── the done-when: created → worked offline → synced → completed ────────────


def test_sync_full_offline_lifecycle(admin, tech_a):
    wo = _create(admin, _new_ac("OF"), technician_id=TECH_A).get_json()["data"]
    wo_id = wo["id"]

    accept_cmid, start_cmid = str(uuid.uuid4()), str(uuid.uuid4())
    r = tech_a.post(
        "/api/v1/maintenance/sync",
        json={
            "mutations": [
                _mutation(accept_cmid, "accept", wo_id),
                _mutation(start_cmid, "start", wo_id),
            ]
        },
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["applied"] == 2
    assert data["rejected"] == 0
    assert _wo_row(wo_id)[0][0] == "IN_PROGRESS"

    # finishing the job online
    r = tech_a.post(
        f"/api/v1/maintenance/{wo_id}/complete",
        json={"work_performed": "replaced capacitor"},
    )
    assert r.status_code == 200, r.get_json()

    detail = admin.get(f"/api/v1/maintenance/{wo_id}").get_json()["data"]
    assert [e["event"] for e in detail["events"]] == [
        "CREATED",
        "ASSIGNED",
        "ACCEPTED",
        "STARTED",
        "COMPLETED",
    ]
    # offline provenance survives on the synced events
    assert [e["client_mutation_id"] for e in detail["events"] if e["client_mutation_id"]] == [
        accept_cmid,
        start_cmid,
    ]
    assert detail["status"] == "COMPLETED"

    # every step notified the job creator (sync included)
    rows = _fetch(
        "SELECT type FROM notifications WHERE user_id = %s AND entity_id = %s" " ORDER BY created_at",
        (TEST_USER_ID, wo_id),
    )
    assert [row[0] for row in rows] == [
        "JOB_ACCEPTED",
        "JOB_STARTED",
        "JOB_COMPLETED",
    ]

    # and the trail is fully audited
    rows = _fetch("SELECT action FROM audit_logs WHERE entity_id = %s", (wo_id,))
    assert {"ACCEPT_JOB", "START_JOB", "COMPLETE_JOB"} <= {row[0] for row in rows}
