"""Resolver regression matrix — §23 A–I, V, W, X (plus §11 units and parity).

Pure ``location_resolver.resolve()`` calls against the shared seeded campus
(conftest: NITTTR-CHENNAI -> Main Academic Block -> Floor 1 ->
{ROOM-102 CSE Computer Lab 1 / Computer Science, ROOM-105 HOD Cabin /
Administration}), plus one real ``apply_many`` + ``sync_acs`` write cycle for
the write-path letters (V, W) — the exact single-writer contract the importer
and the re-resolve endpoint use.

Seeded fixtures create every master/alias they need (unique-tagged names, so
a leftover from an earlier run can never make a name match ambiguous) and are
removed child-first by the ``scratch`` teardown. Letters covered here:
A fully-resolved, B partial (room missing), C block-only, D department-only,
E missing, F ambiguous, G alias, H case/whitespace, I unknown,
J–L + M–U live in the import/api/migration suites, V no fake room,
W no duplicate masters, X no duplicate aliases.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import RealDictCursor  # noqa: E402

from app.config.database import get_db_connection  # noqa: E402
from app.routes import acs as acs_mod  # noqa: E402
from app.services import location_resolver  # noqa: E402
from app.services.location_resolver import (  # noqa: E402
    AMBIGUOUS,
    MISSING,
    RESOLVED,
    UNRESOLVED,
)
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

SOURCE_KEYS = ("college", "block", "floor", "department", "room_type", "room_number")

pytestmark = db_required()

# Tables a test may hand to the scratch teardown. Fixed literals in this
# file — never request input — which is what makes the f-strings safe.
_TRACKED_TABLES = ("acs", "asset_locations", "location_aliases", "departments", "blocks", "floors", "rooms")


@pytest.fixture()
def scratch():
    """Track rows a test created so teardown can remove them child-first."""
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


def _load_campus() -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            return location_resolver.load_directory(cur)
    finally:
        conn.close()


@pytest.fixture()
def campus(db):  # noqa: ARG001 - db seeds the shared campus and gates the skip
    return _load_campus()


def _uniq(prefix: str) -> str:
    """Unique label, so a leftover row from an earlier failed run cannot match."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _resolve(campus: dict, *, previous_ids=None, previous_manual=None, **source) -> object:
    """resolve() with every unspecified source key explicitly blank."""
    payload = {key: None for key in SOURCE_KEYS}
    payload.update({key: value for key, value in source.items()})
    return location_resolver.resolve(
        payload, campus, previous_ids=previous_ids, previous_manual=previous_manual
    )


def _fetch_one(sql: str, params: tuple):
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def _count(table: str) -> int:
    return _fetch_one(f"SELECT COUNT(*) AS n FROM {table}", ())["n"]


def _master_counts() -> dict:
    return {
        table: _count(table)
        for table in ("colleges", "blocks", "floors", "departments", "rooms", "location_aliases")
    }


def _write_resolution(scratch, res) -> str:
    """Run one resolution through the real single-writer contract."""
    ac_id = f"RESW-{uuid.uuid4().hex[:10].upper()}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, room_id) VALUES (%s, NULL)", (ac_id,))
            rows = [(ac_id, res)]
            location_resolver.apply_many(cur, rows, source_ref="resolution-test", resolved_by="test")
            location_resolver.sync_acs(cur, rows)
        conn.commit()
    finally:
        conn.close()
    # Track parent first: teardown runs reversed, deleting the record (child)
    # before the acs row it references.
    scratch("acs", "ac_id", ac_id)
    scratch("asset_locations", "ac_id", ac_id)
    return ac_id


# ─────────────────────────────────────────────────────────────────────────
# §23 A–E: resolution shapes on the shared campus
# ─────────────────────────────────────────────────────────────────────────


