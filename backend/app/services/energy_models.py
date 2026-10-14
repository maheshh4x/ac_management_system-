"""Versioned energy-estimation models (pure functions: no DB, no network).

Two layers, kept separate by design (§11):

* LAYER A — ``baseline-v1``: ``P * H * L`` (rated input kW × hours × average
  load factor). The load factor is an *explicit, uncalibrated assumption* —
  it is NOT a compressor duty cycle, and this module never claims otherwise.
* LAYER B — weather cooling-demand indicator: a temperature-band label
  (Low/Moderate/High/Very high). It is descriptive context ONLY and is never
  multiplied into kWh by any function here.

A third, trivially derived scenario ``rated-upper-bound-v1`` (``P * H``) is
exposed alongside — the energy if the unit drew rated input continuously.

POWER RESOLUTION POLICY (Decision 1 — approved Option A, provisional):

1. ``rated`` — a verified ``power_rating_kw`` is used as-is. Model version
   stays ``baseline-v1``.
2. ``capacity-derived`` — only when NO rated input exists but a positive
   ``ac_capacity_ton`` does, a configured rule-of-thumb
   (``ENERGY_CAPACITY_KW_PER_TON``, default 1.2 kW per ton of refrigeration)
   produces a PROVISIONAL input estimate. It is a different model version
   (``baseline-v1-capacity-derived``), never written back to the database,
   never presented as a manufacturer rating, and always labelled.
3. ``unknown`` — neither usable → the estimate is unavailable and the caller
   reports insufficient data. Never zero, never invented.

The 1.2 kW/ton figure corresponds to an EER of roughly 2.9 (1 ton ≈
3.517 kW of cooling), i.e. a typical non-inverter split system at full
load. Real input varies with inverter type, part load, age and ambient
conditions, so the derived value is an OPERATIONAL PLACEHOLDER for fleets
with no nameplate data — not a rating, not calibrated, and always
replaceable per deployment via that single env knob.

What this module refuses to do (see task safety rules): invent power
ratings, treat cooling tons as electrical kW directly, invent tariffs,
invent weather, auto-adjust kWh by temperature, or claim calibration.
Every estimate the API returns carries ``model_version`` plus the exact
inputs used, so any figure is explainable after the fact.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

MODEL_VERSION_BASELINE = "baseline-v1"
MODEL_VERSION_UPPER_BOUND = "rated-upper-bound-v1"
#: Model version stamped on estimates whose input power was derived from
#: cooling capacity (Decision 1, Option A). Distinct from ``baseline-v1``
#: so no consumer can mistake it for a rated-power calculation.
MODEL_VERSION_CAPACITY_DERIVED = "baseline-v1-capacity-derived"

#: Above this rated input, accept the value but flag it for human review —
#: legitimate industrial equipment exists, so this is a warning, never a
#: rejection.
REVIEW_POWER_KW = 50.0

#: Same policy for cooling capacity (tons of refrigeration). Values above
#: this are accepted with a review warning, never auto-rejected.
REVIEW_CAPACITY_TON = 20.0


def configured_load_factor() -> tuple[float, list[str]]:
    """Default average load factor from the environment.

    Returns ``(value, assumptions)``. The default is an *uncalibrated
    placeholder* for typical part-load operation of cycling split systems —
    it must always be surfaced as an assumption, and operators should
    override it per fleet once meter data exists.
    """
    raw = os.getenv("ENERGY_BASELINE_LOAD_FACTOR", "0.7")
    assumptions = [
        "average load factor is an uncalibrated assumption, not a measurement",
        "load factor is average electrical input relative to rated input, distinct from compressor duty cycle",
    ]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.7, assumptions + [f"ENERGY_BASELINE_LOAD_FACTOR={raw!r} is not numeric; fell back to 0.7"]
    if not math.isfinite(value) or not 0 <= value <= 1:
        return 0.7, assumptions + [f"ENERGY_BASELINE_LOAD_FACTOR={raw!r} is outside [0, 1]; fell back to 0.7"]
    return value, assumptions


def validate_power_kw(value) -> tuple[float | None, str | None]:
    """Validate a rated electrical input power in kW.

    Returns ``(kw, warning)``. Unknown/blank → ``(None, None)`` (caller marks
    the estimate unavailable). Out-of-range or non-numeric → ``(None,
    reason)``. Implausibly large but finite values are ACCEPTED with a
    review warning — never silently dropped, never auto-corrected.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, "power rating is not numeric"
    try:
        kw = float(value)
    except (TypeError, ValueError):
        return None, "power rating is not numeric"
    if not math.isfinite(kw):
        return None, "power rating is not finite"
    if kw <= 0:
        return None, "power rating must be greater than zero"
    if kw > REVIEW_POWER_KW:
        return kw, f"rated input {kw:g} kW exceeds the {REVIEW_POWER_KW:g} kW review threshold — verify the nameplate"
    return kw, None


