"""Location architecture API — review queue, aliases, re-resolve, manual PATCH.

Smoke plus core semantics for the endpoints added on top of the
source-vs-confirmed model (§10 manual confirmation, §12 re-resolution, §13
review queue, §6.1 aliases); the exhaustive §23/§24 regression matrix builds
on the same patterns. Seeded shared campus (conftest): NITTTR-CHENNAI ->
MAIN-ACADEMIC-BLOCK -> MAIN-ACADEMIC-BLOCK-FLR-1 ->
{ROOM-102 (Computer Science), ROOM-105 (Administration)}.

Every asset, block, floor and alias a test creates is tracked by the
``scratch`` fixture and removed child-first on teardown, so the shared campus
never mutates. Re-resolve idempotence uses ``limit: 2000`` (the endpoint's
maximum): the test database holds far fewer rows than that, so one pass
always settles every scan-able record and the second pass must report zero
changes.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.services import location_resolver  # noqa: E402
from tests.conftest import TEST_USER_ID, auth, db_required, make_token  # noqa: E402

CAMPUS = "/api/v1/campus"
LOCATION = f"{CAMPUS}/location"
ASSETS = "/api/v1/assets"
COLLEGE = "NITTTR-CHENNAI"  # seeded by migrations/003, referenced by the FK
BLOCK = "MAIN-ACADEMIC-BLOCK"
FLOOR = "MAIN-ACADEMIC-BLOCK-FLR-1"
ROOM_CSE = "ROOM-102"
ROOM_ADMIN = "ROOM-105"

pytestmark = db_required()

# Tables a test may hand to the scratch teardown. A fixed set of literals in
# this file — never request input — which is what makes the f-string safe.
_TRACKED_TABLES = ("acs", "asset_locations", "location_aliases", "blocks", "floors", "rooms", "departments")


@pytest.fixture()
def admin(db_admin_client):
    """SUPER_ADMIN client — holds MANAGE_CAMPUS and EDIT_ASSET."""
    return db_admin_client


@pytest.fixture()
def scratch():
    """Track rows a test created so teardown can remove them child-first."""
    created: list[tuple[str, str, str]] = []

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
            # reversed: last tracked (child) goes first.
            for table, id_column, value in reversed(created):
                cur.execute(f"DELETE FROM {table} WHERE {id_column} = %s", (value,))
        conn.commit()
    finally:
        conn.close()


def _uniq(prefix: str) -> str:
    """Unique label, so a leftover row from an earlier failed run cannot 409."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _code(r) -> str:
    return ((r.get_json() or {}).get("error") or {}).get("code", "<no error envelope>")


def _seed_asset(scratch, *, room: str | None = None) -> str:
    """Fresh ac_id; the acs mirror trigger has created its resolution record."""
    ac_id = f"LOC-{uuid.uuid4().hex[:10].upper()}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, room_id) VALUES (%s, %s)", (ac_id, room))
        conn.commit()
    finally:
        conn.close()
    # Track parent first: teardown runs reversed, deleting the record (child)
    # before the acs row it references.
    scratch("acs", "ac_id", ac_id)
    scratch("asset_locations", "ac_id", ac_id)
    return ac_id


def _update_record(ac_id: str, **columns) -> None:
    """Direct column write on the resolution record — test-only seeding.

    Keys are fixed literals in this file, never request input.
    """
    assert columns, "at least one column is required"
    sets = ", ".join(f"{key} = %s" for key in columns)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE asset_locations SET {sets} WHERE ac_id = %s", (*columns.values(), ac_id))
        conn.commit()
    finally:
        conn.close()


def _fetch_one(sql: str, params: tuple):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


# ── §13: the review queue ────────────────────────────────────────────────────


