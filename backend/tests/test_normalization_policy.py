"""Normalization policy regression tests (Option A, D1-D8).

Comparison-layer normalization: stored display text is never rewritten;
every name comparison folds through one shared key. These tests pin:

- match_key parity (Python matcher == SQL fold for ASCII input),
- campus CRUD trims + rejects whitespace-only names (D5),
- scoped duplicate checks on rename/move (D5) with cross-parent freedom,
- college-scoped block derived ids (D4),
- LIKE-wildcard escaping on name filters/search (D6),
- import preview == commit classification parity,
- manufacturer fold at read/group (D2) with stored spellings untouched,
- free-text byte-identity (remarks/model/serial never normalized).

Every test owns tagged rows and removes them on teardown; the shared seeded
chain and the 243-row incident replica are never touched.
"""

from __future__ import annotations

import io
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.services import location_resolver, location_sql  # noqa: E402
from tests.conftest import db_required  # noqa: E402

CAMPUS = "/api/v1/campus"
COLLEGE = "NITTTR-CHENNAI"  # seeded by migrations/003, referenced by the FK

pytestmark = db_required()


@pytest.fixture()
def admin(db_admin_client):
    """SUPER_ADMIN client — holds MANAGE_CAMPUS and import permission."""
    return db_admin_client


@pytest.fixture()
def scratch():
    """Track rows a test created so teardown removes them child-first."""
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
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _code(r) -> str:
    return ((r.get_json() or {}).get("error") or {}).get("code", "<no error envelope>")


def _create(admin, scratch, collection: str, payload: dict) -> dict:
    r = admin.post(f"{CAMPUS}/{collection}", json=payload)
    assert r.status_code == 201, r.get_json()
    data = r.get_json()["data"]
    table = {"blocks": "blocks", "floors": "floors", "rooms": "rooms", "departments": "departments"}[
        collection
    ]
    scratch(table, f"{table[:-1]}_id", data[f"{table[:-1]}_id"])
    return data


def _block(admin, scratch, name=None, college=None) -> dict:
    payload = {"block_name": name or _uniq("NP Block")}
    if college is not None:
        payload["college_id"] = college
    return _create(admin, scratch, "blocks", payload)


def _floor(admin, scratch, block_id, name=None) -> dict:
    return _create(admin, scratch, "floors", {"block_id": block_id, "floor_name": name or _uniq("NP Floor")})


def _room(admin, scratch, floor_id, name=None) -> dict:
    return _create(admin, scratch, "rooms", {"floor_id": floor_id, "room_name": name or _uniq("NP Room")})


def _dept(admin, scratch, name=None) -> dict:
    return _create(admin, scratch, "departments", {"department_name": name or _uniq("NP Dept")})


# ── D1: one shared key ──────────────────────────────────────────────────────


def test_match_key_folds_case_whitespace_punctuation():
    assert location_resolver.match_key("  NITTTR-Chennai ") == "nitttrchennai"
    assert location_resolver.match_key("ADMIN  BLOCK!!") == location_resolver.match_key("admin-block")
    assert location_resolver.match_key(None) == ""
    # match_key is normalize, byte-identical.
    assert location_resolver.match_key(" Voltas\t") == location_resolver.normalize("voltas")


def test_sql_fold_matches_match_key_on_ascii(admin):
    samples = ["  Admin Block ", "ADMIN-BLOCK!!", "First", "FIRST", "Blue star", "BLUE STAR", "Tgh 1"]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for sample in samples:
                cur.execute(
                    f"SELECT {location_sql.fold_sql('%s')}",
                    (sample,),
                )
                assert cur.fetchone()[0] == location_resolver.match_key(sample), sample
    finally:
        conn.close()


def test_escape_like_neutralizes_wildcards():
    assert location_sql.escape_like("100%") == "100\\%"
    assert location_sql.escape_like("a_b\\c") == "a\\_b\\\\c"
    assert location_sql.escape_like("plain") == "plain"


# ── D5: trim + blank rejection ──────────────────────────────────────────────


