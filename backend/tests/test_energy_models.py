"""Unit tests for the versioned energy models (no DB, no network).

Covers task examples A–F exactly, plus bounds, interval math, midnight
splitting, overlap detection and the demand indicator.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import energy_models as em  # noqa: E402


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# ── worked examples from the spec (A, B) ────────────────────────────────────


def test_example_a_1_5kw_3_5h_0_75():
    assert em.baseline_kwh(1.5, 3.5, 0.75) == pytest.approx(3.9375)
    assert round(em.baseline_kwh(1.5, 3.5, 0.75), 2) == 3.94


def test_example_b_2kw_2h_0_60():
    assert em.baseline_kwh(2.0, 2.0, 0.60) == pytest.approx(2.4)


# ── validation (C–F) ─────────────────────────────────────────────────────────


def test_zero_runtime_is_zero_energy():
    assert em.baseline_kwh(1.5, 0.0, 0.75) == 0.0
    assert em.upper_bound_kwh(1.5, 0.0) == 0.0


def test_missing_power_is_unavailable_not_zero():
    kw, warning = em.validate_power_kw(None)
    assert kw is None and warning is None  # caller marks the estimate unavailable


@pytest.mark.parametrize(
    "bad,reason",
    [
        ("abc", "not numeric"),
        (float("nan"), "not finite"),
        (float("inf"), "not finite"),
        (0, "greater than zero"),
        (-1.5, "greater than zero"),
        (True, "not numeric"),
    ],
)
def test_invalid_power_rejected_with_reason(bad, reason):
    kw, warning = em.validate_power_kw(bad)
    assert kw is None
    assert reason in (warning or "")


def test_implausible_power_accepted_with_review_warning():
    kw, warning = em.validate_power_kw(120.0)
    assert kw == 120.0  # industrial ratings are never auto-rejected
    assert warning is not None and "verify" in warning


# ── capacity validation + power resolution (Decision 1, Option A) ────────────


@pytest.mark.parametrize(
    "bad,reason",
    [
        ("abc", "not numeric"),
        (float("nan"), "not finite"),
        (float("inf"), "not finite"),
        (0, "greater than zero"),
        (-2, "greater than zero"),
        (True, "not numeric"),
    ],
)
def test_invalid_capacity_rejected_with_reason(bad, reason):
    tons, warning = em.validate_capacity_ton(bad)
    assert tons is None
    assert reason in (warning or "")


def test_implausible_capacity_accepted_with_review_warning():
    tons, warning = em.validate_capacity_ton(40.0)
    assert tons == 40.0
    assert warning is not None and "verify" in warning


def test_resolve_power_prefers_rated_even_when_capacity_present(monkeypatch):
    monkeypatch.setenv("ENERGY_CAPACITY_KW_PER_TON", "1.2")
    out = em.resolve_power(1.5, 1.5)
    assert out["kw"] == 1.5
    assert out["basis"] == "rated"
    assert out["model_version"] == em.MODEL_VERSION_BASELINE


def test_resolve_power_capacity_derived_when_rated_absent(monkeypatch):
    monkeypatch.setenv("ENERGY_CAPACITY_KW_PER_TON", "1.2")
    out = em.resolve_power(None, 2.0)
    assert out["kw"] == pytest.approx(2.4)
    assert out["basis"] == "capacity-derived"
    assert out["model_version"] == em.MODEL_VERSION_CAPACITY_DERIVED
    assert "not a rating" in out["warning"]


def test_resolve_power_capacity_derived_when_rated_invalid(monkeypatch):
    monkeypatch.setenv("ENERGY_CAPACITY_KW_PER_TON", "1.2")
    out = em.resolve_power(-1.0, 1.5)
    assert out["basis"] == "capacity-derived"
    assert out["kw"] == pytest.approx(1.8)
    assert "rated power unusable" in out["warning"]


def test_resolve_power_unknown_when_neither_input(monkeypatch):
    out = em.resolve_power(None, None)
    assert out["kw"] is None and out["basis"] == "unknown" and out["model_version"] is None
    out = em.resolve_power(0, -1)
    assert out["kw"] is None and out["basis"] == "unknown"


def test_capacity_kw_per_ton_configurable_and_bounded(monkeypatch):
    monkeypatch.setenv("ENERGY_CAPACITY_KW_PER_TON", "1.5")
    value, assumptions = em.configured_capacity_kw_per_ton()
    assert value == 1.5
    assert any("not a manufacturer rating" in a for a in assumptions)
    monkeypatch.setenv("ENERGY_CAPACITY_KW_PER_TON", "banana")
    assert em.configured_capacity_kw_per_ton()[0] == 1.2
    monkeypatch.setenv("ENERGY_CAPACITY_KW_PER_TON", "99")  # implausible → default
    assert em.configured_capacity_kw_per_ton()[0] == 1.2


def test_capacity_derived_estimate_differs_from_rated_model_version(monkeypatch):
    monkeypatch.setenv("ENERGY_CAPACITY_KW_PER_TON", "1.2")
    monkeypatch.setenv("ENERGY_BASELINE_LOAD_FACTOR", "0.7")
    rated = em.resolve_power(1.5, None)
    derived = em.resolve_power(None, 1.25)  # 1.25 ton → 1.5 kW derived
    # Same math, different provenance — the model versions must differ so
    # no consumer can mistake a derived figure for a rated one.
    assert rated["kw"] == derived["kw"] == 1.5
    assert rated["model_version"] != derived["model_version"]


def test_missing_and_invalid_load_factor():
    lf, err = em.validate_load_factor(None)
    assert lf is None and err
    for bad in ("x", float("nan"), -0.1, 1.1, True):
        lf, err = em.validate_load_factor(bad)
        assert lf is None and err
    assert em.validate_load_factor(0)[0] == 0.0
    assert em.validate_load_factor(1)[0] == 1.0


def test_upper_bound_is_rated_times_hours():
    assert em.upper_bound_kwh(1.5, 3.5) == pytest.approx(5.25)


def test_configured_default_load_factor(monkeypatch):
    monkeypatch.setenv("ENERGY_BASELINE_LOAD_FACTOR", "0.6")
    value, assumptions = em.configured_load_factor()
    assert value == 0.6
    assert any("uncalibrated" in a for a in assumptions)
    monkeypatch.setenv("ENERGY_BASELINE_LOAD_FACTOR", "banana")
    value, assumptions = em.configured_load_factor()
    assert value == 0.7 and len(assumptions) == 3


# ── interval math ────────────────────────────────────────────────────────────


def test_partial_hour_overlap_matches_spec_example():
    # Session 09:15–12:45 against hourly boundaries.
    s = utc(2026, 1, 5, 9, 15)
    e = utc(2026, 1, 5, 12, 45)
    hours = [
        em.overlap_hours(s, e, utc(2026, 1, 5, h, 0), utc(2026, 1, 5, h + 1, 0)) for h in (9, 10, 11, 12)
    ]
    assert hours == pytest.approx([0.75, 1.0, 1.0, 0.75])
    assert sum(hours) == pytest.approx(3.5)


def test_disjoint_intervals_have_zero_overlap():
    assert em.overlap_hours(utc(2026, 1, 1, 1), utc(2026, 1, 1, 2), utc(2026, 1, 1, 3), utc(2026, 1, 1, 4)) == 0.0


def test_midnight_split_example():
    # 22:00 → 02:00 contributes 2 h + 2 h, never 4 h on both days.
    tz = timezone(timedelta(minutes=330))  # Asia/Kolkata has no DST
    parts = em.split_at_midnight(
        datetime(2026, 1, 5, 22, 0, tzinfo=tz), datetime(2026, 1, 6, 2, 0, tzinfo=tz), tz
    )
    assert [(d, round(h, 6)) for d, h in parts] == [("2026-01-05", 2.0), ("2026-01-06", 2.0)]
    assert sum(h for _, h in parts) == pytest.approx(4.0)


def test_midnight_split_single_day_and_empty():
    tz = timezone(timedelta(minutes=330))
    parts = em.split_at_midnight(
        datetime(2026, 1, 5, 9, 15, tzinfo=tz), datetime(2026, 1, 5, 12, 45, tzinfo=tz), tz
    )
    assert [(d, round(h, 6)) for d, h in parts] == [("2026-01-05", 3.5)]
    assert em.split_at_midnight(utc(2026, 1, 5, 1), utc(2026, 1, 5, 1), tz) == []


def test_overlap_detection_flags_corruption():
    sessions = [
        {"id": "a", "start": utc(2026, 1, 5, 9), "end": utc(2026, 1, 5, 11)},
        {"id": "b", "start": utc(2026, 1, 5, 10), "end": utc(2026, 1, 5, 12)},
        {"id": "c", "start": utc(2026, 1, 5, 13), "end": utc(2026, 1, 5, 14)},
    ]
    findings = em.detect_overlaps(sessions)
    assert len(findings) == 1
    assert findings[0]["session_a"] == "a" and findings[0]["session_b"] == "b"
    assert findings[0]["overlap_hours"] == pytest.approx(1.0)


def test_no_overlaps_on_clean_history():
    sessions = [
        {"id": "a", "start": utc(2026, 1, 5, 9), "end": utc(2026, 1, 5, 11)},
        {"id": "b", "start": utc(2026, 1, 5, 11), "end": utc(2026, 1, 5, 12)},
    ]
    assert em.detect_overlaps(sessions) == []


# ── demand indicator ─────────────────────────────────────────────────────────


def test_demand_bands_and_missing_temperature(monkeypatch):
    monkeypatch.delenv("WEATHER_DEMAND_MODERATE_C", raising=False)
    assert em.classify_demand(25.0)[0] == "Low"
    assert em.classify_demand(30.0)[0] == "Moderate"
    assert em.classify_demand(35.0)[0] == "High"
    assert em.classify_demand(40.0)[0] == "Very high"
    band, explanation = em.classify_demand(None)
    assert band is None and "no temperature" in explanation


def test_campus_tz_falls_back_without_tzdata(monkeypatch):
    import zoneinfo

    def _missing(_name):
        raise zoneinfo.ZoneInfoNotFoundError("no tzdata")

    monkeypatch.setattr(zoneinfo, "ZoneInfo", _missing)
    tz = em.campus_tzinfo()
    assert tz.utcoffset(None) == timedelta(minutes=330)
