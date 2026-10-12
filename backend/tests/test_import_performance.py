"""Batched write path, location resolution and idempotent retries for
POST /api/v1/assets/import (timeout-fix verification).

Contracts pinned here (measured against the ACMS 301 shape: ~300 records):

* a 300-row commit executes a HANDFUL of statements, never ~1000 — the old
  per-row SAVEPOINT/INSERT/SELECT loop cost ~927 round trips (~39 s against
  a remote database); the chunked path measured 16,
* duplicate/update classification is unchanged by batching,
* resolution is read-only: importing never creates duplicate
  colleges/blocks/departments/rooms,
* a row that fails inside a chunk rolls back ONLY itself (savepoint
  fallback), including updates — the previous value survives,
* an identical resubmission carrying an ``Idempotency-Key`` replays the
  first report without touching the database, and an in-flight duplicate is
  rejected with 409 instead of importing twice.
"""

import csv
import io
import logging
import sys
import time
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import RealDictCursor  # noqa: E402

import app.api.v1.assets_import as ai_mod  # noqa: E402
from app.config.database import get_db_connection  # noqa: E402
from app.routes import acs as acs_routes  # noqa: E402

# Flexible-mode fixtures (no canonical markers → no campus-chain validation).
FLEX_HEADER = ["ac_id", "department", "location", "brand", "capacity", "year_of_purchase", "warranty_years"]
VALID_DEPT = "Computer Science"
VALID_ROOM = "CSE Computer Lab 1"