def test_a_fully_resolved_location_is_assigned(campus):
    res = _resolve(
        campus,
        college=COLLEGE_NAME,
        block=BLOCK_NAME,
        floor=FLOOR_NAME,
        department=DEPT_CS_NAME,
        room_type="Lab",
        room_number=ROOM_CSE_NAME,
    )
    assert res.ids == {
        "college": COLLEGE_ID,
        "block": BLOCK_ID,
        "floor": FLOOR_ID,
        "department": DEPT_CS,
        "room": ROOM_CSE,
    }
    assert res.statuses == {level: RESOLVED for level in location_resolver.LEVELS}
    assert res.overall == "ASSIGNED"
    assert res.notes == {}


def test_b_partial_location_room_missing_is_partial(campus):
    # §23 B: block + floor + department, room missing — every known level
    # survives, the room stays explicitly MISSING (never guessed).
    res = _resolve(campus, block=BLOCK_NAME, floor=FLOOR_NAME, department=DEPT_CS_NAME)
    assert res.ids["block"] == BLOCK_ID
    assert res.ids["floor"] == FLOOR_ID
    assert res.ids["department"] == DEPT_CS
    assert res.ids["room"] is None
    # college derives from the confirmed block chain (no college source).
    assert res.ids["college"] == COLLEGE_ID
    assert res.statuses["college"] == RESOLVED
    assert res.statuses["room"] == MISSING
    assert res.notes.get("room") == "room_number not provided"
    assert res.overall == "PARTIAL"


def test_c_block_only_resolves_block_and_derives_college(campus):
    res = _resolve(campus, block=BLOCK_NAME)
    assert res.ids["block"] == BLOCK_ID and res.statuses["block"] == RESOLVED
    assert res.ids["college"] == COLLEGE_ID and res.statuses["college"] == RESOLVED
    assert res.notes.get("college") == "derived from block chain"
    for level in ("floor", "department", "room"):
        assert res.ids[level] is None, level
        assert res.statuses[level] == MISSING, level
    # No level is ever invented from another level's business data.
    assert res.ids["room"] is None
    assert res.overall == "PARTIAL"


def test_d_department_only_resolves_department(campus):
    res = _resolve(campus, department=DEPT_ADM_NAME)
    assert res.ids["department"] == DEPT_ADM
    assert res.statuses["department"] == RESOLVED
    for level in ("college", "block", "floor", "room"):
        assert res.ids[level] is None, level
        assert res.statuses[level] == MISSING, level
    assert res.overall == "PARTIAL"


def test_e_missing_location_is_pending(campus):
    res = _resolve(campus)
    assert all(res.ids[level] is None for level in location_resolver.LEVELS)
    assert res.statuses == {level: MISSING for level in location_resolver.LEVELS}
    assert res.notes == {}
    assert res.overall == "PENDING"


# ─────────────────────────────────────────────────────────────────────────
# §23 F–I: ambiguous / alias / case-whitespace / unknown
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def twin_departments(scratch, db):  # noqa: ARG001 - db seeds the shared campus
    """Two master departments with the same name — ambiguity, deterministically."""
    name = _uniq("Twin Wing")
    ids = []
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for suffix in ("A", "B"):
                dept_id = _uniq(f"DEPT-TWIN-{suffix}")
                cur.execute(
                    "INSERT INTO departments (department_id, department_name) VALUES (%s, %s)",
                    (dept_id, name),
                )
                scratch("departments", "department_id", dept_id)
                ids.append(dept_id)
        conn.commit()
    finally:
        conn.close()
    return name, ids


def test_f_ambiguous_location_is_never_auto_assigned(twin_departments, campus):
    name, ids = twin_departments
    res = _resolve(campus, department=name)
    assert res.statuses["department"] == AMBIGUOUS
    assert res.ids["department"] is None  # never auto-assigned
    assert res.notes["department"] == f"multiple departments match '{name}'"
    assert res.overall == "PENDING"
    assert res.ids["department"] not in ids  # never one of the twins by fiat


