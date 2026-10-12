"""Phase 3: GET /api/v1/dashboard/summary — extended KPIs, groupings and
parity with the GET /assets filter set (Plan #35 DB-side aggregation)."""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_summary_shape(db_admin_client):
    r = db_admin_client.get("/api/v1/dashboard/summary")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]

    kpis = data["kpis"]
    for key in (
        "total",
        "working",
        "under_maint",
        "faulty",
        "offline",
        "unknown",
        "capacity",
        "departments",
        "rooms",
        "due_soon",
        "overdue",
        "completeness_pct",
    ):
        assert key in kpis, sorted(kpis)
    assert isinstance(kpis["capacity"], (int, float))
    assert 0 <= kpis["completeness_pct"] <= 100

    for key in ("by_status", "by_manufacturer", "by_type", "by_block", "by_capacity", "overdue"):
        assert isinstance(data[key], list), key
    for entry in data["by_status"]:
        assert set(entry) == {"status", "count"}
    for entry in data["by_type"]:
        assert set(entry) == {"type", "count"}
    for entry in data["by_block"]:
        assert set(entry) == {"block", "block_id", "count"}
    for entry in data["by_capacity"]:
        assert set(entry) == {"capacity_ton", "count"}

    # KPI totals are internally consistent.
    assert kpis["total"] == sum(e["count"] for e in data["by_status"])


def test_summary_filters_match_list_total(db_admin_client):
    """Same filter -> summary KPI total equals the list endpoint's total."""
    for query in ("?status=Working", "?search=ROOM", "?lifecycle=ACTIVE", "?location_status=PENDING"):
        summary = db_admin_client.get(f"/api/v1/dashboard/summary{query}")
        listing = db_admin_client.get(f"/api/v1/assets{query}&page_size=1")
        assert summary.status_code == 200, (query, summary.get_json())
        assert listing.status_code == 200, (query, listing.get_json())
        kpis_total = summary.get_json()["data"]["kpis"]["total"]
        list_total = listing.get_json()["pagination"]["total"]
        assert kpis_total == list_total, query


# ── Phase 7: role scoping + new KPIs ─────────────────────────────────────────

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import TEST_SECRET, TEST_SUPABASE_URL, AuthClient, make_token  # noqa: E402

TECH_P7 = "66666666-6666-6666-6666-666666666661"
MGR_P7 = "66666666-6666-6666-6666-666666666662"
VIEWER_P7 = "66666666-6666-6666-6666-666666666663"


def _authed_client(monkeypatch, role, sub):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    app = create_app()
    app.config["TESTING"] = True
    return AuthClient(app.test_client(), make_token(role, sub=sub))


@pytest.fixture()
def p7_roles(db, monkeypatch):  # noqa: ARG001
    """Short-lived technician/manager/viewer profiles, removed afterwards."""
    suffix = uuid.uuid4().hex[:6]
    ac_id = f"P7-DASH-{suffix}"
    block = "MAIN-ACADEMIC-BLOCK"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status, is_active, assigned_blocks)"
                " VALUES (%s, 'p7-tech@acms.test', 'P7 Tech', 'TECHNICIAN', 'APPROVED', TRUE, NULL)"
                " ON CONFLICT (id) DO UPDATE SET role = 'TECHNICIAN', status = 'APPROVED',"
                " is_active = TRUE, assigned_blocks = NULL",
                (TECH_P7,),
            )
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status, is_active, assigned_blocks)"
                " VALUES (%s, 'p7-mgr@acms.test', 'P7 Manager', 'FACILITY_MANAGER', 'APPROVED', TRUE, %s)"
                " ON CONFLICT (id) DO UPDATE SET role = 'FACILITY_MANAGER', status = 'APPROVED',"
                " is_active = TRUE, assigned_blocks = EXCLUDED.assigned_blocks",
                (MGR_P7, [block]),
            )
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status, is_active, assigned_blocks)"
                " VALUES (%s, 'p7-viewer@acms.test', 'P7 Viewer', 'VIEWER', 'APPROVED', TRUE, NULL)"
                " ON CONFLICT (id) DO UPDATE SET role = 'VIEWER', status = 'APPROVED',"
                " is_active = TRUE, assigned_blocks = NULL",
                (VIEWER_P7,),
            )
            # One AC + one work order so the technician has a block to scope to.
            cur.execute("INSERT INTO acs (ac_id, room_id) VALUES (%s, 'ROOM-102')", (ac_id,))
            cur.execute(
                "INSERT INTO maintenance_work_orders (ac_id, priority, status, technician_id)"
                " VALUES (%s, 'HIGH', 'IN_PROGRESS', %s)",
                (ac_id, TECH_P7),
            )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM maintenance_work_orders WHERE ac_id = %s", (ac_id,))
            cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            cur.execute(
                "DELETE FROM profiles WHERE id = ANY(%s::uuid[])",
                ([[TECH_P7, MGR_P7, VIEWER_P7]],),
            )
        conn.commit()
    finally:
        conn.close()


def test_summary_reports_new_kpis(db_admin_client):
    r = db_admin_client.get("/api/v1/dashboard/summary")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["scope"] == "all"
    assert isinstance(data["kpis"]["due_7d"], int)
    assert isinstance(data["kpis"]["mttr_hours"], (int, float))
    assert data["kpis"]["mttr_hours"] >= 0
    assert isinstance(data["capacity_by_block"], list)
    for entry in data["capacity_by_block"]:
        assert set(entry) == {"block", "capacity_ton", "count"}
    assert data["my_jobs"] is None  # admin is not a technician


def test_summary_viewer_gets_counts_only(p7_roles, monkeypatch):
    client = _authed_client(monkeypatch, "VIEWER", VIEWER_P7)
    r = client.get("/api/v1/dashboard/summary")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["scope"] == "counts"
    assert data["overdue"] == []  # no row-level detail for viewers
    assert data["my_jobs"] is None
    assert isinstance(data["kpis"]["total"], int)


def test_summary_technician_scopes_to_own_jobs(p7_roles, monkeypatch):
    client = _authed_client(monkeypatch, "TECHNICIAN", TECH_P7)
    r = client.get("/api/v1/dashboard/summary")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["scope"] == "mine"
    jobs = data["my_jobs"]
    assert isinstance(jobs, dict)
    assert jobs["open"] >= 1  # the IN_PROGRESS order seeded above
    assert jobs["in_progress"] == 1
    # Scoped to blocks where this technician holds work: MAIN block only.
    assert all(entry["block"] == "Main Academic Block" for entry in data["by_block"])
    assert data["kpis"]["total"] >= 1


def test_summary_manager_scopes_to_assigned_blocks(p7_roles, monkeypatch):
    client = _authed_client(monkeypatch, "FACILITY_MANAGER", MGR_P7)
    r = client.get("/api/v1/dashboard/summary")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["scope"] == "blocks"
    # Every counted block must be one of the assigned ones (only one here),
    # and the seed AC in ROOM-102 must be visible to this manager.
    assert data["by_block"] == [
        {"block": "Main Academic Block", "block_id": "MAIN-ACADEMIC-BLOCK", "count": data["kpis"]["total"]}
    ]
    assert data["kpis"]["total"] >= 1


def test_summary_include_detail_zero_downgrades_viewer(p7_roles, monkeypatch):
    client = _authed_client(monkeypatch, "VIEWER", VIEWER_P7)
    r = client.get("/api/v1/dashboard/summary?include_detail=0")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["scope"] == "counts"
    assert data["overdue"] == []
