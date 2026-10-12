"""Phase 8.1 — campus CRUD coverage for ``app/api/v1/campus.py``.

That module was the largest untested file in the API when Phase 8 started
(598 statements, 23% covered): its block/floor/room/department lifecycle, the
409 delete guards, the layout validator and the photo endpoints were only
ever reached by the RBAC matrix, which stops at the permission check.

Every test builds its own throwaway rows through the ``scratch`` fixture,
which deletes them in reverse dependency order on teardown. The shared seeded
chain (MAIN-ACADEMIC-BLOCK / Floor 1 / ROOM-102 from ``_seed_shared_test_data``)
is therefore never mutated, and each guard is proved on data this test owns.

No profile row is created for the token subject here: ``audit()`` is
best-effort and swallows its own failures, and ``test_audit.py`` documents why
leaving a profile behind would flip the role-matrix tests.
"""

from __future__ import annotations

import io
import json
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import db_required  # noqa: E402

CAMPUS = "/api/v1/campus"
COLLEGE = "NITTTR-CHENNAI"  # seeded by migrations/003, referenced by the FK

pytestmark = db_required()

# endpoint collection -> (table, primary key column); a fixed set, never user
# input, which is what makes the teardown f-string safe.
_TABLES = {
    "blocks": ("blocks", "block_id"),
    "floors": ("floors", "floor_id"),
    "rooms": ("rooms", "room_id"),
    "departments": ("departments", "department_id"),
}


@pytest.fixture()
def admin(db_admin_client):
    """SUPER_ADMIN client — the only role that holds MANAGE_CAMPUS."""
    return db_admin_client


@pytest.fixture()
def scratch():
    """Track rows a test created so teardown can remove them child-first."""
    created: list[tuple[str, str, str]] = []

    def track(table: str, id_column: str, value: str):
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


