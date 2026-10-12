"""Import regression matrix — §23 J–L, V + §24 future-style cases 1–11.

Every row runs through the real POST /api/v1/assets/import canonical path
(block + floor + department + room_type + room_number, the same five source
values the spec's case fixtures carry) and asserts what "predictable" means
per case: per-field statuses, preserved source values, no invented masters,
no downgrades. Rows carry an explicit ``ac_id`` so identity is stable across
re-imports (the file-derived identity would otherwise change with the room
outcome — location behavior, not identity derivation, is under test here).

Seeded campus (conftest): NITTTR-CHENNAI -> Main Academic Block -> Floor 1 ->
{ROOM-102 (Computer Science), ROOM-105 (Administration)}. Masters a case
needs (its own "Admin Block"/"First", twin rooms for ambiguity, an alias)
are created unique-tagged and removed by the ``scratch`` teardown, so no
test can poison another's name matching on this shared database.
"""

from __future__ import annotations

import csv
import io
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import RealDictCursor  # noqa: E402

from app.config.database import get_db_connection  # noqa: E402
from app.services import location_resolver  # noqa: E402
from tests.conftest import db_required  # noqa: E402

COLLEGE_ID = "NITTTR-CHENNAI"
COLLEGE_NAME = "National Institute of Technical Teachers Training and Research"
BLOCK_ID = "MAIN-ACADEMIC-BLOCK"
BLOCK_NAME = "Main Academic Block"
FLOOR_ID = "MAIN-ACADEMIC-BLOCK-FLR-1"
FLOOR_NAME = "Floor 1"
DEPT_CS = "DEPT-COMPUTER-SCIENCE"
DEPT_CS_NAME = "Computer Science"
DEPT_ADM = "DEPT-ADMINISTRATION"
DEPT_ADM_NAME = "Administration"
ROOM_CSE = "ROOM-102"
ROOM_CSE_NAME = "CSE Computer Lab 1"

HEADER = [
    "ac_id",
    "college_name",
    "block_name",
    "floor_name",
    "department_name",
    "room_type",
    "room_number",
    "ac_make_company",
    "unit_number",
]

pytestmark = db_required()

# Tables a test may hand to the scratch teardown. Fixed literals in this
# file — never request input — which is what makes the f-strings safe.
_TRACKED_TABLES = ("acs", "asset_locations", "location_aliases", "blocks", "floors", "rooms", "departments")


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture()
def scratch():
    """Track master/alias rows a test created so teardown removes them child-first."""
    created: list[tuple[str, str, object]] = []

    def track(table: str, id_column: str, value):
        assert table in _TRACKED_TABLES, table
        created.append((table, id_column, value))
        return value

    yield track

    if not created:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for table, id_column, value in reversed(created):
                cur.execute(f"DELETE FROM {table} WHERE {id_column} = %s", (value,))
        conn.commit()
    finally:
        conn.close()


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _row(ac_id: str, **over) -> dict:
    """Canonical row: full known chain by default; overrides may blank any cell."""
    base = {
        "ac_id": ac_id,
        "college_name": COLLEGE_NAME,
        "block_name": BLOCK_NAME,
        "floor_name": FLOOR_NAME,
        "department_name": DEPT_CS_NAME,
        "room_type": "Lab",
        "room_number": ROOM_CSE_NAME,
        "ac_make_company": "Daikin",
        "unit_number": ac_id,
    }
    base.update(over)
    return base


def _post(client, rows, fields=None):
    fieldnames = fields or HEADER
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return client.post(
        "/api/v1/assets/import",
        data={"file": (io.BytesIO(buf.getvalue().encode("utf-8")), "location-cases.csv")},
    )


def _report(response):
    """Unwrap the v1 envelope: returns data, asserting top-level success."""
    payload = response.get_json()
    assert payload["success"] is True, payload
    return payload["data"]


def _fetch_one(sql: str, params: tuple):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def _loc(ac_id: str) -> dict:
    return _fetch_one("SELECT * FROM asset_locations WHERE ac_id = %s", (ac_id,))


def _ac(ac_id: str) -> dict | None:
    return _fetch_one("SELECT * FROM acs WHERE ac_id = %s", (ac_id,))


def _master_counts() -> dict:
    tables = ("colleges", "blocks", "floors", "departments", "rooms", "location_aliases")
    return {table: _fetch_one(f"SELECT COUNT(*) AS n FROM {table}", ())["n"] for table in tables}


