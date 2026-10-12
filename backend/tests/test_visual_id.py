"""Visual Asset ID unit tests — normalization, structure, suffix policy.

Pure ``app.services.visual_id`` calls (no database): every normalization
rule from the module docstring is pinned against dataset-realistic values
(parens, digits, hyphens, case variants from ACMS 301.xlsx), plus the
structural guarantees — deterministic, no fabricated segments, suffix
configurable, UUID never modified.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.services.visual_id import (  # noqa: E402
    _abbr_floor,
    _abbr_name,
    _abbr_room,
    visual_id_for_row,
)
from app.services.visual_id import (
    generate_visual_asset_id as generate,
)

UUID = "8e498917-1335-4550-b991-cecd0ca6c87c"  # suffix C87C / 0CA6C87C


def test_spec_example_shape():
    """The task's illustrative example, resolved through real names."""
    vid = generate(
        college="National Institute of Technical Teachers Training and Research",
        block="Maha Kavi Bharathiyar Block",
        floor="First Floor",
        department="ERD",
        room_name="Innovation Hub (Cabin 5)",
        asset_uuid=UUID,
    )
    assert vid == "NITTTR-MKB-1F-ERD-IH-C5-0CA6C87C"


def test_name_normalization_matrix():
    assert _abbr_name("Maha Kavi Bharathiyar Block") == "MKB"
    assert _abbr_name("Main Academic Block") == "MA"  # generic "Block" carries no identity
    assert _abbr_name("Admin block") == "ADMIN"
    assert _abbr_name("L block") == "L"
    assert _abbr_name("Resource Center") == "RES"
    assert _abbr_name("JAMBULINGAM GUEST HOUSE") == "JG"
    assert _abbr_name("Administration") == "ADM"
    assert _abbr_name("Seminar Hall L building") == "SL"
    assert _abbr_name("ARCA CENTER FOR INTELLIGENT COMPUTING") == "AIC"
    # Case variants of the same entity spell identically.
    assert _abbr_name("HOSTEL") == _abbr_name("Hostel") == _abbr_name("hostel") == "HOSTEL"
    assert _abbr_name("Demt") == _abbr_name("DEMT") == "DEMT"
    assert _abbr_name("Ground") == _abbr_name("GROUND") == "GROUND"
    # Short codes survive verbatim; digits survive.
    assert _abbr_name("ERD") == "ERD"
    assert _abbr_name("IHR") == "IHR"
    assert _abbr_name("S&P") == "SP"
    assert _abbr_name("Tgh 1") == "T1"
    assert _abbr_name("CE08") == "CE08"
    assert _abbr_name("210") == "210"


def test_floor_normalization():
    assert _abbr_floor("First Floor") == "1F"
    assert _abbr_floor("FIRST") == "1F"
    assert _abbr_floor("first") == "1F"
    assert _abbr_floor("Second") == "2F"
    assert _abbr_floor("Ground") == "GF"
    assert _abbr_floor("ground") == "GF"
    assert _abbr_floor("Floor 01") == "1F"
    assert _abbr_floor("MAIN-ACADEMIC-BLOCK-FLR-1") == "1F"  # id fallback degrades well


def test_room_normalization():
    assert _abbr_room("Innovation Hub (Cabin 5)") == "IH-C5"
    assert _abbr_room("Faculty Cabin(Rajasekhar Sir)") == "FC-RS"
    assert _abbr_room("CSE Computer Lab 1") == "CCL1"
    assert _abbr_room("lab-1") == "L1"
    assert _abbr_room("HOD Cabin") == "HC"
    assert _abbr_room("ADMIN OFFICE") == "AO"
    assert _abbr_room("GHOST-1") == "G1"


def test_partial_and_missing_levels_never_fabricate():
    only_dept = generate(department="Administration", asset_uuid=UUID)
    assert only_dept == "ADM-0CA6C87C"
    assert "NULL" not in only_dept and "--" not in only_dept

    no_room = generate(college="NITTTR-CHENNAI", block="B1", asset_uuid=UUID)
    assert no_room.split("-")[-1] == "0CA6C87C"  # suffix still anchors

    bare = generate(asset_uuid=UUID)
    assert bare == "AC-0CA6C87C"

    assert generate() == "AC-UNRESOLVED"
    assert generate(asset_uuid=None) == "AC-UNRESOLVED"

    # Blanks, placeholders and sentinels contribute nothing.
    for blank in (None, "", "   ", "—", "-", "NULL", "null", "N/A"):
        vid = generate(block=blank, department="ERD", asset_uuid=UUID)
        assert vid == "ERD-0CA6C87C", blank
        assert "NULL" not in vid and "UNDEFINED" not in vid and "--" not in vid


