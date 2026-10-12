"""Dashboard building-filter consistency (§8, §9, §16 dashboard test).

One seeded population, six assets across three block situations:

* Alpha (canonical block): A1 ASSIGNED in its room, A2 PARTIAL (block +
  department resolved, room garbage) + one work order on A1;
* Beta (source-only block text, no masters): B1 PARTIAL (department
  resolved), B0 PENDING (nothing resolves);
* SameName (two DISTINCT canonical blocks sharing one display name, in two
  colleges): S1/S2 ASSIGNED via explicit block_id (the shared name is
  ambiguous, so only the id can resolve them).

"All Buildings" (absent or literal ``All``) means NO filter: every
asset-derived visualization uses the same population. A selected building
filters KPIs, status, type, capacity, the asset table AND the maintenance
trend through the same effective-location population; source-only text stays
findable through search and the block filter; same-named canonical blocks
never merge (facets, by_block and capacity group by canonical id).
"""

from __future__ import annotations

import csv
import io
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from psycopg2.extras import RealDictCursor  # noqa: E402

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import db_required  # noqa: E402

pytestmark = db_required()

COLLEGE_ID = "NITTTR-CHENNAI"
DEPT_CS_NAME = "Computer Science"
DEPT_ADM_NAME = "Administration"

FIELDS = [
    "ac_id",
    "college_name",
    "block_name",
    "block_id",
    "floor_name",
    "floor_id",
    "department_name",
    "room_type",
    "room_number",
    "room_id",
    "ac_make_company",
    "unit_number",
    "capacity_ton",
    "type",
    "status",
]


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture()
def population(client):
    """Seed masters + import six assets + one work order; clean up after."""
    tag = uuid.uuid4().hex[:8]
    college2 = f"COLLEGE-2-{tag}"
    same_name = f"Same Name {tag}"
    beta = f"Beta Source {tag}"
    alpha_name = f"Alpha Block {tag}"
    ids = {
        "tag": tag,
        "college2": college2,
        "same_name": same_name,
        "beta": beta,
        "alpha": f"DBF-BLK-A-{tag}",
        "alpha_room": f"DBF-ROOM-A-{tag}",
        "a1": f"DBF-{tag}-A1",
        "a2": f"DBF-{tag}-A2",
        "b1": f"DBF-{tag}-B1",
        "b0": f"DBF-{tag}-B0",
        "s1": f"DBF-{tag}-S1",
        "s2": f"DBF-{tag}-S2",
        "wo": None,
    }
    blk_s1 = f"DBF-BLK-S1-{tag}"
    blk_s2 = f"DBF-BLK-S2-{tag}"
    ids["s1_block"] = blk_s1
    ids["s2_block"] = blk_s2
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO colleges (college_id, college_name) VALUES (%s, %s)",
                (college2, f"Second College {tag}"),
            )
            cur.execute(
                "INSERT INTO blocks (block_id, college_id, block_name) VALUES (%s, %s, %s)",
                (ids["alpha"], COLLEGE_ID, alpha_name),
            )
            cur.execute(
                "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)" " VALUES (%s, %s, %s, 1)",
                (f"DBF-FLR-A-{tag}", ids["alpha"], f"Alpha Floor {tag}"),
            )
            cur.execute(
                "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
                " VALUES (%s, %s, NULL, %s, 'Lab')",
                (ids["alpha_room"], f"DBF-FLR-A-{tag}", f"Alpha Room {tag}"),
            )
            for blk, college in ((blk_s1, COLLEGE_ID), (blk_s2, college2)):
                cur.execute(
                    "INSERT INTO blocks (block_id, college_id, block_name) VALUES (%s, %s, %s)",
                    (blk, college, same_name),
                )
                cur.execute(
                    "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)"
                    " VALUES (%s, %s, %s, 1)",
                    (f"{blk}-FLR", blk, f"Same Floor {tag}"),
                )
            for blk, room in ((blk_s1, ids["s1"]), (blk_s2, ids["s2"])):
                cur.execute(
                    "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
                    " VALUES (%s, %s, NULL, %s, 'Lab')",
                    (f"{blk}-ROOM", f"{blk}-FLR", f"S Room {room[-2:]} {tag}"),
                )
        conn.commit()
    finally:
        conn.close()

    rows = [
        {  # A1: ASSIGNED in the Alpha room.
            "ac_id": ids["a1"],
            "college_name": "",
            "block_name": alpha_name,
            "block_id": "",
            "floor_name": f"Alpha Floor {tag}",
            "floor_id": "",
            "department_name": DEPT_CS_NAME,
            "room_type": "Lab",
            "room_number": f"Alpha Room {tag}",
            "room_id": "",
            "ac_make_company": "Daikin",
            "unit_number": ids["a1"],
            "capacity_ton": "1.5",
            "type": "Split",
            "status": "Working",
        },
        {  # A2: PARTIAL — Alpha block + Administration, garbage room.
            "ac_id": ids["a2"],
            "college_name": "",
            "block_name": alpha_name,
            "block_id": "",
            "floor_name": "",
            "floor_id": "",
            "department_name": DEPT_ADM_NAME,
            "room_type": "",
            "room_number": f"Ghost Room {tag}",
            "room_id": "",
            "ac_make_company": "Daikin",
            "unit_number": ids["a2"],
            "capacity_ton": "2.0",
            "type": "Window",
            "status": "Working",
        },
        {  # B1: PARTIAL — source-only block, Administration resolves.
            "ac_id": ids["b1"],
            "college_name": "",
            "block_name": beta,
            "block_id": "",
            "floor_name": "",
            "floor_id": "",
            "department_name": DEPT_ADM_NAME,
            "room_type": "",
            "room_number": "",
            "room_id": "",
            "ac_make_company": "Daikin",
            "unit_number": ids["b1"],
            "capacity_ton": "1.5",
            "type": "Split",
            "status": "Fault",
        },
        {  # B0: PENDING — source-only block, unknown department.
            "ac_id": ids["b0"],
            "college_name": "",
            "block_name": beta,
            "block_id": "",
            "floor_name": "",
            "floor_id": "",
            "department_name": f"No Such Dept {tag}",
            "room_type": "",
            "room_number": "",
            "room_id": "",
            "ac_make_company": "Daikin",
            "unit_number": ids["b0"],
            "capacity_ton": "1.0",
            "type": "Split",
            "status": "Working",
        },
        {  # S1/S2: ASSIGNED via explicit block_id (shared name is ambiguous).
            "ac_id": ids["s1"],
            "college_name": "",
            "block_name": same_name,
            "block_id": blk_s1,
            "floor_name": f"Same Floor {tag}",
            "floor_id": "",
            "department_name": DEPT_CS_NAME,
            "room_type": "Lab",
            "room_number": f"S Room S1 {tag}",
            "room_id": "",
            "ac_make_company": "Daikin",
            "unit_number": ids["s1"],
            "capacity_ton": "1.5",
            "type": "Split",
            "status": "Working",
        },
        {
            "ac_id": ids["s2"],
            "college_name": "",
            "block_name": same_name,
            "block_id": blk_s2,
            "floor_name": f"Same Floor {tag}",
            "floor_id": "",
            "department_name": DEPT_CS_NAME,
            "room_type": "Lab",
            "room_number": f"S Room S2 {tag}",
            "room_id": "",
            "ac_make_company": "Daikin",
            "unit_number": ids["s2"],
            "capacity_ton": "1.5",
            "type": "Split",
            "status": "Working",
        },
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in FIELDS})
    response = client.post(
        "/api/v1/assets/import",
        data={"file": (io.BytesIO(buf.getvalue().encode("utf-8")), "dashboard-filter.csv")},
    )
    report = response.get_json()["data"]
    assert report["failed"] == 0, report["errors"]
    assert report["imported"] == 6, report

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO maintenance_work_orders"
                " (ac_id, maintenance_type, priority, status, scheduled_date)"
                " VALUES (%s, 'Preventive Maintenance', 'HIGH', 'ASSIGNED', %s)"
                " RETURNING id",
                (ids["a1"], date.today().isoformat()),
            )
            ids["wo"] = cur.fetchone()["id"]
        conn.commit()
    finally:
        conn.close()

    yield ids

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM maintenance_work_orders WHERE id = %s", (ids["wo"],))
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (f"DBF-{tag}-%",))
            cur.execute(
                "DELETE FROM rooms WHERE room_id LIKE %s",
                (f"DBF-%-{tag}%",),
            )
            cur.execute("DELETE FROM floors WHERE floor_id LIKE %s", (f"DBF-%-{tag}%",))
            cur.execute("DELETE FROM blocks WHERE block_id LIKE %s", (f"DBF-%-{tag}%",))
            cur.execute("DELETE FROM colleges WHERE college_id = %s", (college2,))
        conn.commit()
    finally:
        conn.close()