def test_create_trims_names(admin, scratch):
    block = _block(admin, scratch, name="  Padded NP Block  ")
    assert block["block_name"] == "Padded NP Block"
    assert block["block_id"] == "BLOCK-PADDED-NP-BLOCK"


@pytest.mark.parametrize(
    "collection,payload",
    [
        ("blocks", {"block_name": "   "}),
        ("departments", {"department_name": "\t "}),
    ],
)
def test_create_blank_name_rejected(admin, collection, payload):
    r = admin.post(f"{CAMPUS}/{collection}", json=payload)
    assert r.status_code == 400 and _code(r) == "BLANK_NAME", r.get_json()


def test_create_blank_child_names_rejected(admin, scratch):
    block = _block(admin, scratch)
    r = admin.post(f"{CAMPUS}/floors", json={"block_id": block["block_id"], "floor_name": "  "})
    assert r.status_code == 400 and _code(r) == "BLANK_NAME"
    floor = _floor(admin, scratch, block["block_id"])
    r = admin.post(f"{CAMPUS}/rooms", json={"floor_id": floor["floor_id"], "room_name": "  "})
    assert r.status_code == 400 and _code(r) == "BLANK_NAME"


# ── D5: scoped rename/move guards ───────────────────────────────────────────


def test_patch_same_parent_duplicate_rejected(admin, scratch):
    block = _block(admin, scratch)
    keep = _floor(admin, scratch, block["block_id"], name=_uniq("NP Keep"))
    other = _floor(admin, scratch, block["block_id"], name=_uniq("NP Other"))
    r = admin.patch(f"{CAMPUS}/floors/{other['floor_id']}", json={"floor_name": keep["floor_name"].upper()})
    assert r.status_code == 409 and _code(r) == "DUPLICATE_NAME", r.get_json()
    # Whitespace-only rename is a 400, not a silent no-op.
    r = admin.patch(f"{CAMPUS}/floors/{other['floor_id']}", json={"floor_name": "   "})
    assert r.status_code == 400 and _code(r) == "BLANK_NAME"
    # Case-only self rename (same row) is allowed.
    r = admin.patch(f"{CAMPUS}/floors/{keep['floor_id']}", json={"floor_name": keep["floor_name"].swapcase()})
    assert r.status_code == 200, r.get_json()


def test_patch_room_and_department_duplicates_rejected(admin, scratch):
    block = _block(admin, scratch)
    floor = _floor(admin, scratch, block["block_id"])
    keep = _room(admin, scratch, floor["floor_id"], name=_uniq("NP KeepRoom"))
    other = _room(admin, scratch, floor["floor_id"], name=_uniq("NP OtherRoom"))
    r = admin.patch(f"{CAMPUS}/rooms/{other['room_id']}", json={"room_name": f" {keep['room_name']} "})
    assert r.status_code == 409 and _code(r) == "DUPLICATE_NAME", r.get_json()

    dept_keep = _dept(admin, scratch)
    dept_other = _dept(admin, scratch)
    r = admin.patch(
        f"{CAMPUS}/departments/{dept_other['department_id']}",
        json={"department_name": dept_keep["department_name"].lower()},
    )
    assert r.status_code == 409 and _code(r) == "DUPLICATE_NAME", r.get_json()


def test_cross_parent_duplicates_stay_legal(admin, scratch):
    first = _block(admin, scratch)
    second = _block(admin, scratch)
    shared_floor = _uniq("NP Shared Floor")
    _floor(admin, scratch, first["block_id"], name=shared_floor)
    again = _floor(admin, scratch, second["block_id"], name=shared_floor.lower())
    assert again["floor_id"] != shared_floor  # both created, distinct ids


def test_move_floor_to_occupied_block_rejected(admin, scratch):
    first = _block(admin, scratch)
    second = _block(admin, scratch)
    name = _uniq("NP Moved Floor")
    _floor(admin, scratch, first["block_id"], name=name)
    mover = _floor(admin, scratch, second["block_id"], name=_uniq("NP Mover"))
    r = admin.patch(
        f"{CAMPUS}/floors/{mover['floor_id']}", json={"block_id": first["block_id"], "floor_name": name}
    )
    assert r.status_code == 409 and _code(r) == "DUPLICATE_NAME", r.get_json()


