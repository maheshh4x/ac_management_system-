"""ACMS bulk-import regression: pending-location repair + idempotent re-import.

The reference workbook ``ACMS 301.xlsx`` (300 canonical rows, NO ac_id column)
was imported before its campus hierarchy existed: every row minted the
deterministic unassigned token ``AC-LOC-<block>-<dept>[-U/-S]`` and stayed
PENDING with ``no campus college matches …`` / ``no campus block matches
block_name 'Admin block'`` warnings. This module pins the repair loop end to
end, using synthetic ACMS-shaped vocabulary (college 'NITTTRC', blocks
'Admin Block …', floors 'FIRST ' / 'Ground', departments 'Civil …', unit +
serial discriminators):

* unknown college + block stay unresolved, invent no entities, and surface
  in the §13 review queue;
* case/whitespace variants resolve identically and adopt the same token;
* the verified alias NITTTRC -> NITTTR-CHENNAI resolves the college;
* ambiguous block matches are reported, never guessed;
* a row whose block value is really a FLOOR name ('Ground') never matches a
  block and never creates one;
* duplicate imports of the same generated-identity file are idempotent;
* a re-import AFTER the hierarchy arrives adopts the stored pending token
  (same ac_id, update — never a ``AC-<room>-…`` duplicate beside it) and the
  serial guard refuses adoption when the stored serial disagrees;
* ``POST /api/v1/campus/location/re-resolve`` repairs pending assets in
  place (stable ac_id/asset_uuid) once masters + alias exist.

Every asset, block, floor, room, department and alias a test creates is
tracked by the ``scratch`` fixture (or a ``finally`` cleanup for acs rows) so
the shared seeded campus never mutates.
"""

import csv
import io
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import RealDictCursor  # noqa: E402

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import db_required  # noqa: E402

COLLEGE_ID = "NITTTR-CHENNAI"
COLLEGE_NAME = "National Institute of Technical Teachers Training and Research"
LOCATION = "/api/v1/campus/location"

pytestmark = db_required()

# Tables a test may hand to the scratch teardown. Fixed literals in this
# file — never request input — which is what makes the f-strings safe.
_TRACKED_TABLES = ("acs", "asset_locations", "location_aliases", "blocks", "floors", "rooms", "departments")

# ACMS 301.xlsx-style canonical header: no ac_id column, so identity is
# GENERATED (the exact shape that produced the ~300 pending-location assets).
ACMS_HEADER = [
    "college_name",
    "block_name",
    "floor_name",
    "department_name",
    "room_type",
    "room_number",
    "ac_make_company",
    "unit_number",
    "serial_number",
]


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


def _acms_row(**over) -> dict:
    """ACMS-shaped row (no ac_id); overrides may blank any cell."""
    base = {
        "college_name": "NITTTRC",
        "block_name": "Admin Block",
        "floor_name": "FIRST",
        "department_name": "Civil",
        "room_type": "Office",
        "room_number": "",
        "ac_make_company": "Voltas",
        "unit_number": "1",
        "serial_number": "",
    }
    base.update(over)
    return base


def _post(client, rows, fields=None):
    fieldnames = fields or ACMS_HEADER
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in fieldnames})
    return client.post(
        "/api/v1/assets/import",
        data={"file": (io.BytesIO(buf.getvalue().encode("utf-8")), "acms-pending.csv")},
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


def _ac(ac_id: str) -> dict | None:
    return _fetch_one("SELECT * FROM acs WHERE ac_id = %s", (ac_id,))


def _loc(ac_id: str) -> dict:
    return _fetch_one("SELECT * FROM asset_locations WHERE ac_id = %s", (ac_id,))


def _master_counts() -> dict:
    tables = ("colleges", "blocks", "floors", "departments", "rooms", "location_aliases")
    return {table: _fetch_one(f"SELECT COUNT(*) AS n FROM {table}", ())["n"] for table in tables}


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


def _register_alias(scratch, alias_text: str) -> None:
    """A per-test college alias (tracked, so teardown removes it)."""
    normalized = alias_text.lower().replace(" ", "")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)"
                " VALUES ('college', %s, %s, %s, 'test-ensure') RETURNING id",
                (COLLEGE_ID, alias_text, normalized),
            )
            alias_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    scratch("location_aliases", "id", alias_id)