def test_review_queue_surfaces_source_without_confirmation(admin, scratch):
    """Source text without a confirmed id appears in the queue with the
    nested location payload: preserved source + per-field status (§13)."""
    ac_id = _seed_asset(scratch)
    _update_record(
        ac_id,
        source_ref="import:review-seed",
        source_block_value="Phantom Block",
        source_room_number_value="GHOST-1",
        block_status="UNRESOLVED",
        room_status="UNRESOLVED",
    )

    r = admin.get(f"{LOCATION}/review", query_string={"page_size": 200})
    assert r.status_code == 200, r.get_json()
    items = r.get_json()["data"]
    row = next((item for item in items if item["ac_id"] == ac_id), None)
    assert row is not None, "seeded unresolved asset missing from the review queue"

    levels = row["location"]["levels"]
    assert levels["block"] == {
        "id": None,
        "name": None,
        "status": "UNRESOLVED",
        "source": "Phantom Block",
    }
    assert levels["room"]["status"] == "UNRESOLVED"
    assert levels["room"]["source"] == "GHOST-1"
    assert levels["college"]["status"] == "MISSING"

    # ?level= narrows the queue to one level; the seed qualifies there too.
    r = admin.get(f"{LOCATION}/review", query_string={"level": "block", "page_size": 200})
    assert r.status_code == 200
    assert any(item["ac_id"] == ac_id for item in r.get_json()["data"])


def test_review_rejects_viewer_and_unknown_level(admin):
    r = admin.get(f"{LOCATION}/review", headers=auth(make_token("VIEWER")))
    assert r.status_code == 403

    r = admin.get(f"{LOCATION}/review", query_string={"level": "banana"})
    assert r.status_code == 400
    assert _code(r) == "INVALID_LEVEL"


# ── §6.1: alias CRUD ─────────────────────────────────────────────────────────


