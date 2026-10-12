"""Dashboard/data-flow regressions: the UI "All" sentinel, UNKNOWN status
aggregation, and the maintenance-trend zero state.

Covers the verified bug chain: ``useAssetQueryState.parse()`` defaults every
filter to ``"All"``, ``toAssetQuery`` passed it through verbatim, and
``_build_filters`` turned it into a literal predicate
(``operational_status ILIKE 'All'``) — so /assets and /dashboard/summary
returned zero rows on a database that held assets.

Also pins (against the schema in migrations/):
* ``operational_status`` vocabulary includes UNKNOWN (007 CHECK) — it must
  survive KPI/by_status aggregation,
* ``maintenance_work_orders`` drives the trend, NOT the imported AC records:
  with no work orders the endpoint still answers 200 with a zero-filled
  month window and the exact FE response shape,
* ``?block=All`` is not a block scope (reports),
* ``my_jobs.escalated`` counts ``escalated_at`` — ``status = 'ESCALATED'``
  can never match (002 CHECK: escalate() keeps the status, 008 adds the
  column).
"""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.v1.assets import _build_filters, clean_filter  # noqa: E402
from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import TEST_SECRET, TEST_SUPABASE_URL, AuthClient, make_token  # noqa: E402

LIST = "/api/v1/assets"
SUMMARY = "/api/v1/dashboard/summary"
TREND = "/api/v1/reports/maintenance-trend"

BLOCK = "MAIN-ACADEMIC-BLOCK"  # shared campus seed (conftest)


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture()
def dash_seed(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """Three uniquely-tagged active assets (WORKING / FAULT / UNKNOWN) and one
    work order (scheduled 2000-03-15, in-block) so list, summary and trend
    assertions are deterministic regardless of other tests' leftovers."""
    tag = uuid.uuid4().hex[:8]
    prefix = f"DASHALL-{tag}"
    ids = {"w": f"{prefix}-W", "f": f"{prefix}-F", "u": f"{prefix}-U"}
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            rows = (
                (ids["w"], "DASHALL Maker", "Split", "ROOM-102", "ASSIGNED", "WORKING"),
                (ids["f"], "DASHALL Maker", "Window", "ROOM-105", "ASSIGNED", "FAULT"),
                (ids["u"], "DASHALL Maker", "Split", None, "PENDING", "UNKNOWN"),
            )
            for ac_id, maker, ac_type, room, loc, status in rows:
                cur.execute(
                    "INSERT INTO acs (ac_id, ac_make_company, ac_type, room_id,"
                    " location_status, operational_status)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (ac_id, maker, ac_type, room, loc, status),
                )
            cur.execute(
                "INSERT INTO maintenance_work_orders"
                " (ac_id, priority, status, scheduled_date)"
                " VALUES (%s, 'HIGH', 'SCHEDULED', '2000-03-15')",
                (ids["w"],),
            )
        conn.commit()
    finally:
        conn.close()
    yield {**ids, "prefix": prefix, "tag": tag}

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM maintenance_work_orders WHERE ac_id = ANY(%s)",
                ([ids["w"], ids["f"], ids["u"]],),
            )
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (f"{prefix}%",))
        conn.commit()
    finally:
        conn.close()


def _tagged(payload, prefix):
    """Only this fixture's rows — the shared DB may hold leftovers."""
    return sorted(str(item["ac_id"]) for item in payload if str(item["ac_id"]).startswith(prefix))


ALL_SENTINELS = (
    "status=All&block=All&department=All&asset_type=All&floor=All"
    "&room=All&manufacturer=All&lifecycle=All&location_status=All&due_before=All"
)


# ── 1+3+4: "All" filters are omitted (FE) and never literal (BE) ────────────


def test_list_returns_rows_when_every_filter_is_the_all_sentinel(client, dash_seed):
    """The dashboard's default query must retrieve the imported assets.

    Pre-fix: status=All -> operational_status ILIKE 'All' (and due_before=All
    -> a date-cast 500); the list came back empty/failed on real data."""
    base = client.get(f"{LIST}?search={dash_seed['prefix']}&page_size=100")
    assert base.status_code == 200, base.get_json()
    assert _tagged(base.get_json()["data"], dash_seed["prefix"]) == sorted(
        dash_seed[k] for k in ("w", "f", "u")
    )

    r = client.get(f"{LIST}?search={dash_seed['prefix']}&page_size=100&{ALL_SENTINELS}")
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["success"] is True
    assert body["pagination"]["total"] == 3
    assert _tagged(body["data"], dash_seed["prefix"]) == sorted(dash_seed[k] for k in ("w", "f", "u"))