@pytest.fixture()
def registered_alias(scratch, db):  # noqa: ARG001 - db seeds the shared campus
    """An active block alias in the registry (§6.1)."""
    alias_text = _uniq("Old Admin Wing")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)"
                " VALUES (%s, %s, %s, %s, 'manual') RETURNING id",
                ("block", BLOCK_ID, alias_text, location_resolver.normalize(alias_text)),
            )
            alias_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    scratch("location_aliases", "id", alias_id)
    return alias_text


def test_g_alias_match_resolves_block(registered_alias, campus):
    res = _resolve(campus, block=registered_alias)
    assert res.ids["block"] == BLOCK_ID
    assert res.statuses["block"] == RESOLVED
    # The confirmed block chain supplies the college (no college source).
    assert res.ids["college"] == COLLEGE_ID
    assert res.overall == "PARTIAL"


def test_h_case_and_whitespace_variants_collapse(campus):
    res = _resolve(
        campus,
        college=f"  {COLLEGE_NAME}  ",
        block="main-academic-block!",  # id spelling + punctuation
        floor="\tFLOOR  1\n",
        department="computer   science",
        room_type="LAB",
        room_number=" CSE Computer Lab 1 ",
    )
    assert res.ids == {
        "college": COLLEGE_ID,
        "block": BLOCK_ID,
        "floor": FLOOR_ID,
        "department": DEPT_CS,
        "room": ROOM_CSE,
    }
    assert res.statuses == {level: RESOLVED for level in location_resolver.LEVELS}
    assert res.overall == "ASSIGNED"


def test_i_unknown_location_resolves_nothing(campus):
    res = _resolve(
        campus,
        college="Zzyzx Institute",
        block="Nowhere Block 9",
        floor="Sublevel 7",
        department="Quantum Basketweaving",
        room_number="Room 9999",
    )
    assert all(res.ids[level] is None for level in location_resolver.LEVELS)
    assert res.statuses == {level: UNRESOLVED for level in location_resolver.LEVELS}
    assert res.notes["college"] == "no campus college matches 'Zzyzx Institute'"
    assert res.notes["block"] == "no campus block matches block_name 'Nowhere Block 9'"
    assert res.notes["floor"] == "floor 'Sublevel 7' cannot be resolved while block is unresolved"
    assert res.notes["department"] == "no campus department matches 'Quantum Basketweaving'"
    assert res.notes["room"] == "room 'Room 9999' cannot be resolved while block is unresolved"
    assert res.overall == "PENDING"


# ─────────────────────────────────────────────────────────────────────────
# §23 V + W: write path — no fake rooms, no duplicate masters
# ─────────────────────────────────────────────────────────────────────────


def test_v_unknown_room_never_creates_a_room(campus, scratch, db):  # noqa: ARG001
    rooms_before = _count("rooms")
    res = _resolve(
        campus,
        block=BLOCK_NAME,
        floor=FLOOR_NAME,
        department=DEPT_CS_NAME,
        room_number="Ghost Room 404",
    )
    assert res.ids["room"] is None
    assert res.statuses["room"] == UNRESOLVED
    assert res.notes["room"] == "no room matches 'Ghost Room 404'"
    assert res.overall == "PARTIAL"  # known levels survive the unknown room

    ac_id = _write_resolution(scratch, res)
    loc = _fetch_one(
        "SELECT room_id, room_status, block_status, resolution_notes FROM asset_locations WHERE ac_id = %s",
        (ac_id,),
    )
    assert loc["room_id"] is None and loc["room_status"] == UNRESOLVED
    assert loc["block_status"] == RESOLVED
    notes = loc["resolution_notes"]
    notes = notes if isinstance(notes, dict) else json.loads(notes)
    assert notes.get("room") == "no room matches 'Ghost Room 404'"
    # The acs mirror carries the PARTIAL verdict, room still NULL.
    assert _fetch_one("SELECT room_id, location_status FROM acs WHERE ac_id = %s", (ac_id,)) == {
        "room_id": None,
        "location_status": "PARTIAL",
    }
    assert _count("rooms") == rooms_before, "resolution must never fabricate a room"


