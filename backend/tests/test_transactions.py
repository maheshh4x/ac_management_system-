"""Transaction-safety tests: pool contract + movement completion atomicity."""

import json
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.v1 import movements as movements_mod  # noqa: E402
from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import TEST_USER_ID, AuthClient, make_token  # noqa: E402

# Phase 5: approve requires approver ≠ requester, so the requester must be a
# different profile from the admin client that approves/completes below.
TECH_REQ = "44444444-4444-4444-4444-444444444444"
_PROFILES = (
    (TEST_USER_ID, "tester@acms.test", "Test User", "SUPER_ADMIN"),
    (TECH_REQ, "tech-req@acms.test", "Tech Requester", "TECHNICIAN"),
)


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture(autouse=True)
def _movement_user_profile(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """asset_movements.requested_by/approved_by/completed_by and
    audit_logs.user_id have FKs to profiles, and the movement writer records
    the token subject — so those profiles must exist during these tests.
    They are removed again afterwards because profiles.role is authoritative
    over the JWT claim (leaving them would flip the role-matrix tests, which
    run tokens without a profile row on purpose).

    The upsert FORCES role/status/is_active rather than trusting a row that a
    previous interrupted run may have left behind (a stale PENDING/FALSE row
    used to turn the technician's request into 403 ACCOUNT_PENDING/USER_INACTIVE).
    """
    seeded = [row[0] for row in _PROFILES]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for pid, email, name, role in _PROFILES:
                cur.execute(
                    "INSERT INTO profiles (id, email, name, role, status, is_active) "
                    "VALUES (%s, %s, %s, %s, 'APPROVED', TRUE) "
                    "ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role, "
                    "status = 'APPROVED', is_active = TRUE",
                    (pid, email, name, role),
                )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Break FK references first (leftovers from interrupted runs),
            # then the profiles themselves.
            cur.execute(
                "UPDATE asset_movements SET requested_by = NULL, approved_by = NULL,"
                " completed_by = NULL"
                " WHERE requested_by = ANY(%s::uuid[])"
                "    OR approved_by = ANY(%s::uuid[])"
                "    OR completed_by = ANY(%s::uuid[])",
                (seeded, seeded, seeded),
            )
            cur.execute(
                "DELETE FROM audit_logs WHERE user_id = ANY(%s::uuid[])",
                (seeded,),
            )
            # notifications.user_id cascades with the profile row.
            cur.execute(
                "DELETE FROM movement_comments WHERE author_id = ANY(%s::uuid[])",
                (seeded,),
            )
            cur.execute(
                "DELETE FROM profiles WHERE id = ANY(%s::uuid[])",
                (seeded,),
            )
        conn.commit()
    finally:
        conn.close()


def _ac_exists(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM acs WHERE ac_id = %s", (ac_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def _ac_room(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT room_id FROM acs WHERE ac_id = %s", (ac_id,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _movement_status(movement_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM asset_movements WHERE id::text = %s", (movement_id,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _two_rooms():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT room_id FROM rooms ORDER BY room_id LIMIT 2")
            rooms = [row[0] for row in cur.fetchall()]
    finally:
        conn.close()
    if len(rooms) < 2:
        pytest.skip("movement atomicity test needs two seeded rooms")
    return rooms


def _cleanup(ac_id=None, movement_id=None):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if movement_id:
                cur.execute("DELETE FROM audit_logs WHERE entity_id = %s", (movement_id,))
                # Phase 5: room-change ledger rows point at the movement.
                cur.execute(
                    "DELETE FROM asset_location_history WHERE movement_id::text = %s",
                    (movement_id,),
                )
                cur.execute("DELETE FROM asset_movements WHERE id::text = %s", (movement_id,))
            if ac_id:
                cur.execute("DELETE FROM audit_logs WHERE entity_id = %s", (ac_id,))
                cur.execute("DELETE FROM asset_location_history WHERE ac_id = %s", (ac_id,))
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
        conn.commit()
    finally:
        conn.close()


# ── Pool contract ───────────────────────────────────────────────────────────


def test_close_never_commits_plain_usage():
    ac_id = f"TXN-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id) VALUES (%s)", (ac_id,))
        # deliberately no commit — close() must roll this back
    finally:
        conn.close()
    assert _ac_exists(ac_id) is False


def test_plain_usage_requires_and_honours_explicit_commit():
    ac_id = f"TXN-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id) VALUES (%s)", (ac_id,))
        conn.commit()
    finally:
        conn.close()
    assert _ac_exists(ac_id) is True
    _cleanup(ac_id=ac_id)


def test_with_block_commits_on_success_and_rolls_back_on_exception():
    ac_ok = f"TXN-{uuid.uuid4().hex[:8]}"
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO acs (ac_id) VALUES (%s)", (ac_ok,))
    assert _ac_exists(ac_ok) is True
    _cleanup(ac_id=ac_ok)

    ac_fail = f"TXN-{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError):
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id) VALUES (%s)", (ac_fail,))
            raise RuntimeError("simulated request failure")
    assert _ac_exists(ac_fail) is False


