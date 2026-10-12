"""Phase 8.1 — coverage for ``app.services.query_service``.

Every function here is a thin SQL wrapper behind ``execute_query``, so the
tests assert against the same tables with their own SQL: the wrapper is being
checked for *shape* and for *agreement with the database*, never for a
hard-coded row count that a later seed could invalidate.

``my_jobs`` / ``my_jobs_summary`` are the identity-scoped chat queries, so
they are probed with a profile id that owns nothing — they must return nothing.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.services import query_service as qs  # noqa: E402


def _scalar(sql: str, params=()):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()[0]
    finally:
        conn.close()


def _ids(sql: str, params=()) -> list[str]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [str(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


@pytest.fixture()
def sample_ac(db):  # noqa: ARG001
    """One AC row this module owns, bound to a room when the schema has one.

    The earlier revision picked an ambient row and skipped when ``acs`` was
    empty, so a freshly migrated test database silently turned a third of this
    file into skips (and the group-count tests into failures). Seeding here
    keeps every test runnable against an empty, migrated database.
    """
    ac_id = f"QS-AC-{uuid.uuid4().hex[:10]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT room_id FROM rooms ORDER BY room_id LIMIT 1")
            row = cur.fetchone()
            room_id = str(row[0]) if row else None
            cur.execute(
                "INSERT INTO acs"
                " (ac_id, room_id, ac_make_company, ac_model_number,"
                "  ac_serial_number, operational_status, ac_capacity_ton, ac_type)"
                " VALUES (%s, %s, 'Daikin', 'FTKM50', 'QS-SERIAL', 'WORKING', 1.5, 'Split')",
                (ac_id, room_id),
            )
        conn.commit()
    finally:
        conn.close()
    yield ac_id
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM maintenance_work_orders WHERE ac_id = %s", (ac_id,))
            cur.execute("DELETE FROM audit_logs WHERE entity_id = %s", (ac_id,))
            cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
        conn.commit()
    finally:
        conn.close()


def test_execute_query_returns_dicts(db):  # noqa: ARG001
    assert qs.execute_query("SELECT 1 AS one, 'two' AS two") == [{"one": 1, "two": "two"}]
    assert qs.execute_query("SELECT count(*) AS n FROM acs WHERE %s = %s", [1, 1]) == [{"n": qs.total_acs()}]
    assert qs.execute_query("SELECT ac_id FROM acs WHERE %s = %s", [1, 0]) == []


def test_total_acs_agrees_with_the_table(db):  # noqa: ARG001
    assert qs.total_acs() == _scalar("SELECT count(*) FROM acs")


def test_company_counts_sum_to_the_total(db, sample_ac):  # noqa: ARG001
    rows = qs.ac_company_counts()
    assert rows, "expected at least one company group"
    assert set(rows[0]) == {"company", "count"}
    assert sum(r["count"] for r in rows) == qs.total_acs()
    assert all(r["company"] for r in rows), "COALESCE must never emit NULL groups"
    assert all(r["count"] > 0 for r in rows)


def test_block_counts_sum_to_the_total(db, sample_ac):  # noqa: ARG001
    rows = qs.ac_block_counts()
    assert rows
    assert set(rows[0]) == {"block", "count"}
    assert sum(r["count"] for r in rows) == qs.total_acs()
    assert all(r["block"] for r in rows)


def test_total_tonnage_agrees_with_the_table(db):  # noqa: ARG001
    assert qs.total_tonnage() == _scalar("SELECT COALESCE(SUM(ac_capacity_ton), 0) FROM acs")


def test_missing_model_count_agrees_with_the_table(db):  # noqa: ARG001
    assert qs.missing_model_count() == _scalar(
        "SELECT count(*) FROM acs WHERE NULLIF(TRIM(ac_model_number), '') IS NULL"
    )


def _set_room(ac_id: str, room_id: str | None) -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE acs SET room_id = %s WHERE ac_id = %s", (room_id, ac_id))
        conn.commit()
    finally:
        conn.close()


def test_acs_in_location_joins_through_the_room_tree(db, sample_ac):  # noqa: ARG001
    """sample_ac ships bound to a room; re-bind it to prove the join resolves it."""
    rooms = _ids("SELECT room_id FROM rooms ORDER BY room_id LIMIT 1")
    if not rooms:
        pytest.skip("no rooms in the test database")
    room_id = rooms[0]
    room_name = _scalar("SELECT room_name FROM rooms WHERE room_id = %s", (room_id,))
    original = _scalar("SELECT room_id FROM acs WHERE ac_id = %s", (sample_ac,))
    _set_room(sample_ac, room_id)
    try:
        rows = qs.acs_in_location(room_name)
        assert sample_ac in {r["ac_id"] for r in rows}, "location match did not join the room"
        assert len({r["ac_id"] for r in rows}) == len(rows), "JOIN must not duplicate ACs"
        assert rows[0]["room_name"] == room_name
        assert qs.acs_in_location("definitely-not-a-place") == []
    finally:
        _set_room(sample_ac, original)


def test_ac_status_is_case_insensitive_and_uses_the_synonym_sets(db, sample_ac):  # noqa: ARG001
    working = qs.ac_status("Working")
    assert len(working) == len(qs.ac_status("working"))
    assert len(working) == _scalar("SELECT count(*) FROM acs WHERE operational_status = 'WORKING'")
    assert set(working[0]) >= {
        "ac_id",
        "ac_make_company",
        "operational_status",
        "ac_capacity_ton",
        "ac_type",
    }

    # The synonym branches select a whole *set* of spellings; they must agree
    # with the database's constrained vocabulary instead of falling through to
    # a literal free-text comparison (that column was retired by 013).
    assert {r["ac_id"] for r in qs.ac_status("not working")} == set(
        _ids("SELECT ac_id FROM acs" " WHERE operational_status IN ('FAULT', 'OFFLINE')")
    ), "the 'not working' synonyms did not cover their whole set"
    assert {r["ac_id"] for r in qs.ac_status("under maintenance")} == set(
        _ids("SELECT ac_id FROM acs WHERE operational_status = 'MAINTENANCE'")
    ), "the 'under maintenance' synonyms did not cover their whole set"
    assert qs.ac_status("Working") == qs.ac_status("working")
    # Unrecognised tokens resolve to nothing rather than probing free text.
    assert qs.ac_status("no-such-status-word") == []


def test_ac_by_company_matches_regardless_of_case(db, sample_ac):  # noqa: ARG001
    companies = _ids("SELECT DISTINCT ac_make_company FROM acs WHERE ac_make_company IS NOT NULL")
    if not companies:
        pytest.skip("no ACs with a company in the test database")
    company = companies[0]
    rows = qs.ac_by_company(company)
    assert rows == qs.ac_by_company(company.upper())
    assert len(rows) == _scalar(
        "SELECT count(*) FROM acs WHERE LOWER(ac_make_company) = LOWER(%s)", (company,)
    )
    assert set(rows[0]) >= {"ac_id", "ac_make_company", "operational_status"}
    assert qs.ac_by_company("No Such Company Ltd") == []


def test_ac_details_returns_one_fully_joined_row(db, sample_ac):  # noqa: ARG001
    rows = qs.ac_details(sample_ac)
    assert len(rows) == 1
    assert set(rows[0]) >= {
        "ac_id",
        "ac_make_company",
        "ac_capacity_ton",
        "operational_status",
        "room_name",
        "department_name",
        "floor_name",
        "block_name",
    }
    assert rows[0]["ac_id"] == sample_ac
    assert qs.ac_details("AC-DOES-NOT-EXIST") == []


def test_maintenance_helpers_agree_with_the_table(db, sample_ac):  # noqa: ARG001
    total = _scalar("SELECT count(*) FROM maintenance")
    assert qs.maintenance_count() == total
    assert len(qs.maintenance_list()) == total
    assert isinstance(qs.maintenance_for_ac(sample_ac), list)


def test_ac_list_returns_every_ac(db, sample_ac):  # noqa: ARG001
    rows = qs.ac_list()
    assert len(rows) == qs.total_acs()
    if not rows:
        pytest.skip("no AC rows in the test database")
    assert set(rows[0]) == {
        "ac_id",
        "ac_make_company",
        "ac_model_number",
        "operational_status",
        "ac_capacity_ton",
        "ac_type",
    }
    assert [r["ac_id"] for r in rows] == sorted(r["ac_id"] for r in rows)


def test_my_jobs_sees_nothing_for_a_profile_that_owns_nothing(db):  # noqa: ARG001
    stranger = str(uuid.uuid4())
    assert qs.my_jobs(stranger) == []
    summary = qs.my_jobs_summary(stranger)
    assert len(summary) == 1
    assert summary[0] == {
        "total": 0,
        "open": 0,
        "in_progress": 0,
        "overdue": 0,
        "completed": 0,
    }


def test_my_jobs_limit_is_honoured(db):  # noqa: ARG001
    stranger = str(uuid.uuid4())
    assert qs.my_jobs(stranger, limit=5) == []
    assert qs.my_jobs(stranger, limit=0) == []


@pytest.fixture()
def assigned_job(db, sample_ac):  # noqa: ARG001
    """One profile that owns exactly one work order, removed afterwards."""
    profile_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status)"
                " VALUES (%s, %s, 'P8 Job Owner', 'TECHNICIAN', 'APPROVED')",
                (profile_id, f"{profile_id[:8]}@acms.test"),
            )
            cur.execute(
                "INSERT INTO maintenance_work_orders (id, technician_id, ac_id, priority, status)"
                " VALUES (%s, %s, %s, 'HIGH', 'ASSIGNED')",
                (job_id, profile_id, sample_ac),
            )
        conn.commit()
    finally:
        conn.close()
    yield profile_id, job_id
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM maintenance_work_orders WHERE id = %s", (job_id,))
            cur.execute("DELETE FROM audit_logs WHERE user_id = %s", (profile_id,))
            cur.execute("DELETE FROM profiles WHERE id = %s", (profile_id,))
        conn.commit()
    finally:
        conn.close()


def test_my_jobs_returns_only_the_callers_own_rows(db, assigned_job):  # noqa: ARG001
    owner, job_id = assigned_job
    other = str(uuid.uuid4())

    rows = qs.my_jobs(owner)
    assert [str(r["id"]) for r in rows] == [job_id]
    assert qs.my_jobs(other) == [], "another profile saw someone else's work orders"

    summary = qs.my_jobs_summary(owner)[0]
    assert summary["total"] == 1
    assert summary["open"] == 1
    assert summary["in_progress"] == 0
    assert summary["overdue"] == 0
    assert summary["completed"] == 0

    assert qs.my_jobs_summary(other)[0]["total"] == 0
    assert qs.my_jobs(owner, limit=0) == []