def _uniq(prefix: str) -> str:
    """Unique name, so a leftover row from an earlier failed run cannot 409."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _create(admin, scratch, collection: str, payload: dict) -> dict:
    r = admin.post(f"{CAMPUS}/{collection}", json=payload)
    assert r.status_code == 201, r.get_json()
    data = r.get_json()["data"]
    table, id_column = _TABLES[collection]
    scratch(table, id_column, data[id_column])
    return data


def _code(r) -> str:
    return ((r.get_json() or {}).get("error") or {}).get("code", "<no error envelope>")


def _block(admin, scratch, name: str | None = None) -> dict:
    return _create(admin, scratch, "blocks", {"block_name": name or _uniq("P8 Block")})


def _floor(admin, scratch, block_id: str, name: str | None = None) -> dict:
    return _create(admin, scratch, "floors", {"block_id": block_id, "floor_name": name or _uniq("P8 Floor")})


def _stack(admin, scratch) -> tuple[dict, dict, dict]:
    """block -> floor -> room, all tracked for teardown."""
    block = _block(admin, scratch)
    floor = _floor(admin, scratch, block["block_id"])
    room = _create(
        admin,
        scratch,
        "rooms",
        {"floor_id": floor["floor_id"], "room_name": _uniq("P8 Room"), "room_type": "Lab"},
    )
    return block, floor, room


# ── blocks ──────────────────────────────────────────────────────────────────


def test_block_create_rejects_bad_input(admin, scratch):
    r = admin.post(f"{CAMPUS}/blocks", json={})
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = admin.post(f"{CAMPUS}/blocks", json={"block_name": ""})
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = admin.post(f"{CAMPUS}/blocks", json={"block_name": "x" * 201})
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    # Phase 8: blocks used to fall through to blocks_college_id_fkey and 500.
    r = admin.post(f"{CAMPUS}/blocks", json={"block_name": _uniq("P8"), "college_id": "NO-SUCH-COLLEGE"})
    assert r.status_code == 400, r.get_json()
    assert _code(r) == "INVALID_COLLEGE"


def test_block_create_patch_delete_happy_path(admin, scratch):
    name = _uniq("P8 Block")
    block = _block(admin, scratch, name)
    assert block["block_name"] == name
    assert block["block_id"]

    named = _create(
        admin, scratch, "blocks", {"block_name": _uniq("P8 College Block"), "college_id": COLLEGE}
    )
    assert named["college_id"] == COLLEGE

    renamed = _uniq("P8 Renamed")
    r = admin.patch(f"{CAMPUS}/blocks/{block['block_id']}", json={"block_name": renamed})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["block_name"] == renamed

    r = admin.delete(f"{CAMPUS}/blocks/{block['block_id']}")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["deleted"] is True

    r = admin.delete(f"{CAMPUS}/blocks/{block['block_id']}")
    assert r.status_code == 404 and _code(r) == "BLOCK_NOT_FOUND"


def test_block_duplicate_id_and_derived_name_collide(admin, scratch):
    block = _block(admin, scratch)

    r = admin.post(f"{CAMPUS}/blocks", json={"block_name": _uniq("P8"), "block_id": block["block_id"]})
    assert r.status_code == 409 and _code(r) == "DUPLICATE_ID"

    # No explicit id: the derived id BLOCK-<NAME> collides instead, which is
    # how "duplicate name" is actually enforced.
    duplicate_name = _uniq("P8 Same")
    _block(admin, scratch, duplicate_name)
    r = admin.post(f"{CAMPUS}/blocks", json={"block_name": duplicate_name})
    assert r.status_code == 409 and _code(r) == "DUPLICATE_ID"


def test_block_patch_validation(admin, scratch):
    block = _block(admin, scratch)

    r = admin.patch(f"{CAMPUS}/blocks/{block['block_id']}", json={})
    assert r.status_code == 400 and _code(r) == "NO_FIELDS"

    r = admin.patch(f"{CAMPUS}/blocks/{block['block_id']}", json={"block_name": ""})
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = admin.patch(f"{CAMPUS}/blocks/{block['block_id']}", json={"college_id": "NO-SUCH-COLLEGE"})
    assert r.status_code == 400 and _code(r) == "INVALID_COLLEGE"

    r = admin.patch(f"{CAMPUS}/blocks/{_uniq('MISSING')}", json={"block_name": "x"})
    assert r.status_code == 404 and _code(r) == "BLOCK_NOT_FOUND"


def test_block_delete_is_guarded_by_floors(admin, scratch):
    block = _block(admin, scratch)
    _floor(admin, scratch, block["block_id"])

    r = admin.delete(f"{CAMPUS}/blocks/{block['block_id']}")
    assert r.status_code == 409, r.get_json()
    assert _code(r) == "BLOCK_HAS_FLOORS"
    assert "floor(s)" in r.get_json()["error"]["message"]


# ── floors ──────────────────────────────────────────────────────────────────


def test_floor_create_validates_parent_and_bounds(admin, scratch):
    r = admin.post(f"{CAMPUS}/floors", json={"floor_name": _uniq("P8 Orphan"), "block_id": "NO-SUCH-BLOCK"})
    assert r.status_code == 400 and _code(r) == "INVALID_BLOCK"

    block = _block(admin, scratch)
    r = admin.post(f"{CAMPUS}/floors", json={"block_id": block["block_id"]})
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    for out_of_range in (999, -6):
        r = admin.post(
            f"{CAMPUS}/floors",
            json={
                "floor_name": _uniq("P8 Floor"),
                "block_id": block["block_id"],
                "floor_number": out_of_range,
            },
        )
        assert r.status_code == 400, out_of_range
        assert _code(r) == "VALIDATION_ERROR"

    floor = _create(
        admin,
        scratch,
        "floors",
        {"block_id": block["block_id"], "floor_name": _uniq("P8 Floor"), "floor_number": -5},
    )
    assert floor["floor_number"] == -5  # the documented lower bound is valid


def test_floor_patch_and_delete(admin, scratch):
    block = _block(admin, scratch)
    floor = _floor(admin, scratch, block["block_id"])

    r = admin.patch(f"{CAMPUS}/floors/{floor['floor_id']}", json={})
    assert r.status_code == 400 and _code(r) == "NO_FIELDS"

    r = admin.patch(f"{CAMPUS}/floors/{floor['floor_id']}", json={"block_id": "NO-SUCH-BLOCK"})
    assert r.status_code == 400 and _code(r) == "INVALID_BLOCK"

    r = admin.patch(f"{CAMPUS}/floors/{floor['floor_id']}", json={"floor_name": _uniq("P8 Renamed")})
    assert r.status_code == 200, r.get_json()

    r = admin.patch(f"{CAMPUS}/floors/{_uniq('MISSING')}", json={"floor_name": "x"})
    assert r.status_code == 404 and _code(r) == "FLOOR_NOT_FOUND"

    r = admin.delete(f"{CAMPUS}/floors/{_uniq('MISSING')}")
    assert r.status_code == 404 and _code(r) == "FLOOR_NOT_FOUND"

    r = admin.delete(f"{CAMPUS}/floors/{floor['floor_id']}")
    assert r.status_code == 200, r.get_json()


def test_floor_duplicate_name_on_same_block(admin, scratch):
    block = _block(admin, scratch)
    name = _uniq("P8 Floor")
    _floor(admin, scratch, block["block_id"], name)

    r = admin.post(f"{CAMPUS}/floors", json={"block_id": block["block_id"], "floor_name": name})
    assert r.status_code == 409 and _code(r) == "DUPLICATE_ID"


def test_floor_delete_is_guarded_by_rooms(admin, scratch):
    _block, floor, _room = _stack(admin, scratch)

    r = admin.delete(f"{CAMPUS}/floors/{floor['floor_id']}")
    assert r.status_code == 409, r.get_json()
    assert _code(r) == "FLOOR_HAS_ROOMS"


# ── rooms ───────────────────────────────────────────────────────────────────


def test_room_create_validates_parents(admin, scratch):
    r = admin.post(f"{CAMPUS}/rooms", json={"room_name": _uniq("P8 Room"), "floor_id": "NO-SUCH-FLOOR"})
    assert r.status_code == 400 and _code(r) == "INVALID_FLOOR"

    block, floor, _room = _stack(admin, scratch)

    r = admin.post(
        f"{CAMPUS}/rooms",
        json={
            "room_name": _uniq("P8 Room"),
            "floor_id": floor["floor_id"],
            "department_id": "NO-SUCH-DEPT",
        },
    )
    assert r.status_code == 400 and _code(r) == "INVALID_DEPARTMENT"

    r = admin.post(f"{CAMPUS}/rooms", json={"floor_id": floor["floor_id"]})
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"


def test_room_patch_validates_references(admin, scratch):
    block, floor, room = _stack(admin, scratch)

    r = admin.patch(f"{CAMPUS}/rooms/{room['room_id']}", json={})
    assert r.status_code == 400 and _code(r) == "NO_FIELDS"

    r = admin.patch(f"{CAMPUS}/rooms/{room['room_id']}", json={"floor_id": "NO-SUCH-FLOOR"})
    assert r.status_code == 400 and _code(r) == "INVALID_FLOOR"

    r = admin.patch(f"{CAMPUS}/rooms/{room['room_id']}", json={"department_id": "NO-SUCH-DEPT"})
    assert r.status_code == 400 and _code(r) == "INVALID_DEPARTMENT"

    r = admin.patch(f"{CAMPUS}/rooms/{room['room_id']}", json={"room_name": _uniq("P8 Renamed")})
    assert r.status_code == 200, r.get_json()

    r = admin.patch(f"{CAMPUS}/rooms/{_uniq('MISSING')}", json={"room_name": "x"})
    assert r.status_code == 404 and _code(r) == "ROOM_NOT_FOUND"


def test_room_duplicate_and_delete(admin, scratch):
    block, floor, room = _stack(admin, scratch)

    r = admin.post(f"{CAMPUS}/rooms", json={"floor_id": floor["floor_id"], "room_name": room["room_name"]})
    assert r.status_code == 409 and _code(r) == "DUPLICATE_ID"

    r = admin.delete(f"{CAMPUS}/rooms/{_uniq('MISSING')}")
    assert r.status_code == 404 and _code(r) == "ROOM_NOT_FOUND"

    r = admin.delete(f"{CAMPUS}/rooms/{room['room_id']}")
    assert r.status_code == 200, r.get_json()


def test_room_delete_is_guarded_by_acs(admin, scratch):
    """A room holding an AC must refuse deletion (ROOM_HAS_ASSETS)."""
    block, floor, room = _stack(admin, scratch)

    # Our own asset rather than an ambient one: a freshly migrated database
    # has no AC rows, and borrowing a shared row made this check a skip.
    ac_id = f"SCRATCH-AC-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, room_id) VALUES (%s, %s)", (ac_id, room["room_id"]))
        conn.commit()
    finally:
        conn.close()

    try:
        r = admin.delete(f"{CAMPUS}/rooms/{room['room_id']}")
        assert r.status_code == 409, r.get_json()
        assert _code(r) == "ROOM_HAS_ASSETS"
        assert "AC asset(s)" in r.get_json()["error"]["message"]
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            conn.commit()
        finally:
            conn.close()


# ── departments ─────────────────────────────────────────────────────────────


def test_department_crud(admin, scratch):
    r = admin.post(f"{CAMPUS}/departments", json={})
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = admin.post(f"{CAMPUS}/departments", json={"department_name": "x" * 201})
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    name = _uniq("P8 Dept")
    dept = _create(admin, scratch, "departments", {"department_name": name})
    assert dept["department_name"] == name

    r = admin.post(f"{CAMPUS}/departments", json={"department_name": name})
    assert r.status_code == 409 and _code(r) == "DUPLICATE_ID"

    r = admin.post(
        f"{CAMPUS}/departments", json={"department_id": dept["department_id"], "department_name": "x"}
    )
    assert r.status_code == 409 and _code(r) == "DUPLICATE_ID"

    # department_name is required on a patch (no NO_FIELDS branch exists here).
    r = admin.patch(f"{CAMPUS}/departments/{dept['department_id']}", json={})
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = admin.patch(
        f"{CAMPUS}/departments/{dept['department_id']}", json={"department_name": _uniq("P8 Renamed")}
    )
    assert r.status_code == 200, r.get_json()

    r = admin.patch(f"{CAMPUS}/departments/{_uniq('MISSING')}", json={"department_name": "x"})
    assert r.status_code == 404 and _code(r) == "DEPARTMENT_NOT_FOUND"

    r = admin.delete(f"{CAMPUS}/departments/{_uniq('MISSING')}")
    assert r.status_code == 404 and _code(r) == "DEPARTMENT_NOT_FOUND"

    r = admin.delete(f"{CAMPUS}/departments/{dept['department_id']}")
    assert r.status_code == 200, r.get_json()


def test_department_delete_is_guarded_by_rooms(admin, scratch):
    dept = _create(admin, scratch, "departments", {"department_name": _uniq("P8 Dept")})
    block = _block(admin, scratch)
    floor = _floor(admin, scratch, block["block_id"])
    _create(
        admin,
        scratch,
        "rooms",
        {
            "floor_id": floor["floor_id"],
            "room_name": _uniq("P8 Room"),
            "department_id": dept["department_id"],
        },
    )

    r = admin.delete(f"{CAMPUS}/departments/{dept['department_id']}")
    assert r.status_code == 409, r.get_json()
    assert _code(r) == "DEPARTMENT_HAS_ROOMS"
    assert "room(s) still reference" in r.get_json()["error"]["message"]


# ── layout ──────────────────────────────────────────────────────────────────


def _deep(levels: int) -> dict:
    root: dict = {}
    node = root
    for _ in range(levels):
        child: dict = {}
        node["n"] = child
        node = child
    return root


def test_block_layout_validation(admin, scratch):
    block = _block(admin, scratch)
    url = f"{CAMPUS}/blocks/{block['block_id']}/layout"

    r = admin.patch(url, json=None)
    assert r.status_code == 400 and _code(r) == "INVALID_LAYOUT"

    r = admin.patch(url, json=["not", "an", "object"])
    assert r.status_code == 400 and _code(r) == "INVALID_LAYOUT"

    r = admin.patch(url, json={"layout": _deep(20)})
    assert r.status_code == 400 and _code(r) == "INVALID_LAYOUT"
    assert "nesting" in r.get_json()["error"]["message"]

    r = admin.patch(url, json={"layout": {"blob": "x" * (256 * 1024 + 64)}})
    assert r.status_code == 400 and _code(r) == "INVALID_LAYOUT"
    assert "size limit" in r.get_json()["error"]["message"]

    r = admin.patch(f"{CAMPUS}/blocks/{_uniq('MISSING')}/layout", json={"layout": {"a": 1}})
    assert r.status_code == 404 and _code(r) == "BLOCK_NOT_FOUND"


def test_block_layout_round_trip(admin, scratch):
    block = _block(admin, scratch)
    url = f"{CAMPUS}/blocks/{block['block_id']}/layout"
    layout = {"version": 1, "rooms": [{"id": "ROOM-102", "x": 10, "y": 20}]}

    # The body is always unwrapped through ``body["layout"]`` when it is a
    # dict, so a bare layout object is rejected — only the wrapped form works.
    r = admin.patch(url, json=layout)
    assert r.status_code == 400 and _code(r) == "INVALID_LAYOUT"

    r = admin.patch(url, json={"layout": layout})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["layout"] == layout

    # ...and it survives a read back through the tree.
    tree = admin.get(f"{CAMPUS}/tree")
    assert tree.status_code == 200
    mine = [b for b in tree.get_json()["data"]["orphan_blocks"] if b["block_id"] == block["block_id"]]
    assert mine and mine[0]["layout"] == layout


# ── photos ──────────────────────────────────────────────────────────────────


def test_photo_upload_requires_storage(monkeypatch, admin, scratch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    block = _block(admin, scratch)

    r = admin.post(f"{CAMPUS}/blocks/{block['block_id']}/photos")
    assert r.status_code == 503, r.get_json()
    assert _code(r) == "PHOTO_STORAGE_NOT_CONFIGURED"


def test_photo_upload_validates_the_file(monkeypatch, admin, scratch):
    """Every 4xx above the storage call, which never happens in a test run."""
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-service-role-key-for-tests-only")
    block = _block(admin, scratch)
    url = f"{CAMPUS}/blocks/{block['block_id']}/photos"

    r = admin.post(url)
    assert r.status_code == 400 and _code(r) == "FILE_REQUIRED"

    # Werkzeug only treats a value as an upload when it wraps a file-like
    # object, hence BytesIO rather than raw bytes.
    r = admin.post(url, data={"file": (io.BytesIO(b"just some text"), "notes.txt")})
    assert r.status_code == 400 and _code(r) == "UNSUPPORTED_FILE_TYPE"

    r = admin.post(url, data={"file": (io.BytesIO(b""), "empty.png")})
    assert r.status_code == 400 and _code(r) == "FILE_EMPTY"

    oversized = io.BytesIO(b"\x89PNG" + b"x" * (5 * 1024 * 1024 + 1))
    r = admin.post(url, data={"file": (oversized, "big.png")})
    assert r.status_code == 413, r.get_json()
    assert _code(r) == "FILE_TOO_LARGE"

    r = admin.post(url, data={"file": (io.BytesIO(b"definitely not a png"), "fake.png")})
    assert r.status_code == 400 and _code(r) == "INVALID_FILE_CONTENT"


def test_photo_delete(admin, scratch):
    block = _block(admin, scratch)

    r = admin.delete(f"{CAMPUS}/blocks/{_uniq('MISSING')}/photos/{uuid.uuid4()}")
    assert r.status_code == 404 and _code(r) == "BLOCK_NOT_FOUND"

    r = admin.delete(f"{CAMPUS}/blocks/{block['block_id']}/photos/{uuid.uuid4()}")
    assert r.status_code == 404 and _code(r) == "PHOTO_NOT_FOUND"

    # Seed metadata directly: the entry deliberately omits "path" so the
    # best-effort object-storage cleanup is skipped (no network in tests).
    photo_id = str(uuid.uuid4())
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE blocks SET photos = %s WHERE block_id = %s",
                (json.dumps([{"id": photo_id, "name": "cover.png", "is_cover": True}]), block["block_id"]),
            )
        conn.commit()
    finally:
        conn.close()

    r = admin.delete(f"{CAMPUS}/blocks/{block['block_id']}/photos/{photo_id}")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["deleted"] is True

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT photos FROM blocks WHERE block_id = %s", (block["block_id"],))
            assert cur.fetchone()[0] == []
    finally:
        conn.close()


# ── tree ────────────────────────────────────────────────────────────────────


def test_campus_tree_nests_the_seeded_chain(admin):
    r = admin.get(f"{CAMPUS}/tree")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert set(data) == {"colleges", "orphan_blocks", "departments"}

    blocks = [b for college in data["colleges"] for b in college["blocks"]]
    main = next((b for b in blocks if b["block_id"] == "MAIN-ACADEMIC-BLOCK"), None)
    assert main is not None, "seeded block missing from its college"
    assert isinstance(main["photos"], list)

    rooms = [room for floor in main["floors"] for room in floor["rooms"]]
    assert {room["room_id"] for room in rooms} >= {"ROOM-102", "ROOM-105"}, "floors/rooms not nested"
    assert {d["department_id"] for d in data["departments"]} >= {
        "DEPT-COMPUTER-SCIENCE",
        "DEPT-ADMINISTRATION",
    }


def test_campus_tree_lists_orphans(admin, scratch):
    """A block with no college still surfaces so nothing disappears."""
    block = _block(admin, scratch)
    r = admin.get(f"{CAMPUS}/tree")
    assert r.status_code == 200
    orphans = {b["block_id"] for b in r.get_json()["data"]["orphan_blocks"]}
    assert block["block_id"] in orphans