# Canonical fixtures — the real seeded campus chain of conftest.
COLLEGE = "National Institute of Technical Teachers Training and Research"
BLOCK = "Main Academic Block"
FLOOR = "Floor 1"
DEPT = "Computer Science"
ROOM = "CSE Computer Lab 1"  # room_name of ROOM-102
ROOM_ID = "ROOM-102"
CANONICAL_HEADER = [
    "college_name",
    "block_name",
    "floor_name",
    "department_name",
    "room_type",
    "room_number",
    "ac_make_company",
    "unit_number",
    "capacity_ton",
]


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture(autouse=True)
def _clean_and_isolate(client):  # noqa: ARG001 - client also pins the DB fixture
    """Drop stale perf artifacts and keep the idempotency registry pristine."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id LIKE 'IMPTEST-PERF-%'")
        conn.commit()
    finally:
        conn.close()
    with ai_mod._idempotency_lock:
        ai_mod._inflight_imports.clear()
        ai_mod._completed_imports.clear()
    yield
    with ai_mod._idempotency_lock:
        ai_mod._inflight_imports.clear()
        ai_mod._completed_imports.clear()


# ── statement counting (temporary instrumentation, test-local) ──────────────


class _CountingCursor:
    def __init__(self, cursor):
        self._c = cursor

    def execute(self, *args, **kwargs):
        _COUNTER["statements"] += 1
        return self._c.execute(*args, **kwargs)

    def __enter__(self):
        self._c.__enter__()
        return self

    def __exit__(self, *exc):
        return self._c.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._c, name)

    def __iter__(self):
        return iter(self._c)


class _CountingConn:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return _CountingCursor(self._conn.cursor(*args, **kwargs))

    def commit(self):
        _COUNTER["statements"] += 1
        return self._conn.commit()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        return self._conn.close()


_COUNTER = {"statements": 0}


@pytest.fixture()
def count_statements(monkeypatch):
    _COUNTER["statements"] = 0
    monkeypatch.setattr(ai_mod, "get_db_connection", lambda: _CountingConn(get_db_connection()))
    return _COUNTER


# ── helpers ────────────────────────────────────────────────────────────────


def _flex_csv(header, rows):
    lines = [",".join(header)] + [",".join("" if cell is None else str(cell) for cell in row) for row in rows]
    return "\n".join(lines) + "\n"


def _post_csv(client, body, filename="import.csv", headers=None):
    return client.post(
        "/api/v1/assets/import",
        data={"file": (io.BytesIO(body.encode("utf-8")), filename)},
        headers=headers or {},
    )


def _report(response):
    payload = response.get_json()
    assert payload["success"] is True, payload
    return payload["data"]


def _delete_acs(*ac_ids):
    ac_ids = tuple(ac_ids)
    if not ac_ids:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id IN %s", (ac_ids,))
        conn.commit()
    finally:
        conn.close()


def _ac_row(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT ac_id, ac_make_company FROM acs WHERE ac_id = %s", (ac_id,))
            return cur.fetchone()
    finally:
        conn.close()


def _count_acs(prefix):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM acs WHERE ac_id LIKE %s", (prefix + "%",))
            return cur.fetchone()[0]
    finally:
        conn.close()


def _campus_counts():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM colleges), (SELECT count(*) FROM blocks),"
                " (SELECT count(*) FROM departments), (SELECT count(*) FROM rooms)"
            )
            return tuple(cur.fetchone())
    finally:
        conn.close()


def _canonical_csv(rows, fields=None):
    fieldnames = fields or CANONICAL_HEADER
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    return buf.getvalue()


def _canonical_row(**over):
    base = {
        "college_name": COLLEGE,
        "block_name": BLOCK,
        "floor_name": FLOOR,
        "department_name": DEPT,
        "room_type": "Lab",
        "room_number": ROOM,
        "ac_make_company": "Daikin",
        "unit_number": "1",
        "capacity_ton": "1.5",
    }
    base.update(over)
    return base


def _perf_rows(suffix):
    """294 unique ac_ids + 6 in-file duplicates = 300 records (ACMS-301 scale)."""
    rows = []
    for n in range(294):
        rows.append(
            [
                f"IMPTEST-PERF-{suffix}-{n:03d}",
                VALID_DEPT,
                VALID_ROOM,
                "Voltas",
                "2",
                "2022",
                "3",
            ]
        )
    for n in range(6):  # duplicates of the first six ac_ids (later row wins)
        dup = list(rows[n])
        dup[4] = "5"
        rows.append(dup)
    return rows


# ── 1: 300-row performance path (statement count bound) ─────────────────────


def test_300_row_commit_executes_a_handful_of_statements(client, count_statements):
    suffix = uuid.uuid4().hex[:8]
    body = _flex_csv(FLEX_HEADER, _perf_rows(suffix))
    try:
        start = time.perf_counter()
        report = _report(_post_csv(client, body))
        wall = time.perf_counter() - start

        assert report["total_rows"] == 300
        assert report["created"] == 294
        assert report["duplicates"] == 6
        assert report["invalid"] == 0
        assert report["failed"] == 0
        assert report["attributes_recorded"] == 294  # one attr per unique ac_id
        assert _count_acs(f"IMPTEST-PERF-{suffix}") == 294

        # The whole 300-row commit (preloads + 3 chunks + uuid/attribute
        # statements + COMMIT) fits in a handful of round trips. The old
        # per-row path executed ~927 for this shape; anything near per-row
        # again means the batching regressed. Measured: 15.
        assert count_statements["statements"] <= 30, count_statements
        assert wall < 10  # generous smoke bound (local DB; RTT dominates remote)
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (f"IMPTEST-PERF-{suffix}",))
            conn.commit()
        finally:
            conn.close()


# ── 2: duplicate/update classification is unchanged by batching ─────────────


def test_reimport_classifies_updates_and_in_file_duplicates(client):
    suffix = uuid.uuid4().hex[:8]
    body = _flex_csv(FLEX_HEADER, _perf_rows(suffix))
    prefix = f"IMPTEST-PERF-{suffix}"
    try:
        first = _report(_post_csv(client, body))
        assert (first["created"], first["updated"], first["duplicates"]) == (294, 0, 6)

        second = _report(_post_csv(client, body))
        assert (second["created"], second["updated"], second["duplicates"]) == (0, 294, 6)
        assert _count_acs(prefix) == 294  # no duplicate rows were created
    finally:
        _delete_acs(*[f"{prefix}-{n:03d}" for n in range(294)])


# ── 3: location resolution stays read-only on the batched path ──────────────


def test_resolution_assigns_or_pends_without_duplicating_campus(client):
    unit_ok = uuid.uuid4().hex[:6]
    unit_pending = uuid.uuid4().hex[:6]
    unit_twin = uuid.uuid4().hex[:6]
    rows = [
        _canonical_row(unit_number=unit_ok),
        _canonical_row(unit_number=unit_twin),  # identical references → memo hit
        _canonical_row(room_number="NO SUCH ROOM", unit_number=unit_pending),
    ]
    body = _canonical_csv(rows)
    before = _campus_counts()
    created = []
    try:
        report = _report(_post_csv(client, body))
        assert report["total_rows"] == 3
        assert report["invalid"] == 0
        assert report["pending_location"] == 1  # only the unknown room pends
        created = [e["ac_id"] for e in report["rows"] if e.get("action") == "create"]

        by_row = {e["row"]: e for e in report["rows"]}
        assigned = [by_row[2], by_row[3]]
        pending = by_row[4]
        assert pending["action"] == "create"
        assert _ac_row(pending["ac_id"])["ac_make_company"] == "Daikin"
        for entry in assigned:
            conn = get_db_connection()
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        "SELECT room_id, location_status FROM acs WHERE ac_id = %s", (entry["ac_id"],)
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
            assert row["room_id"] == ROOM_ID
            assert row["location_status"] == "ASSIGNED"

        # Resolution must be read-only: no duplicate colleges/blocks/
        # departments/rooms may appear because of an import.
        assert _campus_counts() == before
    finally:
        _delete_acs(*created)


# ── 4: a failing chunk row rolls back only itself (fallback isolation) ──────


def test_failed_chunk_row_rolls_back_only_itself(client, monkeypatch, caplog):
    suffix = uuid.uuid4().hex[:8]
    good_a, bad, good_c = (f"IMPTEST-PERF-{suffix}-{i}" for i in ("A", "B", "C"))
    header = ["ac_id", "department", "location", "brand", "capacity", "year_of_purchase"]
    first = _flex_csv(
        header,
        [
            [good_a, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022"],
            [bad, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022"],
            [good_c, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022"],
        ],
    )
    try:
        assert _report(_post_csv(client, first))["created"] == 3

        # Re-import with a changed brand while exactly one row is unwritable:
        # the batched chunk must fall back to per-row savepoints so the two
        # good rows update and the bad one keeps its PREVIOUS committed value.
        real_parts = acs_routes._upsert_ac_parts

        def exploding_for_bad_row(values):
            if values["ac_id"] == bad:
                raise RuntimeError("simulated single-row write failure")
            return real_parts(values)

        monkeypatch.setattr(acs_routes, "_upsert_ac_parts", exploding_for_bad_row)
        second = _flex_csv(
            header,
            [
                [good_a, VALID_DEPT, VALID_ROOM, "BlueStar", "2", "2022"],
                [bad, VALID_DEPT, VALID_ROOM, "BlueStar", "2", "2022"],
                [good_c, VALID_DEPT, VALID_ROOM, "BlueStar", "2", "2022"],
            ],
        )
        with caplog.at_level(logging.ERROR, logger="app.api.v1.assets_import"):
            report = _report(_post_csv(client, second))

        assert "retrying row-by-row" in caplog.text  # fallback engaged
        assert report["failed"] == 1
        assert {e["row"]: e["message"] for e in report["errors"]} == {3: "Row 3 could not be written."}

        assert _ac_row(good_a)["ac_make_company"] == "BlueStar"
        assert _ac_row(good_c)["ac_make_company"] == "BlueStar"
        assert _ac_row(bad)["ac_make_company"] == "Voltas"  # rolled back, old value intact
    finally:
        _delete_acs(good_a, bad, good_c)


# ── 5: repeated submission is idempotent (replay + in-flight guard) ─────────


def test_identical_resubmission_replays_and_inflight_guards(client, monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    header = ["ac_id", "department", "location", "brand", "capacity", "year_of_purchase"]
    row = [f"IMPTEST-PERF-{suffix}-1", VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022"]
    body = _flex_csv(header, [row])
    key = uuid.uuid4().hex
    try:
        first = _report(_post_csv(client, body, headers={"Idempotency-Key": key}))
        assert first["created"] == 1

        # From here the database must not be touched: a replay returns the
        # first report, an in-flight duplicate gets 409 — both without a
        # connection, proving no second import can start.
        def _forbidden():
            raise AssertionError("replay/guard must not open a DB connection")

        monkeypatch.setattr(ai_mod, "get_db_connection", _forbidden)

        replay = _post_csv(client, body, headers={"Idempotency-Key": key})
        assert replay.status_code == 201
        data = _report(replay)
        assert data["replayed"] is True
        assert data["created"] == first["created"]  # the FIRST attempt's result
        assert data["updated"] == first["updated"]

        in_flight_key = next(iter(ai_mod._completed_imports))
        ai_mod._inflight_imports.add(in_flight_key)
        try:
            guard = _post_csv(client, body, headers={"Idempotency-Key": key})
            assert guard.status_code == 409
            assert guard.get_json()["error"]["code"] == "IMPORT_IN_PROGRESS"
        finally:
            ai_mod._inflight_imports.discard(in_flight_key)

        # The same key with DIFFERENT bytes is a new import, not a replay.
        monkeypatch.setattr(ai_mod, "get_db_connection", get_db_connection)
        other = _flex_csv(
            header, [[f"IMPTEST-PERF-{suffix}-2", VALID_DEPT, VALID_ROOM, "Voltas", "4", "2021"]]
        )
        fresh = _post_csv(client, other, headers={"Idempotency-Key": key})
        assert fresh.status_code == 201
        assert "replayed" not in _report(fresh)

        assert _count_acs(f"IMPTEST-PERF-{suffix}") == 2
    finally:
        _delete_acs(f"IMPTEST-PERF-{suffix}-1", f"IMPTEST-PERF-{suffix}-2")