def configured_capacity_kw_per_ton() -> tuple[float, list[str]]:
    """Rule-of-thumb kW-per-ton used ONLY for capacity-derived estimates.

    Returns ``(value, assumptions)``. Units: kW of estimated electrical input
    per ton of refrigeration (1 ton ≈ 3.517 kW cooling; 1.2 kW/ton implies
    EER ≈ 2.9 — a typical non-inverter split unit at full load). Purpose:
    give fleets with no nameplate data a labelled provisional estimate.
    Limitations: real input varies with inverter type, part load, age and
    ambient conditions; this is NOT a manufacturer rating and is NEVER
    written back to the database or merged with rated-power results.
    """
    raw = os.getenv("ENERGY_CAPACITY_KW_PER_TON", "1.2")
    assumptions = [
        "capacity-derived input power uses a configured kW-per-ton rule of thumb, not a manufacturer rating",
        "capacity-derived estimates are provisional and carry model version "
        + MODEL_VERSION_CAPACITY_DERIVED,
    ]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.2, assumptions + [f"ENERGY_CAPACITY_KW_PER_TON={raw!r} is not numeric; fell back to 1.2"]
    if not math.isfinite(value) or value <= 0 or value > 5:
        return 1.2, assumptions + [f"ENERGY_CAPACITY_KW_PER_TON={raw!r} is outside (0, 5]; fell back to 1.2"]
    return value, assumptions


def validate_capacity_ton(value) -> tuple[float | None, str | None]:
    """Validate a cooling capacity in tons of refrigeration.

    Returns ``(tons, warning)`` — same contract as :func:`validate_power_kw`.
    Capacity is NEVER treated as electrical power by this function; the
    conversion (if any) happens only in :func:`resolve_power` and is
    always labelled.
    """
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, "capacity is not numeric"
    try:
        tons = float(value)
    except (TypeError, ValueError):
        return None, "capacity is not numeric"
    if not math.isfinite(tons):
        return None, "capacity is not finite"
    if tons <= 0:
        return None, "capacity must be greater than zero"
    if tons > REVIEW_CAPACITY_TON:
        return tons, f"capacity {tons:g} ton exceeds the {REVIEW_CAPACITY_TON:g} ton review threshold — verify the nameplate"
    return tons, None


def resolve_power(power_rating_kw, capacity_ton) -> dict:
    """Single shared power-resolution policy (Decision 1, Option A).

    Returns ``{"kw", "basis", "model_version", "warning"}`` where ``basis``
    is one of:

    * ``"rated"`` — verified nameplate input; model ``baseline-v1``;
    * ``"capacity-derived"`` — provisional input from
      ``capacity_ton × configured kW/ton``; model
      ``baseline-v1-capacity-derived``; never persisted, never presented
      as a rating;
    * ``"unknown"`` — insufficient data; ``kw`` is None (estimate
      unavailable, never zero).

    Rated and capacity-derived paths stay distinct: the returned
    ``model_version`` differs, so every downstream figure remains
    attributable to the rule that produced it.
    """
    kw, warning = validate_power_kw(power_rating_kw)
    if kw is not None:
        return {"kw": kw, "basis": "rated", "model_version": MODEL_VERSION_BASELINE, "warning": warning}

    tons, capacity_warning = validate_capacity_ton(capacity_ton)
    if tons is None:
        return {"kw": None, "basis": "unknown", "model_version": None, "warning": None}

    per_ton, assumptions = configured_capacity_kw_per_ton()
    derived = tons * per_ton
    warning_parts = [
        f"input power derived from {tons:g} ton capacity × {per_ton:g} kW/ton (configured rule of thumb, not a rating)"
    ]
    # Surface why the rated path was skipped and any capacity caveat.
    if power_rating_kw is not None:
        _, rated_reason = validate_power_kw(power_rating_kw)
        warning_parts.append(f"rated power unusable ({rated_reason}); capacity fallback applied")
    if capacity_warning:
        warning_parts.append(capacity_warning)
    warning_parts.extend(assumptions)
    return {
        "kw": derived,
        "basis": "capacity-derived",
        "model_version": MODEL_VERSION_CAPACITY_DERIVED,
        "warning": "; ".join(warning_parts),
    }


def validate_load_factor(value) -> tuple[float | None, str | None]:
    """Validate an average load factor. Returns ``(lf, error)``."""
    if value is None:
        return None, "average load factor is required"
    if isinstance(value, bool):
        return None, "average load factor must be a number in [0, 1]"
    try:
        lf = float(value)
    except (TypeError, ValueError):
        return None, "average load factor must be a number in [0, 1]"
    if not math.isfinite(lf) or not 0 <= lf <= 1:
        return None, "average load factor must be a number in [0, 1]"
    return lf, None


