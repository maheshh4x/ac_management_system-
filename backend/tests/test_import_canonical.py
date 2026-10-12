"""Canonical 22-column import under the FINAL business rule (v1 endpoint).

Only block_name, department_name, ac_make_company are required. Everything
else is optional and never rejects a row. Unresolved/ambiguous rooms never
renounce the asset: it is stored with room_id NULL + location_status PENDING.

Ported from legacy /api/acs/import to POST /api/v1/assets/import — same core
parsers, now behind auth + the standard envelope.
"""

import csv
import io
import json
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import RealDictCursor  # noqa: E402

from app.config.database import get_db_connection  # noqa: E402
from app.routes import acs as acs_mod  # noqa: E402

# Real seeded campus data (college→block→floor→department→room):
COLLEGE = "National Institute of Technical Teachers Training and Research"
BLOCK = "Main Academic Block"
FLOOR = "Floor 1"
DEPT = "Computer Science"
ROOM = "CSE Computer Lab 1"  # room_name of ROOM-102
ROOM_ID = "ROOM-102"
OTHER_ROOM = "HOD Cabin"  # room_name of ROOM-105 (Administration, Floor 1, Main Academic Block)
OTHER_DEPT = "Administration"

HEADER = [
    "college_name",
    "block_name",
    "floor_name",
    "department_name",
    "room_type",
    "room_number",
    "ac_make_company",
    "unit_number",
    "ac_model_number",
    "ac_serial_number",
    "capacity_ton",
    "type",
    "manufacturing_year",
    "installation_year",
    "star_rating",
    "power_factor",
    "status",
    "last_maintenance_type",
    "last_maintenance_date",
    "next_maintenance_date",
    "technician",
    "remarks",
]


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


def _row(**over):
    base = {
        "college_name": COLLEGE,
        "block_name": BLOCK,
        "floor_name": FLOOR,
        "department_name": DEPT,
        "room_type": "Lab",
        "room_number": ROOM,
        "ac_make_company": "Daikin",
        "unit_number": "1",
        "ac_model_number": "",
        "ac_serial_number": "",
        "capacity_ton": "1.5",
        "type": "Split",
        "manufacturing_year": "2022",
        "installation_year": "2023",
        "star_rating": "5",
        "power_factor": "0.95",
        "status": "Working",
        "last_maintenance_type": "preventive",
        "last_maintenance_date": "2026-08-10",
        "next_maintenance_date": "2026-11-10",
        "technician": "Test Tech",
        "remarks": "canonical test",
    }
    base.update(over)
    return base


def _post(client, rows, fields=None):
    fieldnames = fields or HEADER
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fieldnames})
    return client.post(
        "/api/v1/assets/import",
        data={"file": (io.BytesIO(buf.getvalue().encode("utf-8")), "canonical.csv")},
    )


def _report(response):
    """Unwrap the v1 envelope: returns data, asserting top-level success."""
    payload = response.get_json()
    assert payload["success"] is True, payload
    return payload["data"]


