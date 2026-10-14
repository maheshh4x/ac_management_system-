"""Unit tests for the Open-Meteo weather client (no live network — ever).

The provider seam ``weather._http_get`` is monkeypatched in every test that
needs a payload; failure paths raise through simulated transport errors.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import weather as wx  # noqa: E402


def _payload(hours=6, start="2026-01-05T09:00", temp_start=30.0):
    times = []
    base = datetime.fromisoformat(start)
    for index in range(hours):
        times.append((base + timedelta(hours=index)).isoformat(timespec="minutes"))
    return {
        "utc_offset_seconds": 19800,
        "hourly": {
            "time": times,
            "temperature_2m": [temp_start + index * 0.5 for index in range(hours)],
            "relative_humidity_2m": [70 - index for index in range(hours)],
            "dew_point_2m": [24.0] * hours,
            "apparent_temperature": [temp_start + 2 + index * 0.5 for index in range(hours)],
            "rain": [0.0] * hours,
        },
    }


@pytest.fixture(autouse=True)
def _clean_cache():
    wx.reset_cache()
    yield
    wx.reset_cache()


def _ok(monkeypatch, payload, fetched_at=None):
    fetched = fetched_at or datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)

    def fake_get(url, timeout):
        assert "api.open-meteo.com" in url
        assert "temperature_2m" in url
        return payload

    monkeypatch.setattr(wx, "_http_get", fake_get)
    return fetched


# ── parsing / validation ───────────────────────────────────────────────────


def test_request_url_has_configured_campus_and_variables(monkeypatch):
    monkeypatch.setenv("WEATHER_LATITUDE", "13.0878")
    monkeypatch.setenv("WEATHER_LONGITUDE", "80.2785")
    url = wx.request_url()
    assert "latitude=13.0878" in url and "longitude=80.2785" in url
    for var in ("temperature_2m", "relative_humidity_2m", "dew_point_2m", "apparent_temperature", "rain"):
        assert var in url
    assert "past_days=" in url


def test_parse_marks_forecast_vs_history():
    fetched = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    series, meta = wx._parse_series(_payload(hours=6), fetched)
    assert len(series) == 6
    assert meta["source"] == "Open-Meteo"
    # 09:00+05:30 = 03:30Z ... 14:00+05:30 = 08:30Z: all before noon UTC.
    assert all(not p["is_forecast"] for p in series)
    assert series[0]["time_utc"] == datetime(2026, 1, 5, 3, 30, tzinfo=timezone.utc)
    # A later fetch turns the tail into forecast.
    series2, _ = wx._parse_series(_payload(hours=6), datetime(2026, 1, 5, 6, 0, tzinfo=timezone.utc))
    assert [p["is_forecast"] for p in series2] == [False, False, False, True, True, True]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: "not-a-dict",
        lambda p: {**p, "hourly": "nope"},
        lambda p: {**p, "hourly": {**p["hourly"], "temperature_2m": None}},
        lambda p: {**p, "hourly": {**p["hourly"], "rain": [0.0]}},
        lambda p: {**p, "hourly": {**p["hourly"], "time": ["yesterday"]}},
    ],
)
def test_malformed_payloads_raise_unavailable(mutate):
    with pytest.raises(wx.WeatherUnavailable):
        wx._parse_series(mutate(_payload()), datetime.now(timezone.utc))


# ── caching / failure policy ───────────────────────────────────────────────


def test_first_fetch_caches_and_second_is_served(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append(1)
        return _payload()

    monkeypatch.setattr(wx, "_http_get", fake_get)
    series, meta, stale = wx.get_hourly_series()
    assert len(series) == 6 and stale is False and meta["served_from_cache"] is False
    series2, meta2, stale2 = wx.get_hourly_series()
    assert stale2 is False and meta2["served_from_cache"] is True
    assert calls == [1] and [p["time_utc"] for p in series] == [p["time_utc"] for p in series2]


def test_cache_expires_after_ttl_and_refetches(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append(1)
        return _payload()

    monkeypatch.setattr(wx, "_http_get", fake_get)
    monkeypatch.setenv("WEATHER_CACHE_TTL_S", "600")
    wx.get_hourly_series()
    # Age the cached entry past the TTL: the next call must hit the provider.
    wx._cache["at"] = wx._cache["at"] - 601
    wx.get_hourly_series()
    assert calls == [1, 1]


def test_timeout_serves_stale_then_unavailable(monkeypatch):
    _ok(monkeypatch, _payload())
    wx.get_hourly_series()

    def boom(url, timeout):
        raise requests.Timeout("slow provider")

    monkeypatch.setattr(wx, "_http_get", boom)
    series, meta, stale = wx.get_hourly_series(force_refresh=True)
    assert stale is True and len(series) == 6

    wx.reset_cache()
    with pytest.raises(wx.WeatherUnavailable):
        wx.get_hourly_series(force_refresh=True)


def test_current_conditions_never_raise(monkeypatch):
    def boom(url, timeout):
        raise requests.ConnectionError("dns down")

    monkeypatch.setattr(wx, "_http_get", boom)
    body = wx.current_conditions()
    assert body == {"available": False, "reason": body["reason"], "source": "Open-Meteo"}


def test_current_conditions_reports_freshness_and_band(monkeypatch):
    _ok(monkeypatch, _payload(hours=6, temp_start=34.0))
    moment = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)  # covers 08:00–09:00Z
    body = wx.current_conditions(now=moment)
    assert body["available"] is True and body["stale"] is False
    assert body["temp_c"] == pytest.approx(36.5)  # 08:00Z row
    assert body["demand_band"] == "High"
    assert "uncalibrated" in body["demand_note"]


# ── alignment ──────────────────────────────────────────────────────────────


def test_alignment_partial_hours_sum_to_runtime(monkeypatch):
    _ok(monkeypatch, _payload(hours=6))
    series, _, _ = wx.get_hourly_series()
    # Session 09:15–12:45 IST == 03:45–07:15Z.
    start = datetime(2026, 1, 5, 3, 45, tzinfo=timezone.utc)
    end = datetime(2026, 1, 5, 7, 15, tzinfo=timezone.utc)
    out = wx.align_session(start, end, series)
    assert out["total_hours"] == pytest.approx(3.5)
    assert out["covered_hours"] == pytest.approx(3.5)
    assert out["missing"] == []
    # Duration-weighted mean over 03:30Z..07:30Z rows (30.0 … 31.5).
    assert out["mean_temp_c"] == pytest.approx(30.75)
    assert out["mean_humidity_pct"] == pytest.approx(68.5)


def test_alignment_missing_hours_reported_not_invented():
    series = [
        {"time_utc": datetime(2026, 1, 5, 3, 30, tzinfo=timezone.utc), "temp_c": None,
         "humidity_pct": None, "is_forecast": False},
    ]
    out = wx.align_session(
        datetime(2026, 1, 5, 3, 45, tzinfo=timezone.utc),
        datetime(2026, 1, 5, 5, 15, tzinfo=timezone.utc),
        series,
    )
    assert out["covered_hours"] == pytest.approx(0.75)
    assert out["total_hours"] == pytest.approx(1.5)
    assert len(out["missing"]) == 1  # null-temperature hour named, not filled
    assert out["mean_temp_c"] is None


def test_alignment_session_older_than_weather_coverage():
    """A historical session before the provider window (past_days=N) has no
    hourly records: coverage is zero, no temperature is invented, and the
    caller falls back to the baseline estimate."""
    series = [
        {"time_utc": datetime(2026, 1, 5, h, 0, tzinfo=timezone.utc), "temp_c": 30.0,
         "humidity_pct": 70, "is_forecast": False}
        for h in range(6)
    ]
    out = wx.align_session(
        datetime(2025, 12, 20, 4, 0, tzinfo=timezone.utc),  # weeks earlier
        datetime(2025, 12, 20, 6, 0, tzinfo=timezone.utc),
        series,
    )
    assert out["covered_hours"] == 0.0
    assert out["total_hours"] == pytest.approx(2.0)
    assert out["mean_temp_c"] is None and out["mean_humidity_pct"] is None


def test_alignment_forecast_hours_flagged_separately():
    """Forecast share is reported so no consumer can present forecast values
    as historical observations."""
    series = [
        {"time_utc": datetime(2026, 1, 5, h, 0, tzinfo=timezone.utc), "temp_c": 31.0,
         "humidity_pct": 70, "is_forecast": h >= 2}
        for h in range(0, 4)
    ]
    out = wx.align_session(
        datetime(2026, 1, 5, 0, 30, tzinfo=timezone.utc),
        datetime(2026, 1, 5, 3, 30, tzinfo=timezone.utc),
        series,
    )
    assert out["covered_hours"] == pytest.approx(3.0)
    # Hours 2 and 3 are forecast: 0.5 h + 1 h of the covered window.
    assert [p["is_forecast"] for p in series] == [False, False, True, True]