def _summary(client, query=""):
    response = client.get(f"/api/v1/dashboard/summary{query}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]


def _listed_total(client, query=""):
    response = client.get(f"/api/v1/assets{query}&page_size=1" if query else "/api/v1/assets?page_size=1")
    assert response.status_code == 200, response.get_json()
    return response.get_json()["pagination"]["total"]


def test_all_buildings_includes_everything(client, population):
    """All Buildings (absent or literal All) = NO building filter (§8)."""
    for query in ("", "?block=All"):
        data = _summary(client, query)
        assert data["kpis"]["total"] == 6, query
        assert sum(e["count"] for e in data["by_status"]) == 6, query
        assert _listed_total(client, query) == 6, query
    buckets = {(e["block"], e["block_id"]): e["count"] for e in data["by_block"]}
    tag = population["tag"]
    assert buckets[(f"Alpha Block {tag}", population["alpha"])] == 2
    assert buckets[(population["beta"], None)] == 2  # source-only text, no id
    same = [(k, v) for k, v in buckets.items() if k[0] == population["same_name"]]
    assert sorted(v for _, v in same) == [1, 1], "same-named canonical blocks never merge"
    assert {k[1] for k, _ in same} == {population["s1_block"], population["s2_block"]}


def test_selected_building_filters_every_visualization(client, population):
    """One building selected: KPIs, status, type, capacity, table AND the
    maintenance trend use the same effective population (§8, §9)."""
    query = f"?block={population['alpha']}"
    data = _summary(client, query)
    assert data["kpis"]["total"] == 2  # A1 + A2 (PARTIAL assets included)
    assert sum(e["count"] for e in data["by_status"]) == 2
    assert sum(e["count"] for e in data["by_type"]) == 2
    assert sum(e["count"] for e in data["by_capacity"]) == 2
    assert [e["block"] for e in data["by_block"]] == [f"Alpha Block {population['tag']}"]
    assert _listed_total(client, query) == 2

    month = date.today().strftime("%Y-%m")
    trend = client.get(f"/api/v1/reports/maintenance-trend{query}")
    assert trend.status_code == 200, trend.get_json()
    months = {e["fullMonth"]: e["total"] for e in trend.get_json()["data"]["monthlyTrend"]}
    assert months.get(month, 0) >= 1, "the Alpha work order is in the filtered trend"

    # The source-only building shows the legitimate zero-filled trend.
    beta_trend = client.get(f"/api/v1/reports/maintenance-trend?block={population['beta']}")
    assert beta_trend.status_code == 200, beta_trend.get_json()
    beta_months = beta_trend.get_json()["data"]["monthlyTrend"]
    assert len(beta_months) > 0 and all(e["total"] == 0 for e in beta_months)

    # Unfiltered trend still sees the work order.
    all_trend = client.get("/api/v1/reports/maintenance-trend")
    all_months = {e["fullMonth"]: e["total"] for e in all_trend.get_json()["data"]["monthlyTrend"]}
    assert all_months.get(month, 0) >= 1


def test_source_only_block_stays_findable(client, population):
    """Unresolved source-only blocks remain findable through the source value
    — via global search and via the block filter itself (§8, §10)."""
    beta = population["beta"]
    found = client.get(f"/api/v1/assets?search={beta}&page_size=100")
    assert found.status_code == 200, found.get_json()
    ids = {item["ac_id"] for item in found.get_json()["data"]}
    assert population["b1"] in ids and population["b0"] in ids
    assert population["a1"] not in ids

    filtered = client.get(f"/api/v1/assets?block={beta}&page_size=100")
    assert filtered.status_code == 200, filtered.get_json()
    assert {item["ac_id"] for item in filtered.get_json()["data"]} == {population["b1"], population["b0"]}


def test_facets_carry_stable_block_ids(client, population):
    facets = client.get("/api/v1/assets/facets")
    assert facets.status_code == 200, facets.get_json()
    by_value = {e["value"]: e["id"] for e in facets.get_json()["data"]["block"]}
    assert by_value[f"Alpha Block {population['tag']}"] == population["alpha"]
    assert by_value[population["beta"]] is None