def _stable(row: dict) -> dict:
    """A resolution record without its bookkeeping timestamps (J/10 equality)."""
    return {key: value for key, value in row.items() if key not in ("created_at", "updated_at")}


def _cleanup(*ac_ids: str) -> None:
    ids = [ac_id for ac_id in ac_ids if ac_id]
    if not ids:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id = ANY(%s)", (ids,))
        conn.commit()
    finally:
        conn.close()


def _seed_block_with_floor(scratch, tag: str, *, floor_name: str = "First") -> tuple[str, str, str]:
    """Future-style master data: a block of its own with a 'First' floor."""
    block_name = f"Admin Block {tag}"
    block_id = f"BLK-ADM-{tag}"
    floor_id = f"{block_id}-FLR-1"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO blocks (block_id, college_id, block_name) VALUES (%s, %s, %s)",
                (block_id, COLLEGE_ID, block_name),
            )
            scratch("blocks", "block_id", block_id)
            cur.execute(
                "INSERT INTO floors (floor_id, block_id, floor_name, floor_number) VALUES (%s, %s, %s, 1)",
                (floor_id, block_id, floor_name),
            )
            scratch("floors", "floor_id", floor_id)
        conn.commit()
    finally:
        conn.close()
    return block_name, block_id, floor_id


def _seed_twin_rooms(scratch, tag: str) -> str:
    """Two rooms with the same name in the seeded floor — ambiguity, on demand."""
    room_name = f"Ambi Hall {tag}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for suffix in ("A", "B"):
                room_id = f"ROOM-AMBI-{tag}-{suffix}"
                cur.execute(
                    "INSERT INTO rooms (room_id, floor_id, department_id, room_name)"
                    " VALUES (%s, %s, %s, %s)",
                    (room_id, FLOOR_ID, DEPT_ADM, room_name),
                )
                scratch("rooms", "room_id", room_id)
        conn.commit()
    finally:
        conn.close()
    return room_name


# ─────────────────────────────────────────────────────────────────────────
# §23 J–L: re-import behaviour
# ─────────────────────────────────────────────────────────────────────────


def test_j_reimport_same_file_keeps_every_location_identical(client):
    tag = _uid()
    assigned_id, partial_id = f"LOCX-J-{tag}-A", f"LOCX-J-{tag}-P"
    rows = [
        _row(assigned_id),
        _row(partial_id, room_type="", room_number="Phantom Hall"),
    ]
    try:
        masters_before = _master_counts()
        first = _report(_post(client, rows))
        assert first["imported"] == 2 and first["created"] == 2 and first["failed"] == 0
        assert first["pending_location"] == 1  # the Phantom Hall row
        snap = {ac_id: _stable(_loc(ac_id)) for ac_id in (assigned_id, partial_id)}

        second = _report(_post(client, rows))  # the very same file again
        assert second["imported"] == 2
        assert second["created"] == 0 and second["updated"] == 2
        assert second["failed"] == 0
        for ac_id in (assigned_id, partial_id):
            assert _stable(_loc(ac_id)) == snap[ac_id], f"location changed on re-import for {ac_id}"
        assert _master_counts() == masters_before, "re-import must not create masters or aliases"
        # One row per asset, still — never a duplicate.
        for ac_id in (assigned_id, partial_id):
            assert _fetch_one("SELECT COUNT(*) AS n FROM acs WHERE ac_id = %s", (ac_id,))["n"] == 1
    finally:
        _cleanup(assigned_id, partial_id)


