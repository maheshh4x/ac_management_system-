"""Campus import tests (v1): batched single-transaction write, safe errors.

Ported from legacy /api/campus/import to POST /api/v1/campus/import —
same shared core, now behind auth + the standard envelope.
"""

import io
import json
import logging
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.routes import campus_import as campus_mod  # noqa: E402

HEADER = "building_id,building_name,floor,room_id,room_name,department"


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture()
def tag():
    return uuid.uuid4().hex[:8]


def _ids(tag):
    block = f"TSBLK-{tag}"
    floor = f"{block}-FLR-1"
    dept = f"DEPT-TDEPT-{tag}"
    rooms = [f"TSRM-{tag}-1", f"TSRM-{tag}-2"]
    return block, floor, dept, rooms


def _rows(tag, count=2):
    block, floor, dept, rooms = _ids(tag)
    lines = [HEADER]
    for index in range(count):
        lines.append(f"{block},Test Block {tag},1,{rooms[index]},Room {index},TDept-{tag}")
    return "\n".join(lines) + "\n"


def _post(client, body):
    return client.post(
        "/api/v1/campus/import",
        data={"file": (io.BytesIO(body.encode("utf-8")), "campus.csv")},
    )


def _report(response):
    """Unwrap the v1 envelope: returns data, asserting top-level success."""
    payload = response.get_json()
    assert payload["success"] is True, payload
    return payload["data"]


def _exists_rows(tag):
    ids = _ids(tag)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM blocks WHERE block_id = %s", (ids[0],))
            blocks = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM rooms WHERE room_id = ANY(%s)", (ids[3],))
            rooms = cur.fetchone()[0]
    finally:
        conn.close()
    return blocks, rooms


def _cleanup(tag):
    block, floor, dept, rooms = _ids(tag)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rooms WHERE room_id = ANY(%s)", (rooms,))
            cur.execute("DELETE FROM floors WHERE floor_id = %s", (floor,))
            cur.execute("DELETE FROM blocks WHERE block_id = %s", (block,))
            cur.execute("DELETE FROM departments WHERE department_id = %s", (dept,))
        conn.commit()
    finally:
        conn.close()


def test_legacy_campus_import_removed(client):
    """Task 8: /api/campus/import is gone — only the v1 replacement serves
    imports. Authenticated so the legacy guard (which rejects anonymous
    requests before routing) cannot mask the 404."""
    body = _rows(uuid.uuid4().hex[:8])
    r = client.post(
        "/api/campus/import",
        data={"file": (io.BytesIO(body.encode("utf-8")), "campus.csv")},
    )
    assert r.status_code == 404
    assert r.get_json()["error"]["code"] == "NOT_FOUND"


def test_campus_import_success(client, tag):
    try:
        r = _post(client, _rows(tag))
        assert r.status_code == 201
        data = _report(r)
        assert data["imported"] == 2 and data["failed"] == 0 and data["errors"] == []
        blocks, rooms = _exists_rows(tag)
        assert blocks == 1 and rooms == 2
    finally:
        _cleanup(tag)


def test_campus_import_row_validation_is_user_facing(client, tag):
    block = f"TSBLK-{tag}"
    body = (
        HEADER
        + "\n"
        + f"{block},Test Block {tag},1,TSRM-{tag}-1,Room 1,TDept-{tag}\n"
        + f"{block},,1,TSRM-{tag}-2,Room 2,TDept-{tag}\n"
    )
    try:
        r = _post(client, body)
        assert r.status_code == 201
        data = _report(r)
        assert data["imported"] == 1 and data["failed"] == 1
        assert "missing required campus fields: building_name" in data["errors"][0]["message"]
    finally:
        _cleanup(tag)


def test_campus_import_rolls_back_everything_on_db_failure(client, tag, monkeypatch, caplog):
    real_bulk = campus_mod._bulk_upsert

    def _exploding(cursor, table, columns, rows, **kwargs):
        if table == "floors":  # blocks were already inserted moments ago
            raise psycopg2.OperationalError("simulated campus failure")
        return real_bulk(cursor, table, columns, rows, **kwargs)

    monkeypatch.setattr(campus_mod, "_bulk_upsert", _exploding)
    try:
        with caplog.at_level(logging.ERROR, logger="app.routes.campus_import"):
            r = _post(client, _rows(tag))
        assert r.status_code == 500
        payload = r.get_json()
        assert payload["error"]["message"] == "Unable to import campus CSV"
        text = json.dumps(payload)
        for leak in ("simulated", "OperationalError", "psycopg2", "INSERT"):
            assert leak not in text
        blocks, rooms = _exists_rows(tag)
        assert blocks == 0 and rooms == 0  # full rollback of the single transaction
        assert r.headers.get("X-Request-ID") in caplog.text
    finally:
        monkeypatch.setattr(campus_mod, "_bulk_upsert", real_bulk)
        _cleanup(tag)