def _seed_chain(
    scratch,
    tag: str,
    *,
    block_name: str | None = None,
    floor_name: str = "First",
    department_name: str | None = None,
    room_name: str | None = None,
    room_type: str = "Office",
) -> dict:
    """Full campus chain (block + floor + department + room) for one tag.

    Mirrors what a facility manager creates AFTER the pending import via the
    MANAGE_CAMPUS CRUD endpoints — the repair half of the ACMS-301 flow.
    """
    block_name = block_name or f"Admin Block {tag}"
    department_name = department_name or f"Civil {tag}"
    room_name = room_name or f"Room {tag}"
    block_id = f"BLK-{tag}"
    floor_id = f"{block_id}-FLR-1"
    department_id = f"DEPT-{tag}"
    room_id = f"ROOM-{tag}"
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
            cur.execute(
                "INSERT INTO departments (department_id, department_name) VALUES (%s, %s)",
                (department_id, department_name),
            )
            scratch("departments", "department_id", department_id)
            cur.execute(
                "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
                " VALUES (%s, %s, %s, %s, %s)",
                (room_id, floor_id, department_id, room_name, room_type),
            )
            scratch("rooms", "room_id", room_id)
        conn.commit()
    finally:
        conn.close()
    return {
        "block_name": block_name,
        "block_id": block_id,
        "floor_name": floor_name,
        "floor_id": floor_id,
        "department_name": department_name,
        "department_id": department_id,
        "room_name": room_name,
        "room_id": room_id,
    }


# ─────────────────────────────────────────────────────────────────────────
# Missing / unknown locations: unresolved, invent nothing, surface for review
# ─────────────────────────────────────────────────────────────────────────


def test_unknown_college_and_block_stay_pending_and_create_nothing(client):
    """The exact ACMS failure chain: unknown college AND block -> every level
    unresolved, warnings quote the source, and no campus entity is invented."""
    tag = _uid()
    masters_before = _master_counts()
    row = _acms_row(
        college_name=f"Nowhere College {tag}",
        block_name=f"Admin Block {tag}",
        floor_name="FIRST ",
        department_name=f"Civil {tag}",
        room_number=f"Room {tag}",
        unit_number="1",
        serial_number=f"S-{tag}",
    )
    report = _report(_post(client, [row]))
    assert report["imported"] == 1 and report["created"] == 1 and report["failed"] == 0
    assert report["pending_location"] == 1
    ac_id = report["rows"][0]["ac_id"]
    try:
        assert ac_id.startswith("AC-LOC-"), ac_id  # deterministic unassigned token
        record = _loc(ac_id)
        assert record["college_id"] is None and record["college_status"] == "UNRESOLVED"
        assert record["block_id"] is None and record["block_status"] == "UNRESOLVED"
        assert record["floor_id"] is None and record["room_id"] is None
        assert _ac(ac_id)["location_status"] == "PENDING"
        # The warning quotes the first failed level (college), in the incident's
        # words; the block note is persisted per-level in resolution_notes.
        messages = [w["message"] for w in report["warnings"] if w["kind"] == "pending_location"]
        assert any(f"no campus college matches 'Nowhere College {tag}'" in m for m in messages), messages
        assert f"no campus block matches block_name 'Admin Block {tag}'" in (
            record["resolution_notes"] or {}
        ).get("block", "")
        # Nothing was invented: master tables are untouched.
        assert _master_counts() == masters_before
        # The row is offered for authorized review (§13), not silently fixed.
        # The queue pages at 200 and orders by ac_id — walk it until found.
        page, found = 1, False
        while True:
            r = client.get(LOCATION + "/review", query_string={"page_size": 200, "page": page})
            assert r.status_code == 200, r.get_json()
            items = r.get_json()["data"]
            found = any(item["ac_id"] == ac_id for item in items)
            if found or not items or page * 200 >= r.get_json()["pagination"]["total"]:
                break
            page += 1
        assert found, "pending asset must surface in the §13 review queue"
    finally:
        _cleanup(ac_id)


def test_ambiguous_block_matches_are_reported_never_guessed(client, scratch):
    """Two blocks with the same name in one college -> AMBIGUOUS, no id, and
    the dependent floor/room never resolve off a guess."""
    tag = _uid()
    for suffix in ("A", "B"):
        block_id = f"BLK-AMBI-{tag}-{suffix}"
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO blocks (block_id, college_id, block_name) VALUES (%s, %s, %s)",
                    (block_id, COLLEGE_ID, f"Ambi Block {tag}"),
                )
            conn.commit()
        finally:
            conn.close()
        scratch("blocks", "block_id", block_id)
    row = _acms_row(
        college_name=COLLEGE_NAME,
        block_name=f"Ambi Block {tag}",
        floor_name="FIRST",
        department_name=f"Civil {tag}",
        room_number="",
        unit_number="1",
        serial_number=f"S-{tag}",
    )
    report = _report(_post(client, [row]))
    assert report["imported"] == 1 and report["failed"] == 0
    ac_id = report["rows"][0]["ac_id"]
    try:
        record = _loc(ac_id)
        assert record["block_id"] is None and record["block_status"] == "AMBIGUOUS"
        assert record["floor_id"] is None, "floor must not resolve off an ambiguous block"
        assert f"multiple campus blocks match 'Ambi Block {tag}'" in (record["resolution_notes"] or {}).get(
            "block", ""
        )
    finally:
        _cleanup(ac_id)