def test_k_reimport_after_master_data_changes_resolves_the_asset(client, scratch):
    tag = _uid()
    ac_id = f"LOCX-K-{tag}"
    room_name = f"Newly Built Hall {tag}"
    try:
        # Future-style: the room does not exist yet -> PARTIAL, room UNRESOLVED.
        first = _report(
            _post(
                client,
                [_row(ac_id, department_name=DEPT_ADM_NAME, room_type="", room_number=room_name)],
            )
        )
        assert first["imported"] == 1 and first["pending_location"] == 1 and first["failed"] == 0
        assert _ac(ac_id)["location_status"] == "PARTIAL"
        loc = _loc(ac_id)
        assert loc["block_id"] == BLOCK_ID and loc["floor_id"] == FLOOR_ID
        assert loc["room_id"] is None and loc["room_status"] == "UNRESOLVED"
        assert loc["source_room_number_value"] == room_name

        # Master data changes: the room is added to that very floor...
        room_id = f"ROOM-NEW-{tag}"
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO rooms (room_id, floor_id, department_id, room_name)"
                    " VALUES (%s, %s, %s, %s)",
                    (room_id, FLOOR_ID, DEPT_ADM, room_name),
                )
            conn.commit()
        finally:
            conn.close()
        scratch("rooms", "room_id", room_id)

        # ...and the same file now assigns the previously pending asset.
        second = _report(
            _post(
                client,
                [_row(ac_id, department_name=DEPT_ADM_NAME, room_type="", room_number=room_name)],
            )
        )
        assert second["imported"] == 1 and second["updated"] == 1
        assert second["pending_location"] == 0 and second["failed"] == 0
        loc = _loc(ac_id)
        assert loc["room_id"] == room_id and loc["room_status"] == "RESOLVED"
        assert _ac(ac_id)["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)


def test_l_confirmed_manual_location_is_never_downgraded_by_incomplete_import(client):
    tag = _uid()
    ac_id = f"LOCX-L-{tag}"
    garbage_room = f"TOTALLY-UNKNOWN-{tag}"
    try:
        first = _report(_post(client, [_row(ac_id)]))
        assert first["imported"] == 1 and first["pending_location"] == 0
        assert _loc(ac_id)["room_id"] == ROOM_CSE

        # A human physically confirms the room (§10 manual pin).
        r = client.patch(
            f"/api/v1/assets/{ac_id}/location", json={"room_id": ROOM_CSE, "note": "walked the floor"}
        )
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["data"]["location"]["manual_fields"] == ["room"]

        # An incomplete/incorrect future import arrives for the same asset.
        second = _report(
            _post(
                client,
                [_row(ac_id, floor_name="", room_type="", room_number=garbage_room)],
            )
        )
        assert second["imported"] == 1 and second["failed"] == 0
        loc = _loc(ac_id)
        assert loc["room_id"] == ROOM_CSE, "manual pin must survive the import"
        assert loc["manual_fields"] == ["room"]
        # The new source text is still preserved — for the review section.
        assert loc["source_room_number_value"] == garbage_room
        assert "manually confirmed" in (loc["resolution_notes"] or {}).get("room", "")
        acs_row = _ac(ac_id)
        assert acs_row["room_id"] == ROOM_CSE and acs_row["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)


# ─────────────────────────────────────────────────────────────────────────
# §24 cases 1–2: known chain without a room number (and its case variant)
# ─────────────────────────────────────────────────────────────────────────


def test_case_1_known_chain_without_room_number_is_partial(client, scratch):
    tag = _uid()
    block_name, block_id, floor_id = _seed_block_with_floor(scratch, tag)
    ac_id = f"LOCX-C1-{tag}"
    try:
        data = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        college_name="",
                        block_name=block_name,
                        floor_name="First",
                        department_name=DEPT_ADM_NAME,
                        room_type="ADMIN OFFICE",
                        room_number="",
                    )
                ],
            )
        )
        assert data["imported"] == 1 and data["failed"] == 0
        assert data["pending_location"] == 1
        # A room TYPE without a room NUMBER is metadata, not a room.
        assert any("room_number not provided" in w["message"] for w in data["warnings"])
        acs_row = _ac(ac_id)
        assert acs_row["location_status"] == "PARTIAL"
        loc = _loc(ac_id)
        assert loc["block_id"] == block_id and loc["block_status"] == "RESOLVED"
        assert loc["floor_id"] == floor_id and loc["floor_status"] == "RESOLVED"
        assert loc["department_id"] == DEPT_ADM and loc["department_status"] == "RESOLVED"
        assert loc["room_id"] is None and loc["room_status"] == "MISSING"
        # college derives from the block chain; nothing else is invented.
        assert loc["college_id"] == COLLEGE_ID and loc["college_status"] == "RESOLVED"
        assert loc["source_room_type_value"] == "ADMIN OFFICE"
        assert loc["source_room_number_value"] is None
    finally:
        _cleanup(ac_id)