def test_w_resolution_writes_never_touch_master_tables(campus, scratch, db):  # noqa: ARG001
    before = _master_counts()
    resolutions = [
        _resolve(
            campus,
            college=COLLEGE_NAME,
            block=BLOCK_NAME,
            floor=FLOOR_NAME,
            department=DEPT_CS_NAME,
            room_number=ROOM_CSE_NAME,
        ),
        _resolve(campus, block=BLOCK_NAME, department=DEPT_ADM_NAME),
        _resolve(campus, block="Nowhere Block 9", room_number="Ghost Room 404"),
    ]
    for res in resolutions:
        _write_resolution(scratch, res)
    assert _master_counts() == before, "writes may only touch acs + asset_locations"
    # ...while the two write tables did change (the cycle really ran).
    assert _count("acs") >= 3


# ─────────────────────────────────────────────────────────────────────────
# §23 X: no duplicate aliases (DB-enforced) + §11 units + delegate parity
# ─────────────────────────────────────────────────────────────────────────


def test_x_duplicate_aliases_are_rejected_by_the_database(scratch, db):  # noqa: ARG001
    alias_text = _uniq("Dup Alias")
    normalized = location_resolver.normalize(alias_text)
    conn = get_db_connection()
    alias_id = None
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)"
                " VALUES ('block', %s, %s, %s, 'manual') RETURNING id",
                (BLOCK_ID, alias_text, normalized),
            )
            alias_id = cur.fetchone()[0]
        conn.commit()
        scratch("location_aliases", "id", alias_id)
        # Same (type, normalized alias) against a DIFFERENT target: the
        # registry allows exactly one mapping, so ambiguity can never enter.
        with pytest.raises(psycopg2.errors.UniqueViolation):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)"
                    " VALUES ('block', 'SOME-OTHER-BLOCK', %s, %s, 'manual')",
                    (alias_text, normalized),
                )
    finally:
        try:
            conn.rollback()  # discard the aborted transaction (failed INSERT)
        finally:
            conn.close()
    assert (
        _fetch_one(
            "SELECT COUNT(*) AS n FROM location_aliases WHERE entity_type = 'block' AND normalized_alias = %s",
            (normalized,),
        )["n"]
        == 1
    )


def test_blank_source_never_downgrades_a_confirmed_location(campus):
    """§11: an empty cell keeps the previously confirmed chain (re-import safety)."""
    confirmed = {
        "college": COLLEGE_ID,
        "block": BLOCK_ID,
        "floor": FLOOR_ID,
        "department": DEPT_CS,
        "room": ROOM_CSE,
    }
    res = _resolve(campus, previous_ids=confirmed)
    assert res.ids == confirmed
    assert res.statuses == {level: RESOLVED for level in location_resolver.LEVELS}
    assert res.overall == "ASSIGNED"


def test_manual_room_pin_survives_unknown_source(campus):
    """§10/§11: a manual pin skips matching entirely — even active garbage."""
    res = _resolve(
        campus,
        room_number="Phantom Room",
        previous_ids={"room": ROOM_CSE},
        previous_manual=frozenset({"room"}),
    )
    assert res.ids["room"] == ROOM_CSE
    assert res.statuses["room"] == RESOLVED
    assert "manually confirmed room" in res.notes["room"]
    # The confirmed room chain still derives its non-manual parents.
    assert res.ids["floor"] == FLOOR_ID
    assert res.ids["block"] == BLOCK_ID
    assert res.ids["college"] == COLLEGE_ID
    assert res.overall == "ASSIGNED"


