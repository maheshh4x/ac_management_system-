"""ACMS bulk-import regression: room-aware pending identities (no silent merges).

Root cause under test: the pre-``-R-`` pending token
``AC-LOC-<block>-<dept>[-U/-S]`` ignored the source room, so two physical
units sharing block/department/unit but living in different rooms minted the
SAME identity — the later row was classified ``duplicate`` and merged into
the first, and the asset silently disappeared from the database.

The current token is ``AC-LOC-<block>-<dept>[-R-<room>][-U/-S]``. Rows with no
source room keep the historical shape byte-identical (existing tests pin
that). Re-imports bridge to stored legacy tokens via first-claim adoption
(same room identity merges; a different room identity mints its own asset;
a mismatched serial refuses) — existing ``ac_id``/``asset_uuid``/QR identity
and history are never rewritten.

Coverage: distinct rooms sharing a unit stay distinct; missing room records
stay pending and create no rooms; repeated/missing serials and units;
existing-asset updates; repeat-import idempotency; legacy adoption, split
and serial-guard refusal; preview==commit parity; additive report contract;
dry-run purity (rollback evidence).
"""

import csv
import io
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import RealDictCursor  # noqa: E402

from app.api.v1 import assets_import as ai  # noqa: E402
from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import db_required  # noqa: E402

pytestmark = db_required()

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

_TRACKED_TABLES = ("acs", "blocks", "floors", "rooms", "departments")


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture()
def scratch():
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


def _row(**over) -> dict:
    base = {
        "college_name": "Nowhere College",
        "block_name": "North Block",
        "floor_name": "",
        "department_name": "Physics",
        "room_type": "",
        "room_number": "",
        "ac_make_company": "Voltas",
        "unit_number": "",
        "serial_number": "",
    }
    base.update(over)
    return base


def _post(client, rows, dry_run=False):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ACMS_HEADER, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in ACMS_HEADER})
    suffix = "?dry_run=true" if dry_run else ""
    return client.post(
        f"/api/v1/assets/import{suffix}",
        data={"file": (io.BytesIO(buf.getvalue().encode("utf-8")), "identity.csv")},
    )


def _report(response, status=201):
    payload = response.get_json()
    assert response.status_code == status, payload
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


def _ac(ac_id: str):
    return _fetch_one("SELECT * FROM acs WHERE ac_id = %s", (ac_id,))


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


def _legacy_token(block: str, dept: str, unit: str = "", serial: str | None = None, room: str = "") -> str:
    """Legacy (pre-``-R-``) token via the importer's own builder."""
    return ai._legacy_identity(
        {"block_name": block, "department_name": dept, "unit_number": unit, "room_number": room},
        {"ac_serial_number": serial},
    )


