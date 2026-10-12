"""Phase 3 task 9 — focused tests for the P3 v1 asset surface.

Covers: list filters (status/sort/floor/room/department/lifecycle/
location_status/due_before), /assets/facets, the enriched detail payload
(histories, attributes, full location chain, qr_version) with triple-ref
resolution (asset_uuid / asset_code / ac_id), streaming CSV export,
schema validation and the status_map vocabulary unit tests.
"""

import csv
import io
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.services.qr_service import QR_VERSION  # noqa: E402

LIST = "/api/v1/assets"
FACETS = "/api/v1/assets/facets"
EXPORT = "/api/v1/assets/export"


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture()
def seeds(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """Four uniquely-tagged assets covering the filter matrix, plus one
    maintenance row, one movement and one extensible attribute on a1."""
    tag = uuid.uuid4().hex[:8]
    ids = {
        "a1": f"V1FLT-{tag}-1",  # active, ASSIGNED, ROOM-102, WORKING, due 2026-01-15, 2.5t
        "a2": f"V1FLT-{tag}-2",  # active, ASSIGNED, ROOM-105, FAULT, due 2099-06-01, 1.5t
        "a3": f"V1FLT-{tag}-3",  # active, PENDING, no room, UNKNOWN, no due date, 2.0t
        "a4": f"V1FLT-{tag}-4",  # DECOMMISSIONED + is_active=false
    }
    asset_code = f"CODE-{tag}"
    movement_id = None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            rows = (
                (
                    ids["a1"],
                    asset_code,
                    "V1 Maker A",
                    "Split",
                    2.5,
                    "ROOM-102",
                    "ASSIGNED",
                    "Working",
                    "WORKING",
                    "2026-01-15",
                    "ACTIVE",
                    True,
                ),
                (
                    ids["a2"],
                    ids["a2"],
                    "V1 Maker B",
                    "Window",
                    1.5,
                    "ROOM-105",
                    "ASSIGNED",
                    "Not Working",
                    "FAULT",
                    "2099-06-01",
                    "ACTIVE",
                    True,
                ),
                (
                    ids["a3"],
                    ids["a3"],
                    "V1 Maker A",
                    "Split",
                    2.0,
                    None,
                    "PENDING",
                    None,
                    "UNKNOWN",
                    None,
                    "ACTIVE",
                    True,
                ),
                (
                    ids["a4"],
                    ids["a4"],
                    "V1 Maker C",
                    "Split",
                    3.0,
                    "ROOM-102",
                    "ASSIGNED",
                    "Working",
                    "WORKING",
                    None,
                    "DECOMMISSIONED",
                    False,
                ),
            )
            for row in rows:
                # current_status retired (013): the tuples still carry its old
                # position, so drop element 7 before binding the 11 columns.
                cur.execute(
                    "INSERT INTO acs (ac_id, asset_code, ac_make_company, ac_type,"
                    " ac_capacity_ton, room_id, location_status,"
                    " operational_status, next_service_date, lifecycle_status, is_active)"
                    " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    row[:7] + row[8:],
                )
            cur.execute("SELECT asset_uuid FROM acs WHERE ac_id = %s", (ids["a1"],))
            asset_uuid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO asset_attributes (asset_uuid, attribute_name, attribute_value,"
                " attribute_type, source) VALUES (%s, 'warranty_expiry', '2028-03-31', 'TEXT', 'import')",
                (asset_uuid,),
            )
            cur.execute(
                "INSERT INTO maintenance (ac_id, maintenance_type, maintenance_date, remarks)"
                " VALUES (%s, 'preventive', '2026-08-01', 'p3 detail test')",
                (ids["a1"],),
            )
            cur.execute(
                "INSERT INTO asset_movements (ac_id, from_room_id, to_room_id, status)"
                " VALUES (%s, 'ROOM-105', 'ROOM-102', 'COMPLETED')",
                (ids["a1"],),
            )
            cur.execute("SELECT id FROM asset_movements WHERE ac_id = %s", (ids["a1"],))
            movement_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    yield {**ids, "tag": tag, "asset_code": asset_code, "movement_id": movement_id}

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            if movement_id:
                cur.execute("DELETE FROM asset_movements WHERE id = %s", (movement_id,))
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (f"V1FLT-{tag}-%",))
        conn.commit()
    finally:
        conn.close()


def _tagged(payload, seeds):
    """Only this test's rows — the shared DB may hold leftovers."""
    prefix = f"V1FLT-{seeds['tag']}-"
    return [item["ac_id"] for item in payload if str(item["ac_id"]).startswith(prefix)]


def _get(client, seeds, query=""):
    r = client.get(f"{LIST}?page_size=100{query}")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["success"] is True
    assert {"page", "page_size", "total", "total_pages"} <= set(body["pagination"])
    return _tagged(body["data"], seeds)


# ── status_map (Phase 3 task 1 vocabulary) ───────────────────────────────────