def test_alias_crud_lifecycle(admin, scratch):
    """Create -> idempotent repeat -> 409 on repoint -> list -> soft delete ->
    hidden by default -> reactivation -> 404 on unknown id."""
    alias_text = _uniq("Legacy Block")

    r = admin.post(
        f"{LOCATION}/aliases",
        json={"entity_type": "block", "entity_id": BLOCK, "alias": alias_text},
    )
    assert r.status_code == 201, r.get_json()
    created = r.get_json()["data"]
    scratch("location_aliases", "id", created["id"])
    assert created["normalized_alias"] == location_resolver.normalize(alias_text)
    assert created["is_active"] is True

    # Identical alias -> same row back, idempotent 200 (never a duplicate).
    r = admin.post(
        f"{LOCATION}/aliases",
        json={"entity_type": "block", "entity_id": BLOCK, "alias": alias_text},
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["id"] == created["id"]

    # A real second block: pointing the SAME alias elsewhere is 409 — an
    # active alias is never silently repointed.
    r = admin.post(f"{CAMPUS}/blocks", json={"block_name": _uniq("Loc Block"), "college_id": COLLEGE})
    assert r.status_code == 201, r.get_json()
    other_block = r.get_json()["data"]["block_id"]
    scratch("blocks", "block_id", other_block)
    r = admin.post(
        f"{LOCATION}/aliases",
        json={"entity_type": "block", "entity_id": other_block, "alias": alias_text},
    )
    assert r.status_code == 409
    assert _code(r) == "ALIAS_EXISTS"

    # Validation: unknown entity_type and unknown target.
    r = admin.post(f"{LOCATION}/aliases", json={"entity_type": "banana", "entity_id": BLOCK, "alias": "x"})
    assert r.status_code == 400
    assert _code(r) == "VALIDATION_ERROR"
    r = admin.post(
        f"{LOCATION}/aliases",
        json={"entity_type": "room", "entity_id": "NO-SUCH-ROOM", "alias": _uniq("Ghost")},
    )
    assert r.status_code == 400
    assert _code(r) == "INVALID_LOCATION_TARGET"

    # List: type filter applies, active rows only by default.
    r = admin.get(f"{LOCATION}/aliases", query_string={"entity_type": "block"})
    assert r.status_code == 200
    listed = r.get_json()["data"]["aliases"]
    assert any(row["id"] == created["id"] for row in listed)
    assert all(row["entity_type"] == "block" for row in listed)

    # Soft delete: leaves the active list, stays visible with include_inactive.
    r = admin.delete(f"{LOCATION}/aliases/{created['id']}")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["deleted"] is True

    r = admin.get(f"{LOCATION}/aliases", query_string={"q": alias_text})
    assert r.status_code == 200
    assert created["id"] not in {row["id"] for row in r.get_json()["data"]["aliases"]}

    r = admin.get(f"{LOCATION}/aliases", query_string={"q": alias_text, "include_inactive": "true"})
    inactive = r.get_json()["data"]["aliases"]
    assert [row["id"] for row in inactive] == [created["id"]]
    assert inactive[0]["is_active"] is False

    # Reactivation: same alias comes back as the same active row (200).
    r = admin.post(
        f"{LOCATION}/aliases",
        json={"entity_type": "block", "entity_id": BLOCK, "alias": alias_text},
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["id"] == created["id"]
    assert r.get_json()["data"]["is_active"] is True

    # Unknown alias id -> 404.
    r = admin.delete(f"{LOCATION}/aliases/999999999")
    assert r.status_code == 404
    assert _code(r) == "ALIAS_NOT_FOUND"


def test_location_endpoints_require_their_permissions(admin):
    """MANAGE_CAMPUS gates review/aliases/re-resolve; EDIT_ASSET gates the
    manual location PATCH (admin/manager scope, §13)."""
    viewer = auth(make_token("VIEWER"))
    assert admin.get(f"{LOCATION}/review", headers=viewer).status_code == 403
    assert admin.get(f"{LOCATION}/aliases", headers=viewer).status_code == 403
    assert (
        admin.post(
            f"{LOCATION}/aliases",
            json={"entity_type": "block", "entity_id": BLOCK, "alias": "x"},
            headers=viewer,
        ).status_code
        == 403
    )
    assert admin.post(f"{LOCATION}/re-resolve", json={}, headers=viewer).status_code == 403
    assert (
        admin.patch(f"{ASSETS}/ANY-REF/location", json={"room_id": ROOM_CSE}, headers=viewer).status_code
        == 403
    )


# ── §12: batch re-resolution ─────────────────────────────────────────────────


def test_re_resolve_applies_alias_and_is_idempotent(admin, scratch):
    """An alias created after import lets the re-resolver finish the row —
    once; a second pass reports zero changes and provenance follows the row."""
    alias_text = _uniq("Legacy Wing")
    r = admin.post(
        f"{LOCATION}/aliases",
        json={"entity_type": "block", "entity_id": BLOCK, "alias": alias_text},
    )
    assert r.status_code == 201, r.get_json()
    scratch("location_aliases", "id", r.get_json()["data"]["id"])

    ac_id = _seed_asset(scratch)
    _update_record(
        ac_id,
        source_ref="import:reseed",
        source_block_value=alias_text,
        block_status="UNRESOLVED",
    )

    r = admin.post(f"{LOCATION}/re-resolve", json={"limit": 2000})
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["scanned"] >= 1
    assert data["updated"] >= 1

    r = admin.get(f"{ASSETS}/{ac_id}")
    assert r.status_code == 200, r.get_json()
    loc = r.get_json()["data"]["location"]
    assert loc["levels"]["block"]["id"] == BLOCK
    assert loc["levels"]["block"]["status"] == "RESOLVED"
    assert loc["levels"]["block"]["source"] == alias_text  # source never discarded
    assert loc["status"] == "PARTIAL"  # room still missing

    row = _fetch_one("SELECT source_ref, resolved_by FROM asset_locations WHERE ac_id = %s", (ac_id,))
    assert row[0] == "import:reseed"  # provenance follows the row, not the actor
    assert row[1] == TEST_USER_ID  # the actor is still recorded

    # Idempotent: everything scan-able settled on the first pass.
    r = admin.post(f"{LOCATION}/re-resolve", json={"limit": 2000})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["updated"] == 0


def test_re_resolve_rejects_bad_limit(admin):
    for bad in (0, 2001, 2.5, "500", True, None):
        r = admin.post(f"{LOCATION}/re-resolve", json={"limit": bad})
        assert r.status_code == 400, (bad, r.get_json())
        assert _code(r) == "VALIDATION_ERROR"


def test_re_resolve_never_touches_manual_pins(admin, scratch):
    """A manually confirmed room survives re-resolution with its contradicting
    source intact while the unresolved block finishes through a new alias."""
    alias_text = _uniq("Manual Wing")
    r = admin.post(
        f"{LOCATION}/aliases",
        json={"entity_type": "block", "entity_id": BLOCK, "alias": alias_text},
    )
    assert r.status_code == 201, r.get_json()
    scratch("location_aliases", "id", r.get_json()["data"]["id"])

    ac_id = _seed_asset(scratch, room=ROOM_ADMIN)
    # The trigger derived the chain from the room; drop every level except the
    # pinned room, then contradict it with source text the matcher cannot use.
    _update_record(
        ac_id,
        block_id=None,
        block_status="UNRESOLVED",
        floor_id=None,
        floor_status="MISSING",
        college_id=None,
        college_status="MISSING",
        source_block_value=alias_text,
        source_room_number_value="TOTALLY-WRONG",
        manual_fields=["room"],
    )

    r = admin.post(f"{LOCATION}/re-resolve", json={"limit": 2000})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["updated"] >= 1

    r = admin.get(f"{ASSETS}/{ac_id}")
    assert r.status_code == 200, r.get_json()
    loc = r.get_json()["data"]["location"]
    assert loc["manual_fields"] == ["room"]
    assert loc["levels"]["room"]["id"] == ROOM_ADMIN
    assert loc["levels"]["room"]["status"] == "RESOLVED"
    assert loc["levels"]["room"]["source"] == "TOTALLY-WRONG"  # source preserved
    assert loc["levels"]["block"]["id"] == BLOCK  # finished via the alias
    assert loc["levels"]["floor"]["id"] == FLOOR  # derived from the pinned room

    assert _fetch_one("SELECT room_id FROM acs WHERE ac_id = %s", (ac_id,)) == (ROOM_ADMIN,)


# ── §10: per-level manual confirmation ───────────────────────────────────────


def test_asset_location_patch_confirms_and_clears(admin, scratch):
    """A provided id pins the level and the chain derives around it; an
    explicit null un-pins it while blank-source levels keep their value."""
    ac_id = _seed_asset(scratch)

    # Error shapes first: no levels, missing asset, unknown level id.
    r = admin.patch(f"{ASSETS}/{ac_id}/location", json={})
    assert r.status_code == 400 and _code(r) == "NO_FIELDS"
    r = admin.patch(f"{ASSETS}/{ac_id}/location", json={"note": "no levels"})
    assert r.status_code == 400 and _code(r) == "NO_FIELDS"
    r = admin.patch(f"{ASSETS}/NO-SUCH-ASSET/location", json={"room_id": ROOM_CSE})
    assert r.status_code == 404 and _code(r) == "ASSET_NOT_FOUND"
    r = admin.patch(f"{ASSETS}/{ac_id}/location", json={"room_id": "NO-SUCH-ROOM"})
    assert r.status_code == 400 and _code(r) == "INVALID_LOCATION_LEVEL"

    # Manual confirmation: room pins, floor/block/college derive from it.
    r = admin.patch(f"{ASSETS}/{ac_id}/location", json={"room_id": ROOM_CSE, "note": "physically verified"})
    assert r.status_code == 200, r.get_json()
    loc = r.get_json()["data"]["location"]
    assert loc["levels"]["room"]["id"] == ROOM_CSE
    assert loc["levels"]["room"]["status"] == "RESOLVED"
    assert loc["levels"]["floor"]["id"] == FLOOR
    assert loc["levels"]["block"]["id"] == BLOCK
    assert loc["levels"]["college"]["id"] == COLLEGE
    assert loc["manual_fields"] == ["room"]
    assert loc["status"] == "ASSIGNED"

    # The writer contract's second step: the acs mirror carries room + status.
    assert _fetch_one("SELECT room_id, location_status FROM acs WHERE ac_id = %s", (ac_id,)) == (
        ROOM_CSE,
        "ASSIGNED",
    )
    assert _fetch_one("SELECT manual_fields FROM asset_locations WHERE ac_id = %s", (ac_id,))[0] == ["room"]

    # Clearing un-pins: room goes to MISSING, blank-source levels keep their
    # previously confirmed value (§11 — no downgrade from an empty cell).
    r = admin.patch(f"{ASSETS}/{ac_id}/location", json={"room_id": None})
    assert r.status_code == 200, r.get_json()
    loc = r.get_json()["data"]["location"]
    assert loc["levels"]["room"]["id"] is None
    assert loc["levels"]["room"]["status"] == "MISSING"
    assert loc["levels"]["floor"]["id"] == FLOOR
    assert loc["levels"]["block"]["id"] == BLOCK
    assert loc["manual_fields"] == []
    assert loc["status"] == "PARTIAL"

    assert _fetch_one("SELECT room_id FROM acs WHERE ac_id = %s", (ac_id,)) == (None,)
    assert _fetch_one("SELECT manual_fields FROM asset_locations WHERE ac_id = %s", (ac_id,))[0] == []


def test_asset_location_patch_rejects_contradicting_chain(admin, scratch):
    """A floor of one block pinned against a different block: the resolver
    keeps both manual pins, the composite FK is the chain authority, so the
    answer is 400 — and nothing was written."""
    ac_id = _seed_asset(scratch)

    r = admin.post(f"{CAMPUS}/blocks", json={"block_name": _uniq("Loc Block"), "college_id": COLLEGE})
    assert r.status_code == 201, r.get_json()
    other_block = r.get_json()["data"]["block_id"]
    scratch("blocks", "block_id", other_block)
    r = admin.post(f"{CAMPUS}/floors", json={"block_id": other_block, "floor_name": _uniq("Loc Floor")})
    assert r.status_code == 201, r.get_json()
    other_floor = r.get_json()["data"]["floor_id"]
    scratch("floors", "floor_id", other_floor)

    r = admin.patch(
        f"{ASSETS}/{ac_id}/location",
        json={"floor_id": other_floor, "block_id": BLOCK},
    )
    assert r.status_code == 400, r.get_json()
    assert _code(r) == "INVALID_LOCATION_CHAIN"

    # Savepoint rollback: the record still holds only the seed state.
    assert _fetch_one(
        "SELECT floor_id, block_id FROM asset_locations WHERE ac_id = %s",
        (ac_id,),
    ) == (None, None)


# ─────────────────────────────────────────────────────────────────────────
# §23 M–T: effective-location READ paths (dashboard, detail, list, reports,
# facets) over three controlled review states — the matrix_seed fixture.
# ─────────────────────────────────────────────────────────────────────────

SUMMARY = "/api/v1/dashboard/summary"
REPORT_BY_BLOCK = "/api/v1/reports/assets-by-block"
FACETS = f"{ASSETS}/facets"
BLOCK_NAME = "Main Academic Block"
FLOOR_NAME = "Floor 1"
COLLEGE_NAME = "National Institute of Technical Teachers Training and Research"
DEPT_ADM_ID = "DEPT-ADMINISTRATION"
DEPT_ADM_NAME = "Administration"
# The UI's default filter row: every "All" sentinel must mean "no filter".
ALL_SENTINELS = (
    "status=All&block=All&department=All&asset_type=All&floor=All"
    "&room=All&manufacturer=All&lifecycle=All&location_status=All&due_before=All"
)


@pytest.fixture()
def matrix_seed(admin, scratch):
    """Three active assets in controlled review states (§16–§19 read paths):

    * ``p`` — PARTIAL: full confirmed chain, room unresolved (kept for review),
    * ``s`` — source-only: nothing confirmed, imported block/dept text preserved,
    * ``x`` — PENDING: nothing known at all.

    Tag-scoped so every assertion is deterministic on the shared database;
    the assets-by-block report is snapshotted BEFORE seeding so section S can
    assert exact deltas instead of global counts.
    """
    tag = uuid.uuid4().hex[:8]
    p, s, x = f"LOCT-{tag}-P", f"LOCT-{tag}-S", f"LOCT-{tag}-X"
    source_block = f"Main Academic Block (old wing {tag})"
    source_dept = f"Dept of Old Wing {tag}"

    snapshot = admin.get(REPORT_BY_BLOCK)
    assert snapshot.status_code == 200, snapshot.get_json()
    report_before = snapshot.get_json()["data"]["items"]

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for ac_id in (p, s, x):
                cur.execute("INSERT INTO acs (ac_id, room_id) VALUES (%s, NULL)", (ac_id,))
            # p: everything confirmed except the room the import never matched.
            cur.execute(
                "UPDATE asset_locations SET"
                " college_id = %s, block_id = %s, floor_id = %s, department_id = %s,"
                " college_status = 'RESOLVED', block_status = 'RESOLVED',"
                " floor_status = 'RESOLVED', department_status = 'RESOLVED',"
                " room_status = 'UNRESOLVED',"
                " source_college_value = %s, source_block_value = %s,"
                " source_floor_value = %s, source_department_value = %s,"
                " source_room_number_value = %s"
                " WHERE ac_id = %s",
                (
                    COLLEGE,
                    BLOCK,
                    FLOOR,
                    DEPT_ADM_ID,
                    COLLEGE_NAME,
                    BLOCK_NAME,
                    FLOOR_NAME,
                    DEPT_ADM_NAME,
                    f"Ghost Room {tag}",
                    p,
                ),
            )
            # s: nothing confirmed — only the text the import supplied.
            cur.execute(
                "UPDATE asset_locations SET"
                " block_status = 'UNRESOLVED', source_block_value = %s,"
                " department_status = 'UNRESOLVED', source_department_value = %s"
                " WHERE ac_id = %s",
                (source_block, source_dept, s),
            )
            # Overall status on acs: the mirror trigger only reacts to room_id,
            # so a test seeds the verdict the resolver would have written.
            cur.execute("UPDATE acs SET location_status = 'PARTIAL' WHERE ac_id = %s", (p,))
            cur.execute(
                "UPDATE acs SET location_status = 'PENDING' WHERE ac_id = ANY(%s)",
                ([s, x],),
            )
        conn.commit()
    finally:
        conn.close()
    for ac_id in (p, s, x):
        scratch("acs", "ac_id", ac_id)

    yield {
        "tag": tag,
        "p": p,
        "s": s,
        "x": x,
        "source_block": source_block,
        "report_before": report_before,
    }


def _tagged_ids(admin, query: str, tag: str) -> set[str]:
    r = admin.get(f"{ASSETS}?{query}&search={tag}&page_size=100")
    assert r.status_code == 200, r.get_json()
    # The list envelope: data = bare rows, pagination alongside it.
    return {item["ac_id"] for item in r.get_json()["data"]}


def test_m_dashboard_all_buildings_counts_every_review_state(admin, matrix_seed):
    r = admin.get(f"{SUMMARY}?search={matrix_seed['tag']}&{ALL_SENTINELS}")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["kpis"]["total"] == 3, "All sentinels never filter anything out"
    by_block = {entry["block"]: entry["count"] for entry in data["by_block"]}
    assert by_block == {
        BLOCK_NAME: 1,  # PARTIAL asset under its canonical block
        matrix_seed["source_block"]: 1,  # preserved text surfaces as its own bucket
        "Unassigned": 1,  # nothing known at all
    }


def test_n_dashboard_specific_building_matches_canonical_and_source_rows(admin, matrix_seed):
    ids = _tagged_ids(admin, f"block={BLOCK_NAME}", matrix_seed["tag"])
    assert matrix_seed["p"] in ids, "PARTIAL asset with a confirmed block"
    assert matrix_seed["s"] in ids, "unresolved rows are findable by their source text"
    assert matrix_seed["x"] not in ids, "a row that knows nothing matches nothing"
    r = admin.get(f"{ASSETS}?block={BLOCK_NAME}&search={matrix_seed['tag']}&page_size=100")
    item = next(i for i in r.get_json()["data"] if i["ac_id"] == matrix_seed["p"])
    assert item["block_name"] == BLOCK_NAME  # flat effective field rides along
    assert item["location"]["levels"]["block"]["id"] == BLOCK


def test_o_dashboard_specific_department_matches_effective_department(admin, matrix_seed):
    ids = _tagged_ids(admin, f"department={DEPT_ADM_NAME}", matrix_seed["tag"])
    assert matrix_seed["p"] in ids
    assert matrix_seed["s"] not in ids  # its department text is a different value
    assert matrix_seed["x"] not in ids


def test_p_dashboard_specific_floor_matches_effective_floor(admin, matrix_seed):
    ids = _tagged_ids(admin, f"floor={FLOOR_NAME}", matrix_seed["tag"])
    assert matrix_seed["p"] in ids
    assert matrix_seed["s"] not in ids
    assert matrix_seed["x"] not in ids


def test_q_asset_detail_carries_the_full_location_object(admin, matrix_seed):
    r = admin.get(f"{ASSETS}/{matrix_seed['p']}")
    assert r.status_code == 200, r.get_json()
    loc = r.get_json()["data"]["location"]
    assert loc["status"] == "PARTIAL"
    block_level = loc["levels"]["block"]
    assert block_level["id"] == BLOCK
    assert block_level["name"] == BLOCK_NAME
    assert block_level["status"] == "RESOLVED"
    room_level = loc["levels"]["room"]
    assert room_level["id"] is None
    assert room_level["status"] == "UNRESOLVED"
    assert room_level["source"] == f"Ghost Room {matrix_seed['tag']}"
    assert loc["manual_fields"] == []

    # The source-only asset reads as PENDING with every level on review.
    r = admin.get(f"{ASSETS}/{matrix_seed['s']}")
    loc_s = r.get_json()["data"]["location"]
    assert loc_s["status"] == "PENDING"
    assert loc_s["levels"]["block"]["id"] is None
    assert loc_s["levels"]["block"]["status"] == "UNRESOLVED"
    assert loc_s["levels"]["block"]["source"] == matrix_seed["source_block"]


def test_r_asset_list_rows_carry_nested_and_flat_effective_location(admin, matrix_seed):
    r = admin.get(f"{ASSETS}?search={matrix_seed['tag']}&page_size=100")
    assert r.status_code == 200, r.get_json()
    items = r.get_json()["data"]
    assert {item["ac_id"] for item in items} == {matrix_seed["p"], matrix_seed["s"], matrix_seed["x"]}
    by_id = {item["ac_id"]: item for item in items}

    p = by_id[matrix_seed["p"]]
    assert p["block_name"] == BLOCK_NAME and p["floor_name"] == FLOOR_NAME  # flat effective
    assert p["location"]["levels"]["room"]["status"] == "UNRESOLVED"  # nested per-field

    s = by_id[matrix_seed["s"]]
    # Flat fields carry the EFFECTIVE text: unresolved source stays visible
    # instead of vanishing into NULL (§6); the nested level keeps the
    # canonical/source split (id None, name None, source preserved).
    assert s["block_name"] == matrix_seed["source_block"]
    assert s["location"]["levels"]["block"]["id"] is None
    assert s["location"]["levels"]["block"]["name"] is None
    assert s["location"]["levels"]["block"]["source"] == matrix_seed["source_block"]
    assert s["location"]["levels"]["block"]["status"] == "UNRESOLVED"

    x = by_id[matrix_seed["x"]]
    assert x["block_name"] is None  # nothing known at all: genuinely blank
    assert x["location"]["levels"]["block"]["status"] == "MISSING"


def test_s_reports_count_partial_assets_under_their_block(admin, matrix_seed):
    r = admin.get(REPORT_BY_BLOCK)
    assert r.status_code == 200, r.get_json()
    after = {row["block"]: row["count"] for row in r.get_json()["data"]["items"]}
    before = {row["block"]: row["count"] for row in matrix_seed["report_before"]}
    # Exact deltas: each seeded state lands in exactly one bucket.
    assert after.get(BLOCK_NAME, 0) == before.get(BLOCK_NAME, 0) + 1
    assert after.get(matrix_seed["source_block"], 0) == before.get(matrix_seed["source_block"], 0) + 1
    assert after.get("Unassigned", 0) == before.get("Unassigned", 0) + 1


def test_t_facets_surface_effective_labels_without_duplicates(admin, matrix_seed):
    r = admin.get(FACETS)
    assert r.status_code == 200, r.get_json()
    facets = r.get_json()["data"]

    block_values = [entry["value"] for entry in facets["block"]]
    assert len(block_values) == len(set(block_values)), "one bucket per effective label"
    assert BLOCK_NAME in block_values
    assert matrix_seed["source_block"] in block_values  # unresolved text surfaces

    # Stable canonical ids ride along (None for source-only values), so the
    # dashboard filters by id instead of display text (§8).
    block_ids = {entry["value"]: entry["id"] for entry in facets["block"]}
    assert block_ids[BLOCK_NAME] == BLOCK
    assert block_ids[matrix_seed["source_block"]] is None

    dept_values = [entry["value"] for entry in facets["department"]]
    assert len(dept_values) == len(set(dept_values))
    assert DEPT_ADM_NAME in dept_values

    status_values = {entry["value"] for entry in facets["location_status"]}
    assert "PARTIAL" in status_values  # the extended vocabulary is browsable
    assert "PENDING" in status_values


def test_alias_create_reports_matching_review_rows(admin, scratch):
    """§11: creating an alias reports how many review rows it will finish,
    so the UI can prompt the required re-resolve (which stays explicit —
    nothing is re-resolved implicitly)."""
    bait = _uniq("Alias Bait Block")
    ac_id = _seed_asset(scratch)
    _update_record(
        ac_id,
        source_ref="import:alias-bait",
        source_block_value=bait,
        block_status="UNRESOLVED",
    )

    r = admin.post(
        f"{LOCATION}/aliases",
        json={"entity_type": "block", "entity_id": BLOCK, "alias": bait},
    )
    assert r.status_code == 201, r.get_json()
    created = r.get_json()["data"]
    scratch("location_aliases", "id", created["id"])
    assert created["matching_review_rows"] >= 1

    # An alias nobody's source mentions matches nothing.
    r = admin.post(
        f"{LOCATION}/aliases",
        json={"entity_type": "block", "entity_id": BLOCK, "alias": _uniq("Unmentioned Wing")},
    )
    assert r.status_code == 201, r.get_json()
    other = r.get_json()["data"]
    scratch("location_aliases", "id", other["id"])
    assert other["matching_review_rows"] == 0


def test_016_seeds_nitttrc_alias_idempotently(admin):
    """§5/§23-U: the verified college alias exists and re-applying the seed
    is a no-op (ON CONFLICT DO NOTHING)."""
    row = _fetch_one(
        "SELECT entity_type, entity_id, alias, normalized_alias, is_active"
        " FROM location_aliases WHERE entity_type = 'college' AND normalized_alias = 'nitttrc'",
        (),
    )
    assert row is not None, "migration 016 must seed NITTTRC -> NITTTR-CHENNAI"
    assert row[1] == "NITTTR-CHENNAI" and row[2] == "NITTTRC" and row[4] is True
    before = _fetch_one("SELECT COUNT(*) FROM location_aliases WHERE normalized_alias = 'nitttrc'", ())[0]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)"
                " VALUES ('college', 'NITTTR-CHENNAI', 'NITTTRC', 'nitttrc', 'migration-016')"
                " ON CONFLICT (entity_type, normalized_alias) DO NOTHING"
            )
        conn.commit()
    finally:
        conn.close()
    after = _fetch_one("SELECT COUNT(*) FROM location_aliases WHERE normalized_alias = 'nitttrc'", ())[0]
    assert after == before == 1