def _insert_legacy(ac_id: str, serial: str | None) -> dict:
    """Store a pre-``-R-`` incident-style asset directly (the 243-record shape)."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO acs (ac_id, asset_code, ac_serial_number, location_status)"
                " VALUES (%s, %s, %s, 'PENDING') RETURNING ac_id, asset_uuid",
                (ac_id, ac_id, serial),
            )
            row = cur.fetchone()
        conn.commit()
        return dict(row)
    finally:
        conn.close()


def _ids(data) -> set:
    return {entry["ac_id"] for entry in data["rows"] if entry.get("ac_id")}


# ── the core defect: same unit, different rooms ─────────────────────────────


def test_same_unit_different_rooms_creates_distinct_assets(client):
    tag = _uid()
    rows = [
        _row(block_name=f"Block {tag}", room_number=f"Room A {tag}", unit_number="U1"),
        _row(block_name=f"Block {tag}", room_number=f"Room B {tag}", unit_number="U1"),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 2 and data["duplicates"] == 0 and data["failed"] == 0
        ids = sorted(_ids(data))
        assert len(ids) == 2
        assert all("-R-" in ac_id for ac_id in ids)
        first, second = data["rows"][0], data["rows"][1]
        assert first["action"] == "create" and second["action"] == "create"
        assert _ac(ids[0]) is not None and _ac(ids[1]) is not None
        assert _ac(ids[0])["asset_uuid"] != _ac(ids[1])["asset_uuid"]
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_import_creates_no_rooms(client):
    tag = _uid()
    before = _fetch_one("SELECT COUNT(*) AS n FROM rooms", ())["n"]
    rows = [_row(block_name=f"Block {tag}", room_number=f"Room {tag}", unit_number="U9")]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 1 and data["pending_location"] == 1
        after = _fetch_one("SELECT COUNT(*) AS n FROM rooms", ())["n"]
        assert after == before, "import must never invent room records"
    finally:
        _cleanup(*(_ids(data) if data else ()))


# ── serial / unit discriminator matrix ──────────────────────────────────────


def test_repeated_serials_different_units_stay_distinct(client):
    tag = _uid()
    rows = [
        _row(room_number=f"Room {tag}", unit_number="U1", serial_number=f"SAME-{tag}"),
        _row(room_number=f"Room {tag}", unit_number="U2", serial_number=f"SAME-{tag}"),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 2 and data["duplicates"] == 0, data["rows"]
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_missing_serials_same_identity_merge(client):
    tag = _uid()
    rows = [
        _row(room_number=f"Room {tag}", unit_number="U1", serial_number=""),
        _row(room_number=f"Room {tag}", unit_number="U1", serial_number=""),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 1 and data["duplicates"] == 1, data["rows"]
        assert len(data["duplicate_groups"]) == 1
        assert sorted(data["duplicate_groups"][0]["rows"]) == [2, 3]
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_missing_units_distinct_serials_split(client):
    tag = _uid()
    rows = [
        _row(room_number=f"Room {tag}", unit_number="", serial_number=f"S1-{tag}"),
        _row(room_number=f"Room {tag}", unit_number="", serial_number=f"S2-{tag}"),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 2 and data["duplicates"] == 0, data["rows"]
        slugs = {f"S1-{tag}".upper(), f"S2-{tag}".upper()}
        assert {ac_id for ac_id in _ids(data)} == {
            ac_id for ac_id in _ids(data) if any(slug in ac_id for slug in slugs)
        } and len(_ids(data)) == 2
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_missing_unit_and_serial_merge(client):
    tag = _uid()
    rows = [
        _row(room_number=f"Room {tag}", unit_number="", serial_number=""),
        _row(room_number=f"Room {tag}", unit_number="", serial_number=""),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 1 and data["duplicates"] == 1, data["rows"]
    finally:
        _cleanup(*(_ids(data) if data else ()))


# ── updates, idempotency, legacy bridge ─────────────────────────────────────


def test_repeat_import_is_idempotent_and_stable(client):
    tag = _uid()
    rows = [
        _row(room_number=f"Room A {tag}", unit_number="U1"),
        _row(room_number=f"Room B {tag}", unit_number="U1"),
        _row(room_number=f"Room A {tag}", unit_number="U1"),
    ]
    first = None
    try:
        first = _report(_post(client, rows))
        assert (first["created"], first["duplicates"]) == (2, 1)
        uuids = {
            entry["ac_id"]: _ac(entry["ac_id"])["asset_uuid"] for entry in first["rows"] if entry.get("ac_id")
        }
        second = _report(_post(client, rows))
        assert second["created"] == 0, second["rows"]
        assert second["updated"] == 2 and second["duplicates"] == 1
        for entry in second["rows"]:
            if entry.get("ac_id"):
                assert _ac(entry["ac_id"])["asset_uuid"] == uuids[entry["ac_id"]]
    finally:
        _cleanup(*(_ids(first) if first else ()))


def test_legacy_token_adopted_not_duplicated(client):
    block, dept, unit = "Nowhere College", "Physics", "U7"
    # NOTE: the row below carries a college (and no room/floor/type), so its
    # new token differs from the legacy shape exactly by the college segment
    # and takes the legacy-bridge path (first claim).
    legacy_id = _legacy_token(block, dept, unit=unit)
    stored = _insert_legacy(legacy_id, None)
    data = None
    try:
        rows = [_row(block_name=block, department_name=dept, unit_number=unit)]
        data = _report(_post(client, rows))
        assert data["created"] == 0 and data["updated"] == 1, data["rows"]
        assert data["pending_identities_adopted"] == 1
        assert data["legacy_identities_adopted"] == 1
        assert data["rows"][0]["ac_id"] == legacy_id
        assert _ac(legacy_id)["asset_uuid"] == str(stored["asset_uuid"])
        groups = [g for g in data["identity_collisions"] if g["legacy_token"] == legacy_id]
        assert len(groups) == 1 and groups[0]["adopted_by_row"] == 2
    finally:
        _cleanup(legacy_id, *(_ids(data) if data else ()))


def test_legacy_token_split_across_rooms(client):
    tag = _uid()
    block, dept, unit = f"Split Block {tag}", f"Split Dept {tag}", "U3"
    legacy_id = _legacy_token(block, dept, unit=unit)
    stored = _insert_legacy(legacy_id, None)
    data = None
    try:
        rows = [
            _row(block_name=block, department_name=dept, room_number=f"Room A {tag}", unit_number=unit),
            _row(block_name=block, department_name=dept, room_number=f"Room B {tag}", unit_number=unit),
        ]
        data = _report(_post(client, rows))
        assert data["updated"] == 1 and data["created"] == 1, data["rows"]
        assert data["legacy_identities_adopted"] == 1
        assert data["rows"][0]["ac_id"] == legacy_id
        assert _ac(legacy_id)["asset_uuid"] == str(stored["asset_uuid"])
        assert data["rows"][1]["ac_id"] != legacy_id
        assert "-R-" in data["rows"][1]["ac_id"]
        groups = [g for g in data["identity_collisions"] if g["legacy_token"] == legacy_id]
        assert len(groups) == 1
        assert groups[0]["stored"] is True
        assert groups[0]["adopted_by_row"] == 2
        outcomes = {c["row"]: c["outcome"] for c in groups[0]["claims"]}
        assert outcomes == {2: "adopted", 3: "split_new_asset"}
    finally:
        _cleanup(legacy_id, *(_ids(data) if data else ()))


def test_legacy_adoption_serial_guard_refuses(client):
    tag = _uid()
    block, dept, unit = f"Guard Block {tag}", f"Guard Dept {tag}", "U5"
    legacy_id = _legacy_token(block, dept, unit=unit)
    _insert_legacy(legacy_id, f"STORED-{tag}")
    data = None
    try:
        rows = [
            _row(
                block_name=block,
                department_name=dept,
                room_number=f"Room {tag}",
                unit_number=unit,
                serial_number=f"OTHER-{tag}",
            )
        ]
        data = _report(_post(client, rows))
        assert data["created"] == 1 and data["legacy_identities_adopted"] == 0, data["rows"]
        assert data["rows"][0]["ac_id"] != legacy_id
        assert _ac(legacy_id)["location_status"] == "PENDING"
        groups = [g for g in data["identity_collisions"] if g["legacy_token"] == legacy_id]
        assert len(groups) == 1 and groups[0]["adopted_by_row"] is None
        assert groups[0]["claims"][0]["outcome"] == "refused_serial"
    finally:
        _cleanup(legacy_id, *(_ids(data) if data else ()))


# ── contract, parity, purity ────────────────────────────────────────────────


def test_report_contract_additive(client):
    tag = _uid()
    rows = [
        _row(block_name=f"Block {tag}", room_number=f"Room {tag}", unit_number="U1"),
        _row(block_name=f"Block {tag}", room_number=f"Room {tag}", unit_number="U1"),
        _row(block_name=f"Block {tag}", department_name="", unit_number="U2"),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        for key in (
            "created",
            "updated",
            "duplicates",
            "invalid",
            "failed",
            "pending_location",
            "pending_identities_adopted",
            "rows",
            "errors",
            "warnings",
        ):
            assert key in data, key
        assert data["legacy_identities_adopted"] == 0
        assert isinstance(data["duplicate_groups"], list)
        assert isinstance(data["identity_collisions"], list)
        assert sum(len(g["rows"]) - 1 for g in data["duplicate_groups"]) == data["duplicates"]
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_preview_commit_parity_room_aware(client):
    tag = _uid()
    rows = [
        _row(block_name=f"Block {tag}", room_number=f"Room A {tag}", unit_number="U1"),
        _row(block_name=f"Block {tag}", room_number=f"Room B {tag}", unit_number="U1"),
        _row(block_name=f"Block {tag}", room_number=f"Room A {tag}", unit_number="U1"),
    ]
    data = None
    try:
        dry = _report(_post(client, rows, dry_run=True), status=200)
        live = _report(_post(client, rows))
        for key in ("imported", "created", "updated", "duplicates", "failed", "pending_location"):
            assert live[key] == dry[key], key
        assert (live["created"], live["duplicates"]) == (2, 1)
        data = live
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_dry_run_writes_nothing(client):
    tag = _uid()
    before = _fetch_one("SELECT COUNT(*) AS n FROM acs", ())["n"]
    rows = [_row(block_name=f"Block {tag}", room_number=f"Room {tag}", unit_number="U1")]
    dry = _report(_post(client, rows, dry_run=True), status=200)
    assert dry["created"] == 1
    after = _fetch_one("SELECT COUNT(*) AS n FROM acs", ())["n"]
    assert after == before


# ── canonical components (§6.1–§6.5): floor / type / dept / block / variants ─


def test_same_room_different_floors_split(client):
    tag = _uid()
    rows = [
        _row(block_name=f"Block {tag}", floor_name="Ground", room_number="1", unit_number="U1"),
        _row(block_name=f"Block {tag}", floor_name="First", room_number="1", unit_number="U1"),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 2 and data["duplicates"] == 0, data["rows"]
        ids = _ids(data)
        assert any("-F-GROUND-" in ac_id for ac_id in ids)
        assert any("-F-FIRST-" in ac_id for ac_id in ids)
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_same_room_different_types_split(client):
    tag = _uid()
    rows = [
        _row(block_name=f"Block {tag}", room_number="1", room_type="Office", unit_number="U1"),
        _row(block_name=f"Block {tag}", room_number="1", room_type="Cabin", unit_number="U1"),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 2 and data["duplicates"] == 0, data["rows"]
        ids = _ids(data)
        assert any("-T-OFFICE-" in ac_id for ac_id in ids)
        assert any("-T-CABIN-" in ac_id for ac_id in ids)
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_same_room_different_departments_and_blocks_split(client):
    tag = _uid()
    rows = [
        _row(block_name=f"Block A {tag}", department_name="Physics", room_number="1", unit_number="U1"),
        _row(block_name=f"Block A {tag}", department_name="Chemistry", room_number="1", unit_number="U1"),
        _row(block_name=f"Block B {tag}", department_name="Physics", room_number="1", unit_number="U1"),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 3 and data["duplicates"] == 0, data["rows"]
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_case_whitespace_variants_collapse_across_components(client):
    rows = [
        _row(
            college_name="  Nowhere College ",
            block_name="North Block",
            department_name="PHYSICS",
            floor_name="ground",
            room_type="OFFICE ",
            room_number=" 101 ",
            unit_number="U1",
        ),
        _row(
            college_name="nowhere college",
            block_name="north block",
            department_name=" physics ",
            floor_name="Ground",
            room_type="office",
            room_number="101",
            unit_number="u1",
        ),
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 1 and data["duplicates"] == 1, data["rows"]
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_multiple_units_same_room_no_serials(client):
    tag = _uid()
    rows = [_row(room_number=f"Room {tag}", unit_number=u) for u in ("U1", "U2", "U3")]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 3 and data["duplicates"] == 0, data["rows"]
    finally:
        _cleanup(*(_ids(data) if data else ()))


def test_byte_identical_shape_takes_plain_update_path(client):
    # No college/floor/type/room anywhere: new token == legacy shape, so a
    # stored row is a plain post-loop update (no bridge event needed).
    legacy_id = _legacy_token("Bare Block", "Bare Dept", unit="U4")
    stored = _insert_legacy(legacy_id, None)
    data = None
    try:
        rows = [
            _row(
                college_name="",
                block_name="Bare Block",
                floor_name="",
                department_name="Bare Dept",
                room_type="",
                room_number="",
                unit_number="U4",
            )
        ]
        data = _report(_post(client, rows))
        assert data["created"] == 0 and data["updated"] == 1, data["rows"]
        assert data["pending_identities_adopted"] == 0
        assert data["legacy_identities_adopted"] == 0
        assert data["rows"][0]["ac_id"] == legacy_id
        assert _ac(legacy_id)["asset_uuid"] == str(stored["asset_uuid"])
        assert data["identity_collisions"] == []
    finally:
        _cleanup(legacy_id, *(_ids(data) if data else ()))


# ── §6.9 + §4: history/maintenance survive re-imports ───────────────────────


def test_reimport_preserves_history_maintenance_and_code(client):
    tag = _uid()
    ac_id = f"NP-HIST-{tag}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO acs (ac_id, asset_code, ac_make_company, location_status)"
                " VALUES (%s, %s, 'Voltas', 'PENDING')",
                (ac_id, ac_id),
            )
            cur.execute(
                "INSERT INTO asset_location_history (ac_id, from_room_id, to_room_id)"
                " VALUES (%s, NULL, NULL)",
                (ac_id,),
            )
            cur.execute(
                "INSERT INTO maintenance (ac_id, maintenance_type, maintenance_date, remarks)"
                " VALUES (%s, 'Preventive', CURRENT_DATE, %s)",
                (ac_id, f"note {tag}"),
            )
        conn.commit()
    finally:
        conn.close()
    before_asset = _ac(ac_id)
    try:
        rows = [
            _row(
                block_name=f"Block {tag}",
                room_number=f"Room {tag}",
                unit_number="U1",
                ac_make_company="Voltas",
            )
        ]
        data = _report(_post(client, rows))
        assert data["created"] == 1
        # The hand-built asset is untouched: same uuid/code, history intact.
        assert _ac(ac_id)["asset_uuid"] == str(before_asset["asset_uuid"])
        assert _ac(ac_id)["asset_code"] == ac_id
        assert (
            _fetch_one("SELECT COUNT(*) AS n FROM asset_location_history WHERE ac_id = %s", (ac_id,))["n"]
            == 1
        )
        assert _fetch_one("SELECT COUNT(*) AS n FROM maintenance WHERE ac_id = %s", (ac_id,))["n"] == 1
    finally:
        _cleanup(ac_id, *(_ids(data) if data else ()))


def test_serial_correction_forks_explicitly_never_overwrites(client):
    tag = _uid()
    block, dept = f"Correct Block {tag}", f"Correct Dept {tag}"
    first = [
        _row(
            block_name=block,
            department_name=dept,
            room_number=f"Room {tag}",
            unit_number="U1",
            serial_number=f"OLD-{tag}",
        )
    ]
    data = None
    data = None
    first_ids: set = set()
    try:
        first_rep = _report(_post(client, first))
        original_id = first_rep["rows"][0]["ac_id"]
        first_ids = {original_id}
        original_uuid = _ac(original_id)["asset_uuid"]
        corrected = [
            _row(
                block_name=block,
                department_name=dept,
                room_number=f"Room {tag}",
                unit_number="U1",
                serial_number=f"NEW-{tag}",
            )
        ]
        second = _report(_post(client, corrected))
        assert second["created"] == 1, second["rows"]
        assert second["rows"][0]["ac_id"] != original_id
        # The original is untouched and still pending review — nothing was
        # silently overwritten or merged by the corrected serial.
        assert _ac(original_id)["asset_uuid"] == original_uuid
        assert _ac(original_id)["location_status"] == "PENDING"
        data = second
    finally:
        _cleanup(*first_ids, *(_ids(data) if data else ()))


# ── §6.11: concurrent same-file imports converge ────────────────────────────


def test_concurrent_same_file_imports_converge(client):
    import threading

    tag = _uid()
    rows = [
        _row(block_name=f"Race Block {tag}", room_number=f"Room A {tag}", unit_number="U1"),
        _row(block_name=f"Race Block {tag}", room_number=f"Room B {tag}", unit_number="U2"),
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ACMS_HEADER, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in ACMS_HEADER})
    payload = buf.getvalue().encode("utf-8")

    barrier = threading.Barrier(3, timeout=60)
    outcomes: list = [None] * 3

    def worker(index: int) -> None:
        try:
            barrier.wait()
            outcomes[index] = client.post(
                "/api/v1/assets/import",
                data={"file": (io.BytesIO(payload), "race.csv")},
            )
        except BaseException as exc:  # noqa: BLE001 - reported below, not swallowed
            outcomes[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not [o for o in outcomes if isinstance(o, BaseException)]
    assert all(o.status_code == 201 for o in outcomes), [o.status_code for o in outcomes]
    persisted: list = []
    try:
        conn = get_db_connection()
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT ac_id FROM acs WHERE ac_id LIKE %s", (f"%RACE-BLOCK-{tag.upper()}%",))
                persisted = sorted(r["ac_id"] for r in cur.fetchall())
        finally:
            conn.close()
        # The database — not the per-response tallies — decides: exactly the
        # two room-distinct assets exist, no duplicates from the race.
        assert len(persisted) == 2, persisted
        total_created = sum(o.get_json()["data"]["created"] for o in outcomes)
        assert total_created >= 2, "at least one worker must have created the assets"
    finally:
        _cleanup(*persisted)


def test_unresolved_room_stays_pending_with_hierarchy_present(client, scratch):
    tag = _uid()
    block_name, floor_name, dept_name = f"Hier Block {tag}", "Ground", f"Hier Dept {tag}"
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO blocks (block_id, college_id, block_name) VALUES (%s, %s, %s)",
                (f"IDT-BLK-{tag}", "NITTTR-CHENNAI", block_name),
            )
            scratch("blocks", "block_id", f"IDT-BLK-{tag}")
            cur.execute(
                "INSERT INTO floors (floor_id, block_id, floor_name, floor_number) VALUES (%s, %s, %s, 0)",
                (f"IDT-BLK-{tag}-FLR", f"IDT-BLK-{tag}", floor_name),
            )
            scratch("floors", "floor_id", f"IDT-BLK-{tag}-FLR")
            cur.execute(
                "INSERT INTO departments (department_id, department_name) VALUES (%s, %s)",
                (f"IDT-DEPT-{tag}", dept_name),
            )
            scratch("departments", "department_id", f"IDT-DEPT-{tag}")
        conn.commit()
    finally:
        conn.close()
    rows = [
        _row(
            college_name="NITTTR-CHENNAI",
            block_name=block_name,
            floor_name=floor_name,
            department_name=dept_name,
            room_number=f"Ghost Room {tag}",
            unit_number="U1",
        )
    ]
    data = None
    try:
        data = _report(_post(client, rows))
        assert data["created"] == 1 and data["pending_location"] == 1
        ac_id = data["rows"][0]["ac_id"]
        assert f"GHOST-ROOM-{tag}".upper().replace(" ", "-") in ac_id
        loc = _fetch_one("SELECT * FROM asset_locations WHERE ac_id = %s", (ac_id,))
        assert loc["room_id"] is None and loc["room_status"] in ("UNRESOLVED", "MISSING")
    finally:
        _cleanup(*(data["rows"][0]["ac_id"],) if data else ())


def test_invalid_rows_still_rejected(client):
    rows = [_row(department_name=""), _row(ac_make_company="")]
    data = _report(_post(client, rows))
    assert data["failed"] == 2 and data["imported"] == 0
    assert all(entry["action"] == "invalid" for entry in data["rows"])