def test_ground_as_block_value_never_matches_or_creates_a_block(client, scratch):
    """Workbook rows 76–77 carry block_name='Ground' although 'Ground' is a
    FLOOR value in that file: the resolver must not cross levels — the block
    stays unresolved for review and no 'Ground' block is invented."""
    tag = _uid()
    chain = _seed_chain(scratch, tag, floor_name="Ground")
    row = _acms_row(
        college_name=COLLEGE_NAME,
        block_name="Ground",  # the anomaly: a floor name in the block column
        floor_name="ground",
        department_name=chain["department_name"],
        room_number="",
        unit_number="1",
        serial_number=f"S-{tag}",
    )
    report = _report(_post(client, [row]))
    assert report["imported"] == 1 and report["failed"] == 0
    ac_id = report["rows"][0]["ac_id"]
    try:
        record = _loc(ac_id)
        assert record["block_id"] is None and record["block_status"] == "UNRESOLVED"
        assert "no campus block matches block_name 'Ground'" in (record["resolution_notes"] or {}).get(
            "block", ""
        )
        assert record["floor_id"] is None, "floor must not resolve while its block is unresolved"
        # No block named 'Ground' was created; the seeded 'Ground' floor is untouched.
        assert _fetch_one("SELECT COUNT(*) AS n FROM blocks WHERE block_name = 'Ground'", ())["n"] == 0
        assert (
            _fetch_one(
                "SELECT COUNT(*) AS n FROM floors WHERE floor_name = 'Ground' AND block_id = %s",
                (chain["block_id"],),
            )["n"]
            == 1
        ), "exactly the seeded floor — no cross-level invention"
    finally:
        _cleanup(ac_id)


# ─────────────────────────────────────────────────────────────────────────
# Case/whitespace normalization + the verified NITTTRC alias
# ─────────────────────────────────────────────────────────────────────────


def test_alias_resolves_college_and_case_variants_resolve_the_full_chain(client):
    """'NITTTRC' + 'ADMIN BLOCK …' / 'first' resolve exactly like their
    canonical spellings once masters + alias exist (normalize() collapse)."""
    _ensure_nitttrc_alias()
    tag = _uid()
    report = _report(
        _post(
            client,
            [
                _acms_row(
                    college_name="NITTTRC",
                    block_name="MAIN ACADEMIC BLOCK",
                    floor_name="floor 1 ",  # case/whitespace variant of 'Floor 1'
                    department_name="COMPUTER SCIENCE",
                    room_type="LAB",
                    room_number="cse computer lab 1",
                    unit_number=f"U{tag}",
                    serial_number=f"S-{tag}",
                )
            ],
        )
    )
    assert report["imported"] == 1 and report["failed"] == 0
    assert report["pending_location"] == 0, report["warnings"]
    ac_id = report["rows"][0]["ac_id"]
    try:
        record = _loc(ac_id)
        assert record["college_id"] == COLLEGE_ID and record["college_status"] == "RESOLVED"
        assert record["block_id"] == "MAIN-ACADEMIC-BLOCK"
        assert record["floor_id"] == "MAIN-ACADEMIC-BLOCK-FLR-1"
        assert record["room_id"] == "ROOM-102" and record["room_status"] == "RESOLVED"
        assert _ac(ac_id)["location_status"] == "ASSIGNED"
    finally:
        _cleanup(ac_id)


# ─────────────────────────────────────────────────────────────────────────
# Idempotent duplicate imports (generated identities)
# ─────────────────────────────────────────────────────────────────────────