def test_suffix_policy():
    full = generate(block="B", asset_uuid=UUID)
    assert full.endswith("-0CA6C87C")  # default: last 8 hex, hyphens stripped
    assert generate(block="B", asset_uuid=UUID, suffix_len=4).endswith("-C87C")
    # Configurable via environment.
    import os

    os.environ["VISUAL_ID_SUFFIX_LEN"] = "6"
    try:
        assert generate(block="B", asset_uuid=UUID).endswith("-A6C87C")
    finally:
        del os.environ["VISUAL_ID_SUFFIX_LEN"]
    # Garbage config falls back to the default instead of crashing.
    os.environ["VISUAL_ID_SUFFIX_LEN"] = "huge"
    try:
        assert generate(block="B", asset_uuid=UUID).endswith("-0CA6C87C")
    finally:
        del os.environ["VISUAL_ID_SUFFIX_LEN"]


def test_deterministic_and_uuid_untouched():
    kwargs = dict(
        college="C",
        block="B",
        floor="1F",
        department="D",
        room_name="R",
        asset_uuid=UUID,
    )
    assert generate(**kwargs) == generate(**kwargs)
    changed_location = dict(kwargs, block="OTHER")
    assert generate(**changed_location) != generate(**kwargs)
    changed_uuid = dict(kwargs, asset_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    first, second = generate(**kwargs), generate(**changed_uuid)
    assert first != second
    assert first.split("-")[:-1] == second.split("-")[:-1]  # only the suffix moves
    assert kwargs["asset_uuid"] == UUID  # inputs never mutated


def test_visual_id_for_row_uses_effective_values():
    row = {
        "college_name": "NITTTR Chennai",
        "block_name": "Phantom Block",  # effective (unresolved source)
        "floor_name": None,
        "department_name": "Administration",  # canonical
        "canon_room_name": None,
        "room_type": "ADMIN OFFICE",  # effective (source type)
        "room_id": None,
        "source_room_number_value": None,
        "asset_uuid": UUID,
    }
    before = dict(row)
    assert visual_id_for_row(row) == "NC-PHA-ADM-AO-0CA6C87C"
    assert row == before  # the row is never mutated


def test_visual_id_for_row_matches_display_rule():
    """Canonical name wins for the room; otherwise the effective type."""
    base = {
        "college_name": None,
        "block_name": "B",
        "floor_name": None,
        "department_name": None,
        "room_id": None,
        "source_room_number_value": None,
        "asset_uuid": UUID,
    }
    named = dict(base, canon_room_name="CSE Computer Lab 1", room_type="Lab")
    typed = dict(base, canon_room_name=None, room_type="Lab")
    assert visual_id_for_row(named).split("-")[-2] == "CCL1"
    assert visual_id_for_row(typed).split("-")[-2] == "LAB"


# ─────────────────────────────────────────────────────────────────────────
# API wiring: list/detail/export carry the derived identifier
# ─────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def api_client(db_admin_client):
    return db_admin_client


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs a live database")
def test_visual_id_served_on_list_detail_and_export(api_client):
    import uuid as uuid_mod

    ac_id = f"VID-{uuid_mod.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id) VALUES (%s) RETURNING asset_uuid", (ac_id,))
            asset_uuid = str(cur.fetchone()[0])
        conn.commit()
    finally:
        conn.close()
    try:
        suffix = asset_uuid.replace("-", "")[-8:].upper()

        listed = api_client.get(f"/api/v1/assets?search={ac_id}&page_size=1")
        assert listed.status_code == 200, listed.get_json()
        item = listed.get_json()["data"][0]
        assert item["visual_id"] == f"AC-{suffix}"
        assert item["asset_uuid"] == asset_uuid  # technical identity untouched

        detail = api_client.get(f"/api/v1/assets/{ac_id}")
        assert detail.status_code == 200, detail.get_json()
        assert detail.get_json()["data"]["visual_id"] == f"AC-{suffix}"

        export = api_client.get("/api/v1/assets/export?format=csv")
        assert export.status_code == 200
        header = export.data.decode("utf-8-sig").splitlines()[0].split(",")
        assert header[-1] == "visual_id"
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            conn.commit()
        finally:
            conn.close()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs a live database")