def test_case_2_case_and_whitespace_variant_resolves_identically(client, scratch):
    """Same chain as case 1, shouted and padded — same ids, same statuses."""
    tag = _uid()
    block_name, block_id, floor_id = _seed_block_with_floor(scratch, tag)
    ac_plain, ac_shouted = f"LOCX-C2-{tag}-A", f"LOCX-C2-{tag}-B"
    try:
        data = _report(
            _post(
                client,
                [
                    _row(
                        ac_plain,
                        college_name="",
                        block_name=block_name,
                        floor_name="First",
                        department_name=DEPT_ADM_NAME,
                        room_type="ADMIN OFFICE",
                        room_number="",
                    ),
                    _row(
                        ac_shouted,
                        college_name="",
                        block_name=f"  {block_name.upper().replace(' ', '   ')}  ",
                        floor_name=" FIRST ",
                        department_name="administration",
                        room_type="Admin Office",
                        room_number="   ",
                    ),
                ],
            )
        )
        assert data["imported"] == 2 and data["failed"] == 0
        plain, shouted = _loc(ac_plain), _loc(ac_shouted)
        for level in ("college", "block", "floor", "department", "room"):
            assert shouted[f"{level}_id"] == plain[f"{level}_id"], level
            assert shouted[f"{level}_status"] == plain[f"{level}_status"], level
        assert plain["room_status"] == "MISSING" and plain["room_id"] is None
        assert plain["block_id"] == block_id and plain["floor_id"] == floor_id
        assert plain["department_id"] == DEPT_ADM
        # Source text is preserved verbatim (padding and all) — it only has
        # to NORMALIZE equal for the match to have been legal.
        assert location_resolver.normalize(shouted["source_block_value"]) == location_resolver.normalize(
            block_name
        )
        assert shouted["source_block_value"].strip() != block_name  # raw text kept, not rewritten
        assert shouted["source_room_type_value"] == "Admin Office"
        assert plain["source_room_type_value"] == "ADMIN OFFICE"
        assert _ac(ac_shouted)["location_status"] == "PARTIAL"
    finally:
        _cleanup(ac_plain, ac_shouted)


# ─────────────────────────────────────────────────────────────────────────
# §24 cases 3–5: partially resolvable chains
# ─────────────────────────────────────────────────────────────────────────


def test_case_3_unknown_block_known_department(client):
    tag = _uid()
    ac_id = f"LOCX-C3-{tag}"
    unknown_block = f"Unknown Block {tag}"
    try:
        before = _master_counts()
        data = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        college_name="",
                        block_name=unknown_block,
                        floor_name="",
                        department_name=DEPT_ADM_NAME,
                        room_type="",
                        room_number="",
                    )
                ],
            )
        )
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 1
        loc = _loc(ac_id)
        # The known department still resolves — resolution is not all-or-nothing.
        assert loc["department_id"] == DEPT_ADM and loc["department_status"] == "RESOLVED"
        assert loc["block_id"] is None and loc["block_status"] == "UNRESOLVED"
        assert loc["source_block_value"] == unknown_block, "source is preserved for review"
        assert loc["room_id"] is None and loc["room_status"] == "MISSING"
        assert _ac(ac_id)["location_status"] == "PARTIAL"
        assert _master_counts() == before, "an unknown block must not be created"
    finally:
        _cleanup(ac_id)


def test_case_4_known_block_unknown_floor_keeps_the_block(client):
    tag = _uid()
    ac_id = f"LOCX-C4-{tag}"
    unknown_floor = f"Basement Level {tag}"
    try:
        data = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        college_name="",
                        block_name=BLOCK_NAME,
                        floor_name=unknown_floor,
                        department_name=DEPT_CS_NAME,
                        room_type="",
                        room_number="",
                    )
                ],
            )
        )
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 1
        loc = _loc(ac_id)
        assert loc["block_id"] == BLOCK_ID and loc["block_status"] == "RESOLVED"
        assert loc["floor_id"] is None and loc["floor_status"] == "UNRESOLVED"
        assert loc["source_floor_value"] == unknown_floor
        assert loc["department_id"] == DEPT_CS and loc["department_status"] == "RESOLVED"
        assert loc["room_id"] is None and loc["room_status"] == "MISSING"
        assert loc["college_id"] == COLLEGE_ID  # derived from the confirmed block
        assert _ac(ac_id)["location_status"] == "PARTIAL"
    finally:
        _cleanup(ac_id)