def test_duplicate_import_of_generated_identities_is_idempotent(client):
    """Importing the same ac_id-less file twice must update, never duplicate,
    even while every row is still pending (the ACMS steady state)."""
    tag = _uid()
    rows = [
        _acms_row(
            block_name=f"Admin Block {tag}",
            department_name=f"Civil {tag}",
            unit_number="1",
            serial_number=f"S-{tag}-1",
        ),
        _acms_row(
            block_name=f"Admin Block {tag}",
            department_name=f"Civil {tag}",
            unit_number="2",
            serial_number=f"S-{tag}-2",
        ),
    ]
    first = _report(_post(client, rows))
    assert first["created"] == 2 and first["pending_location"] == 2
    ids = {entry["ac_id"] for entry in first["rows"] if entry.get("ac_id")}
    assert len(ids) == 2 and all(ac_id.startswith("AC-LOC-") for ac_id in ids)
    try:
        second = _report(_post(client, rows))  # the very same file again
        assert second["imported"] == 2
        assert second["created"] == 0 and second["updated"] == 2 and second["failed"] == 0
        assert {entry["ac_id"] for entry in second["rows"] if entry.get("ac_id")} == ids
        for ac_id in ids:
            assert _fetch_one("SELECT COUNT(*) AS n FROM acs WHERE ac_id = %s", (ac_id,))["n"] == 1
    finally:
        _cleanup(*ids)


# ─────────────────────────────────────────────────────────────────────────
# Pending → resolved repair: the re-import must ADOPT the stored identity
# ─────────────────────────────────────────────────────────────────────────


def test_pending_identity_is_adopted_when_the_hierarchy_arrives(client, scratch):
    """The core idempotency fix: a file imported while its location was
    unresolvable stored AC-LOC-… tokens. Re-importing it AFTER the campus
    hierarchy + alias exist must UPDATE those same assets (stable ac_id and
    asset_uuid — the QR payload never changes) instead of minting a second,
    room-based ``AC-<room>-…`` identity beside each one."""
    tag = _uid()
    alias = f"NITTTRC {tag}"  # per-test alias: absent during the first import
    row = _acms_row(
        college_name=alias,
        block_name=f"Admin Block {tag}",
        floor_name="FIRST ",
        department_name=f"Civil {tag}",
        room_type="Office",
        room_number=f"Room {tag}",
        unit_number="1",
        serial_number=f"S-{tag}",
    )
    first = _report(_post(client, [row]))
    assert first["created"] == 1 and first["pending_location"] == 1
    ac_id = first["rows"][0]["ac_id"]
    try:
        assert ac_id.startswith("AC-LOC-"), ac_id
        uuid_before = _ac(ac_id)["asset_uuid"]
        assert _ac(ac_id)["location_status"] == "PENDING"

        # The repair: campus managers create the hierarchy + register the alias.
        chain = _seed_chain(scratch, tag)
        _register_alias(scratch, alias)

        second = _report(_post(client, [row]))  # the very same file again
        assert second["imported"] == 1 and second["failed"] == 0
        assert second["created"] == 0 and second["updated"] == 1
        assert second["pending_identities_adopted"] == 1
        assert second["pending_location"] == 0, second["warnings"]
        assert second["rows"][0]["ac_id"] == ac_id, "the stored token must be adopted, not replaced"

        acs_row = _ac(ac_id)
        assert acs_row["room_id"] == chain["room_id"]
        assert acs_row["location_status"] == "ASSIGNED"
        assert acs_row["asset_uuid"] == uuid_before, "stable identifier must never change"
        record = _loc(ac_id)
        assert record["college_id"] == COLLEGE_ID and record["college_status"] == "RESOLVED"
        assert record["block_id"] == chain["block_id"] and record["block_status"] == "RESOLVED"
        assert record["floor_id"] == chain["floor_id"]
        assert record["room_id"] == chain["room_id"] and record["room_status"] == "RESOLVED"
        # No room-based duplicate was minted next to the adopted asset.
        assert _fetch_one("SELECT COUNT(*) AS n FROM acs WHERE ac_id LIKE %s", (f"AC-ROOM-{tag}%",))["n"] == 0
        assert _fetch_one("SELECT COUNT(*) AS n FROM acs WHERE ac_id = %s", (ac_id,))["n"] == 1
    finally:
        _cleanup(ac_id)


def test_pending_adoption_is_case_and_whitespace_stable(client, scratch):
    """The token normalizes identically across spellings, so a re-import that
    'tidied' case/whitespace ('ADMIN BLOCK …' / 'first') still adopts the
    asset stored under 'Admin Block …' / 'FIRST '."""
    tag = _uid()
    first_row = _acms_row(
        block_name=f"Admin Block {tag}",
        floor_name="FIRST ",
        department_name=f"Civil {tag}",
        room_number=f"Room {tag}",
        unit_number="1",
        serial_number=f"S-{tag}",
    )
    tidy_row = dict(first_row, block_name=f" ADMIN BLOCK {tag} ", floor_name="first")
    first = _report(_post(client, [first_row]))
    ac_id = first["rows"][0]["ac_id"]
    try:
        assert ac_id.startswith("AC-LOC-")
        chain = _seed_chain(scratch, tag)

        second = _report(_post(client, [tidy_row]))
        assert second["created"] == 0 and second["updated"] == 1
        assert second["pending_identities_adopted"] == 1
        assert second["rows"][0]["ac_id"] == ac_id
        assert _ac(ac_id)["room_id"] == chain["room_id"]
    finally:
        _cleanup(ac_id)