# ── D4: college-scoped block ids ────────────────────────────────────────────


def test_block_derived_id_college_scoped(admin, scratch):
    college_id = f"NP-COL-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO colleges (college_id, college_name) VALUES (%s, %s)",
                (college_id, f"NP College {college_id}"),
            )
        conn.commit()
    finally:
        conn.close()
    scratch("colleges", "college_id", college_id)

    scoped = _block(admin, scratch, name=_uniq("NP Scoped"), college=college_id)
    # _derived_id uppercases: the scope segment is the uppercased college id.
    assert scoped["block_id"].startswith(f"BLOCK-{college_id.upper()}-"), scoped["block_id"]
    twin = _block(admin, scratch, name=scoped["block_name"], college=COLLEGE)
    assert twin["block_id"].startswith("BLOCK-NITTTR-CHENNAI-")
    assert twin["block_id"] != scoped["block_id"]
    # Unscoped creates keep the legacy shape; every issued id stays valid.
    legacy = _block(admin, scratch)
    assert legacy["block_id"].startswith("BLOCK-") and college_id not in legacy["block_id"]


def test_move_block_to_occupied_college_rejected(admin, scratch):
    name = _uniq("NP College Move")
    home = _block(admin, scratch, name=name, college=COLLEGE)
    away = _block(admin, scratch, name=_uniq("NP Away"))
    # Same name already lives in the destination college scope.
    r = admin.patch(f"{CAMPUS}/blocks/{away['block_id']}", json={"college_id": COLLEGE, "block_name": name})
    assert r.status_code == 409 and _code(r) == "DUPLICATE_NAME", r.get_json()
    assert home["block_id"] != away["block_id"]


# ── D6: wildcard escaping on name filters/search ────────────────────────────