def test_case_5_known_block_and_floor_unknown_department(client):
    tag = _uid()
    ac_id = f"LOCX-C5-{tag}"
    unknown_dept = f"Dept of Quantum Basketweaving {tag}"
    try:
        before = _master_counts()
        data = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        college_name="",
                        block_name=BLOCK_NAME,
                        floor_name=FLOOR_NAME,
                        department_name=unknown_dept,
                        room_type="",
                        room_number="",
                    )
                ],
            )
        )
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 1
        loc = _loc(ac_id)
        assert loc["block_id"] == BLOCK_ID and loc["block_status"] == "RESOLVED"
        assert loc["floor_id"] == FLOOR_ID and loc["floor_status"] == "RESOLVED"
        assert loc["department_id"] is None and loc["department_status"] == "UNRESOLVED"
        assert loc["source_department_value"] == unknown_dept
        assert loc["room_id"] is None and loc["room_status"] == "MISSING"
        assert _ac(ac_id)["location_status"] == "PARTIAL"
        assert _master_counts() == before, "an unknown department must not be created"
    finally:
        _cleanup(ac_id)


# ─────────────────────────────────────────────────────────────────────────
# §24 cases 6–8: resolvable room, ambiguous room, alias college
# ─────────────────────────────────────────────────────────────────────────


def test_case_6_known_room_number_resolves_to_assigned(client):
    tag = _uid()
    ac_id = f"LOCX-C6-{tag}"
    try:
        data = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        block_name=BLOCK_NAME,
                        floor_name=FLOOR_NAME,
                        department_name=DEPT_CS_NAME,
                        room_type="",
                        room_number="ROOM-102",  # canonical id spelling
                    )
                ],
            )
        )
        assert data["imported"] == 1 and data["failed"] == 0
        assert data["pending_location"] == 0
        loc = _loc(ac_id)
        assert loc["room_id"] == ROOM_CSE and loc["room_status"] == "RESOLVED"
        for level in ("college", "block", "floor", "department"):
            assert loc[f"{level}_status"] == "RESOLVED", level
        assert loc["college_id"] == COLLEGE_ID
        assert loc["block_id"] == BLOCK_ID and loc["floor_id"] == FLOOR_ID
        assert loc["department_id"] == DEPT_CS
        assert _ac(ac_id)["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)


def test_case_7_ambiguous_room_number_is_reported_not_guessed(client, scratch):
    tag = _uid()
    room_name = _seed_twin_rooms(scratch, tag)
    ac_id = f"LOCX-C7-{tag}"
    try:
        before = _master_counts()
        data = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        college_name="",
                        block_name=BLOCK_NAME,
                        floor_name=FLOOR_NAME,
                        department_name=DEPT_ADM_NAME,
                        room_type="",
                        room_number=room_name,
                    )
                ],
            )
        )
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 1
        assert any("ambiguous" in w["message"] for w in data["warnings"])
        loc = _loc(ac_id)
        assert loc["room_id"] is None and loc["room_status"] == "AMBIGUOUS"
        # The rest of the chain still resolves around the ambiguity.
        assert loc["block_id"] == BLOCK_ID and loc["floor_id"] == FLOOR_ID
        assert loc["department_id"] == DEPT_ADM
        assert _ac(ac_id)["location_status"] == "PARTIAL"
        assert _master_counts() == before, "ambiguity must not duplicate or create masters"
    finally:
        _cleanup(ac_id)


def test_case_8_alias_college_name_resolves(client, scratch):
    tag = _uid()
    ac_id = f"LOCX-C8-{tag}"
    alias_text = f"NITTTR Legacy {tag}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)"
                " VALUES ('college', %s, %s, %s, 'manual') RETURNING id",
                (COLLEGE_ID, alias_text, location_resolver.normalize(alias_text)),
            )
            alias_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    scratch("location_aliases", "id", alias_id)
    try:
        data = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        college_name=alias_text,
                        block_name=BLOCK_NAME,
                        floor_name=FLOOR_NAME,
                        department_name=DEPT_CS_NAME,
                        room_type="Lab",
                        room_number=ROOM_CSE_NAME,
                    )
                ],
            )
        )
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 0
        loc = _loc(ac_id)
        assert loc["college_id"] == COLLEGE_ID and loc["college_status"] == "RESOLVED"
        assert loc["room_id"] == ROOM_CSE
        assert _ac(ac_id)["location_status"] == "ASSIGNED"
        # The alias was consumed, not copied or duplicated.
        assert (
            _fetch_one(
                "SELECT COUNT(*) AS n FROM location_aliases WHERE entity_type = 'college'"
                " AND normalized_alias = %s",
                (location_resolver.normalize(alias_text),),
            )["n"]
            == 1
        )
    finally:
        _cleanup(ac_id)