def test_legacy_delegate_is_byte_identical_to_the_central_resolver(campus):
    """§6: routes.acs._resolve_room_best_effort delegates — same room, same note."""
    lookups = [
        {
            "college_name": COLLEGE_NAME,
            "block_name": BLOCK_NAME,
            "floor_name": FLOOR_NAME,
            "department_name": DEPT_CS_NAME,
            "room_type": "Lab",
            "room_number": ROOM_CSE_NAME,
        },
        {  # unknown room on a known chain
            "college_name": "",
            "block_name": BLOCK_NAME,
            "floor_name": FLOOR_NAME,
            "department_name": DEPT_CS_NAME,
            "room_type": "",
            "room_number": "Phantom Hall",
        },
        {  # room_number blank
            "college_name": "",
            "block_name": BLOCK_NAME,
            "floor_name": "",
            "department_name": DEPT_ADM_NAME,
            "room_type": "",
            "room_number": "",
        },
        {  # unknown block
            "college_name": "",
            "block_name": "Nowhere Block 9",
            "floor_name": "",
            "department_name": DEPT_ADM_NAME,
            "room_type": "",
            "room_number": "Somewhere",
        },
    ]
    for lookup in lookups:
        # Same source mapping the delegate builds internally (§6 delegation).
        source = {
            "college": acs_mod._empty_to_none(lookup.get("college_name")),
            "block": acs_mod._empty_to_none(lookup.get("block_name")),
            "floor": acs_mod._empty_to_none(lookup.get("floor_name")),
            "department": acs_mod._empty_to_none(lookup.get("department_name")),
            "room_type": acs_mod._empty_to_none(lookup.get("room_type")),
            "room_number": acs_mod._empty_to_none(lookup.get("room_number")),
        }
        res = location_resolver.resolve(source, campus)
        room_id, note = acs_mod._resolve_room_best_effort(lookup, campus)
        assert room_id == res.ids["room"], lookup
        assert note == location_resolver.room_failure_note(source, res), lookup
    # The legacy normalizer is a delegation, not a second implementation.
    sample = " Main-Academic! Block "
    assert acs_mod._match_key(sample) == location_resolver.normalize(sample)


# ─────────────────────────────────────────────────────────────────────────
# Explicit canonical source ids (D-ID priority: id > alias > name)
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def second_chain(scratch, db):  # noqa: ARG001 - db seeds the shared campus
    """A second canonical block/floor/room chain in the same college."""
    block_id, floor_id, room_id = _uniq("BLOCK-2"), _uniq("FLR-2"), _uniq("ROOM-2")
    room_name = _uniq("Second Lab")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO blocks (block_id, college_id, block_name) VALUES (%s, %s, %s)",
                (block_id, COLLEGE_ID, _uniq("Second Block")),
            )
            scratch("blocks", "block_id", block_id)
            cur.execute(
                "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)" " VALUES (%s, %s, %s, 2)",
                (floor_id, block_id, _uniq("Second Floor")),
            )
            scratch("floors", "floor_id", floor_id)
            cur.execute(
                "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
                " VALUES (%s, %s, NULL, %s, 'Lab')",
                (room_id, floor_id, room_name),
            )
            scratch("rooms", "room_id", room_id)
        conn.commit()
    finally:
        conn.close()
    return block_id, floor_id, room_id, room_name


@pytest.fixture()
def twin_rooms(scratch, db):  # noqa: ARG001 - db seeds the shared campus
    """Two rooms with the same name on different floors — room ambiguity."""
    name = _uniq("Twin Lab")
    floor2 = _uniq("FLR-TWIN")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)" " VALUES (%s, %s, %s, 9)",
                (floor2, BLOCK_ID, _uniq("Twin Floor")),
            )
            scratch("floors", "floor_id", floor2)
            for floor_id in (FLOOR_ID, floor2):
                room_id = _uniq("ROOM-TWIN")
                cur.execute(
                    "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
                    " VALUES (%s, %s, NULL, %s, 'Lab')",
                    (room_id, floor_id, name),
                )
                scratch("rooms", "room_id", room_id)
        conn.commit()
    finally:
        conn.close()
    return name


def test_exact_block_id_resolves_without_names(campus):
    res = _resolve(campus, block_id=BLOCK_ID)
    assert res.ids["block"] == BLOCK_ID and res.statuses["block"] == RESOLVED
    assert res.ids["college"] == COLLEGE_ID  # derived from the block chain
    assert res.overall == "PARTIAL"


