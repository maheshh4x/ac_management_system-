"""Phase 7: the five GET /api/v1/reports/* endpoints.

Covers auth (401/403), the shared ``?from&to&block&format`` argument
handling, CSV downloads, the trend window (including the 24-month cap) and
the date-range semantics of each report.

Seeding rule (same as the other DB suites): every row these tests create
carries a ``P7-`` prefix / unique block id and is removed again afterwards,
so the role-matrix and shape assertions of the other suites stay stable.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import TEST_SECRET, TEST_SUPABASE_URL, TEST_USER_ID, AuthClient, make_token  # noqa: E402

REPORT_ENDPOINTS = (
    "/api/v1/reports/assets-by-block",
    "/api/v1/reports/assets-by-type",
    "/api/v1/reports/capacity",
    "/api/v1/reports/maintenance-trend",
    "/api/v1/reports/due-for-maintenance",
)


def _authed_client(monkeypatch, role, sub=TEST_USER_ID):
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
def admin(db_admin_client):
    return db_admin_client


@pytest.fixture()
def technician(monkeypatch, db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    return _authed_client(monkeypatch, "TECHNICIAN")


@pytest.fixture()
def viewer(monkeypatch, db):  # noqa: ARG001
    return _authed_client(monkeypatch, "VIEWER")


@pytest.fixture()
def report_data(db):  # noqa: ARG001
    """One throwaway block + room + three ACs + two work orders.

    AC-A: installed long ago, due in 3 days (the only "upcoming" unit).
    AC-B: installed recently, past-due (5 days ago) — excluded unless the
          caller widens ``?from``.
    AC-C: installed recently, no service date at all.
    """
    suffix = uuid.uuid4().hex[:8]
    ids = {
        "block": f"P7-BLOCK-{suffix}",
        "floor": f"P7-FLR-{suffix}",
        "room": f"P7-ROOM-{suffix}",
        "ac_a": f"P7-AC-{suffix}-A",
        "ac_b": f"P7-AC-{suffix}-B",
        "ac_c": f"P7-AC-{suffix}-C",
        "wo_now": None,
        "wo_old": None,
    }
    today = date.today()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO blocks (block_id, college_id, block_name)" " VALUES (%s, 'NITTTR-CHENNAI', %s)",
                (ids["block"], f"P7 Report Block {suffix}"),
            )
            cur.execute(
                "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)"
                " VALUES (%s, %s, 'Floor 1', 1)",
                (ids["floor"], ids["block"]),
            )
            cur.execute(
                "INSERT INTO rooms (room_id, floor_id, room_name, room_type)"
                " VALUES (%s, %s, 'P7 Room', 'Lab')",
                (ids["room"], ids["floor"]),
            )
            for ac_id, capacity, ac_type, installed, service in (
                (ids["ac_a"], 2.0, "Split", today - timedelta(days=400), today + timedelta(days=3)),
                (ids["ac_b"], 3.5, "Window", today - timedelta(days=10), today - timedelta(days=5)),
                (ids["ac_c"], 2.0, "Split", today - timedelta(days=10), None),
            ):
                cur.execute(
                    "INSERT INTO acs (ac_id, room_id, ac_type, ac_capacity_ton,"
                    " installation_date, next_service_date, operational_status)"
                    " VALUES (%s, %s, %s, %s, %s, %s, 'WORKING')",
                    (ac_id, ids["room"], ac_type, capacity, installed, service),
                )
            cur.execute(
                "INSERT INTO maintenance_work_orders"
                " (ac_id, maintenance_type, priority, status, scheduled_date)"
                " VALUES (%s, 'Preventive Maintenance', 'HIGH', 'ASSIGNED', %s)"
                " RETURNING id",
                (ids["ac_a"], today.isoformat()),
            )
            ids["wo_now"] = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO maintenance_work_orders"
                " (ac_id, maintenance_type, priority, status, scheduled_date,"
                " started_at, completed_at)"
                " VALUES (%s, 'Repair', 'LOW', 'COMPLETED', %s, %s, %s)"
                " RETURNING id",
                (
                    ids["ac_b"],
                    (today - timedelta(days=60)).isoformat(),
                    f"{(today - timedelta(days=60)).isoformat()} 09:00:00+00",
                    f"{(today - timedelta(days=60)).isoformat()} 11:00:00+00",
                ),
            )
            ids["wo_old"] = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    yield ids

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM maintenance_work_orders WHERE id = ANY(%s::uuid[])",
                ([[ids["wo_now"], ids["wo_old"]]],),
            )
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (f"P7-AC-{suffix}-%",))
            cur.execute("DELETE FROM rooms WHERE room_id = %s", (ids["room"],))
            cur.execute("DELETE FROM floors WHERE floor_id = %s", (ids["floor"],))
            cur.execute("DELETE FROM blocks WHERE block_id = %s", (ids["block"],))
        conn.commit()
    finally:
        conn.close()


def _error_code(resp):
    return (resp.get_json() or {}).get("error", {}).get("code")


def _qs(ids, **extra):
    parts = [f"block={ids['block']}"]
    parts += [f"{k}={v}" for k, v in extra.items()]
    return "&".join(parts)


# ── auth ─────────────────────────────────────────────────────────────────────


def test_reports_require_auth(client):
    for path in REPORT_ENDPOINTS:
        assert client.get(path).status_code == 401, path


def test_reports_forbidden_for_viewer(viewer, report_data):
    for path in REPORT_ENDPOINTS:
        r = viewer.get(path)
        assert r.status_code == 403, (path, r.get_json())
        assert _error_code(r) == "FORBIDDEN"


def test_reports_forbidden_for_technician(technician, report_data):
    for path in REPORT_ENDPOINTS:
        assert technician.get(path).status_code == 403, path


def test_report_csv_is_also_gated(admin, viewer, report_data):
    """CSV is not a back door: same VIEW_REPORTS requirement."""
    for path in REPORT_ENDPOINTS:
        assert viewer.get(f"{path}?format=csv").status_code == 403, path
    assert admin.get(f"{REPORT_ENDPOINTS[0]}?format=csv").status_code == 200


# ── shared argument validation ───────────────────────────────────────────────


def test_unsupported_format_rejected(admin, report_data):
    for path in REPORT_ENDPOINTS:
        r = admin.get(f"{path}?format=xml")
        assert r.status_code == 400, (path, r.get_json())
        assert _error_code(r) == "UNSUPPORTED_FORMAT"


def test_invalid_date_rejected(admin, report_data):
    for path in REPORT_ENDPOINTS:
        r = admin.get(f"{path}?from=not-a-date")
        assert r.status_code == 400, (path, r.get_json())
        assert _error_code(r) == "INVALID_DATE", path


def test_from_after_to_rejected(admin, report_data):
    today = date.today()
    r = admin.get(
        f"{REPORT_ENDPOINTS[0]}?from={(today + timedelta(days=10)).isoformat()}"
        f"&to={(today - timedelta(days=10)).isoformat()}"
    )
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_DATE"


# ── assets-by-block ──────────────────────────────────────────────────────────


def test_assets_by_block_shape_and_block_scope(admin, report_data):
    r = admin.get(f"/api/v1/reports/assets-by-block?{_qs(report_data)}")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["count"] == len(data["items"]) == 1
    row = data["items"][0]
    assert set(row) == {"block", "count", "capacity_ton"}
    assert row["count"] == 3  # AC-A + AC-B + AC-C
    assert row["capacity_ton"] == pytest.approx(7.5)


def test_assets_by_block_installation_date_filter(admin, report_data):
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    r = admin.get(f"/api/v1/reports/assets-by-block?{_qs(report_data, **{'from': cutoff})}")
    assert r.status_code == 200, r.get_json()
    row = r.get_json()["data"]["items"][0]
    assert row["count"] == 2  # AC-B + AC-C installed within the last 30 days


# ── assets-by-type / capacity ────────────────────────────────────────────────


def test_assets_by_type_groups(admin, report_data):
    r = admin.get(f"/api/v1/reports/assets-by-type?{_qs(report_data)}")
    assert r.status_code == 200, r.get_json()
    items = r.get_json()["data"]["items"]
    by_type = {row["type"]: row["count"] for row in items}
    assert by_type == {"Split": 2, "Window": 1}


def test_capacity_groups_only_known_capacities(admin, report_data):
    r = admin.get(f"/api/v1/reports/capacity?{_qs(report_data)}")
    assert r.status_code == 200, r.get_json()
    items = r.get_json()["data"]["items"]
    assert items == [
        {"capacity_ton": 2.0, "count": 2},
        {"capacity_ton": 3.5, "count": 1},
    ]


# ── due-for-maintenance ──────────────────────────────────────────────────────


def test_due_defaults_to_today_and_sorts_ascending(admin, report_data):
    r = admin.get(f"/api/v1/reports/due-for-maintenance?{_qs(report_data)}")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["limit"] == 500
    assert [row["ac_id"] for row in data["items"]] == [report_data["ac_a"]]
    assert data["count"] == 1


def test_due_earlier_from_pulls_past_due_units_in(admin, report_data):
    start = (date.today() - timedelta(days=10)).isoformat()
    r = admin.get(f"/api/v1/reports/due-for-maintenance?{_qs(report_data, **{'from': start})}")
    assert r.status_code == 200, r.get_json()
    items = r.get_json()["data"]["items"]
    # earliest first: AC-B (past due) then AC-A (due in 3 days)
    assert [row["ac_id"] for row in items] == [report_data["ac_b"], report_data["ac_a"]]
    assert items[0]["block"] == f"P7 Report Block {report_data['block'].split('-')[-1]}"


def test_due_to_upper_bound(admin, report_data):
    horizon = (date.today() + timedelta(days=1)).isoformat()
    r = admin.get(f"/api/v1/reports/due-for-maintenance?{_qs(report_data, **{'to': horizon})}")
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["items"] == []  # AC-A is due in 3 days


# ── maintenance-trend ────────────────────────────────────────────────────────


def test_trend_shape(admin, report_data):
    r = admin.get("/api/v1/reports/maintenance-trend")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert len(data["monthlyTrend"]) >= 6  # default window: current month + 5
    first = data["monthlyTrend"][0]
    assert set(first) == {"month", "fullMonth", "total", "Preventive", "Repair", "Emergency", "Inspection"}
    assert [p["name"] for p in data["priorityDistribution"]] == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    for key in (
        "total",
        "scheduled",
        "assigned",
        "in_progress",
        "completed",
        "cancelled",
        "mtdCount",
        "mttrDays",
        "overdueCount",
        "upcomingCount",
    ):
        assert key in data["stats"], sorted(data["stats"])


def test_trend_window_override(admin, report_data):
    today = date.today()
    start = (today - timedelta(days=70)).isoformat()
    r = admin.get(
        f"/api/v1/reports/maintenance-trend?{_qs(report_data, **{'from': start, 'to': today.isoformat()})}"
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert sum(m["total"] for m in data["monthlyTrend"]) == 2  # both seeded WOs
    assert data["stats"]["total"] == 2
    assert data["stats"]["completed"] == 1
    names = {p["name"]: p["value"] for p in data["priorityDistribution"]}
    assert names == {"LOW": 1, "MEDIUM": 0, "HIGH": 1, "CRITICAL": 0}


def test_trend_range_wider_than_cap_rejected(admin, report_data):
    r = admin.get(
        "/api/v1/reports/maintenance-trend?from=2020-01-01&to=2030-01-01" f"&block={report_data['block']}"
    )
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_RANGE"


def test_trend_block_scope_keeps_other_blocks_out(admin, report_data):
    today = date.today()
    start = (today - timedelta(days=70)).isoformat()
    unscoped = admin.get(f"/api/v1/reports/maintenance-trend?from={start}&to={today.isoformat()}")
    scoped = admin.get(
        f"/api/v1/reports/maintenance-trend?from={start}&to={today.isoformat()}"
        f"&block={report_data['block']}"
    )
    assert unscoped.status_code == scoped.status_code == 200
    # Scoping can only shrink (or keep) the count — never invent rows.
    assert scoped.get_json()["data"]["stats"]["total"] <= unscoped.get_json()["data"]["stats"]["total"]


# ── CSV downloads ────────────────────────────────────────────────────────────


def test_csv_download_headers_and_body(admin, report_data):
    r = admin.get(f"/api/v1/reports/assets-by-block?{_qs(report_data)}&format=csv")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    assert "attachment" in r.headers.get("Content-Disposition", "")
    body = r.get_data(as_text=True)
    assert body.startswith("﻿")  # Excel-safe BOM
    assert body.splitlines()[0].lstrip("﻿") == "block,count,capacity_ton"
    assert len(body.splitlines()) == 2  # header + our single block


def test_trend_csv_streams_month_series(admin, report_data):
    r = admin.get("/api/v1/reports/maintenance-trend?format=csv")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    header = r.get_data(as_text=True).splitlines()[0].lstrip("﻿")
    assert header == "month,fullMonth,total,Preventive,Repair,Emergency,Inspection"


def test_due_csv_uses_higher_cap(admin, report_data):
    r = admin.get(f"/api/v1/reports/due-for-maintenance?{_qs(report_data)}&format=csv")
    assert r.status_code == 200
    assert r.mimetype == "text/csv"
    lines = r.get_data(as_text=True).splitlines()
    assert lines[0].lstrip("﻿").startswith("ac_id,asset_code,manufacturer")
    assert len(lines) == 2  # header + AC-A
