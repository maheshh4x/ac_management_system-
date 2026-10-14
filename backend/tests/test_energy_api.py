"""Per-AC and aggregate energy estimates + weather-now endpoint coverage.

Weather is never live in this suite: ``weather.get_hourly_series`` is
monkeypatched per test (success series or WeatherUnavailable). Session
timestamps are fixed UTC instants inserted directly, so every kWh figure
below is hand-computable.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.services import weather as wx_module  # noqa: E402
from tests.conftest import auth, make_token  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="needs a live database — set TEST_DATABASE_URL"
)


def _code(r) -> str:
    return ((r.get_json() or {}).get("error") or {}).get("code", "<no error envelope>")


@pytest.fixture()
def profiles(db):
    created: list[str] = []

    def add(role="VIEWER", status="APPROVED", **extra):
        pid = str(uuid.uuid4())
        cols = {"id": pid, "role": role, "status": status,
                "email": f"{pid[:8]}@acms.test", "name": "Energy Test"}
        cols.update(extra)
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                names = ", ".join(cols)
                marks = ", ".join(["%s"] * len(cols))
                cur.execute(f"INSERT INTO profiles ({names}) VALUES ({marks})", tuple(cols.values()))
            conn.commit()
        finally:
            conn.close()
        created.append(pid)
        return pid

    yield add

    if not created:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE user_id = ANY(%s::uuid[])", (created,))
            cur.execute("DELETE FROM profiles WHERE id = ANY(%s::uuid[])", (created,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def spec_asset(db):
    """Asset with known specs: 1.5 kW rated, 09:15–12:45 session (3.5 h)."""
    ac_id = f"NRG-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO acs (ac_id, room_id, ac_make_company, power_rating_kw,"
                " ac_capacity_ton, ac_star_rating) VALUES (%s, 'ROOM-102', 'Daikin', 1.5, 1.5, 5)",
                (ac_id,),
            )
            cur.execute(
                "INSERT INTO ac_usage_sessions (ac_id, started_at, ended_at) VALUES (%s, %s, %s)",
                (ac_id, datetime(2026, 1, 5, 9, 15, tzinfo=timezone.utc),
                 datetime(2026, 1, 5, 12, 45, tzinfo=timezone.utc)),
            )
        conn.commit()
    finally:
        conn.close()
    yield ac_id
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
        conn.commit()
    finally:
        conn.close()


def _tok(role, sub):
    return auth(make_token(role, sub=sub))


def _no_weather(monkeypatch):
    def boom():
        raise wx_module.WeatherUnavailable("provider down in test")

    monkeypatch.setattr(wx_module, "get_hourly_series", boom)


def _series_weather(monkeypatch):
    base = datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)
    series = [
        {"time_utc": base.replace(hour=h), "temp_c": 30.0, "humidity_pct": 70,
         "dew_point_c": 24.0, "apparent_c": 32.0, "rain_mm": 0.0, "is_forecast": False}
        for h in range(0, 12)
    ]

    def fake():
        return series, {"source": "Open-Meteo", "fetched_at": base.isoformat()}, False

    monkeypatch.setattr(wx_module, "get_hourly_series", fake)


# ── per-AC endpoint ──────────────────────────────────────────────────────────


def test_per_ac_baseline_matches_worked_example(secret_client, profiles, spec_asset, monkeypatch):
    _no_weather(monkeypatch)
    viewer = _tok("VIEWER", profiles("VIEWER"))
    r = secret_client.get(
        f"/api/v1/assets/{spec_asset}/energy?from=2026-01-05&to=2026-01-05&load_factor=0.75",
        headers=viewer,
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["data"]
    assert body["ac_id"] == spec_asset
    assert body["model"]["version"] == "baseline-v1"
    assert body["model"]["load_factor"] == 0.75
    assert body["totals"]["runtime_hours"] == pytest.approx(3.5)
    assert body["totals"]["baseline_kwh"] == pytest.approx(3.9375)  # 1.5 * 3.5 * 0.75
    assert body["totals"]["upper_bound_kwh"] == pytest.approx(5.25)  # 1.5 * 3.5
    assert body["quality"] == "BASIC_ENERGY_ESTIMATE"
    assert body["cost"] is None  # no tariff configured
    assert body["cost_unavailable_reason"] is not None
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["status"] == "closed"
    assert body["daily"] == [
        {"date": "2026-01-05", "runtime_hours": pytest.approx(3.5),
         "baseline_kwh": pytest.approx(3.9375), "sessions": 1}
    ]


def test_per_ac_second_example_and_validation(secret_client, profiles, spec_asset, monkeypatch):
    _no_weather(monkeypatch)
    viewer = _tok("VIEWER", profiles("VIEWER"))
    # load_factor=0.60 with the fixture's 1.5 kW / 3.5 h shape
    r = secret_client.get(
        f"/api/v1/assets/{spec_asset}/energy?from=2026-01-05&to=2026-01-05&load_factor=0.60",
        headers=viewer,
    )
    assert r.get_json()["data"]["totals"]["baseline_kwh"] == pytest.approx(1.5 * 3.5 * 0.60)
    # invalid load factors are 400, never silent clamping
    r = secret_client.get(f"/api/v1/assets/{spec_asset}/energy?load_factor=banana", headers=viewer)
    assert r.status_code == 400
    r = secret_client.get(f"/api/v1/assets/{spec_asset}/energy?load_factor=2", headers=viewer)
    assert r.status_code == 400
    # unknown asset / bad dates / anonymous
    r = secret_client.get("/api/v1/assets/NOPE/energy", headers=viewer)
    assert r.status_code == 404
    r = secret_client.get(f"/api/v1/assets/{spec_asset}/energy?from=xx", headers=viewer)
    assert r.status_code == 400
    assert secret_client.get(f"/api/v1/assets/{spec_asset}/energy").status_code == 401


def test_per_ac_capacity_derived_estimate_is_labelled(monkeypatch, secret_client, profiles, db):
    """Decision 1 (Option A): no rated power + capacity present → provisional
    capacity-derived estimate with its own model version and quality label —
    never presented as a rated-power figure, never written to the DB."""
    _no_weather(monkeypatch)
    ac_id = f"NRG-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO acs (ac_id, room_id, ac_capacity_ton) VALUES (%s, 'ROOM-102', 2.0)", (ac_id,)
            )
            cur.execute(
                "INSERT INTO ac_usage_sessions (ac_id, started_at, ended_at) VALUES (%s, %s, %s)",
                (ac_id, datetime(2026, 1, 5, 9, 15, tzinfo=timezone.utc),
                 datetime(2026, 1, 5, 12, 45, tzinfo=timezone.utc)),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        viewer = _tok("VIEWER", profiles("VIEWER"))
        r = secret_client.get(
            f"/api/v1/assets/{ac_id}/energy?from=2026-01-05&to=2026-01-05&load_factor=0.75",
            headers=viewer,
        )
        assert r.status_code == 200, r.get_json()
        body = r.get_json()["data"]
        # 2.0 ton × 1.2 kW/ton = 2.4 kW derived input; 2.4 × 3.5 × 0.75 = 6.3 kWh.
        assert body["model"]["version"] == "baseline-v1-capacity-derived"
        assert body["model"]["power_basis"] == "capacity-derived"
        assert body["totals"]["baseline_kwh"] == pytest.approx(6.3)
        assert body["quality"] == "BASIC_ENERGY_ESTIMATE_CAPACITY_DERIVED"
        assert body["sessions"][0]["power_basis"] == "capacity-derived"
        assert any("not a manufacturer rating" in reason for reason in body["quality_reasons"])
        # The derived power must never be persisted as a rating.
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT power_rating_kw FROM acs WHERE ac_id = %s", (ac_id,))
                assert cur.fetchone()[0] is None
        finally:
            conn.close()
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            conn.commit()
        finally:
            conn.close()


def test_per_ac_missing_power_is_unavailable_not_zero(secret_client, profiles, db, monkeypatch):
    _no_weather(monkeypatch)
    ac_id = f"NRG-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, room_id) VALUES (%s, 'ROOM-102')", (ac_id,))
            cur.execute(
                "INSERT INTO ac_usage_sessions (ac_id, started_at, ended_at) VALUES (%s, %s, %s)",
                (ac_id, datetime(2026, 1, 5, 9, 15, tzinfo=timezone.utc),
                 datetime(2026, 1, 5, 12, 45, tzinfo=timezone.utc)),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        viewer = _tok("VIEWER", profiles("VIEWER"))
        r = secret_client.get(f"/api/v1/assets/{ac_id}/energy?from=2026-01-05&to=2026-01-05", headers=viewer)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()["data"]
        assert body["quality"] == "RUNTIME_RECORDED"
        assert body["totals"]["runtime_hours"] == pytest.approx(3.5)
        assert body["totals"]["baseline_kwh"] == 0.0
        assert any("power" in reason.lower() for reason in body["quality_reasons"])
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            conn.commit()
        finally:
            conn.close()


def test_per_ac_no_sessions_quality_and_open_provisional(secret_client, profiles, db, monkeypatch):
    _no_weather(monkeypatch)
    ac_id = f"NRG-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO acs (ac_id, room_id, power_rating_kw) VALUES (%s, 'ROOM-102', 2.0)", (ac_id,)
            )
        conn.commit()
    finally:
        conn.close()
    try:
        viewer = _tok("VIEWER", profiles("VIEWER"))
        r = secret_client.get(f"/api/v1/assets/{ac_id}/energy", headers=viewer)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["data"]["quality"] == "NO_USAGE_RECORDED"

        tech = _tok("TECHNICIAN", profiles("TECHNICIAN"))
        r = secret_client.post(f"/api/v1/assets/{ac_id}/sessions/start", json={}, headers=tech)
        assert r.status_code == 201, r.get_json()
        r = secret_client.get(f"/api/v1/assets/{ac_id}/energy", headers=viewer)
        body = r.get_json()["data"]
        assert body["sessions"][0]["status"] == "open"
        r = secret_client.post(f"/api/v1/assets/{ac_id}/sessions/stop", json={}, headers=tech)
        assert r.status_code == 200, r.get_json()
        before = secret_client.get(f"/api/v1/assets/{ac_id}/energy", headers=viewer).get_json()["data"]
        after = secret_client.get(f"/api/v1/assets/{ac_id}/energy", headers=viewer).get_json()["data"]
        # Closing replaces the provisional contribution: totals do not grow on re-read.
        assert before["totals"]["runtime_hours"] == pytest.approx(after["totals"]["runtime_hours"])
        assert before["totals"]["baseline_kwh"] == pytest.approx(after["totals"]["baseline_kwh"])
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            conn.commit()
        finally:
            conn.close()


def test_per_ac_weather_context_available_but_kwh_unadjusted(secret_client, profiles, spec_asset, monkeypatch):
    _series_weather(monkeypatch)
    viewer = _tok("VIEWER", profiles("VIEWER"))
    r = secret_client.get(
        f"/api/v1/assets/{spec_asset}/energy?from=2026-01-05&to=2026-01-05&load_factor=0.75",
        headers=viewer,
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["data"]
    assert body["quality"] == "WEATHER_CONTEXT_AVAILABLE"
    assert body["totals"]["baseline_kwh"] == pytest.approx(3.9375)  # unchanged by weather
    session = body["sessions"][0]
    assert session["weather"]["aligned"] is True
    assert session["weather"]["mean_temp_c"] == pytest.approx(30.0)
    assert session["weather"]["demand_band"] in ("Low", "Moderate", "High", "Very high")


def test_per_ac_decommissioned_history_still_reported(secret_client, profiles, spec_asset, monkeypatch):
    _no_weather(monkeypatch)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE acs SET lifecycle_status = 'DECOMMISSIONED', is_active = FALSE WHERE ac_id = %s",
                (spec_asset,),
            )
        conn.commit()
    finally:
        conn.close()
    viewer = _tok("VIEWER", profiles("VIEWER"))
    r = secret_client.get(
        f"/api/v1/assets/{spec_asset}/energy?from=2026-01-05&to=2026-01-05", headers=viewer
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["totals"]["runtime_hours"] == pytest.approx(3.5)


# ── summary aggregates ───────────────────────────────────────────────────────


def test_summary_sums_per_ac_without_fanout(secret_client, profiles, spec_asset, monkeypatch):
    _no_weather(monkeypatch)
    viewer = _tok("VIEWER", profiles("VIEWER"))
    # Second asset in the same room: per-AC sums must add, never multiply.
    ac2 = f"NRG-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO acs (ac_id, room_id, power_rating_kw) VALUES (%s, 'ROOM-102', 2.0)", (ac2,)
            )
            cur.execute(
                "INSERT INTO ac_usage_sessions (ac_id, started_at, ended_at) VALUES (%s, %s, %s)",
                (ac2, datetime(2026, 1, 6, 9, 0, tzinfo=timezone.utc),
                 datetime(2026, 1, 6, 11, 0, tzinfo=timezone.utc)),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        r = secret_client.get(
            "/api/v1/energy/summary?from=2026-01-05&to=2026-01-06&load_factor=0.75", headers=viewer
        )
        assert r.status_code == 200, r.get_json()
        body = r.get_json()["data"]
        assert body["totals"]["runtime_hours"] == pytest.approx(3.5 + 2.0)
        assert body["totals"]["baseline_kwh"] == pytest.approx(1.5 * 3.5 * 0.75 + 2.0 * 2.0 * 0.75)
        assert body["totals"]["assets"] == 2
        assert body["cost"] is None  # no tariff configured
        assert body["model"]["version"] == "baseline-v1"
        days = {d["date"]: d for d in body["daily"]}
        assert set(days) == {"2026-01-05", "2026-01-06"}
        assert days["2026-01-05"]["runtime_hours"] == pytest.approx(3.5)
        assert days["2026-01-06"]["runtime_hours"] == pytest.approx(2.0)
        assert body["overlaps"] == []
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac2,))
            conn.commit()
        finally:
            conn.close()


def test_summary_midnight_split_and_validation(secret_client, profiles, db, monkeypatch):
    _no_weather(monkeypatch)
    ac_id = f"NRG-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, room_id, power_rating_kw) VALUES (%s, 'ROOM-102', 1.0)", (ac_id,))
            # 22:00 → 02:00 IST == 16:30Z → 20:30Z: 2 h + 2 h across days.
            cur.execute(
                "INSERT INTO ac_usage_sessions (ac_id, started_at, ended_at) VALUES (%s, %s, %s)",
                (ac_id, datetime(2026, 1, 5, 16, 30, tzinfo=timezone.utc),
                 datetime(2026, 1, 5, 20, 30, tzinfo=timezone.utc)),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        viewer = _tok("VIEWER", profiles("VIEWER"))
        r = secret_client.get(
            f"/api/v1/energy/summary?from=2026-01-05&to=2026-01-06&load_factor=1.0", headers=viewer
        )
        assert r.status_code == 200, r.get_json()
        days = {d["date"]: d for d in r.get_json()["data"]["daily"]}
        assert days["2026-01-05"]["runtime_hours"] == pytest.approx(2.0)
        assert days["2026-01-06"]["runtime_hours"] == pytest.approx(2.0)
        assert r.get_json()["data"]["totals"]["runtime_hours"] == pytest.approx(4.0)
        assert secret_client.get("/api/v1/energy/summary?load_factor=nope", headers=viewer).status_code == 400
        assert secret_client.get("/api/v1/energy/summary").status_code == 401
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            conn.commit()
        finally:
            conn.close()


def test_summary_overlaps_excluded_and_reported(secret_client, profiles, db, monkeypatch):
    _no_weather(monkeypatch)
    ac_id = f"NRG-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, room_id, power_rating_kw) VALUES (%s, 'ROOM-102', 1.0)", (ac_id,))
            cur.execute(
                "INSERT INTO ac_usage_sessions (ac_id, started_at, ended_at) VALUES (%s, %s, %s), (%s, %s, %s)",
                (ac_id, datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc),
                 datetime(2026, 1, 5, 11, 0, tzinfo=timezone.utc),
                 ac_id, datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
                 datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        viewer = _tok("VIEWER", profiles("VIEWER"))
        r = secret_client.get(
            f"/api/v1/energy/summary?from=2026-01-05&to=2026-01-05&load_factor=1.0", headers=viewer
        )
        assert r.status_code == 200, r.get_json()
        body = r.get_json()["data"]
        assert len(body["overlaps"]) == 1
        assert body["overlaps"][0]["overlap_hours"] == pytest.approx(1.0)
        # Corrupted pair contributes nothing — never double-counted.
        assert body["totals"]["runtime_hours"] == 0.0
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            conn.commit()
        finally:
            conn.close()


# ── weather-now endpoint ─────────────────────────────────────────────────────


def test_weather_now_degrades_without_provider(secret_client, profiles, monkeypatch):
    def boom(url, timeout):
        import requests

        raise requests.Timeout("cut")

    import app.services.weather as wx

    monkeypatch.setattr(wx, "_http_get", boom)
    wx.reset_cache()
    viewer = _tok("VIEWER", profiles("VIEWER"))
    r = secret_client.get("/api/v1/weather/now", headers=viewer)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["data"]
    assert body["available"] is False and body["source"] == "Open-Meteo"
    assert secret_client.get("/api/v1/weather/now").status_code == 401