def test_exact_floor_id_resolves_and_derives_block(campus):
    res = _resolve(campus, floor_id=FLOOR_ID)
    assert res.ids["floor"] == FLOOR_ID and res.statuses["floor"] == RESOLVED
    assert res.ids["block"] == BLOCK_ID
    assert res.ids["college"] == COLLEGE_ID
    assert res.overall == "PARTIAL"


def test_exact_room_id_resolves_full_chain(campus):
    res = _resolve(campus, room_id=ROOM_CSE)
    assert res.ids["room"] == ROOM_CSE and res.statuses["room"] == RESOLVED
    assert res.ids["floor"] == FLOOR_ID
    assert res.ids["block"] == BLOCK_ID
    assert res.ids["department"] == DEPT_CS
    assert res.overall == "ASSIGNED"


def test_invalid_block_id_falls_back_to_name(campus):
    """D-ID-2: an id that names nothing resolves via the name instead."""
    res = _resolve(campus, block="Main Academic Block", block_id="BLOCK-NOPE")
    assert res.ids["block"] == BLOCK_ID and res.statuses["block"] == RESOLVED
    assert "matches no campus block" in res.notes["block_id"]


def test_id_and_name_disagreement_prefers_id_with_note(second_chain, campus):
    """D-ID-1: the canonical id is authoritative; the disagreement is noted."""
    block2, _floor2, _room2, _name2 = second_chain
    res = _resolve(campus, block="Main Academic Block", block_id=block2)
    assert res.ids["block"] == block2 and res.statuses["block"] == RESOLVED
    assert "disagrees with" in res.notes["block"]
    assert "canonical id applied" in res.notes["block"]


def test_explicit_id_conflicting_with_manual_scope_requires_review(second_chain, campus):
    """D-ID-3: a manual ancestor stands its ground; the child goes to review."""
    _block2, floor2, _room2, _name2 = second_chain
    prev = {"college": None, "block": BLOCK_ID, "floor": None, "department": None, "room": None}
    res = _resolve(campus, floor_id=floor2, previous_ids=prev, previous_manual={"block"})
    assert res.ids["floor"] is None and res.statuses["floor"] == UNRESOLVED
    assert "manually confirmed" in res.notes["floor"]
    assert res.ids["block"] == BLOCK_ID  # the pin itself is untouched


def test_explicit_child_clears_contradicted_name_matched_parent(second_chain, campus):
    """D-ID-4: the explicit id wins; the contradicted scope stays for review."""
    _block2, floor2, _room2, _name2 = second_chain
    res = _resolve(campus, block="Main Academic Block", floor_id=floor2)
    assert res.ids["floor"] == floor2 and res.statuses["floor"] == RESOLVED
    assert res.ids["block"] is None and res.statuses["block"] == UNRESOLVED
    assert "contradicted by explicit floor_id" in res.notes["block"]
    assert res.overall == "PARTIAL"


def test_valid_explicit_id_replaces_kept_previous(second_chain, campus):
    """A later valid match replaces a non-manual previous value (§4)."""
    _block2, _floor2, room2, name2 = second_chain
    prev = {
        "college": COLLEGE_ID,
        "block": BLOCK_ID,
        "floor": FLOOR_ID,
        "department": DEPT_CS,
        "room": ROOM_CSE,
    }
    res = _resolve(campus, room_id=room2, previous_ids=prev)
    assert res.ids["room"] == room2 and res.statuses["room"] == RESOLVED
    assert "room" not in res.kept
    assert res.overall == "ASSIGNED"


# ─────────────────────────────────────────────────────────────────────────
# Independent levels (§16 matrix rows)
# ─────────────────────────────────────────────────────────────────────────


def test_unresolved_block_with_resolved_department(campus):
    res = _resolve(campus, block="Nowhere Block 9", department="Administration")
    assert res.ids["block"] is None and res.statuses["block"] == UNRESOLVED
    assert res.ids["department"] == DEPT_ADM and res.statuses["department"] == RESOLVED
    assert res.overall == "PARTIAL"