def test_status_map_derivation():
    from app.services.status_map import derive_operational_status

    assert derive_operational_status("Working") == "WORKING"
    assert derive_operational_status("not working") == "FAULT"
    assert derive_operational_status("Damaged") == "FAULT"
    assert derive_operational_status("Under Maintenance") == "MAINTENANCE"
    assert derive_operational_status("Inactive") == "OFFLINE"
    assert derive_operational_status("  ") == "UNKNOWN"
    assert derive_operational_status(None) == "UNKNOWN"


def test_status_map_filter_normalization():
    from app.services.status_map import normalize_status_filter

    assert normalize_status_filter("WORKING") == "WORKING"
    assert normalize_status_filter("Working") == "WORKING"
    assert normalize_status_filter("not working") == "FAULT"
    assert normalize_status_filter("Under Maintenance") == "MAINTENANCE"
    assert normalize_status_filter("Free Text Thing") is None  # caller falls back
    assert normalize_status_filter("") is None
    assert normalize_status_filter(None) is None


# ── schema validation ────────────────────────────────────────────────────────


def test_asset_schemas_reject_bad_input():
    from pydantic import ValidationError

    from app.schemas.v1_schemas import AssetCreate, AssetUpdate

    with pytest.raises(ValidationError):
        AssetCreate(ac_id="X1", ac_make_company="M", ac_capacity_ton=0)  # must be > 0
    with pytest.raises(ValidationError):
        AssetCreate(ac_id="X2", ac_star_rating=7)  # 1..5
    with pytest.raises(ValidationError):
        AssetCreate(ac_id="X3", manufacturing_year=1800)  # 1900..2100
    with pytest.raises(ValidationError):
        AssetCreate()  # ac_id required
    with pytest.raises(ValidationError):
        AssetUpdate(ac_star_rating=9)
    with pytest.raises(ValidationError):
        AssetUpdate(power_factor=1.5)  # 0..1


# ── list filters (Phase 3 task 3) ────────────────────────────────────────────


def test_default_list_hides_decommissioned(client, seeds):
    present = _get(client, seeds)
    assert set(seeds[a] for a in ("a1", "a2", "a3")) <= set(present)
    assert seeds["a4"] not in present


def test_lifecycle_filter_browses_decommissioned(client, seeds):
    assert _get(client, seeds, "&lifecycle=DECOMMISSIONED") == [seeds["a4"]]
    assert _get(client, seeds, "&lifecycle=decommissioned") == [seeds["a4"]]
    active = _get(client, seeds, "&lifecycle=ACTIVE")
    assert set(seeds[a] for a in ("a1", "a2", "a3")) <= set(active)
    assert seeds["a4"] not in active


def test_location_status_filter(client, seeds):
    assert _get(client, seeds, "&location_status=pending") == [seeds["a3"]]
    assigned = _get(client, seeds, "&location_status=ASSIGNED")
    assert set(seeds[a] for a in ("a1", "a2")) <= set(assigned)
    assert seeds["a3"] not in assigned


def test_due_before_filter(client, seeds):
    assert _get(client, seeds, "&due_before=2026-12-31") == [seeds["a1"]]
    both = _get(client, seeds, "&due_before=2099-12-31")
    assert set(seeds[a] for a in ("a1", "a2")) <= set(both)
    assert seeds["a3"] not in both  # NULL next_service_date never matches


def test_status_filter_canonical_and_legacy_text(client, seeds):
    assert _get(client, seeds, "&status=WORKING") == [seeds["a1"]]
    assert _get(client, seeds, "&status=Working") == [seeds["a1"]]
    assert _get(client, seeds, "&status=Not%20Working") == [seeds["a2"]]


def test_room_floor_and_department_filters(client, seeds):
    assert _get(client, seeds, "&room=ROOM-102") == [seeds["a1"]]
    on_floor = _get(client, seeds, "&floor=MAIN-ACADEMIC-BLOCK-FLR-1")
    assert set(seeds[a] for a in ("a1", "a2")) <= set(on_floor)
    assert seeds["a3"] not in on_floor
    cs = _get(client, seeds, "&department=Computer%20Science")
    assert seeds["a1"] in cs
    assert seeds["a2"] not in cs
    assert _get(client, seeds, "&block=MAIN-ACADEMIC-BLOCK") != []


def test_sort_whitelist_and_unknown_key_fallback(client, seeds):
    # Whitelisted key, descending: capacity 2.5 > 2.0 > 1.5
    assert _get(client, seeds, "&sort=-capacity") == [seeds["a1"], seeds["a3"], seeds["a2"]]
    # Unknown sort key falls back to ac_id ASC — stable, never a 500.
    assert _get(client, seeds, "&sort=drop_table;--") == [
        seeds["a1"],
        seeds["a2"],
        seeds["a3"],
    ]
    assert _get(client, seeds, "&sort=ac_id") == [seeds["a1"], seeds["a2"], seeds["a3"]]


# ── facets (Phase 3 task 3) ──────────────────────────────────────────────────