def _ac(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT ac_id, asset_code, asset_uuid, room_id, location_status, ac_make_company, "
                "ac_serial_number, ac_capacity_ton, ac_star_rating, manufacturing_year FROM acs WHERE ac_id = %s",
                (ac_id,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _attrs(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT attribute_name, attribute_value FROM asset_attributes a "
                "JOIN acs ON acs.asset_uuid = a.asset_uuid WHERE acs.ac_id = %s",
                (ac_id,),
            )
            return {r["attribute_name"]: r["attribute_value"] for r in cur.fetchall()}
    finally:
        conn.close()


def _cleanup(*ac_ids):
    ids = [a for a in ac_ids if a]
    if not ids:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id IN %s", (tuple(ids),))
        conn.commit()
    finally:
        conn.close()


MINIMAL = ["block_name", "department_name", "ac_make_company"]


def test_row_with_only_the_3_required_fields_succeeds(client):
    # room_number blank → no resolvable room → identity falls back to the
    # deterministic block+department token:
    ac_id = f"AC-LOC-{BLOCK.upper().replace(' ', '-')}-{DEPT.upper().replace(' ', '-')}-UB1"
    try:
        r = _post(
            client,
            [_row(unit_number="B1", room_number="")],
            fields=[
                "block_name",
                "department_name",
                "ac_make_company",
                "unit_number",
                "room_number",  # canonical marker (keeps file canonical); blank below
            ],
        )
        data = _report(r)
        assert data["canonical_format"] is True
        assert data["imported"] == 1 and data["failed"] == 0
        # room_number blank → unresolved → stored unassigned, not rejected
        assert data["pending_location"] == 1
        row = _ac(ac_id)
        assert row is not None
        # room_number blank → room MISSING/unassigned, but block and
        # department resolved → PARTIAL, not PENDING (§5 partial status).
        assert row["room_id"] is None and row["location_status"] == "PARTIAL"
        assert row["asset_code"] == ac_id and row["asset_uuid"] is not None
    finally:
        _cleanup(ac_id)


def test_missing_block_name_fails(client):
    r = _post(client, [_row(block_name="", unit_number="Z1")])
    data = _report(r)
    msg = data["errors"][0]["message"]
    assert data["failed"] == 1
    assert "block_name is required" in msg


def test_missing_department_name_fails(client):
    r = _post(client, [_row(department_name="", unit_number="Z2")])
    msg = _report(r)["errors"][0]["message"]
    assert "department_name is required" in msg


def test_missing_ac_make_company_fails(client):
    r = _post(client, [_row(ac_make_company="", unit_number="Z3")])
    data = _report(r)
    msg = data["errors"][0]["message"]
    assert "Brand/company is required" in msg
    assert data["failed"] == 1
    assert _ac(f"AC-{ROOM_ID}-UZ3") is None


def test_each_optional_field_may_be_missing(client):
    """Sweep: every optional field blanked one at a time — none may reject."""
    cases = [
        "college_name",
        "floor_name",
        "room_type",
        "room_number",
        "capacity_ton",
        "star_rating",
        "ac_model_number",
        "ac_serial_number",
        "manufacturing_year",
        "installation_year",
        "last_maintenance_type",
        "last_maintenance_date",
        "next_maintenance_date",
        "technician",
        "remarks",
        "power_factor",
        "status",
        "type",
    ]
    ids = []
    try:
        rows = []
        for index, field in enumerate(cases, 1):
            rows.append(_row(unit_number=f"MX{index:02d}", **{field: ""}))
        r = _post(client, rows)
        data = _report(r)
        assert data["failed"] == 0, data["errors"]
        assert data["imported"] == len(cases)
        ids = [f"AC-{ROOM_ID}-UMX{index:02d}" for index in range(1, len(cases) + 1)]
        ids[3] = None  # room_number blanked → unscoped identity, found via unit
        for ac_id in ids:
            if ac_id is not None:
                assert _ac(ac_id) is not None, ac_id
        pending_row = _find_by_unit("MX04")
        assert pending_row is not None and pending_row["room_id"] is None
        assert data["pending_location"] >= 1
    finally:
        _cleanup(*[i for i in ids if i])
        _cleanup_ids_by_unit("MX04")


def test_unresolvable_room_imports_asset_pending(client):
    try:
        r = _post(client, [_row(room_number="Phantom Hall", unit_number="51")])
        data = _report(r)
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 1
        assert any("Phantom Hall" in w["message"] for w in data["warnings"])
        found = _find_by_unit("51")
        assert found is not None and found["room_id"] is None
        # Room unresolved, but block/department resolved → PARTIAL (§5):
        # partial resolution never all-or-nothing.
        assert found["location_status"] == "PARTIAL"
        assert found["ac_id"].startswith("AC-LOC-")  # deterministic unscoped identity
    finally:
        _cleanup_ids_by_unit("51")


def test_full_location_information_assigns_room(client):
    ac_id = f"AC-{ROOM_ID}-U61"
    try:
        r = _post(client, [_row(unit_number="61")])
        data = _report(r)
        assert data["imported"] == 1 and data["pending_location"] == 0 and data["failed"] == 0
        row = _ac(ac_id)
        assert row["room_id"] == ROOM_ID and row["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)


def test_reimport_updates_instead_of_duplicating(client):
    ac_id = f"AC-{ROOM_ID}-U62"
    try:
        r1 = _report(_post(client, [_row(unit_number="62")]))
        r2 = _report(_post(client, [_row(unit_number="62", ac_make_company="Voltas")]))
        assert r1["created"] == 1 and r2["created"] == 0 and r2["updated"] == 1
        assert _ac(ac_id)["ac_make_company"] == "Voltas"
    finally:
        _cleanup(ac_id)


def test_invalid_optional_value_still_validates_when_supplied(client):
    # bad capacity when provided
    r = _post(client, [_row(capacity_ton="not-a-number", unit_number="71")])
    data = _report(r)
    assert data["failed"] == 1
    assert "invalid ac_capacity_ton" in data["errors"][0]["message"]
    # out-of-range star rating when provided
    r = _post(client, [_row(star_rating="7", unit_number="72")])
    assert "ac_star_rating must be between 1 and 5" in _report(r)["errors"][0]["message"]
    # out-of-range power factor when provided
    r = _post(client, [_row(power_factor="1.8", unit_number="73")])
    assert "power_factor must be between 0 and 1" in _report(r)["errors"][0]["message"]
    _cleanup(f"AC-{ROOM_ID}-U71", f"AC-{ROOM_ID}-U72", f"AC-{ROOM_ID}-U73")


def test_canonical_recognition_and_extended_attributes(client):
    ac_id = f"AC-{ROOM_ID}-U81"
    try:
        r = _post(client, [_row(unit_number="81", ac_serial_number="SN-C81")])
        data = _report(r)
        assert data["unknown_columns"] == [] and data["ignored_columns"] == []
        for field in (
            "college_name",
            "block_name",
            "floor_name",
            "department_name",
            "room_type",
            "room_number",
            "ac_make_company",
            "unit_number",
            "ac_model_number",
            "ac_serial_number",
            "ac_capacity_ton",
            "ac_type",
            "manufacturing_year",
            "installation_year",
            "ac_star_rating",
            "power_factor",
            "current_status",
            "last_maintenance_type",
            "last_maintenance_date",
            "next_service_date",
            "technician",
            "remarks",
        ):
            assert field in data["recognized_columns"], f"missing recognized field {field}"
        # unit_number + last_maintenance_type preserved as recognized attributes
        attrs = _attrs(ac_id)
        assert attrs.get("unit_number") == "81"
        assert attrs.get("last_maintenance_type") == "preventive"
    finally:
        _cleanup(ac_id)


def test_unexpected_db_failure_rolls_back_and_stays_safe(client, monkeypatch, caplog):
    """Systemic failure outside the per-row savepoints (the campus reference
    preload): the whole file aborts with a generic envelope, nothing lands,
    and the detail stays in the server log next to the request id."""
    ids = [f"AC-{ROOM_ID}-U91", f"AC-{ROOM_ID}-U92"]

    def _boom(cursor):
        raise Exception("simulated fatal canonical failure")

    monkeypatch.setattr(acs_mod, "_load_campus_directory", _boom)
    try:
        with caplog.at_level(logging.ERROR, logger="app.api.v1.assets_import"):
            r = _post(client, [_row(unit_number="91"), _row(unit_number="92")])
        assert r.status_code == 500
        payload = r.get_json()
        assert payload["error"]["message"] == "Import failed. No data was written."
        request_id = payload["error"]["request_id"]
        text = json.dumps(payload)
        for leak in ("simulated", "psycopg2", "INSERT INTO", "constraint", "ON CONFLICT"):
            assert leak not in text
        assert "simulated fatal canonical failure" in caplog.text
        assert request_id in caplog.text
        for ac_id in ids:
            assert _ac(ac_id) is None
    finally:
        _cleanup(*ids)


# helpers for room-unscoped rows (identity = AC-LOC-…-U…)
def _find_by_unit(unit):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT a.* FROM acs a JOIN asset_attributes at ON at.asset_uuid = a.asset_uuid "
                "WHERE at.attribute_name = 'unit_number' AND at.attribute_value = %s",
                (unit,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _cleanup_ids_by_unit(*units):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for unit in units:
                cur.execute(
                    "DELETE FROM acs WHERE ac_id IN (SELECT a.ac_id FROM acs a JOIN asset_attributes at "
                    "ON at.asset_uuid = a.asset_uuid WHERE at.attribute_name='unit_number' AND at.attribute_value=%s)",
                    (unit,),
                )
        conn.commit()
    finally:
        conn.close()