# ─────────────────────────────────────────────────────────────────────────
# §24 case 9 + §23 V/W/X at import level: nothing invented, nothing lost
# ─────────────────────────────────────────────────────────────────────────


def test_case_9_completely_unknown_location_is_pending_and_invents_nothing(client):
    tag = _uid()
    ac_id = f"LOCX-C9-{tag}"
    unknown = {
        "college_name": f"Institute of Nowhere {tag}",
        "block_name": f"Unknown Block {tag}",
        "floor_name": f"Unknown Floor {tag}",
        "department_name": f"Unknown Department {tag}",
        "room_type": "",
        "room_number": f"Imaginary Room {tag}",
    }
    try:
        before = _master_counts()
        data = _report(_post(client, [_row(ac_id, **unknown)]))
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 1
        loc = _loc(ac_id)
        for level in ("college", "block", "floor", "department", "room"):
            assert loc[f"{level}_id"] is None, level
            assert loc[f"{level}_status"] == "UNRESOLVED", level
        # Every source value survives exactly as supplied (review material).
        assert loc["source_college_value"] == unknown["college_name"]
        assert loc["source_block_value"] == unknown["block_name"]
        assert loc["source_floor_value"] == unknown["floor_name"]
        assert loc["source_department_value"] == unknown["department_name"]
        assert loc["source_room_number_value"] == unknown["room_number"]
        assert _ac(ac_id)["location_status"] == "PENDING"
        # §23 W + X: no masters, no rooms, no aliases were fabricated.
        assert _master_counts() == before
    finally:
        _cleanup(ac_id)


def test_v_unknown_room_import_never_creates_a_room_master(client):
    tag = _uid()
    ac_id = f"LOCX-V-{tag}"
    phantom = f"Phantom Room {tag}"
    try:
        rooms_before = _fetch_one("SELECT COUNT(*) AS n FROM rooms", ())["n"]
        data = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        college_name="",
                        block_name=BLOCK_NAME,
                        floor_name=FLOOR_NAME,
                        department_name=DEPT_CS_NAME,
                        room_type="Lab",
                        room_number=phantom,
                    )
                ],
            )
        )
        assert data["imported"] == 1 and data["failed"] == 0 and data["pending_location"] == 1
        loc = _loc(ac_id)
        assert loc["room_id"] is None and loc["room_status"] == "UNRESOLVED"
        assert loc["source_room_number_value"] == phantom
        assert _ac(ac_id)["location_status"] == "PARTIAL"  # known levels survive
        assert (
            _fetch_one("SELECT COUNT(*) AS n FROM rooms", ())["n"] == rooms_before
        ), "an unknown room number must never fabricate a room"
    finally:
        _cleanup(ac_id)


# ─────────────────────────────────────────────────────────────────────────
# §24 cases 10–11: same asset twice; confirmed asset with a blank location
# ─────────────────────────────────────────────────────────────────────────


def test_case_10_same_asset_imported_twice_updates_one_row(client):
    tag = _uid()
    ac_id = f"LOCX-C10-{tag}"
    try:
        before = _master_counts()
        first = _report(_post(client, [_row(ac_id)]))
        assert first["imported"] == 1 and first["created"] == 1
        snap = _stable(_loc(ac_id))

        second = _report(_post(client, [_row(ac_id)]))
        assert second["imported"] == 1 and second["created"] == 0 and second["updated"] == 1
        assert second["failed"] == 0
        # Exactly one asset, exactly one resolution record, identical location.
        assert _fetch_one("SELECT COUNT(*) AS n FROM acs WHERE ac_id = %s", (ac_id,))["n"] == 1
        assert _fetch_one("SELECT COUNT(*) AS n FROM asset_locations WHERE ac_id = %s", (ac_id,))["n"] == 1
        assert _stable(_loc(ac_id)) == snap
        assert _master_counts() == before
    finally:
        _cleanup(ac_id)