def test_facets_shape_and_membership(client, seeds):
    r = client.get(FACETS)
    assert r.status_code == 200
    facets = r.get_json()["data"]
    expected = {
        "status",
        "asset_type",
        "manufacturer",
        "block",
        "department",
        "lifecycle",
        "location_status",
    }
    assert expected <= set(facets)
    for entries in facets.values():
        assert isinstance(entries, list)
        for entry in entries:
            # Location facets additionally carry the stable canonical id
            # (None for source-only values); every other facet is unchanged.
            assert set(entry) == {"value", "count"} or set(entry) == {"value", "count", "id"}
            assert isinstance(entry["count"], int) and entry["count"] >= 1

    def has(key, value):
        return any(e["value"] == value for e in facets[key])

    assert has("status", "WORKING")
    assert has("status", "FAULT")
    assert has("manufacturer", "V1 Maker A")
    assert has("asset_type", "Split")
    assert has("lifecycle", "DECOMMISSIONED")  # spans all rows, not active-only
    assert has("location_status", "PENDING")
    assert has("block", "Main Academic Block")
    assert has("department", "Computer Science")


# ── enriched detail (Phase 3 task 4) ─────────────────────────────────────────


def test_detail_sections_location_chain_and_qr_version(client, seeds):
    r = client.get(f"{LIST}/{seeds['a1']}")
    assert r.status_code == 200
    data = r.get_json()["data"]

    # Location chain (full, no extra calls) — the four values the detail
    # page renders: Building=block_name, Floor=floor_name,
    # Room=room_type (with room_name/id fallbacks), Department=department_name.
    assert data["room_id"] == "ROOM-102"
    assert data["room_name"] == "CSE Computer Lab 1"
    assert data["room_type"] == "Lab"
    assert data["floor_id"] == "MAIN-ACADEMIC-BLOCK-FLR-1"
    assert data["floor_name"] == "Floor 1"
    assert data["block_id"] == "MAIN-ACADEMIC-BLOCK"
    assert data["block_name"] == "Main Academic Block"
    assert data["department_id"] == "DEPT-COMPUTER-SCIENCE"
    assert data["department_name"] == "Computer Science"

    # Histories + attributes + QR payload version.
    assert any(h.get("remarks") == "p3 detail test" for h in data["maintenance_history"])
    assert any(m.get("to_room_name") == "CSE Computer Lab 1" for m in data["movement_history"])
    assert any(
        a["attribute_name"] == "warranty_expiry" and a["attribute_value"] == "2028-03-31"
        for a in data["attributes"]
    )
    assert data["qr_version"] == QR_VERSION
    assert data["operational_status"] == "WORKING"


def test_detail_resolves_uuid_code_and_ac_id(client, seeds):
    by_code = client.get(f"{LIST}/{seeds['asset_code']}")
    assert by_code.status_code == 200
    assert by_code.get_json()["data"]["ac_id"] == seeds["a1"]

    asset_uuid = client.get(f"{LIST}/{seeds['a1']}").get_json()["data"]["asset_uuid"]
    by_uuid = client.get(f"{LIST}/{asset_uuid}")
    assert by_uuid.status_code == 200
    assert by_uuid.get_json()["data"]["ac_id"] == seeds["a1"]

    missing = client.get(f"{LIST}/V1FLT-{seeds['tag']}-NOPE")
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "ASSET_NOT_FOUND"


# ── CSV export (Phase 3 task 7) ──────────────────────────────────────────────


def test_export_csv_shape_and_filters(client, seeds):
    r = client.get(f"{EXPORT}?format=csv&search=V1FLT-{seeds['tag']}")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert "attachment; filename=" in r.headers.get("Content-Disposition", "")

    text = r.data.decode("utf-8-sig")  # BOM present for Excel
    assert r.data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(text)))
    header, body = rows[0], rows[1:]
    assert "ac_id" in header and "operational_status" in header
    ac_id_col = header.index("ac_id")
    exported = [row[ac_id_col] for row in body]
    assert set(seeds[a] for a in ("a1", "a2", "a3")) <= set(exported)
    assert seeds["a4"] not in exported  # active-only default applies


def test_export_rejects_unknown_format(client, seeds):
    r = client.get(f"{EXPORT}?format=json")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "UNSUPPORTED_FORMAT"


# ── PATCH semantics (Phase 3 task 3: edit forms must be able to clear) ──────


def test_patch_explicit_null_clears_omitted_keeps(client, seeds):
    ac_id = seeds["a1"]
    r = client.patch(f"{LIST}/{ac_id}", json={"technician": "Pat", "remarks": "hello"})
    assert r.status_code == 200, r.get_json()
    data = client.get(f"{LIST}/{ac_id}").get_json()["data"]
    assert data["technician"] == "Pat"
    assert data["remarks"] == "hello"

    # Explicit null clears the column; omitted keys stay untouched.
    r = client.patch(f"{LIST}/{ac_id}", json={"technician": None})
    assert r.status_code == 200, r.get_json()
    data = client.get(f"{LIST}/{ac_id}").get_json()["data"]
    assert data["technician"] is None
    assert data["remarks"] == "hello"

    # No recognised keys at all -> NO_FIELDS.
    r = client.patch(f"{LIST}/{ac_id}", json={"nope": 1})
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "NO_FIELDS"