def test_search_matches_visual_id_and_preserves_uuid_search(api_client):
    """A Visual-ID query finds the asset; plain text + UUID search survive.

    The derived id (``NC-MKB-1F-ERD-IH-C5-<suffix>``) matches NO single
    indexed field, so finding it proves the centralized expansion; the block
    name and full uuid checks prove the ordinary textual paths still work.
    """
    import uuid as uuid_mod

    tag = uuid_mod.uuid4().hex[:8]
    college_id, blk, flr, dept, room = (
        f"VIDS-COL-{tag}",
        f"VIDS-BLK-{tag}",
        f"VIDS-FLR-{tag}",
        f"VIDS-DEPT-{tag}",
        f"VIDS-ROOM-{tag}",
    )
    ac_id = f"VIDS-{tag}-A1"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO colleges (college_id, college_name) VALUES (%s, %s)",
                (college_id, "NITTTR Chennai"),
            )
            cur.execute(
                "INSERT INTO blocks (block_id, college_id, block_name) VALUES (%s, %s, %s)",
                (blk, college_id, "Maha Kavi Bharathiyar Block"),
            )
            cur.execute(
                "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)" " VALUES (%s, %s, %s, 1)",
                (flr, blk, "First Floor"),
            )
            cur.execute(
                "INSERT INTO departments (department_id, department_name) VALUES (%s, %s)",
                (dept, "ERD"),
            )
            cur.execute(
                "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
                " VALUES (%s, %s, %s, %s, %s)",
                (room, flr, dept, "Innovation Hub (Cabin 5)", "Cabin"),
            )
            cur.execute(
                "INSERT INTO acs (ac_id, room_id) VALUES (%s, %s) RETURNING asset_uuid",
                (ac_id, room),
            )
            asset_uuid = str(cur.fetchone()[0])
        conn.commit()
    finally:
        conn.close()

    try:
        listed = api_client.get(f"/api/v1/assets?search={ac_id}&page_size=5")
        assert listed.status_code == 200, listed.get_json()
        mine = [r for r in listed.get_json()["data"] if r["ac_id"] == ac_id]
        assert len(mine) == 1
        visual_id = mine[0]["visual_id"]
        suffix = asset_uuid.replace("-", "")[-8:].upper()
        assert visual_id == f"NC-MKB-1F-ERD-IH-C5-{suffix}"

        # Full Visual ID — matches no individual column; only the centralized
        # expansion can find it.
        found = api_client.get(f"/api/v1/assets?search={visual_id}&page_size=20")
        assert found.status_code == 200, found.get_json()
        assert ac_id in [r["ac_id"] for r in found.get_json()["data"]]

        # Partial Visual ID (college segment dropped) still resolves.
        partial = "-".join(visual_id.split("-")[1:])
        assert partial != visual_id
        found = api_client.get(f"/api/v1/assets?search={partial}&page_size=20")
        assert found.status_code == 200, found.get_json()
        assert ac_id in [r["ac_id"] for r in found.get_json()["data"]]

        # UUID search is preserved (technical identity, full value).
        found = api_client.get(f"/api/v1/assets?search={asset_uuid}&page_size=20")
        assert found.status_code == 200, found.get_json()
        assert ac_id in [r["ac_id"] for r in found.get_json()["data"]]

        # Ordinary textual search is untouched.
        found = api_client.get("/api/v1/assets?search=Maha Kavi Bharathiyar Block&page_size=20")
        assert found.status_code == 200, found.get_json()
        assert ac_id in [r["ac_id"] for r in found.get_json()["data"]]

        # Dashboard summary sees the same slice as the list (shared builder).
        summary = api_client.get(f"/api/v1/dashboard/summary?search={visual_id}")
        assert summary.status_code == 200, summary.get_json()
        assert summary.get_json()["data"]["kpis"]["total"] == 1
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
                cur.execute("DELETE FROM rooms WHERE room_id = %s", (room,))
                cur.execute("DELETE FROM departments WHERE department_id = %s", (dept,))
                cur.execute("DELETE FROM floors WHERE floor_id = %s", (flr,))
                cur.execute("DELETE FROM blocks WHERE block_id = %s", (blk,))
                cur.execute("DELETE FROM colleges WHERE college_id = %s", (college_id,))
            conn.commit()
        finally:
            conn.close()


def test_looks_like_visual_id_shape_gate():
    from app.api.v1.assets import _looks_like_visual_id

    assert _looks_like_visual_id("NC-MKB-1F-ERD-IH-C5-0CA6C87C")
    assert _looks_like_visual_id("MKB-1F-C87C")  # hex suffix required
    assert not _looks_like_visual_id("Allergy")  # plain word
    assert not _looks_like_visual_id("MKB-1F")  # suffix too short to be a uuid tail
    assert not _looks_like_visual_id("WORKING")  # no separator
    assert not _looks_like_visual_id("a; DROP TABLE acs--")
    assert not _looks_like_visual_id(None)