def test_pending_adoption_serial_guard_refuses_a_mismatched_unit(client, scratch):
    """Conservative adoption: when the stored token carries a DIFFERENT
    serial than the incoming row (same unit slot), the token belongs to
    another physical unit — a fresh room-based asset is created and the old
    pending asset is left untouched for review, never overwritten."""
    tag = _uid()
    new_id = None
    pending_row = _acms_row(
        college_name=f"Nowhere College {tag}",  # per-tag: never resolves, so the
        # first import is deterministically PENDING regardless of ambient aliases
        block_name=f"Admin Block {tag}",
        department_name=f"Civil {tag}",
        room_number=f"Room {tag}",  # resolves only after the hierarchy arrives
        unit_number="7",
        serial_number=f"SERIAL-A-{tag}",
    )
    first = _report(_post(client, [pending_row]))
    token = first["rows"][0]["ac_id"]
    try:
        assert token.startswith("AC-LOC-")
        chain = _seed_chain(scratch, tag)

        changed_row = dict(pending_row, serial_number=f"SERIAL-B-{tag}")
        second = _report(_post(client, [changed_row]))
        assert second["created"] == 1 and second["updated"] == 0
        assert second["pending_identities_adopted"] == 0
        new_id = second["rows"][0]["ac_id"]
        assert new_id != token and new_id.startswith(f"AC-ROOM-{tag}")
        # The old pending asset is untouched: still PENDING, serial preserved.
        old = _ac(token)
        assert old["room_id"] is None and old["location_status"] == "PENDING"
        assert old["ac_serial_number"] == f"SERIAL-A-{tag}"
        # The new asset carries the room and the incoming serial.
        new = _ac(new_id)
        assert new["room_id"] == chain["room_id"] and new["location_status"] == "ASSIGNED"
        assert new["ac_serial_number"] == f"SERIAL-B-{tag}"
    finally:
        _cleanup(token, new_id)


# ─────────────────────────────────────────────────────────────────────────
# The re-resolve repair path (no re-import): stable identifiers, in place
# ─────────────────────────────────────────────────────────────────────────


def test_re_resolve_repairs_pending_assets_without_changing_identifiers(client, scratch):
    """The runbook path for assets already imported: once masters + alias
    exist, POST /campus/location/re-resolve finishes the row IN PLACE — the
    ac_id and asset_uuid are untouched and no new asset appears."""
    tag = _uid()
    row = _acms_row(
        college_name="NITTTRC",
        block_name=f"Admin Block {tag}",
        floor_name="FIRST ",
        department_name=f"Civil {tag}",
        room_number=f"Room {tag}",
        unit_number="1",
        serial_number=f"S-{tag}",
    )
    report = _report(_post(client, [row]))
    ac_id = report["rows"][0]["ac_id"]
    try:
        assert ac_id.startswith("AC-LOC-")
        uuid_before = _ac(ac_id)["asset_uuid"]

        chain = _seed_chain(scratch, tag)
        _ensure_nitttrc_alias()

        r = client.post(LOCATION + "/re-resolve", json={"limit": 2000})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["data"]["scanned"] >= 1

        acs_row = _ac(ac_id)
        assert acs_row["asset_uuid"] == uuid_before, "re-resolve must keep the stable identifier"
        assert acs_row["room_id"] == chain["room_id"]
        assert acs_row["location_status"] == "ASSIGNED"
        record = _loc(ac_id)
        assert record["block_id"] == chain["block_id"] and record["block_status"] == "RESOLVED"
        assert record["room_id"] == chain["room_id"] and record["room_status"] == "RESOLVED"
        assert record["college_id"] == COLLEGE_ID and record["college_status"] == "RESOLVED"
        # Idempotent: a second pass changes nothing.
        again = client.post(LOCATION + "/re-resolve", json={"limit": 2000})
        assert again.status_code == 200
        assert again.get_json()["data"]["updated"] == 0
    finally:
        _cleanup(ac_id)