def test_case_11_confirmed_asset_with_blank_location_is_not_downgraded(client):
    tag = _uid()
    ac_id = f"LOCX-C11-{tag}"
    try:
        first = _report(_post(client, [_row(ac_id)]))
        assert first["imported"] == 1 and first["pending_location"] == 0
        before = _loc(ac_id)
        assert before["room_id"] == ROOM_CSE and before["room_status"] == "RESOLVED"

        # Same asset, incomplete file: room and floor cells blank.
        second = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        college_name="",
                        floor_name="",
                        room_type="",
                        room_number="",
                    )
                ],
            )
        )
        assert second["imported"] == 1 and second["updated"] == 1 and second["failed"] == 0
        assert second["pending_location"] == 0, "the confirmed room never became pending"
        after = _loc(ac_id)
        # Blank source keeps the previously confirmed values (§11).
        assert after["room_id"] == ROOM_CSE and after["room_status"] == "RESOLVED"
        assert after["floor_id"] == FLOOR_ID and after["floor_status"] == "RESOLVED"
        assert after["block_id"] == BLOCK_ID and after["department_id"] == DEPT_CS
        # ...and blank cells never erase the preserved source text either.
        assert after["source_room_number_value"] == before["source_room_number_value"]
        assert after["source_floor_value"] == before["source_floor_value"]
        acs_row = _ac(ac_id)
        assert acs_row["room_id"] == ROOM_CSE and acs_row["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)


# ─────────────────────────────────────────────────────────────────────────
# Explicit canonical source ids (§2/§3) + verified alias (§5)
# ─────────────────────────────────────────────────────────────────────────

HEADER_IDS = HEADER + ["block_id", "floor_id", "room_id"]


def _ensure_nitttrc_alias() -> None:
    """The migration-016 seed, made independent of ambient DB state."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)"
                " VALUES ('college', 'NITTTR-CHENNAI', 'NITTTRC', 'nitttrc', 'test-ensure')"
                " ON CONFLICT (entity_type, normalized_alias) DO NOTHING"
            )
        conn.commit()
    finally:
        conn.close()


def test_explicit_ids_resolve_without_usable_names(client):
    """Canonical ids are honoured first: garbage names resolve via the ids,
    and every source value — names and ids — is preserved verbatim."""
    tag = _uid()
    ac_id = f"LOCX-ID-{tag}"
    try:
        report = _report(
            _post(
                client,
                [
                    {
                        "ac_id": ac_id,
                        "college_name": COLLEGE_NAME,
                        "block_name": "Garbage Block",
                        "block_id": BLOCK_ID,
                        "floor_name": "",
                        "floor_id": FLOOR_ID,
                        "department_name": DEPT_CS_NAME,
                        "room_type": "",
                        "room_number": "",
                        "room_id": ROOM_CSE,
                        "ac_make_company": "Daikin",
                        "unit_number": ac_id,
                    }
                ],
                fields=HEADER_IDS,
            )
        )
        assert report["imported"] == 1 and report["failed"] == 0
        assert report["pending_location"] == 0, report["warnings"]
        record = _loc(ac_id)
        assert record["room_id"] == ROOM_CSE and record["room_status"] == "RESOLVED"
        assert record["floor_id"] == FLOOR_ID and record["block_id"] == BLOCK_ID
        assert record["department_id"] == DEPT_CS and record["college_id"] == COLLEGE_ID
        # Source values stay the original import text — never overwritten
        # with canonical display names.
        assert record["source_block_value"] == "Garbage Block"
        assert record["source_block_id_value"] == BLOCK_ID
        assert record["source_floor_id_value"] == FLOOR_ID
        assert record["source_room_id_value"] == ROOM_CSE
        acs_row = _ac(ac_id)
        assert acs_row["room_id"] == ROOM_CSE and acs_row["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)


def test_nitttrc_alias_resolves_college(client):
    """The verified source alias NITTTRC -> NITTTR-CHENNAI (§5)."""
    _ensure_nitttrc_alias()
    tag = _uid()
    ac_id = f"LOCX-NC-{tag}"
    try:
        report = _report(_post(client, [_row(ac_id, college_name="NITTTRC", room_number="")]))
        assert report["imported"] == 1 and report["failed"] == 0
        record = _loc(ac_id)
        assert record["college_id"] == COLLEGE_ID and record["college_status"] == "RESOLVED"
        assert record["source_college_value"] == "NITTTRC"
    finally:
        _cleanup(ac_id)


def test_garbage_reimport_keeps_room_and_warns(client):
    """Nonblank garbage never downgrades a confirmed room (§4): the id is
    kept, the status stays ASSIGNED, and the report still surfaces the
    failed source (unresolved_source) for review."""
    tag = _uid()
    ac_id = f"LOCX-GK-{tag}"
    try:
        first = _report(_post(client, [_row(ac_id)]))
        assert first["imported"] == 1 and first["pending_location"] == 0
        second = _report(_post(client, [_row(ac_id, room_number="Ghost 9")]))
        assert second["imported"] == 1 and second["updated"] == 1 and second["failed"] == 0
        assert second["pending_location"] == 0, "the confirmed room never became pending"
        kinds = [w["kind"] for w in second["warnings"]]
        assert "unresolved_source" in kinds
        assert any("Ghost 9" in w["message"] for w in second["warnings"] if w["kind"] == "unresolved_source")
        record = _loc(ac_id)
        assert record["room_id"] == ROOM_CSE and record["room_status"] == "RESOLVED"
        assert "kept previously confirmed room" in (record["resolution_notes"] or {}).get("room", "")
        assert record["source_room_number_value"] == "Ghost 9", "the failed source stays stored"
        acs_row = _ac(ac_id)
        assert acs_row["room_id"] == ROOM_CSE and acs_row["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)


def test_blank_reimport_keeps_room_quietly(client):
    """Blank cells keep confirmed values with no warnings at all (§4).

    block_name/department_name stay (canonical mode requires them); every
    other location cell goes blank.
    """
    tag = _uid()
    ac_id = f"LOCX-BK-{tag}"
    try:
        first = _report(_post(client, [_row(ac_id)]))
        assert first["imported"] == 1 and first["pending_location"] == 0
        second = _report(
            _post(
                client,
                [_row(ac_id, college_name="", floor_name="", room_type="", room_number="")],
            )
        )
        assert second["imported"] == 1 and second["updated"] == 1 and second["failed"] == 0
        assert second["pending_location"] == 0
        assert second["warnings"] == [], second["warnings"]
        record = _loc(ac_id)
        assert record["room_id"] == ROOM_CSE and record["room_status"] == "RESOLVED"
        acs_row = _ac(ac_id)
        assert acs_row["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)


def test_import_partial_row_serves_effective_detail(client):
    """The exact original failure path (§16 backend): a room-less row with a
    garbage block, a known department and a room TYPE serves effective
    building/department/room metadata from GET /assets/<id> — room_id NULL
    no longer hides the other dimensions."""
    tag = _uid()
    ac_id = f"LOCX-EF-{tag}"
    try:
        report = _report(
            _post(
                client,
                [
                    _row(
                        ac_id,
                        college_name="",
                        block_name="Phantom Block",
                        floor_name="",
                        department_name=DEPT_ADM_NAME,
                        room_type="ADMIN OFFICE",
                        room_number="",
                    )
                ],
            )
        )
        assert report["imported"] == 1 and report["failed"] == 0
        record = _loc(ac_id)
        assert record["room_id"] is None
        assert record["department_id"] == DEPT_ADM
        assert record["department_status"] == "RESOLVED"
        assert record["block_id"] is None and record["block_status"] == "UNRESOLVED"
        assert record["source_block_value"] == "Phantom Block"
        assert record["source_room_type_value"] == "ADMIN OFFICE"

        detail = client.get(f"/api/v1/assets/{ac_id}")
        assert detail.status_code == 200, detail.get_json()
        data = detail.get_json()["data"]
        assert data["location_status"] == "PARTIAL"
        # Flat fields carry the effective text: unresolved source visible.
        assert data["block_name"] == "Phantom Block"
        assert data["department_name"] == DEPT_ADM_NAME
        assert data["room_type"] == "ADMIN OFFICE"
        assert data["room_id"] is None
        # Nested levels keep the canonical/source split.
        levels = data["location"]["levels"]
        assert levels["block"] == {
            "id": None,
            "name": None,
            "status": "UNRESOLVED",
            "source": "Phantom Block",
        }
        assert levels["department"]["id"] == DEPT_ADM
        assert levels["department"]["status"] == "RESOLVED"
    finally:
        _cleanup(ac_id)