def test_resolved_block_with_unresolved_floor(campus):
    res = _resolve(campus, block="Main Academic Block", floor="Attic 9")
    assert res.ids["block"] == BLOCK_ID and res.statuses["block"] == RESOLVED
    assert res.ids["floor"] is None and res.statuses["floor"] == UNRESOLVED
    assert res.overall == "PARTIAL"


def test_resolved_chain_with_unresolved_room(campus):
    res = _resolve(
        campus,
        block="Main Academic Block",
        floor="Floor 1",
        department="Computer Science",
        room_number="Ghost 9",
    )
    assert res.ids["block"] == BLOCK_ID
    assert res.ids["floor"] == FLOOR_ID
    assert res.ids["department"] == DEPT_CS
    assert res.ids["room"] is None and res.statuses["room"] == UNRESOLVED
    assert res.overall == "PARTIAL"


def test_ambiguous_room_is_never_auto_assigned(twin_rooms, campus):
    res = _resolve(campus, room_number=twin_rooms)
    assert res.statuses["room"] == AMBIGUOUS
    assert res.ids["room"] is None  # never auto-assigned
    assert "ambiguous" in res.notes["room"]
    assert res.overall == "PENDING"


# ─────────────────────────────────────────────────────────────────────────
# Re-import safety (§4): failed sources never downgrade confirmed values
# ─────────────────────────────────────────────────────────────────────────


def _prev_full_room() -> dict:
    return {
        "college": COLLEGE_ID,
        "block": BLOCK_ID,
        "floor": FLOOR_ID,
        "department": DEPT_CS,
        "room": ROOM_CSE,
    }


def test_blank_reimport_keeps_confirmed_room(campus):
    res = _resolve(campus, previous_ids=_prev_full_room())
    assert res.ids["room"] == ROOM_CSE and res.statuses["room"] == RESOLVED
    assert res.overall == "ASSIGNED"


def test_unresolved_reimport_keeps_confirmed_room(campus):
    """Nonblank garbage must not erase a confirmed non-manual room (§4)."""
    res = _resolve(campus, room_number="Ghost 9", previous_ids=_prev_full_room())
    assert res.ids["room"] == ROOM_CSE and res.statuses["room"] == RESOLVED
    assert "room" in res.kept
    assert "kept previously confirmed room" in res.notes["room"]
    assert res.overall == "ASSIGNED"


def test_ambiguous_reimport_keeps_confirmed_room(twin_rooms, campus):
    res = _resolve(campus, room_number=twin_rooms, previous_ids=_prev_full_room())
    assert res.ids["room"] == ROOM_CSE and res.statuses["room"] == RESOLVED
    assert "room" in res.kept
    assert res.overall == "ASSIGNED"


def test_valid_reimport_replaces_non_manual_previous(second_chain, campus):
    _block2, _floor2, _room2, name2 = second_chain
    res = _resolve(campus, room_number=name2, previous_ids=_prev_full_room())
    assert res.ids["room"] != ROOM_CSE and res.statuses["room"] == RESOLVED
    assert "room" not in res.kept
    assert res.overall == "ASSIGNED"


def test_manual_pin_survives_garbage_source_at_resolver(campus):
    res = _resolve(
        campus,
        room_number="Ghost 9",
        previous_ids=_prev_full_room(),
        previous_manual={"room"},
    )
    assert res.ids["room"] == ROOM_CSE and res.statuses["room"] == RESOLVED
    assert "manually confirmed room" in res.notes["room"]
    assert "room" not in res.kept  # manual wins outright; nothing "kept"


def test_explicit_manual_clear_lets_source_resolve_again(second_chain, campus):
    """Unpinning (manual set drops the level) re-opens it to matching."""
    _block2, _floor2, _room2, name2 = second_chain
    res = _resolve(campus, room_number=name2, previous_ids=_prev_full_room(), previous_manual=set())
    assert res.ids["room"] != ROOM_CSE and res.statuses["room"] == RESOLVED
    assert res.overall == "ASSIGNED"
