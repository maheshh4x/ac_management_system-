"""Flexible AC import tests (data preservation + pending location + attributes).

Ported from legacy /api/acs/import to POST /api/v1/assets/import (and the
campus CSV to POST /api/v1/campus/import) — same cores, now behind auth +
the standard envelope.
"""

import io
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import RealDictCursor  # noqa: E402

from app.config.database import get_db_connection  # noqa: E402
from app.routes import acs as acs_mod  # noqa: E402

VALID_DEPT = "Computer Science"
VALID_ROOM = "CSE Computer Lab 1"
HEADER = ["ac_id", "department", "location", "brand", "capacity", "year_of_purchase"]


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


def _csv(header, rows):
    lines = [",".join(header)] + [",".join("" if c is None else str(c) for c in row) for row in rows]
    return "\n".join(lines) + "\n"


def _post(client, body):
    return client.post(
        "/api/v1/assets/import",
        data={"file": (io.BytesIO(body.encode("utf-8")), "f.csv")},
    )


def _report(response):
    """Unwrap the v1 envelope: returns data, asserting top-level success."""
    payload = response.get_json()
    assert payload["success"] is True, payload
    return payload["data"]


def _row(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM acs WHERE ac_id = %s", (ac_id,))
            return cur.fetchone()
    finally:
        conn.close()


def _attrs(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT attribute_name, attribute_value, attribute_type, source FROM asset_attributes a "
                "JOIN acs ON acs.asset_uuid = a.asset_uuid WHERE acs.ac_id = %s ORDER BY attribute_name",
                (ac_id,),
            )
            return cur.fetchall()
    finally:
        conn.close()


def _cleanup(*ac_ids):
    if not ac_ids:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id IN %s", (tuple(ac_ids),))
        conn.commit()
    finally:
        conn.close()


def _sid():
    return uuid.uuid4().hex[:6]


def test_valid_ac_with_only_required_fields_imports_pending(client):
    ac_id = f"FLEX-REQ-{_sid()}"
    body = _csv(["ac_id", "brand"], [[ac_id, "Daikin"]])
    try:
        r = _post(client, body)
        assert r.status_code == 201
        data = _report(r)
        assert data["imported"] == 1 and data["failed"] == 0
        assert data["pending_location"] == 1
        assert data["warnings"] and data["warnings"][0]["kind"] == "pending_location"
        row = _row(ac_id)
        assert row["room_id"] is None
        assert row["location_status"] == "PENDING"
        assert row["ac_make_company"] == "Daikin"
        assert row["asset_code"] == ac_id
    finally:
        _cleanup(ac_id)


def test_valid_ac_with_all_optional_fields_assigned(client):
    ac_id = f"FLEX-ALL-{_sid()}"
    header = HEADER + [
        "model_number",
        "serial_number",
        "ac_type",
        "star_rating",
        "power_rating_kw",
        "power_factor",
        "manufacturing_year",
        "technician",
        "remarks",
        "status",
    ]
    rows = [
        [
            ac_id,
            VALID_DEPT,
            VALID_ROOM,
            "Daikin",
            "2",
            2024,
            "FTK50",
            "SN-77",
            "Split",
            5,
            1.9,
            0.98,
            2023,
            "Tech A",
            "Near window",
            "Working",
        ]
    ]
    try:
        r = _post(client, _csv(header, rows))
        assert r.status_code == 201
        data = _report(r)
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 0
        row = _row(ac_id)
        assert row["location_status"] == "ASSIGNED"
        assert row["room_id"] is not None
        assert row["ac_serial_number"] == "SN-77"
        assert row["ac_star_rating"] == 5
        assert row["power_factor"] == Decimal("0.98")
        assert row["operational_status"] == "WORKING"
    finally:
        _cleanup(ac_id)


def test_missing_optional_fields_do_not_block_import(client):
    ac_id = f"FLEX-SPARSE-{_sid()}"
    body = _csv(["ac_id", "brand", "capacity"], [[ac_id, "Blue Star", 1.5]])
    try:
        r = _post(client, body)
        data = _report(r)
        assert r.status_code == 201
        assert data["imported"] == 1 and data["failed"] == 0
        row = _row(ac_id)
        assert row["ac_capacity_ton"] == Decimal("1.5")
        assert row["ac_serial_number"] is None
    finally:
        _cleanup(ac_id)


def test_missing_brand_is_rejected_with_clear_message(client):
    ac_id = f"FLEX-NOBRAND-{_sid()}"
    body = _csv(HEADER, [[ac_id, VALID_DEPT, VALID_ROOM, "", 2, 2022]])
    r = _post(client, body)
    assert r.status_code == 201
    data = _report(r)
    assert data["imported"] == 0 and data["failed"] == 1
    message = data["errors"][0]["message"]
    assert "Brand/company is required" in message
    for leak in ("psycopg2", "constraint", "INSERT INTO"):
        assert leak not in message
    assert _row(ac_id) is None


def test_invalid_star_rating_is_rejected(client):
    ac_id = f"FLEX-STAR-{_sid()}"
    body = _csv(HEADER + ["star_rating"], [[ac_id, VALID_DEPT, VALID_ROOM, "Voltas", 2, 2022, 7]])
    r = _post(client, body)
    data = _report(r)
    assert data["failed"] == 1
    assert "ac_star_rating must be between 1 and 5" in data["errors"][0]["message"]
    assert _row(ac_id) is None


def test_unresolved_room_preserves_asset_pending(client):
    ac_id = f"FLEX-UNMATCH-{_sid()}"
    body = _csv(HEADER, [[ac_id, "Totally New Wing", "Mystery Hall", "Voltas", 2, 2022]])
    try:
        r = _post(client, body)
        data = _report(r)
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 1
        assert data["unmatched_rooms"] == ["Totally New Wing | Mystery Hall"]
        assert data["unmatched_room_details"][0]["count"] == 1
        row = _row(ac_id)
        assert row["room_id"] is None and row["location_status"] == "PENDING"
    finally:
        _cleanup(ac_id)


def test_blank_optional_fields_do_not_erase_existing_data(client):
    ac_id = f"FLEX-BLANK-{_sid()}"
    rich = _csv(HEADER, [[ac_id, VALID_DEPT, VALID_ROOM, "Voltas", 2.0, 2020]])
    with_serial = _csv(
        HEADER + ["serial_number", "remarks"],
        [[ac_id, VALID_DEPT, VALID_ROOM, "Voltas", 2.0, 2020, "SER-KEEP-123", "first import"]],
    )
    try:
        assert _report(_post(client, with_serial))["imported"] == 1
        assert _row(ac_id)["ac_serial_number"] == "SER-KEEP-123"
        # Re-import the SAME asset from a file without serial_number and with a
        # blank capacity cell: neither the serial nor the capacity may be erased.
        r = _post(client, _csv(HEADER, [[ac_id, VALID_DEPT, VALID_ROOM, "LG", "", 2023]]))
        data = _report(r)
        assert data["updated"] == 1 and data["created"] == 0
        row = _row(ac_id)
        assert row["ac_serial_number"] == "SER-KEEP-123"  # column absent → untouched
        assert row["ac_capacity_ton"] == Decimal("2.0")  # blank cell → untouched
        assert row["ac_make_company"] == "LG"  # real value → updated
        assert row["installation_year"] == 2023
        assert rich is not None
    finally:
        _cleanup(ac_id)


def test_unknown_columns_are_reported_and_preserved(client):
    ac_id = f"FLEX-COL-{_sid()}"
    header = HEADER + ["warranty_expiry", "amc_vendor"]
    rows = [[ac_id, VALID_DEPT, VALID_ROOM, "Voltas", 2, 2022, "2028-03-31", "CoolAir Services"]]
    try:
        r = _post(client, _csv(header, rows))
        assert r.status_code == 201
        data = _report(r)
        assert sorted(data["unknown_columns"]) == ["amc_vendor", "warranty_expiry"]
        assert data["ignored_columns"] == data["unknown_columns"]
        assert data["attributes_recorded"] == 2
        attrs = {a["attribute_name"]: a for a in _attrs(ac_id)}
        assert attrs["warranty_expiry"]["attribute_value"] == "2028-03-31"
        assert attrs["amc_vendor"]["attribute_value"] == "CoolAir Services"
        assert attrs["amc_vendor"]["source"] == "import"
    finally:
        _cleanup(ac_id)  # asset_attributes rows cascade via ON DELETE CASCADE


def test_duplicate_import_updates_and_grouped_unmatched_details(client):
    ac_id = f"FLEX-DUP-{_sid()}"
    rows = [
        [ac_id, "Zone Nowhere", "Room X", "Voltas", 2, 2021],
        [ac_id, "Zone Nowhere", "Room X", "Daikin", 2.5, 2024],
    ]
    try:
        r = _post(client, _csv(HEADER, rows))
        data = _report(r)
        assert data["imported"] == 2  # both rows accepted...
        assert data["created"] == 1  # ...but one asset written
        assert data["failed"] == 0 and data["pending_location"] == 2
        detail = data["unmatched_room_details"][0]
        assert detail["reference"] == "Zone Nowhere | Room X"
        assert detail["count"] == 2 and detail["rows"] == [2, 3]
        row = _row(ac_id)
        assert row["ac_make_company"] == "Daikin"  # last row wins
        assert row["location_status"] == "PENDING"

        # second import of the same file = updates, never duplicates
        r2 = _post(client, _csv(HEADER, rows))
        data2 = _report(r2)
        assert data2["created"] == 0 and data2["updated"] == 1
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM acs WHERE ac_id = %s", (ac_id,))
                assert cur.fetchone()[0] == 1
        finally:
            conn.close()
    finally:
        _cleanup(ac_id)


def test_pending_asset_resolves_after_campus_import(client):
    """Import AC first (pending), then import the campus CSV, then a re-import
    of the AC file silently links the now-existing room — no duplicates."""
    tag = _sid()
    ac_id = f"FLEX-LINK-{tag}"
    block, room, dept = f"FBLK-{tag}", f"FRM-{tag}", f"FDept-{tag}"
    floor_id = f"{block}-FLR-1"
    campus_csv = (
        "building_id,building_name,floor,room_id,room_name,department\n"
        f"{block},Flexible Block {tag},1,{room},Room One,{dept}\n"
    )
    ac_body = _csv(HEADER, [[ac_id, dept, "Room One", "Voltas", 2, 2022]])
    try:
        r1 = _report(_post(client, ac_body))
        assert r1["pending_location"] == 1
        assert _row(ac_id)["room_id"] is None

        r2 = client.post(
            "/api/v1/campus/import",
            data={"file": (io.BytesIO(campus_csv.encode()), "campus.csv")},
        )
        assert r2.status_code == 201 and _report(r2)["imported"] == 1

        r3 = _report(_post(client, ac_body))
        assert r3["pending_location"] == 0 and r3["updated"] == 1
        row = _row(ac_id)
        assert row["room_id"] == room and row["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM rooms WHERE room_id = %s", (room,))
                cur.execute("DELETE FROM floors WHERE floor_id = %s", (floor_id,))
                cur.execute("DELETE FROM blocks WHERE block_id = %s", (block,))
                cur.execute("DELETE FROM departments WHERE department_id = %s", (f"DEPT-{dept.upper()}",))
            conn.commit()
        finally:
            conn.close()


# ── Header alias layer (importer normalization) ─────────────────────────────
# Raw headers are compared AFTER _header() normalization, so the spellings
# below all collapse onto the same snake_case key. The database schema is
# canonical; only this alias layer adapts incoming files to it.

MAKE_HEADER_VARIANTS = [
    "Brand / Company",  # ACMS 301.xlsx reference header -> "brand_company"
    "Brand",
    "Company",
    "Manufacturer",
    "Make",
    "AC Make",
    "AC Manufacturer",
    "brand_company",
]


@pytest.mark.parametrize("raw_header", MAKE_HEADER_VARIANTS)
def test_make_header_variants_map_to_ac_make_company(raw_header):
    row = {acs_mod._header(raw_header): "CoolCorp"}
    values, _lookup = acs_mod._csv_payload(row, 2)
    assert values["ac_make_company"] == "CoolCorp"


def test_reference_workbook_headers_are_all_recognized():
    """Every header of the reference workbook ACMS 301.xlsx must resolve to a
    canonical import field (header names only — its data is never read)."""
    reference_headers = [
        "college_name",
        "block_name",
        "floor_name",
        "department_name",
        "room_type",
        "room_number",
        "Brand / Company",
        "unit_number",
        "model",
        "serial_number",
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
    recognized = {alias for aliases in acs_mod.CSV_ALIASES.values() for alias in aliases}
    assert [h for h in reference_headers if acs_mod._header(h) not in recognized] == []


def test_brand_company_header_imports_end_to_end(client):
    ac_id = f"FLEX-BRAND-{_sid()}"
    body = _csv(["ac_id", "Brand / Company"], [[ac_id, "Voltas"]])
    try:
        r = _post(client, body)
        assert r.status_code == 201
        data = _report(r)
        assert data["imported"] == 1 and data["failed"] == 0
        assert data["unknown_columns"] == []  # brand_company is recognized, not unknown
        row = _row(ac_id)
        assert row["ac_make_company"] == "Voltas"
    finally:
        _cleanup(ac_id)
