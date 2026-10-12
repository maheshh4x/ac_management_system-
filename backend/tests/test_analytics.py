"""Dashboard analytics endpoint tests (§15 backend).

One seeded population (masters + six canonical imports + cleanup), then:
KPIs/year-range/install/company×year/departments/heatmap sections, the
parameterized explorer (dims × measures, validation, NULL-bucket rules),
global-filter narrowing (college/block/year/capacity/make), viewer access
with scope counts, empty results, and the statement budget (one request,
bounded round trips — the §10 fan-out rule).
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

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import auth, db_required, make_token  # noqa: E402
from tests.test_api_read_perf import _measure  # noqa: E402

# `_count_stmts` is imported as a pytest FIXTURE: the budget test injects it
# as a parameter, which ruff reads as a module-level redefinition.
# ruff: noqa: F811
from tests.test_api_read_perf import count_stmts as _count_stmts  # noqa: E402,F401

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
    "installation_year",
]


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture()
def population(client):
    """Six assets across Alpha (canonical), Beta (source-only) and two
    same-named canonical blocks — with years, capacities, makes, statuses."""
    tag = uuid.uuid4().hex[:8]
    college2 = f"COLLEGE-2-{tag}"
    beta = f"Beta Source {tag}"
    alpha_name = f"Alpha Block {tag}"
    same_name = f"Same Name {tag}"
    alpha = f"AN-BLK-A-{tag}"
    blk_s1 = f"AN-BLK-S1-{tag}"
    blk_s2 = f"AN-BLK-S2-{tag}"
    ids = {
        "tag": tag,
        "alpha": alpha,
        "beta": beta,
        "same_name": same_name,
        "s1_block": blk_s1,
        "s2_block": blk_s2,
        "a1": f"AN-{tag}-A1",
        "a2": f"AN-{tag}-A2",
        "b1": f"AN-{tag}-B1",
        "b0": f"AN-{tag}-B0",
        "s1": f"AN-{tag}-S1",
        "s2": f"AN-{tag}-S2",
    }
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO colleges (college_id, college_name) VALUES (%s, %s)",
                (college2, f"Second College {tag}"),
            )
            cur.execute(
                "INSERT INTO blocks (block_id, college_id, block_name) VALUES (%s, %s, %s)",
                (alpha, COLLEGE_ID, alpha_name),
            )
            cur.execute(
                "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)" " VALUES (%s, %s, %s, 1)",
                (f"AN-FLR-A-{tag}", alpha, f"Alpha Floor {tag}"),
            )
            cur.execute(
                "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
                " VALUES (%s, %s, NULL, %s, 'Lab')",
                (f"AN-ROOM-A-{tag}", f"AN-FLR-A-{tag}", f"Alpha Room {tag}"),
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
                cur.execute(
                    "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
                    " VALUES (%s, %s, NULL, %s, 'Lab')",
                    (f"{blk}-ROOM", f"{blk}-FLR", f"S Room {blk[-2:]} {tag}"),
                )
        conn.commit()
    finally:
        conn.close()

    def row(ac_id, **over):
        base = {
            "ac_id": ac_id,
            "college_name": "",
            "block_name": "",
            "block_id": "",
            "floor_name": "",
            "floor_id": "",
            "department_name": "",
            "room_type": "",
            "room_number": "",
            "room_id": "",
            "ac_make_company": "Daikin",
            "unit_number": ac_id,
            "capacity_ton": "1.5",
            "type": "Split",
            "status": "Working",
            "installation_year": "2021",
        }
        base.update(over)
        return base

    rows = [
        row(
            ids["a1"],
            block_name=alpha_name,
            floor_name=f"Alpha Floor {tag}",
            department_name=DEPT_CS_NAME,
            room_type="Lab",
            room_number=f"Alpha Room {tag}",
            capacity_ton="1.5",
            type="Split",
            status="Working",
            installation_year="2021",
        ),
        row(
            ids["a2"],
            block_name=alpha_name,
            department_name=DEPT_ADM_NAME,
            room_number=f"Ghost Room {tag}",
            capacity_ton="2.0",
            type="Window",
            status="Working",
            installation_year="2022",
        ),
        row(
            ids["b1"],
            block_name=beta,
            department_name=DEPT_ADM_NAME,
            capacity_ton="1.5",
            type="Split",
            status="Fault",
            installation_year="2022",
            ac_make_company="LG",
        ),
        row(
            ids["b0"],
            block_name=beta,
            department_name=f"No Such Dept {tag}",
            capacity_ton="1.0",
            type="Split",
            status="Working",
            installation_year="2015",
            ac_make_company="Samsung",
        ),
        row(
            ids["s1"],
            block_name=same_name,
            block_id=blk_s1,
            floor_name=f"Same Floor {tag}",
            department_name=DEPT_CS_NAME,
            room_type="Lab",
            room_number=f"S Room S1 {tag}",
            capacity_ton="1.5",
            type="Split",
            status="Working",
            installation_year="2024",
        ),
        row(
            ids["s2"],
            block_name=same_name,
            block_id=blk_s2,
            floor_name=f"Same Floor {tag}",
            department_name=DEPT_CS_NAME,
            room_type="Lab",
            room_number=f"S Room S2 {tag}",
            capacity_ton="2.5",
            type="Cassette",
            status="Maintenance",
            installation_year="2024",
            ac_make_company="Carrier",
        ),
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in FIELDS})
    response = client.post(
        "/api/v1/assets/import",
        data={"file": (io.BytesIO(buf.getvalue().encode("utf-8")), "analytics-seed.csv")},
    )
    report = response.get_json()["data"]
    assert report["failed"] == 0, report["errors"]
    assert report["imported"] == 6, report

    yield ids

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (f"AN-{tag}-%",))
            cur.execute("DELETE FROM rooms WHERE room_id LIKE %s", (f"AN-%-{tag}%",))
            cur.execute("DELETE FROM rooms WHERE room_id LIKE %s", (f"{blk_s1}-ROOM",))
            cur.execute("DELETE FROM rooms WHERE room_id LIKE %s", (f"{blk_s2}-ROOM",))
            cur.execute("DELETE FROM floors WHERE floor_id LIKE %s", (f"AN-%-{tag}%",))
            cur.execute("DELETE FROM floors WHERE floor_id LIKE %s", (f"{blk_s1}-FLR",))
            cur.execute("DELETE FROM floors WHERE floor_id LIKE %s", (f"{blk_s2}-FLR",))
            cur.execute("DELETE FROM blocks WHERE block_id LIKE %s", (f"AN-%-{tag}%",))
            cur.execute("DELETE FROM blocks WHERE block_id = %s", (blk_s1,))
            cur.execute("DELETE FROM blocks WHERE block_id = %s", (blk_s2,))
            cur.execute("DELETE FROM colleges WHERE college_id = %s", (college2,))
        conn.commit()
    finally:
        conn.close()


def _analytics(client, query=""):
    response = client.get(f"/api/v1/dashboard/analytics{query}")
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]


def test_kpis_year_range_and_core_sections(client, population):
    data = _analytics(client)
    assert data["scope"] == "all"
    assert data["kpis"] == {"total": 6, "working": 4, "offline_maintenance": 1, "total_capacity": 10.0}
    assert data["year_range"] == {"min": 2015, "max": 2024}
    assert data["install_by_year"] == [
        {"year": 2015, "count": 1},
        {"year": 2021, "count": 1},
        {"year": 2022, "count": 2},
        {"year": 2024, "count": 2},
    ]
    companies = {(r["company"], r["year"]): r["count"] for r in data["company_by_year"]}
    assert companies == {
        ("Samsung", 2015): 1,
        ("Daikin", 2021): 1,
        ("Daikin", 2022): 1,
        ("LG", 2022): 1,
        ("Daikin", 2024): 1,
        ("Carrier", 2024): 1,
    }
    departments = {r["department"]: r["count"] for r in data["departments"]}
    assert departments["Computer Science"] == 3
    assert departments["Administration"] == 2
    blocks = {(r["block"], r["block_id"]): r["count"] for r in data["blocks"]}
    tag = population["tag"]
    assert blocks[(f"Alpha Block {tag}", population["alpha"])] == 2
    assert blocks[(population["beta"], None)] == 2
    same = [(k, v) for k, v in blocks.items() if k[0] == population["same_name"]]
    assert sorted(v for _, v in same) == [1, 1], "same-named canonical blocks never merge"
    heat = {(r["department"], r["year"]): r["count"] for r in data["heatmap"]}
    assert heat[("Computer Science", 2021)] == 1
    assert heat[("Computer Science", 2024)] == 2
    assert heat[("Administration", 2022)] == 2
    assert {r["status"]: r["count"] for r in data["by_status"]} == {
        "WORKING": 4,
        "FAULT": 1,
        "MAINTENANCE": 1,
    }
    assert {r["type"]: r["count"] for r in data["by_type"]} == {
        "Split": 4,
        "Window": 1,
        "Cassette": 1,
    }
    assert {r["capacity_ton"]: r["count"] for r in data["by_capacity"]} == {
        1.0: 1,
        1.5: 3,
        2.0: 1,
        2.5: 1,
    }
    assert data["explorer"] is None  # no dims requested: section skipped


def test_explorer_dims_and_measures(client, population):
    data = _analytics(client, "?dim1=company&dim2=year")
    explorer = data["explorer"]
    assert (explorer["dim1"], explorer["dim2"], explorer["measure"]) == ("company", "year", "count")
    assert {(r["d1"], r["d2"]): r["value"] for r in explorer["rows"]} == {
        ("Samsung", 2015): 1,
        ("Daikin", 2021): 1,
        ("Daikin", 2022): 1,
        ("LG", 2022): 1,
        ("Daikin", 2024): 1,
        ("Carrier", 2024): 1,
    }

    # Same dimension twice collapses to a single grouping.
    collapsed = _analytics(client, "?dim1=type&dim2=type")["explorer"]
    assert collapsed["dim2"] is None
    assert {r["d1"]: r["value"] for r in collapsed["rows"]} == {
        "Split": 4,
        "Window": 1,
        "Cassette": 1,
    }

    # Average age honors installation years dynamically (no hardcoding).
    year_now = date.today().year
    ages = _analytics(client, "?dim1=department&measure=avg_age")["explorer"]["rows"]
    by_dept = {r["d1"]: r["value"] for r in ages}
    assert by_dept["Computer Science"] == pytest.approx((year_now - 2021 + 2 * (year_now - 2024)) / 3)
    assert by_dept["Administration"] == pytest.approx(year_now - 2022)

    # Total capacity per company.
    caps = _analytics(client, "?dim1=company&measure=total_capacity")["explorer"]["rows"]
    assert {r["d1"]: r["value"] for r in caps} == {
        "Daikin": pytest.approx(5.0),
        "LG": pytest.approx(1.5),
        "Samsung": pytest.approx(1.0),
        "Carrier": pytest.approx(2.5),
    }


def test_explorer_validation(client, population):
    assert client.get("/api/v1/dashboard/analytics?dim1=nope").status_code == 400
    assert client.get("/api/v1/dashboard/analytics?dim1=company&measure=nope").status_code == 400
    assert client.get("/api/v1/dashboard/analytics?installed_from=2024&installed_to=2020").status_code == 400
    assert client.get("/api/v1/dashboard/analytics?installed_from=soon").status_code == 400
    assert client.get("/api/v1/dashboard/analytics?capacity_ton=lots").status_code == 400


def test_global_filters_narrow_every_section(client, population):
    alpha = _analytics(client, f"?block={population['alpha']}")
    assert alpha["kpis"]["total"] == 2
    assert alpha["year_range"] == {"min": 2021, "max": 2022}

    recent = _analytics(client, "?installed_from=2022")
    assert recent["kpis"]["total"] == 4
    old = _analytics(client, "?installed_to=2021")
    assert old["kpis"]["total"] == 2

    tons = _analytics(client, "?capacity_ton=1.5")
    assert tons["kpis"]["total"] == 3

    maker = _analytics(client, "?manufacturer=Daikin")
    assert maker["kpis"]["total"] == 3

    # S2 lives in the second college and B1/B0 have no college at all:
    # the college filter keeps exactly the three NITTTR-CHENNAI assets.
    home = _analytics(client, f"?college={COLLEGE_ID}")
    assert home["kpis"]["total"] == 3

    # Filters compose with the explorer.
    scoped = _analytics(client, f"?block={population['alpha']}&dim1=company")
    assert {(r["d1"]): r["value"] for r in scoped["explorer"]["rows"]} == {"Daikin": 2}


def test_empty_result_is_stable(client, population):
    data = _analytics(client, "?block=NOPE&dim1=company&dim2=year")
    assert data["kpis"] == {"total": 0, "working": 0, "offline_maintenance": 0, "total_capacity": 0.0}
    assert data["year_range"] == {"min": None, "max": None}
    assert data["install_by_year"] == []
    assert data["company_by_year"] == []
    assert data["departments"] == []
    assert data["heatmap"] == []
    assert data["explorer"]["rows"] == []


def test_viewer_gets_aggregates_with_counts_scope(client, population):
    viewer_headers = auth(make_token("VIEWER", sub=str(uuid.uuid4())))
    response = client.get("/api/v1/dashboard/analytics", headers=viewer_headers)
    assert response.status_code == 200, response.get_json()
    data = response.get_json()["data"]
    assert data["scope"] == "counts"
    assert data["kpis"]["total"] == 6


def test_analytics_statement_budget(client, population, _count_stmts):
    statements, wall_ms = _measure(client, _count_stmts, "/api/v1/dashboard/analytics?dim1=company&dim2=year")
    assert statements <= 12, statements
    print(f"\nanalytics (explorer) statements={statements} wall_ms={wall_ms:.1f}")
    statements, wall_ms = _measure(client, _count_stmts, "/api/v1/dashboard/analytics")
    assert statements <= 11, statements
    print(f"analytics (sections) statements={statements} wall_ms={wall_ms:.1f}")


def test_analytics_reports_distinct_capacities(client, population):
    data = _analytics(client)
    assert data["capacities"] == [1.0, 1.5, 2.0, 2.5]