def test_summary_ignores_all_sentinels_and_keeps_unknown(client, dash_seed):
    plain = client.get(f"{SUMMARY}?search={dash_seed['prefix']}")
    assert plain.status_code == 200, plain.get_json()
    alls = client.get(f"{SUMMARY}?search={dash_seed['prefix']}&{ALL_SENTINELS}")
    assert alls.status_code == 200, alls.get_json()

    plain_data = plain.get_json()["data"]
    all_data = alls.get_json()["data"]

    # Same slice with or without the sentinel -> the bug is gone.
    assert all_data["kpis"]["total"] == plain_data["kpis"]["total"] == 3
    assert all_data["kpis"] == plain_data["kpis"]
    assert all_data["by_status"] == plain_data["by_status"]

    # UNKNOWN is a persisted state (007) and must be aggregated, not dropped.
    kpis = all_data["kpis"]
    assert (kpis["working"], kpis["faulty"], kpis["unknown"]) == (1, 1, 1)
    by_status = {e["status"]: e["count"] for e in all_data["by_status"]}
    assert by_status.get("UNKNOWN") == 1
    assert sum(by_status.values()) == 3


def test_specific_filters_still_apply(client, dash_seed):
    """The sentinel fix must not weaken legitimate filters."""
    p = dash_seed["prefix"]

    working = client.get(f"{LIST}?search={p}&status=Working&page_size=100")
    assert working.status_code == 200
    assert _tagged(working.get_json()["data"], p) == [dash_seed["w"]]

    # Canonical UNKNOWN is filterable as itself (never folded into another).
    unknown = client.get(f"{LIST}?search={p}&status=UNKNOWN&page_size=100")
    assert unknown.status_code == 200
    assert _tagged(unknown.get_json()["data"], p) == [dash_seed["u"]]

    in_block = client.get(f"{LIST}?search={p}&block={BLOCK}&page_size=100")
    assert in_block.status_code == 200
    # PENDING asset has no room -> no block; WORKING+FAULT do.
    assert _tagged(in_block.get_json()["data"], p) == sorted([dash_seed["w"], dash_seed["f"]])

    window = client.get(f"{LIST}?search={p}&asset_type=Window&page_size=100")
    assert window.status_code == 200
    assert _tagged(window.get_json()["data"], p) == [dash_seed["f"]]

    by_maker = client.get(f"{LIST}?search={p}&manufacturer=DASHALL&page_size=100")
    assert by_maker.status_code == 200
    assert _tagged(by_maker.get_json()["data"], p) == sorted(dash_seed[k] for k in ("w", "f", "u"))


def test_build_filters_treats_literal_all_as_no_filter():
    """Unit: every dimension's sentinel vanishes; only the active default stays."""
    where, params = _build_filters(
        {
            "status": "All",
            "block": "All",
            "department": "All",
            "asset_type": "All",
            "manufacturer": "All",
            "floor": "All",
            "room": "All",
            "lifecycle": "All",
            "location_status": "All",
            "search": "All",
            "due_before": "All",
        }
    )
    assert where == ["COALESCE(a.is_active, TRUE) = TRUE"]
    assert params == []

    # Whitespace-padded sentinel (the API strips it anyway) is still "All".
    where, params = _build_filters({"status": "  All  "})
    assert where == ["COALESCE(a.is_active, TRUE) = TRUE"]
    assert params == []


def test_build_filters_preserves_legitimate_values():
    where, params = _build_filters({"status": "Working"})
    assert where == [
        "COALESCE(a.is_active, TRUE) = TRUE",
        "a.operational_status = %s",
    ]
    assert params == ["WORKING"]

    # Only the exact sentinel is dropped — a block whose name starts with
    # "All" must still filter. The clause matches canonical id, canonical
    # name AND the preserved source text (§7), so unresolved rows stay
    # findable by the block text the import supplied.
    where, params = _build_filters({"block": "All Saints Block", "search": "Allergy"})
    assert where[0] == "COALESCE(a.is_active, TRUE) = TRUE"
    assert "(b.block_id ILIKE %s OR b.block_name ILIKE %s OR al.source_block_value ILIKE %s)" in where
    assert params[0:3] == ["%All Saints Block%"] * 3
    assert "%Allergy%" in params

    assert clean_filter("All") == ""
    assert clean_filter(None) == ""
    assert clean_filter("  ") == ""
    assert clean_filter("Alley") == "Alley"


