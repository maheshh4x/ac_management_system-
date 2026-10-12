"""Phase 8.1 — concurrency tests: movement complete and sync replay.

Both hot paths were written for a race and both were only ever exercised
serially:

* ``POST /movements/<id>/complete`` locks the asset row and re-reads the
  movement *after* the lock, so a second completer must lose with 409 instead
  of inserting a second ``asset_location_history`` row.
* ``POST /maintenance/sync`` de-duplicates on ``client_mutation_id``, with
  ``maintenance_events`` / ``maintenance_work_orders`` unique constraints as
  the backstop when two threads pass the pre-check before either commits.

Workers are released from a ``threading.Barrier`` so the requests overlap,
each thread gets its own test client, and every assertion is made against the
database afterwards — a "1 applied / N-1 duplicate" tally means nothing if the
table then shows two ledger rows.

Rate limiting is disabled for the duration: flask-limiter would otherwise turn
the very burst under test into 429s and hide the state conflict.
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import TEST_SECRET, TEST_SUPABASE_URL, auth, make_token  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="needs a live database — set TEST_DATABASE_URL"
)

WORKERS = 5


# ── helpers ──────────────────────────────────────────────────────────────────


def _rows(sql: str, params=()) -> list[tuple]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


def _scalar(sql: str, params=()):
    rows = _rows(sql, params)
    return rows[0][0] if rows else None


def _burst(send):
    """Run ``send()`` in WORKERS threads released at the same instant."""
    barrier = threading.Barrier(WORKERS, timeout=30)
    results: list = [None] * WORKERS

    def worker(index: int) -> None:
        try:
            barrier.wait()
            results[index] = send()
        except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
            results[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    raised = [r for r in results if isinstance(r, BaseException)]
    assert not raised, f"{len(raised)} worker(s) exploded: {raised!r}"
    assert all(r is not None for r in results), "a worker never returned"
    return results


@pytest.fixture()
def race_app(db, monkeypatch):  # noqa: ARG001 - db pins the target database
    """One app instance; every worker builds its own client from it."""
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")

    from app import create_app
    from app.auth import jwt as jwt_mod
    from app.extensions import limiter

    jwt_mod.reset_caches()
    app = create_app()
    app.config["TESTING"] = True
    monkeypatch.setattr(limiter, "enabled", False)
    return app


@pytest.fixture()
def profiles(db):  # noqa: ARG001 - db pins (and resets) the connection pool first
    """Throwaway profile rows, removed (with their audit trail) afterwards."""
    created: list[str] = []

    def add(role: str, status: str = "APPROVED") -> str:
        pid = str(uuid.uuid4())
        _rows(
            "INSERT INTO profiles (id, email, name, role, status) VALUES (%s,%s,%s,%s,%s)",
            (pid, f"{pid[:8]}@acms.test", "P8 Race", role, status),
        )
        created.append(pid)
        return pid

    yield add

    if not created:
        return
    _rows("DELETE FROM audit_logs WHERE user_id = ANY(%s::uuid[])", (created,))
    _rows("DELETE FROM notifications WHERE user_id = ANY(%s::uuid[])", (created,))
    _rows("DELETE FROM profiles WHERE id = ANY(%s::uuid[])", (created,))


# ── movement complete ────────────────────────────────────────────────────────


def test_concurrent_movement_complete_has_exactly_one_winner(race_app, profiles):
    actor = profiles("SUPER_ADMIN")

    rooms = _rows("SELECT room_id FROM rooms ORDER BY room_id LIMIT 1")
    if not rooms:
        pytest.skip("no rooms in the test database")
    target_room = rooms[0][0]

    # Our own history-free asset: a freshly migrated database has no AC rows,
    # and borrowing a shared one used to turn this race into a skip. NULL
    # from_room_id is what the original ambient (never-installed) ACs looked
    # like, so the completion still installs the AC in the target room.
    ac_id = f"P8-RACE-{uuid.uuid4().hex[:8]}"
    _rows("INSERT INTO acs (ac_id, room_id) VALUES (%s, NULL)", (ac_id,))
    original_room = None

    mv_id = str(uuid.uuid4())
    _rows(
        "INSERT INTO asset_movements"
        " (id, ac_id, from_room_id, to_room_id, requested_by, status, reason)"
        " VALUES (%s,%s,%s,%s,%s,'APPROVED','phase 8 concurrency')",
        (mv_id, ac_id, original_room, target_room, actor),
    )

    headers = auth(make_token("SUPER_ADMIN", sub=actor))
    try:
        responses = _burst(
            lambda: race_app.test_client().post(f"/api/v1/movements/{mv_id}/complete", headers=headers)
        )

        codes = [r.status_code for r in responses]
        assert codes.count(200) == 1, f"expected exactly one winner, got {codes}"
        assert set(codes) <= {200, 409}, f"unexpected status from a racer: {codes}"

        for response in responses:
            if response.status_code == 409:
                assert response.get_json()["error"]["code"] == "INVALID_STATE"

        winner = next(r for r in responses if r.status_code == 200)
        data = winner.get_json()["data"]
        assert data["status"] == "COMPLETED"
        assert data["room_id"] == target_room

        # The database — not the response tally — decides the test.
        assert (
            _scalar("SELECT count(*) FROM asset_location_history WHERE movement_id = %s", (mv_id,)) == 1
        ), "a duplicate history row was written"
        assert (
            _scalar(
                "SELECT count(*) FROM asset_movements WHERE id = %s AND status = 'COMPLETED'",
                (mv_id,),
            )
            == 1
        )
        assert _scalar("SELECT room_id FROM acs WHERE ac_id = %s", (ac_id,)) == target_room
        assert (
            _scalar(
                "SELECT count(*) FROM audit_logs" " WHERE entity_id = %s AND action = 'COMPLETE_MOVEMENT'",
                (mv_id,),
            )
            == 1
        ), "side effects were emitted by more than one request"
    finally:
        _rows("UPDATE acs SET room_id = %s WHERE ac_id = %s", (original_room, ac_id))
        _rows("DELETE FROM asset_location_history WHERE movement_id = %s::uuid", (mv_id,))
        _rows(
            "DELETE FROM audit_logs WHERE entity_id = %s OR user_id = %s::uuid",
            (mv_id, actor),
        )
        _rows("DELETE FROM asset_movements WHERE id = %s::uuid", (mv_id,))
        _rows("DELETE FROM acs WHERE ac_id = %s", (ac_id,))


def test_sequential_second_complete_is_always_a_conflict(race_app, profiles):
    """The serial equivalent: completion is a one-way transition."""
    actor = profiles("SUPER_ADMIN")
    room = _rows("SELECT room_id FROM rooms ORDER BY room_id LIMIT 1")
    if not room:
        pytest.skip("no rooms in the test database")
    target_room = room[0][0]

    ac_id = f"P8-RACE-{uuid.uuid4().hex[:8]}"
    _rows("INSERT INTO acs (ac_id, room_id) VALUES (%s, NULL)", (ac_id,))
    original_room = None

    mv_id = str(uuid.uuid4())
    _rows(
        "INSERT INTO asset_movements"
        " (id, ac_id, from_room_id, to_room_id, requested_by, status)"
        " VALUES (%s,%s,%s,%s,%s,'APPROVED')",
        (mv_id, ac_id, original_room, target_room, actor),
    )
    headers = auth(make_token("SUPER_ADMIN", sub=actor))
    client = race_app.test_client()
    try:
        first = client.post(f"/api/v1/movements/{mv_id}/complete", headers=headers)
        assert first.status_code == 200, first.get_json()

        second = client.post(f"/api/v1/movements/{mv_id}/complete", headers=headers)
        assert second.status_code == 409
        assert second.get_json()["error"]["code"] == "INVALID_STATE"
        assert second.get_json()["error"]["details"]["status"] == "COMPLETED"

        assert (
            _scalar("SELECT count(*) FROM asset_location_history WHERE movement_id = %s::uuid", (mv_id,)) == 1
        )
    finally:
        _rows("UPDATE acs SET room_id = %s WHERE ac_id = %s", (original_room, ac_id))
        _rows("DELETE FROM asset_location_history WHERE movement_id = %s::uuid", (mv_id,))
        _rows(
            "DELETE FROM audit_logs WHERE entity_id = %s OR user_id = %s::uuid",
            (mv_id, actor),
        )
        _rows("DELETE FROM asset_movements WHERE id = %s::uuid", (mv_id,))
        _rows("DELETE FROM acs WHERE ac_id = %s", (ac_id,))


# ── sync replay ──────────────────────────────────────────────────────────────


@pytest.fixture()
def race_job(profiles):
    """One ASSIGNED work order owned by a freshly created technician."""
    tech = profiles("TECHNICIAN")
    ac_id = f"P8-RACE-{uuid.uuid4().hex[:8]}"
    _rows("INSERT INTO acs (ac_id) VALUES (%s)", (ac_id,))
    rows = _rows(
        "INSERT INTO maintenance_work_orders"
        " (ac_id, technician_id, priority, status, assigned_at)"
        " VALUES (%s,%s,'MEDIUM','ASSIGNED',NOW()) RETURNING id",
        (ac_id, tech),
    )
    job_id = str(rows[0][0])
    yield {"job_id": job_id, "ac_id": ac_id, "technician_id": tech}

    _rows("DELETE FROM maintenance_events WHERE work_order_id = %s::uuid", (job_id,))
    _rows("DELETE FROM maintenance_work_orders WHERE id = %s::uuid", (job_id,))
    _rows("DELETE FROM acs WHERE ac_id = %s", (ac_id,))


def test_concurrent_sync_replay_applies_the_mutation_exactly_once(race_app, race_job):
    tech = race_job["technician_id"]
    headers = auth(make_token("TECHNICIAN", sub=tech))
    cmid = str(uuid.uuid4())
    batch = {
        "mutations": [
            {
                "client_mutation_id": cmid,
                "action": "accept",
                "job_id": race_job["job_id"],
                "payload": {},
            }
        ]
    }

    responses = _burst(
        lambda: race_app.test_client().post("/api/v1/maintenance/sync", json=batch, headers=headers)
    )

    bodies = []
    for response in responses:
        assert response.status_code == 200, response.get_json()
        body = response.get_json()["data"]
        assert len(body["results"]) == 1
        bodies.append(body)

    assert sum(b["applied"] for b in bodies) == 1, [b["results"] for b in bodies]
    assert sum(b["duplicate"] for b in bodies) == WORKERS - 1, [b["results"] for b in bodies]
    assert sum(b["rejected"] for b in bodies) == 0, [b["results"] for b in bodies]

    # The tally is self-reported; the tables are the evidence.
    assert (
        _scalar("SELECT count(*) FROM maintenance_events WHERE client_mutation_id = %s", (cmid,)) == 1
    ), "the ledger recorded the same client mutation more than once"
    assert (
        _scalar(
            "SELECT count(*) FROM maintenance_work_orders WHERE client_mutation_id = %s",
            (cmid,),
        )
        == 1
    )
    assert (
        _scalar(
            "SELECT count(*) FROM maintenance_events WHERE work_order_id = %s::uuid",
            (race_job["job_id"],),
        )
        == 1
    ), "the job timeline grew more than one row"
    assert (
        _scalar(
            "SELECT accepted_at FROM maintenance_work_orders WHERE id = %s::uuid",
            (race_job["job_id"],),
        )
        is not None
    )


def test_sync_replay_after_the_race_is_still_a_duplicate(race_app, race_job):
    """A late retry (the original bug) must stay idempotent once settled."""
    tech = race_job["technician_id"]
    headers = auth(make_token("TECHNICIAN", sub=tech))
    cmid = str(uuid.uuid4())
    batch = {
        "mutations": [
            {
                "client_mutation_id": cmid,
                "action": "accept",
                "job_id": race_job["job_id"],
                "payload": {},
            }
        ]
    }

    first = race_app.test_client().post("/api/v1/maintenance/sync", json=batch, headers=headers)
    assert first.status_code == 200
    assert first.get_json()["data"]["applied"] == 1

    late = race_app.test_client().post("/api/v1/maintenance/sync", json=batch, headers=headers)
    assert late.status_code == 200
    data = late.get_json()["data"]
    assert data["applied"] == 0
    assert data["duplicate"] == 1

    assert _scalar("SELECT count(*) FROM maintenance_events WHERE client_mutation_id = %s", (cmid,)) == 1