def test_pool_returns_connection_exactly_once_on_double_return():
    conn = get_db_connection()
    conn.close()
    conn.close()  # must be a no-op, not a second putconn (would corrupt the pool)
    other = get_db_connection()  # a sane connection must still be available
    try:
        with other.cursor() as cur:
            cur.execute("SELECT 1")
        other.commit()
    finally:
        other.close()


# ── Movement completion atomicity (asset room + movement status) ────────────


def test_movement_complete_atomicity(client, monkeypatch):
    src_room, dst_room = _two_rooms()
    ac_id = f"TXN-MV-{uuid.uuid4().hex[:8]}"
    movement_id = None
    try:
        r = client.post("/api/v1/assets", json={"ac_id": ac_id, "room_id": src_room})
        assert r.status_code == 201
        # Phase 5: a technician requests (approver ≠ requester is enforced),
        # the admin client below approves and completes it.
        tech_client = AuthClient(client._client, make_token("TECHNICIAN", sub=TECH_REQ))
        r = tech_client.post(
            "/api/v1/movements", json={"ac_id": ac_id, "to_room_id": dst_room, "reason": "txn test"}
        )
        assert r.status_code == 201, r.get_json()
        movement_id = r.get_json()["data"]["id"]
        r = client.post(f"/api/v1/movements/{movement_id}/approve")
        assert r.status_code == 200
        assert _movement_status(movement_id) == "APPROVED"

        # Inject a mid-transaction failure: the asset UPDATE has happened,
        # then the movement-status UPDATE explodes.
        real_factory = movements_mod.get_db_connection

        class _ExplodingCursor:
            def __init__(self, real):
                self._real = real

            def execute(self, sql, params=None):
                if "SET status='COMPLETED'" in str(sql):
                    raise psycopg2.OperationalError("simulated mid-transaction failure")
                return self._real.execute(sql, params)

            def __getattr__(self, name):
                return getattr(self._real, name)

        class _ProxyConn:
            def __init__(self, real):
                self._real = real

            def cursor(self, *args, **kwargs):
                return _ExplodingCursor(self._real.cursor(*args, **kwargs))

            def __getattr__(self, name):
                return getattr(self._real, name)

        monkeypatch.setattr(movements_mod, "get_db_connection", lambda: _ProxyConn(real_factory()))
        try:
            r = client.post(f"/api/v1/movements/{movement_id}/complete")
            assert r.status_code == 500
            body = json.dumps(r.get_json())
            for leak in ("simulated", "OperationalError", "psycopg2", "UPDATE"):
                assert leak not in body
        finally:
            monkeypatch.setattr(movements_mod, "get_db_connection", real_factory)

        # Both sides rolled back atomically — no partial state.
        assert _ac_room(ac_id) == src_room
        assert _movement_status(movement_id) == "APPROVED"

        # The same request without the injected failure commits both sides.
        r = client.post(f"/api/v1/movements/{movement_id}/complete")
        assert r.status_code == 200
        assert _ac_room(ac_id) == dst_room
        assert _movement_status(movement_id) == "COMPLETED"
    finally:
        _cleanup(ac_id=ac_id, movement_id=movement_id)