def _seed_makes(tag: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for ac_id, make in (
                (f"NP-{tag}-PCT", "100% Pure"),
                (f"NP-{tag}-THOU", "1000 Islands"),
                (f"NP-{tag}-UNDER", "A_B Make"),
                (f"NP-{tag}-PLAIN", "AXB Make"),
            ):
                cur.execute(
                    "INSERT INTO acs (ac_id, asset_code, ac_make_company) VALUES (%s, %s, %s)",
                    (ac_id, ac_id, f"{make} {tag}"),
                )
        conn.commit()
    finally:
        conn.close()


def _drop_makes(tag: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (f"NP-{tag}-%",))
        conn.commit()
    finally:
        conn.close()


def _asset_ids(resp) -> set:
    return {row["ac_id"] for row in resp.get_json()["data"]}


def test_name_filters_treat_wildcards_literally(admin):
    tag = uuid.uuid4().hex[:8]
    _seed_makes(tag)
    try:
        r = admin.get("/api/v1/assets", query_string={"manufacturer": f"100% Pure {tag}", "page_size": 50})
        assert r.status_code == 200, r.get_json()
        assert _asset_ids(r) == {f"NP-{tag}-PCT"}, r.get_json()["data"]

        r = admin.get("/api/v1/assets", query_string={"manufacturer": f"A_B Make {tag}", "page_size": 50})
        assert r.status_code == 200, r.get_json()
        assert _asset_ids(r) == {f"NP-{tag}-UNDER"}, r.get_json()["data"]

        r = admin.get("/api/v1/assets", query_string={"search": f"100% Pure {tag}", "page_size": 50})
        assert r.status_code == 200, r.get_json()
        assert _asset_ids(r) == {f"NP-{tag}-PCT"}, r.get_json()["data"]
    finally:
        _drop_makes(tag)


# ── preview == commit parity ────────────────────────────────────────────────


def _import(admin, payload: bytes, dry_run: bool):
    suffix = "?dry_run=true" if dry_run else ""
    return admin.post(
        f"/api/v1/assets/import{suffix}",
        data={"file": (io.BytesIO(payload), "np-parity.csv")},
    )


def test_import_preview_matches_commit(admin):
    tag = uuid.uuid4().hex[:8]
    header = "block_name,department_name,ac_make_company,room_number"
    body = "\n".join(
        [
            header,
            f"NP Parity B1 {tag},NP Parity D1 {tag},Daikin,",
            f"NP Parity B2 {tag},NP Parity D2 {tag},Voltas,",
            f"NP Parity B1 {tag},NP Parity D1 {tag},Daikin,",
            f"NP Parity B3 {tag},,LG,",
        ]
    ).encode()
    created_ids: list = []
    try:
        dry = _import(admin, body, True)
        assert dry.status_code == 200, dry.get_json()
        live = _import(admin, body, False)
        assert live.status_code == 201, live.get_json()
        for key in ("imported", "created", "updated", "duplicates", "failed", "pending_location"):
            assert (
                live.get_json()["data"][key]
                == dry.get_json()["data"][key]
                == {
                    "imported": 3,
                    "created": 2,
                    "updated": 0,
                    "duplicates": 1,
                    "failed": 1,
                    "pending_location": 3,
                }[key]
            )
        created_ids = [
            row["ac_id"] for row in live.get_json()["data"]["rows"] if row.get("action") == "create"
        ]
        assert len(created_ids) == 2
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = ANY(%s)", (created_ids,))
            conn.commit()
        finally:
            conn.close()


# ── D2: manufacturer fold at read/group ─────────────────────────────────────


def test_manufacturer_variants_fold_in_dashboard(admin):
    tag = uuid.uuid4().hex[:8]
    makes = [
        (f"NP-{tag}-M1", f"FoldTag {tag}"),
        (f"NP-{tag}-M2", f"FOLDTAG {tag}"),
        (f"NP-{tag}-M3", "Daikin"),
    ]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for ac_id, make in makes:
                cur.execute(
                    "INSERT INTO acs (ac_id, asset_code, ac_make_company, installation_year)"
                    " VALUES (%s, %s, %s, 2021)",
                    (ac_id, ac_id, make),
                )
        conn.commit()
    finally:
        conn.close()
    try:
        r = admin.get("/api/v1/dashboard/summary")
        assert r.status_code == 200, r.get_json()
        by_mfr = {row["manufacturer"]: row["count"] for row in r.get_json()["data"]["by_manufacturer"]}
        folded = [name for name in by_mfr if f"foldtag{tag}".lower() in name.replace(" ", "").lower()]
        assert len(folded) == 1 and by_mfr[folded[0]] == 2, by_mfr
        assert by_mfr.get("Daikin", 0) >= 1

        r = admin.get("/api/v1/dashboard/analytics?dim1=company")
        assert r.status_code == 200, r.get_json()
        rows = r.get_json()["data"]["explorer"]["rows"]
        folded_rows = [row for row in rows if f"foldtag{tag}".lower() in row["d1"].replace(" ", "").lower()]
        assert len(folded_rows) == 1 and folded_rows[0]["value"] == 2, rows

        # Stored spellings are untouched by the fold.
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT DISTINCT ac_make_company FROM acs WHERE ac_id LIKE %s", (f"NP-{tag}-%",))
                assert sorted(row[0] for row in cur.fetchall()) == sorted(m for _, m in makes)
        finally:
            conn.close()
    finally:
        _drop_makes(tag)


# ── free text stays byte-identical ──────────────────────────────────────────


def test_remarks_and_model_stay_verbatim(admin):
    tag = uuid.uuid4().hex[:8]
    ac_id = f"NP-{tag}-TEXT"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, asset_code) VALUES (%s, %s)", (ac_id, ac_id))
        conn.commit()
    finally:
        conn.close()
    try:
        remarks, model = "  spaced   CaSe  notes\t", "  MiXeD-Model_X  "
        r = admin.patch(f"/api/v1/assets/{ac_id}", json={"remarks": remarks, "ac_model_number": model})
        assert r.status_code == 200, r.get_json()
        r = admin.get(f"/api/v1/assets/{ac_id}")
        assert r.status_code == 200, r.get_json()
        data = r.get_json()["data"]
        assert data["remarks"] == remarks, data
        assert data["ac_model_number"] == model, data
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            conn.commit()
        finally:
            conn.close()
