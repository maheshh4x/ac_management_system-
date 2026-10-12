"""Phase 8.3 — object-level authorization (OWASP API1 / IDOR) evidence.

The plan names three scenarios, plus the read/write surface each one implies.
Everything here is proven by calling the running Flask app, not by reading
code:

  1. technician A reading technician B's jobs
  2. viewer fetching /users
  3. manager outside assigned blocks

For (1) both paths matter. The list was already scoped
(``test_technician_sees_only_own_jobs``), but the *by-id* endpoints are the
classic direct-object-reference: ``GET /maintenance/<id>``,
``PATCH /maintenance/<id>`` and ``POST /maintenance/<id>/comments``. They must
agree with the list — a job the list hides must be unreachable by id too.
The lifecycle writes were already guarded by ``_owner_or_manager``; the test
below pins that down so the guard cannot silently regress.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import (  # noqa: E402
    TEST_SECRET,
    TEST_SUPABASE_URL,
    AuthClient,
    make_token,
)

# Deliberately outside the 1111…/3333…/4444… ranges other suites seed, so a
# leak here shows up as a failure there rather than silently passing.
ADMIN = "9a000000-0000-4000-8000-0000000000a1"
TECH_IA = "9a000000-0000-4000-8000-0000000000a2"
TECH_IB = "9a000000-0000-4000-8000-0000000000a3"
VIEWER_I = "9a000000-0000-4000-8000-0000000000a4"
MGR_I = "9a000000-0000-4000-8000-0000000000a5"
PEND_A = "9a000000-0000-4000-8000-0000000000b1"  # pending, block A
PEND_B = "9a000000-0000-4000-8000-0000000000b2"  # pending, block B

BLOCK_A = "IDOR-BLOCK-A"
BLOCK_B = "IDOR-BLOCK-B"

PROFILES = (ADMIN, TECH_IA, TECH_IB, VIEWER_I, MGR_I, PEND_A, PEND_B)
AC_PREFIX = "P8-IDOR-%"


@pytest.fixture(autouse=True)
def _idor_profiles(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """Seed every actor these checks need and remove all traces afterwards."""
    seed = (
        (ADMIN, "idor-admin@acms.test", "Idor Admin", "SUPER_ADMIN", "APPROVED", None),
        (TECH_IA, "idor-tech-a@acms.test", "Idor Tech A", "TECHNICIAN", "APPROVED", None),
        (TECH_IB, "idor-tech-b@acms.test", "Idor Tech B", "TECHNICIAN", "APPROVED", None),
        (VIEWER_I, "idor-viewer@acms.test", "Idor Viewer", "VIEWER", "APPROVED", None),
        (MGR_I, "idor-mgr@acms.test", "Idor Manager", "FACILITY_MANAGER", "APPROVED", [BLOCK_A]),
        (PEND_A, "idor-pend-a@acms.test", "Idor Pending A", "VIEWER", "PENDING", [BLOCK_A]),
        (PEND_B, "idor-pend-b@acms.test", "Idor Pending B", "VIEWER", "PENDING", [BLOCK_B]),
    )
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for pid, email, name, role, status, blocks in seed:
                cur.execute(
                    "INSERT INTO profiles"
                    " (id, email, name, role, status, is_active, assigned_blocks)"
                    " VALUES (%s, %s, %s, %s, %s, TRUE, %s)"
                    " ON CONFLICT (id) DO UPDATE SET assigned_blocks = EXCLUDED.assigned_blocks,"
                    "                                status = EXCLUDED.status,"
                    "                                role = EXCLUDED.role",
                    (pid, email, name, role, status, blocks),
                )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Children of the work orders first, then anything that points at
            # these profiles (audit_logs has a plain FK, no cascade).
            for table in ("maintenance_events", "maintenance_comments"):
                cur.execute(
                    f"DELETE FROM {table} WHERE work_order_id IN ("
                    "  SELECT id FROM maintenance_work_orders WHERE ac_id LIKE %s)",
                    (AC_PREFIX,),
                )
            cur.execute(
                "DELETE FROM audit_logs"
                " WHERE user_id = ANY(%s::uuid[])"
                "    OR entity_id IN (SELECT id::text FROM maintenance_work_orders"
                "                      WHERE ac_id LIKE %s)"
                "    OR entity_id IN (SELECT ac_id FROM acs WHERE ac_id LIKE %s)",
                (list(PROFILES), AC_PREFIX, AC_PREFIX),
            )
            cur.execute(
                "DELETE FROM notifications WHERE user_id = ANY(%s::uuid[])",
                (list(PROFILES),),
            )
            cur.execute(
                "DELETE FROM maintenance_work_orders WHERE ac_id LIKE %s"
                "    OR created_by = ANY(%s::uuid[])",
                (AC_PREFIX, list(PROFILES)),
            )
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (AC_PREFIX,))
            cur.execute("DELETE FROM profiles WHERE id = ANY(%s::uuid[])", (list(PROFILES),))
        conn.commit()
    finally:
        conn.close()


# ── fixtures ─────────────────────────────────────────────────────────────────


def _client(monkeypatch, role, sub):
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
def admin(monkeypatch, db):  # noqa: ARG001
    """SUPER_ADMIN on this module's own seeded profile.

    Deliberately *not* ``db_admin_client``: that signs for the shared
    TEST_USER_ID row, and seeding/owning that row broke other suites'
    teardowns (their profile delete hit an unrelated FK and leaked work
    orders). Everything here stays inside the 9a… range.
    """
    return _client(monkeypatch, "SUPER_ADMIN", ADMIN)


@pytest.fixture()
def tech_a(monkeypatch, db):  # noqa: ARG001
    return _client(monkeypatch, "TECHNICIAN", TECH_IA)


@pytest.fixture()
def tech_b(monkeypatch, db):  # noqa: ARG001
    return _client(monkeypatch, "TECHNICIAN", TECH_IB)


@pytest.fixture()
def viewer(monkeypatch, db):  # noqa: ARG001
    return _client(monkeypatch, "VIEWER", VIEWER_I)


@pytest.fixture()
def manager(monkeypatch, db):  # noqa: ARG001
    return _client(monkeypatch, "FACILITY_MANAGER", MGR_I)


# ── helpers ──────────────────────────────────────────────────────────────────


def _new_ac() -> str:
    ac_id = f"{AC_PREFIX.rstrip('%')}{uuid.uuid4().hex[:6]}"
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
        "scheduled_date": datetime.now(timezone.utc).date().isoformat(),
        "problem_description": "quarterly service",
    }
    body.update(overrides)
    return client.post("/api/v1/maintenance", json=body)


def _job_assigned_to_admin(client) -> str:
    """A job owned by ADMIN (created_by) and assigned to TECH_IA.

    Neither ``technician_id`` nor ``created_by`` is TECH_IB, so it is outside
    tech B's scope on every by-id path.
    """
    resp = _create(client, _new_ac(), technician_id=TECH_IA)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["data"]["id"]


def _code(resp) -> str:
    return resp.get_json()["error"]["code"]


# ── 1. technician A reading technician B's jobs ──────────────────────────────


def test_technician_cannot_fetch_a_colleagues_job_by_id(admin, tech_a, tech_b):
    wo = _job_assigned_to_admin(admin)

    # The list already hides it…
    listing = tech_b.get("/api/v1/maintenance?page_size=200")
    assert listing.status_code == 200
    assert wo not in {item["id"] for item in listing.get_json()["data"]}

    # …so the direct object reference must hide it too.
    detail = tech_b.get(f"/api/v1/maintenance/{wo}")
    assert detail.status_code == 404
    assert _code(detail) == "WORK_ORDER_NOT_FOUND"

    # And the owner still reads it: the scope must not over-restrict.
    own = tech_a.get(f"/api/v1/maintenance/{wo}")
    assert own.status_code == 200
    assert own.get_json()["data"]["id"] == wo


def test_admin_and_viewer_still_read_any_job(admin, viewer):
    wo = _job_assigned_to_admin(admin)

    for client, who in ((admin, "admin"), (viewer, "viewer")):
        r = client.get(f"/api/v1/maintenance/{wo}")
        assert r.status_code == 200, f"{who} lost read access to {wo}"
        assert r.get_json()["data"]["id"] == wo


def test_technician_cannot_comment_on_a_colleagues_job(admin, tech_b):
    wo = _job_assigned_to_admin(admin)

    r = tech_b.post(f"/api/v1/maintenance/{wo}/comments", json={"body": "hello"})
    assert r.status_code == 404
    assert _code(r) == "WORK_ORDER_NOT_FOUND"

    # No comment row was written by the rejected call.
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM maintenance_comments WHERE work_order_id = %s", (wo,))
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()


def test_technician_cannot_rewrite_a_colleagues_job(admin, tech_b):
    wo = _job_assigned_to_admin(admin)

    r = tech_b.patch(f"/api/v1/maintenance/{wo}", json={"priority": "LOW"})
    assert r.status_code == 404
    assert _code(r) == "NOT_FOUND"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT priority FROM maintenance_work_orders WHERE id = %s", (wo,))
            assert cur.fetchone()[0] == "MEDIUM"  # untouched
    finally:
        conn.close()


def test_technician_cannot_run_a_lifecycle_action_on_a_colleagues_job(admin, tech_b):
    """The state machine already guards writes — pin it so it cannot regress."""
    wo = _job_assigned_to_admin(admin)

    r = tech_b.post(f"/api/v1/maintenance/{wo}/accept")
    assert r.status_code == 403
    assert _code(r) == "FORBIDDEN"
    assert "assigned to you" in r.get_json()["error"]["message"]


def test_a_missing_job_and_a_foreign_job_are_indistinguishable(admin, tech_b):
    """404 must not leak which ids exist (uniform response shape)."""
    wo = _job_assigned_to_admin(admin)
    missing = "9a000000-0000-4000-8000-00000000ffff"

    foreign = tech_b.get(f"/api/v1/maintenance/{wo}")
    absent = tech_b.get(f"/api/v1/maintenance/{missing}")
    assert foreign.status_code == absent.status_code == 404
    assert _code(foreign) == _code(absent)


# ── 2. viewer fetching /users ────────────────────────────────────────────────


def test_viewer_cannot_list_users(admin, viewer):
    r = viewer.get("/api/v1/users?page_size=200")
    assert r.status_code == 403
    assert _code(r) == "FORBIDDEN"


def test_viewer_cannot_approve_profiles(admin, viewer):
    r = viewer.post(f"/api/v1/approvals/{PEND_A}/approve", json={})
    assert r.status_code == 403
    assert _code(r) == "FORBIDDEN"


# ── 3. manager outside assigned blocks ───────────────────────────────────────


def test_manager_sees_only_pending_profiles_in_their_blocks(admin, manager):
    r = manager.get("/api/v1/approvals?status=PENDING&page_size=200")
    assert r.status_code == 200
    seen = {item["id"] for item in r.get_json()["data"]}

    assert PEND_A in seen, "manager lost sight of a profile in an assigned block"
    assert PEND_B not in seen, "manager saw a profile outside every assigned block"


def test_unscoped_manager_sees_every_block(admin, monkeypatch, db):  # noqa: ARG001
    unscoped = "9a000000-0000-4000-8000-0000000000a6"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles"
                " (id, email, name, role, status, is_active, assigned_blocks)"
                " VALUES (%s, 'idor-unscoped@acms.test', 'Idor Unscoped',"
                "         'FACILITY_MANAGER', 'APPROVED', TRUE, NULL)"
                " ON CONFLICT (id) DO NOTHING",
                (unscoped,),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        client = _client(monkeypatch, "FACILITY_MANAGER", unscoped)
        r = client.get("/api/v1/approvals?status=PENDING&page_size=200")
        assert r.status_code == 200
        seen = {item["id"] for item in r.get_json()["data"]}
        assert PEND_A in seen and PEND_B in seen
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM profiles WHERE id = %s", (unscoped,))
            conn.commit()
        finally:
            conn.close()