# ── 6: maintenance trend is work-order based and zero-fills honestly ─────────


def test_trend_zero_filled_with_no_work_orders(client):
    """No work orders in the window -> 200, exact FE shape, all months zero.

    The trend never fabricates rows from imported AC records."""
    r = client.get(f"{TREND}?from=1999-01-01&to=1999-06-01")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]

    monthly = data["monthlyTrend"]
    assert len(monthly) == 6  # zero-filled window, not an empty list
    assert [m["fullMonth"] for m in monthly] == [
        "1999-01",
        "1999-02",
        "1999-03",
        "1999-04",
        "1999-05",
        "1999-06",
    ]
    for month in monthly:
        assert set(month) == {
            "month",
            "fullMonth",
            "total",
            "Preventive",
            "Repair",
            "Emergency",
            "Inspection",
        }
        assert month["total"] == 0
        assert month["Preventive"] == month["Repair"] == month["Emergency"] == 0

    # Response shape exactly as useMaintenanceTrend() types it.
    assert [p["name"] for p in data["priorityDistribution"]] == [
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
    ]
    assert all(p["value"] == 0 for p in data["priorityDistribution"])
    stats = data["stats"]
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
        assert key in stats, sorted(stats)
        assert stats[key] == 0


def test_trend_block_all_is_not_a_scope(client, dash_seed):
    """?block=All must behave exactly like no block (same rows, same stats)."""
    window = "from=2000-01-01&to=2000-06-01"  # contains the seeded work order

    plain = client.get(f"{TREND}?{window}")
    assert plain.status_code == 200, plain.get_json()
    plain_data = plain.get_json()["data"]
    assert plain_data["stats"]["total"] >= 1  # our 2000-03-15 job

    all_block = client.get(f"{TREND}?{window}&block=All")
    assert all_block.status_code == 200, all_block.get_json()
    assert all_block.get_json()["data"] == plain_data

    # A real block scope still works and keeps our in-block job.
    scoped = client.get(f"{TREND}?{window}&block={BLOCK}")
    assert scoped.status_code == 200, scoped.get_json()
    scoped_data = scoped.get_json()["data"]
    assert scoped_data["stats"]["total"] >= 1
    assert scoped_data["stats"]["total"] <= plain_data["stats"]["total"]


# ── 8: my_jobs.escalated counts escalated_at, not an impossible status ───────


def test_technician_my_jobs_escalated_counts_escalated_at(db, monkeypatch):  # noqa: ARG001
    """Migration 002's CHECK has no 'ESCALATED' status; escalate() writes
    escalated_at (008). The KPI must count real escalations, not 0 forever."""
    tech_id = str(uuid.uuid4())
    tag = uuid.uuid4().hex[:8]
    ac_id = f"ESC-{tag}"
    wo_id = None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status, is_active, assigned_blocks)"
                " VALUES (%s, %s, 'Escalation Tech', 'TECHNICIAN', 'APPROVED', TRUE, NULL)",
                (tech_id, f"esc-{tag}@acms.test"),
            )
            cur.execute(
                "INSERT INTO acs (ac_id, room_id, operational_status)" " VALUES (%s, 'ROOM-102', 'WORKING')",
                (ac_id,),
            )
            cur.execute(
                "INSERT INTO maintenance_work_orders"
                " (ac_id, priority, status, technician_id, escalated_at, escalation_reason)"
                " VALUES (%s, 'CRITICAL', 'ASSIGNED', %s, NOW(), 'slipped SLA') RETURNING id",
                (ac_id, tech_id),
            )
            wo_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    try:
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
        tech = AuthClient(app.test_client(), make_token("TECHNICIAN", sub=tech_id))

        r = tech.get(SUMMARY)
        assert r.status_code == 200, r.get_json()
        my_jobs = r.get_json()["data"]["my_jobs"]
        assert my_jobs is not None, "technicians get job detail"
        assert my_jobs["escalated"] == 1
        assert my_jobs["open"] == 1  # ASSIGNED is still an open status
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                if wo_id is not None:
                    cur.execute("DELETE FROM maintenance_work_orders WHERE id = %s", (wo_id,))
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
                cur.execute("DELETE FROM profiles WHERE id = %s", (tech_id,))
            conn.commit()
        finally:
            conn.close()