def baseline_kwh(power_kw: float, hours: float, load_factor: float) -> float:
    """``P * H * L``. Callers must validate inputs first; this is pure math."""
    return power_kw * hours * load_factor


def upper_bound_kwh(power_kw: float, hours: float) -> float:
    """``P * H`` — rated input drawn continuously. An upper bound, not an expectation."""
    return power_kw * hours


def overlap_hours(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> float:
    """Hours of overlap between two aware intervals (0 when disjoint)."""
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    delta = (earliest_end - latest_start).total_seconds()
    return max(0.0, delta / 3600.0)


def campus_tzinfo():
    """Campus timezone: IANA name when tzdata exists, else fixed offset.

    Asia/Kolkata has no DST, so the fixed-offset fallback
    (``CAMPUS_UTC_OFFSET_MINUTES``, default 330) is exact for the campus.
    Any future DST-observing location MUST provide tzdata instead — the
    fallback logs every time it is used so the substitution is visible.
    """
    name = os.getenv("WEATHER_TIMEZONE", "Asia/Kolkata")
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - missing tzdata on minimal installs (e.g. Windows)
        offset = int(os.getenv("CAMPUS_UTC_OFFSET_MINUTES", "330"))
        logger.warning("tzdata unavailable for %s; using fixed UTC%+dmin offset", name, offset)
        return timezone(timedelta(minutes=offset), name=name)


def split_at_midnight(start: datetime, end: datetime, tz) -> list[tuple[str, float]]:
    """Split an aware interval at campus-local midnights.

    Returns ``[(YYYY-MM-DD, hours), ...]`` whose hours sum to the interval
    length (within float tolerance). A session fully inside one day yields a
    single entry; start >= end yields ``[]``.
    """
    if end <= start:
        return []
    local_start = start.astimezone(tz)
    local_end = end.astimezone(tz)
    out: list[tuple[str, float]] = []
    cursor = local_start
    while cursor < local_end:
        next_midnight = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        # Same-day case: cursor is already at/after midnight; guard the loop.
        if next_midnight <= cursor:
            next_midnight = local_end
        segment_end = min(next_midnight, local_end)
        day = cursor.date().isoformat()
        out.append((day, (segment_end - cursor).total_seconds() / 3600.0))
        cursor = segment_end
    return out


def detect_overlaps(sessions: list[dict]) -> list[dict]:
    """Find same-asset session pairs with overlapping intervals.

    Each session is ``{"id", "ac_id", "start", "end|None}`` with aware
    datetimes; open sessions (``end`` None) are checked against ``now`` only
    when ``now`` is supplied — otherwise open sessions are skipped here (the
    partial unique index already prevents two open rows per asset).
    Returns ``[{"session_a", "session_b", "overlap_hours"}]``.
    """
    closed = [s for s in sessions if s.get("end") is not None]
    findings = []
    ordered = sorted(closed, key=lambda s: s["start"])
    for index, current in enumerate(ordered):
        for other in ordered[index + 1 :]:
            if other["start"] >= current["end"]:
                break
            hours = overlap_hours(current["start"], current["end"], other["start"], other["end"])
            if hours > 0:
                findings.append(
                    {
                        "session_a": current["id"],
                        "session_b": other["id"],
                        "overlap_hours": hours,
                    }
                )
    return findings


def _demand_thresholds() -> tuple[float, float, float]:
    def _float(name: str, default: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    return (
        _float("WEATHER_DEMAND_MODERATE_C", 28.0),
        _float("WEATHER_DEMAND_HIGH_C", 33.0),
        _float("WEATHER_DEMAND_VERY_HIGH_C", 38.0),
    )


def classify_demand(temp_c: float | None) -> tuple[str | None, str]:
    """Cooling-demand indicator from outdoor air temperature ONLY.

    Bands are initial *operational* classifications, not calibrated
    predictions — humidity/dew/apparent temperature are reported alongside
    as context and never folded into this label (no arbitrary combined
    score, no double counting: apparent temperature already embeds those
    effects). Returns ``(band|None, explanation)``.
    """
    moderate, high, very_high = _demand_thresholds()
    explanation = (
        f"initial operational bands (Low < {moderate:g}°C ≤ Moderate < {high:g}°C"
        f" ≤ High < {very_high:g}°C ≤ Very high); uncalibrated, humidity shown separately"
    )
    if temp_c is None or not isinstance(temp_c, (int, float)) or not math.isfinite(temp_c):
        return None, explanation + "; no temperature available"
    if temp_c >= very_high:
        return "Very high", explanation
    if temp_c >= high:
        return "High", explanation
    if temp_c >= moderate:
        return "Moderate", explanation
    return "Low", explanation
