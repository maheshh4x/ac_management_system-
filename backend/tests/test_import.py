"""Focused tests for POST /api/v1/assets/import (Phase 3 task 5/8).

Ported from the legacy /api/acs/import suite: same scenarios and report keys,
now behind auth + the standard success/error envelope. Covers: successful bulk
import, unmatched/ambiguous rooms, duplicate + update behaviour, per-row
savepoint isolation, systemic-failure abort, and safe (no raw DB/psycopg text)
error responses.

These tests touch the shared database with uniquely prefixed ac_ids and
always clean up after themselves.
"""

import io
import json
import logging
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import RealDictCursor  # noqa: E402

from app.config.database import get_db_connection  # noqa: E402
from app.routes import acs as acs_routes  # noqa: E402

VALID_DEPT = "Computer Science"
VALID_ROOM = "CSE Computer Lab 1"
HEADER = ["ac_id", "department", "location", "brand", "capacity", "year_of_purchase"]


@pytest.fixture(autouse=True)
def _clean_stale_test_artifacts(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """Remove leftovers from interrupted earlier runs (test rooms would
    otherwise make the seeded room permanently ambiguous)."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id LIKE 'IMPTEST-%'")
            cur.execute("DELETE FROM rooms WHERE room_id LIKE 'IMPTEST-ROOM-%'")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


def _csv(header, rows):
    lines = [",".join(header)] + [",".join("" if cell is None else str(cell) for cell in row) for row in rows]
    return "\n".join(lines) + "\n"


def _post_csv(client, body, filename="import.csv", dry_run=False):
    data = {"file": (io.BytesIO(body.encode("utf-8")), filename)}
    url = "/api/v1/assets/import" + ("?dry_run=true" if dry_run else "")
    return client.post(url, data=data)


def _report(response):
    """Unwrap the v1 envelope: returns data, asserting top-level success."""
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
            cur.execute(
                "SELECT ac_id, ac_capacity_ton, ac_make_company, room_id, location_status FROM acs WHERE ac_id = %s",
                (ac_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def test_bulk_import_success(client):
    prefix = f"IMPTEST-OK-{uuid.uuid4().hex[:8]}"
    ac_ids = [f"{prefix}-{index:02d}" for index in range(10)]
    body = _csv(HEADER, [[ac_id, VALID_DEPT, VALID_ROOM, "Voltas", "2.5", "2022"] for ac_id in ac_ids])
    try:
        r = _post_csv(client, body)
        assert r.status_code == 201
        data = _report(r)
        assert data["total_rows"] == 10
        assert data["imported"] == 10
        assert data["failed"] == 0
        assert data["errors"] == []
        assert data["unmatched_rooms"] == []
        for ac_id in ac_ids:
            row = _ac_row(ac_id)
            assert row is not None, f"{ac_id} not persisted"
            assert row["room_id"] is not None
            assert row["ac_capacity_ton"] == Decimal("2.5")
    finally:
        _delete_acs(*ac_ids)


def test_unmatched_room_keeps_asset_pending_and_other_rows_import(client):
    suffix = uuid.uuid4().hex[:8]
    ok_id, bad_id = f"IMPTEST-UNM-{suffix}-1", f"IMPTEST-UNM-{suffix}-2"
    body = _csv(
        HEADER,
        [
            [ok_id, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022"],
            [bad_id, "Nonexistent Department", "Ghost Room 99", "Voltas", "2", "2022"],
        ],
    )
    try:
        r = _post_csv(client, body)
        assert r.status_code == 201
        data = _report(r)
        # Valid AC data is preserved: unresolved room -> room_id NULL + PENDING,
        # reported as an unmatched reference (never rejected, never guessed).
        assert data["imported"] == 2
        assert data["failed"] == 0
        assert data["pending_location"] == 1
        assert data["unmatched_rooms"] == ["Nonexistent Department | Ghost Room 99"]
        warning = data["warnings"][0]
        assert warning["row"] == 3
        assert warning["kind"] == "unmatched"
        assert "no room matches Department 'Nonexistent Department'" in warning["message"]

        row = _ac_row(bad_id)
        assert row is not None
        assert row["room_id"] is None
        assert row["location_status"] == "PENDING"
        good = _ac_row(ok_id)
        assert good["room_id"] is not None
        assert good["location_status"] == "ASSIGNED"
    finally:
        _delete_acs(ok_id, bad_id)


def test_ambiguous_room_keeps_asset_pending(client):
    ac_id = f"IMPTEST-AMB-{uuid.uuid4().hex[:8]}"
    duplicate_room_id = f"IMPTEST-ROOM-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        # Temporarily add a second room with the same department + room name
        # so the resolver sees two matches (cleaned up in finally).
        with conn.cursor() as cur:
            cur.execute("SELECT floor_id, department_id FROM rooms WHERE room_name = %s", (VALID_ROOM,))
            source = cur.fetchone()
            assert source is not None, "expected the seeded room for this test"
            cur.execute(
                "INSERT INTO rooms (room_id, floor_id, department_id, room_name) VALUES (%s, %s, %s, %s)",
                (duplicate_room_id, source[0], source[1], VALID_ROOM),
            )
        conn.commit()

        body = _csv(HEADER, [[ac_id, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022"]])
        r = _post_csv(client, body)
        assert r.status_code == 201
        data = _report(r)
        # Ambiguity must not crash or guess: the asset is preserved with a
        # pending location and the ambiguity is reported.
        assert data["imported"] == 1
        assert data["failed"] == 0
        assert data["pending_location"] == 1
        warning = data["warnings"][0]
        assert warning["kind"] == "ambiguous"
        assert "multiple rooms match" in warning["message"]
        assert data["ambiguous_rooms"] == [f"{VALID_DEPT} | {VALID_ROOM}"]
        row = _ac_row(ac_id)
        assert row is not None
        assert row["room_id"] is None
        # Department resolved but room ambiguous → PARTIAL (§5): the
        # ambiguity never guesses, yet the resolved levels are recorded.
        assert row["location_status"] == "PARTIAL"
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM rooms WHERE room_id = %s", (duplicate_room_id,))
            conn.commit()
        finally:
            conn.close()
        _delete_acs(ac_id)


def test_duplicate_rows_and_existing_update(client):
    ac_id = f"IMPTEST-DUP-{uuid.uuid4().hex[:8]}"
    try:
        # In-file duplicate: last row wins per column (old sequential-upsert
        # outcome preserved) and both rows are counted as imported.
        body = _csv(
            HEADER,
            [
                [ac_id, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2021"],
                [ac_id, VALID_DEPT, VALID_ROOM, "Blue Star", "3.5", "2023"],
            ],
        )
        r = _post_csv(client, body)
        assert r.status_code == 201
        data = _report(r)
        assert data["imported"] == 2
        assert data["failed"] == 0
        row = _ac_row(ac_id)
        assert row is not None
        assert row["ac_capacity_ton"] == Decimal("3.5")
        assert row["ac_make_company"] == "Blue Star"

        # Existing row: a second upload updates instead of duplicating.
        body2 = _csv(HEADER, [[ac_id, VALID_DEPT, VALID_ROOM, "LG", "1.5", "2024"]])
        r2 = _post_csv(client, body2)
        assert r2.status_code == 201
        assert _report(r2)["imported"] == 1
        row = _ac_row(ac_id)
        assert row["ac_make_company"] == "LG"
        assert row["ac_capacity_ton"] == Decimal("1.5")
    finally:
        _delete_acs(ac_id)


def test_systemic_failure_aborts_whole_file_safely(client, monkeypatch, caplog):
    """Anything outside the per-row savepoints (here: the reference preload)
    still aborts the entire file: generic envelope, full rollback, no DB
    internals, detail only in the server log next to the request id."""
    suffix = uuid.uuid4().hex[:8]
    ac_ids = [f"IMPTEST-RBK-{suffix}-{index}" for index in range(3)]
    body = _csv(HEADER, [[ac_id, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022"] for ac_id in ac_ids])

    def exploding_directory(cursor):  # noqa: ARG001
        raise psycopg2.OperationalError("simulated database failure")

    monkeypatch.setattr(acs_routes, "_load_room_directory", exploding_directory)
    try:
        with caplog.at_level(logging.ERROR, logger="app.api.v1.assets_import"):
            r = _post_csv(client, body)
        assert r.status_code == 500
        payload = r.get_json()
        assert payload["success"] is False
        assert payload["error"]["message"] == "Import failed. No data was written."
        request_id = payload["error"]["request_id"]
        assert request_id

        # No raw DB/psycopg detail may reach the client.
        text = json.dumps(payload)
        for leak in ("simulated", "OperationalError", "psycopg2", "INSERT INTO", "ac_id"):
            assert leak not in text

        # Nothing persisted.
        for ac_id in ac_ids:
            assert _ac_row(ac_id) is None

        # Detail is logged server-side together with the response request id.
        assert "simulated database failure" in caplog.text
        assert request_id in caplog.text
    finally:
        _delete_acs(*ac_ids)


def test_single_row_write_failure_rolls_back_only_that_row(client, monkeypatch, caplog):
    """Plan contract for /api/v1/assets/import: ONE transaction with a
    per-row SAVEPOINT — one unwritable row never poisons the file."""
    suffix = uuid.uuid4().hex[:8]
    bad_id, good_id = f"IMPTEST-SVP-{suffix}-1", f"IMPTEST-SVP-{suffix}-2"
    body = _csv(
        HEADER,
        [
            [bad_id, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022"],
            [good_id, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022"],
        ],
    )

    # Both write paths (chunked fast path + per-row savepoint fallback) build
    # their statement through _upsert_ac_parts — fail exactly one row there to
    # simulate an unwritable row without touching the database.
    real_parts = acs_routes._upsert_ac_parts

    def exploding_for_bad_row(values):
        if values["ac_id"] == bad_id:
            raise psycopg2.OperationalError("simulated database failure")
        return real_parts(values)

    monkeypatch.setattr(acs_routes, "_upsert_ac_parts", exploding_for_bad_row)
    try:
        with caplog.at_level(logging.ERROR, logger="app.api.v1.assets_import"):
            r = _post_csv(client, body)
        assert r.status_code == 201
        data = _report(r)
        assert data["imported"] == 1  # only the good row landed
        assert data["created"] == 1
        assert data["failed"] == 1
        messages = {error["row"]: error["message"] for error in data["errors"]}
        assert messages == {2: "Row 2 could not be written."}

        # Its savepoint rolled back; the rest of the file committed.
        assert _ac_row(bad_id) is None
        assert _ac_row(good_id) is not None

        # Safe client text: only generic messages, no DB internals.
        text = json.dumps(r.get_json())
        for leak in ("simulated", "OperationalError", "psycopg2", "INSERT INTO"):
            assert leak not in text
        assert "simulated database failure" in caplog.text
    finally:
        _delete_acs(bad_id, good_id)


def test_row_validation_errors_are_user_facing_and_safe(client):
    suffix = uuid.uuid4().hex[:8]
    good_id, bad_capacity_id, bad_stars_id = (
        f"IMPTEST-VAL-{suffix}-1",
        f"IMPTEST-VAL-{suffix}-2",
        f"IMPTEST-VAL-{suffix}-3",
    )
    # Bad capacity, bad star rating and a missing ac_id in one upload: the
    # good row still imports and every failure keeps a clear row message.
    body_with_stars = _csv(
        HEADER + ["star_rating"],
        [
            [good_id, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022", ""],
            [bad_capacity_id, VALID_DEPT, VALID_ROOM, "Voltas", "0", "2022", ""],
            [bad_stars_id, VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022", "9"],
            ["", VALID_DEPT, VALID_ROOM, "Voltas", "2", "2022", ""],
        ],
    )
    try:
        r = _post_csv(client, body_with_stars)
        assert r.status_code == 201
        data = _report(r)
        # One good row survives; bad rows are reported, never silently dropped.
        assert data["imported"] == 1
        assert data["failed"] == 3

        messages = {error["row"]: error["message"] for error in data["errors"]}
        assert messages[3] == "row 3: ac_capacity_ton must be greater than 0"
        assert messages[4] == "row 4: ac_star_rating must be between 1 and 5"
        assert messages[5] == "ac_id is required"

        # No raw database internals in any row message (migration 002 CHECK
        # constraint names, psycopg text, SQL, relation names).
        joined = " ".join(messages.values()).lower()
        for leak in ("constraint", "psycopg", "insert into", "relation", "check", "sql"):
            assert leak not in joined

        assert _ac_row(good_id) is not None
        assert _ac_row(bad_capacity_id) is None
        assert _ac_row(bad_stars_id) is None
    finally:
        _delete_acs(good_id, bad_capacity_id, bad_stars_id)
