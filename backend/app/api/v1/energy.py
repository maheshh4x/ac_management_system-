"""Estimated electricity consumption: per-AC figures and aggregates (read-only).

All kWh here are ESTIMATES from the usage-session ledger — never meter
readings, never telemetry. Two layers, never mixed (§11):

* baseline (``baseline-v1``): rated input kW × hours × average load factor;
* rated upper bound (``rated-upper-bound-v1``): rated input kW × hours.

The weather cooling-demand indicator (Low/Moderate/High/Very high) is
descriptive context computed from outdoor temperature ONLY; it is never
multiplied into kWh by any code path here.

Cost appears only when ``ENERGY_TARIFF_PER_KWH`` configures a valid tariff;
otherwise ``cost`` is null with an explicit reason. Sessions are never
double-counted: each session contributes once (overlapping sessions are
detected, excluded from totals, and reported). Open sessions accrue to the
request time and are always labeled provisional.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from flask import Blueprint, g, request
from psycopg2.extras import RealDictCursor

from app.auth.jwt import get_current_user, require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, success
from app.middleware.rate_limit import rate_limit
from app.services import energy_models as em
from app.services import location_sql
from app.services import weather as wx

energy_v1_bp = Blueprint("energy_v1", __name__)

logger = logging.getLogger(__name__)


# ── shared parsing ───────────────────────────────────────────────────────────


def _parse_range(args) -> tuple[date, date]:
    """Campus-date range, defaulting to the trailing 30 days (inclusive)."""
    tz = em.campus_tzinfo()
    today = datetime.now(tz).date()
    default_from = today.fromordinal(today.toordinal() - 29)
    raw_from = (args.get("from") or "").strip() or None
    raw_to = (args.get("to") or "").strip() or None
    try:
        parsed_from = date.fromisoformat(raw_from) if raw_from else default_from
        parsed_to = date.fromisoformat(raw_to) if raw_to else today
    except ValueError as err:
        raise ValueError("from/to must be YYYY-MM-DD dates.") from err
    if parsed_from > parsed_to:
        raise ValueError("from must not be after to.")
    return parsed_from, parsed_to


def _load_factor(args) -> tuple[float, list[str], bool]:
    """(value, assumptions, used_default) — explicit param or env default."""
    raw = (args.get("load_factor") or "").strip() or None
    if raw is None:
        value, assumptions = em.configured_load_factor()
        return value, assumptions + ["load factor taken from server default (no per-request value)"], True
    value, err = em.validate_load_factor(raw)
    if err is not None:
        raise ValueError(err)
    return value, ["load factor supplied explicitly for this request"], False


def _tariff() -> tuple[dict | None, list[str]]:
    """Configured tariff or (None, [reason]). Never invented."""
    import math
    import os

    raw = (os.getenv("ENERGY_TARIFF_PER_KWH") or "").strip()
    if not raw:
        return None, ["no electricity tariff is configured"]
    try:
        per_kwh = float(raw)
    except (TypeError, ValueError):
        return None, [f"ENERGY_TARIFF_PER_KWH={raw!r} is not numeric; cost unavailable"]
    if not math.isfinite(per_kwh) or per_kwh <= 0:
        return None, [f"ENERGY_TARIFF_PER_KWH={raw!r} is not a positive finite rate; cost unavailable"]
    currency = (os.getenv("ENERGY_TARIFF_CURRENCY") or "INR").strip() or "INR"
    return {"per_kwh": per_kwh, "currency": currency}, []


def _range_bounds(from_date: date, to_date: date, tz):
    """Aware [start, end) of the inclusive campus-date range."""
    start = datetime(from_date.year, from_date.month, from_date.day, tzinfo=tz)
    end_day = to_date.fromordinal(to_date.toordinal() + 1)
    end = datetime(end_day.year, end_day.month, end_day.day, tzinfo=tz)
    return start, end


def _resolve_asset_full(cur, ref: str):
    cur.execute(
        "SELECT a.ac_id, a.asset_uuid, a.asset_code, a.ac_make_company, a.ac_model_number,"
        " a.ac_serial_number, a.ac_capacity_ton, a.ac_type, a.ac_star_rating,"
        " a.power_rating_kw, a.operational_status,"
        " a.lifecycle_status, a.is_active, a.installation_year, a.installation_date,"
        f" {location_sql.label('block')} AS block,"
        f" {location_sql.label('floor')} AS floor,"
        f" {location_sql.label('room')} AS room,"
        f" {location_sql.label('department')} AS department,"
        f" {location_sql.label('college')} AS college"
        f" FROM acs a {location_sql.LOCATION_JOIN} {location_sql.EFFECTIVE_JOINS}"
        " WHERE a.asset_uuid::text = %s OR a.ac_id = %s OR a.asset_code = %s LIMIT 1",
        (ref, ref, ref),
    )
    return cur.fetchone()


# ── per-session estimation ───────────────────────────────────────────────────


def _estimate_one(asset: dict, started_at, ended_at, now, load_factor, series) -> dict:
    """One session's contribution, clipped later by the caller if needed."""
    start = started_at if started_at.tzinfo else started_at.replace(tzinfo=timezone.utc)
    if ended_at is None:
        end, provisional = now, True
    else:
        end = ended_at if ended_at.tzinfo else ended_at.replace(tzinfo=timezone.utc)
        provisional = False
    hours = max(0.0, (end - start).total_seconds() / 3600.0) if end > start else 0.0

    resolution = em.resolve_power(asset.get("power_rating_kw"), asset.get("ac_capacity_ton"))
    power_kw, power_basis = resolution["kw"], resolution["basis"]
    power_warning = resolution["warning"]
    baseline = em.baseline_kwh(power_kw, hours, load_factor) if power_kw is not None else None
    upper = em.upper_bound_kwh(power_kw, hours) if power_kw is not None else None

    weather_info = {"aligned": False, "mean_temp_c": None, "mean_humidity_pct": None,
                    "missing_intervals": [], "demand_band": None, "demand_note": None,
                    "forecast_hours": 0}
    if series is not None and hours > 0:
        aligned = wx.align_session(start, end, series)
        covered = aligned["covered_hours"]
        weather_info = {
            "aligned": covered > 0,
            "mean_temp_c": aligned["mean_temp_c"],
            "mean_humidity_pct": aligned["mean_humidity_pct"],
            "missing_intervals": aligned["missing"],
            "demand_band": None,
            "demand_note": None,
            "forecast_hours": 0,
        }
        if covered > 0 and aligned["mean_temp_c"] is not None:
            band, note = em.classify_demand(aligned["mean_temp_c"])
            weather_info["demand_band"] = band
            weather_info["demand_note"] = note + "; indicator only — kWh above is the unadjusted baseline"
        # Forecast share of the covered portion (informational only).
        if covered > 0:
            f_hours = 0.0
            for point in series:
                part = em.overlap_hours(
                    start, end, point["time_utc"], point["time_utc"] + __import__("datetime").timedelta(hours=1)
                )
                if part > 0 and point.get("is_forecast"):
                    f_hours += part
            weather_info["forecast_hours"] = f_hours
    return {
        "runtime_hours": hours,
        "baseline_kwh": baseline,
        "upper_bound_kwh": upper,
        "power_kw": power_kw,
        "power_basis": power_basis,
        "power_warning": power_warning,
        "provisional": provisional,
        "weather": weather_info,
    }


def _quality_label(
    sessions_present: bool, power_basis: str, weather_ok: bool
) -> tuple[str, list[str]]:
    """Evidence-honest quality label (§21).

    ``power_basis`` comes from :func:`energy_models.resolve_power`:
    ``rated`` / ``capacity-derived`` / ``unknown``. Capacity-derived
    results get their own label so they can never be mistaken for
    rated-power estimates.
    """
    if not sessions_present:
        return "NO_USAGE_RECORDED", ["no session has been recorded for the selected period"]
    if power_basis == "unknown":
        return "RUNTIME_RECORDED", [
            "operating duration is known; no usable rated input power or capacity on record"
        ]
    derived = power_basis == "capacity-derived"
    basis_note = (
        "input power was derived from cooling capacity (provisional, not a manufacturer rating)"
        if derived
        else "rated electrical input power is available"
    )
    label = "BASIC_ENERGY_ESTIMATE_CAPACITY_DERIVED" if derived else "BASIC_ENERGY_ESTIMATE"
    if weather_ok:
        return (
            "WEATHER_CONTEXT_AVAILABLE_CAPACITY_DERIVED" if derived else "WEATHER_CONTEXT_AVAILABLE",
            [
                basis_note,
                "weather data is available for the interval but does not change the kWh value",
            ],
        )
    return label, [basis_note]


# ── GET /api/v1/assets/<ref>/energy ──────────────────────────────────────────


@energy_v1_bp.route("/api/v1/assets/<ref>/energy", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(60, 60, "energy-ac")
def asset_energy(ref):
    """Per-AC estimated electricity consumption (strict, explainable)."""
    try:
        from_date, to_date = _parse_range(request.args)
    except ValueError as exc:
        return error("INVALID_DATE", str(exc), 400)
    try:
        load_factor, lf_assumptions, _ = _load_factor(request.args)
    except ValueError as exc:
        return error("INVALID_LOAD_FACTOR", str(exc), 400)
    tariff, tariff_notes = _tariff()
    tz = em.campus_tzinfo()
    range_start, range_end = _range_bounds(from_date, to_date, tz)
    now = datetime.now(timezone.utc)

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            asset = _resolve_asset_full(cur, ref)
            if asset is None:
                cur.close()
                return error("ASSET_NOT_FOUND", "No asset matches this reference.", 404)
            cur.execute(
                "SELECT id, started_at, ended_at FROM ac_usage_sessions"
                " WHERE ac_id = %s ORDER BY started_at",
                (asset["ac_id"],),
            )
            sessions = [dict(r) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("asset energy failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ENERGY_FAILED", "Unable to compute the energy estimate.", 500)

    # Weather is best-effort: baseline math never depends on it.
    try:
        series, _, _ = wx.get_hourly_series()
        weather_available = True
    except wx.WeatherUnavailable as exc:
        series, weather_available, weather_error = None, False, str(exc)

    # Clip every session to the requested range; open sessions accrue to
    # min(now, range end). Corrupted (negative/zero-length) intervals are
    # reported, never counted.
    clipped = []
    invalid = []
    for row in sessions:
        start = row["started_at"]
        end = row["ended_at"] or now
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if end <= start:
            invalid.append(str(row["id"]))
            continue
        clip_start, clip_end = max(start, range_start), min(end, range_end)
        if clip_end > clip_start:
            clipped.append((row, clip_start, clip_end, row["ended_at"] is None))

    # Overlap detection across clipped intervals (same asset).
    overlap_input = [
        {"id": str(row["id"]), "start": cs, "end": ce} for row, cs, ce, _ in clipped
    ]
    overlaps = em.detect_overlaps(overlap_input)
    overlapped_ids = {f["session_a"] for f in overlaps} | {f["session_b"] for f in overlaps}

    session_rows = []
    daily: dict[str, dict] = {}
    totals = {"runtime_hours": 0.0, "baseline_kwh": 0.0, "upper_bound_kwh": 0.0}
    power_basis = "unknown"
    weather_ok_any = False
    for row, clip_start, clip_end, is_open in clipped:
        est = _estimate_one(asset, clip_start, clip_end, now, load_factor, series if weather_available else None)
        if str(row["id"]) in overlapped_ids:
            est["baseline_kwh"] = None
            est["upper_bound_kwh"] = None
            est["excluded_reason"] = "overlaps another session; reported, not counted"
        else:
            est["excluded_reason"] = None
            totals["runtime_hours"] += est["runtime_hours"]
            if est["baseline_kwh"] is not None:
                totals["baseline_kwh"] += est["baseline_kwh"]
                totals["upper_bound_kwh"] += est["upper_bound_kwh"]
                power_basis = est["power_basis"]
        if est["weather"]["aligned"]:
            weather_ok_any = True
        for day, hours in em.split_at_midnight(clip_start, clip_end, tz):
            bucket = daily.setdefault(day, {"date": day, "runtime_hours": 0.0, "baseline_kwh": 0.0, "sessions": 0})
            bucket["runtime_hours"] += hours
            if est["baseline_kwh"] is not None and est["runtime_hours"] > 0:
                bucket["baseline_kwh"] += est["baseline_kwh"] * (hours / est["runtime_hours"])
            bucket["sessions"] += 1
        session_rows.append(
            {
                "id": str(row["id"]),
                "started_at": clip_start.isoformat(),
                "ended_at": None if is_open else clip_end.isoformat(),
                "status": "open" if is_open else "closed",
                "runtime_hours": est["runtime_hours"],
                "baseline_kwh": est["baseline_kwh"],
                "upper_bound_kwh": est["upper_bound_kwh"],
                "power_kw": est["power_kw"],
                "power_basis": est["power_basis"],
                "excluded_reason": est["excluded_reason"],
                "weather": est["weather"],
            }
        )

    quality, quality_reasons = _quality_label(bool(clipped), power_basis, weather_ok_any)
    if overlapped_ids:
        quality_reasons = quality_reasons + [f"{len(overlapped_ids)} session(s) overlap and were excluded from totals"]
    if invalid:
        quality_reasons = quality_reasons + [f"{len(invalid)} session(s) have non-positive duration and were excluded"]

    cost = None
    if tariff is not None:
        cost = {
            "amount": round(totals["baseline_kwh"] * tariff["per_kwh"], 2),
            "currency": tariff["currency"],
            "tariff_per_kwh": tariff["per_kwh"],
            "basis": "estimated energy × configured tariff",
        }

    def _num(value):
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return success(
        {
            "ac_id": asset["ac_id"],
            "asset_uuid": str(asset["asset_uuid"]) if asset.get("asset_uuid") else None,
            "asset_code": asset.get("asset_code"),
            "specs": {
                "make": asset.get("ac_make_company"),
                "model": asset.get("ac_model_number"),
                "type": asset.get("ac_type"),
                "serial": asset.get("ac_serial_number"),
                "power_rating_kw": _num(asset.get("power_rating_kw")),
                "capacity_ton": _num(asset.get("ac_capacity_ton")),
                "star_rating": asset.get("ac_star_rating"),
                "status": asset.get("operational_status"),
                "lifecycle_status": asset.get("lifecycle_status"),
                "room": asset.get("room"),
                "floor": asset.get("floor"),
                "block": asset.get("block"),
                "department": asset.get("department"),
                "college": asset.get("college"),
                "installation_year": asset.get("installation_year"),
                "installation_date": str(asset["installation_date"]) if asset.get("installation_date") else None,
            },
            "range": {"from": from_date.isoformat(), "to": to_date.isoformat()},
            "model": {
                "version": (
                    em.MODEL_VERSION_CAPACITY_DERIVED
                    if power_basis == "capacity-derived"
                    else em.MODEL_VERSION_BASELINE
                ),
                "load_factor": load_factor,
                "power_basis": power_basis,
                "assumptions": lf_assumptions
                + (
                    [
                        "input power derived from cooling capacity via the configured kW-per-ton rule (provisional, not a manufacturer rating)",
                    ]
                    if power_basis == "capacity-derived"
                    else [
                        "rated electrical input power is used when present; cooling capacity alone is never treated as electrical kW",
                    ]
                )
                + [
                    "open sessions accrue to request time and are labeled provisional",
                ],
            },
            "sessions": session_rows,
            "totals": totals,
            "daily": [daily[day] for day in sorted(daily)],
            "overlaps": overlaps,
            "invalid_sessions": invalid,
            "cost": cost,
            "cost_unavailable_reason": None if tariff is not None else tariff_notes[0] if tariff_notes else None,
            "quality": quality,
            "quality_reasons": quality_reasons,
            "weather": (
                {"available": True, "source": "Open-Meteo"}
                if weather_available
                else {"available": False, "reason": weather_error, "source": "Open-Meteo"}
            ),
            "calculated_at": now.isoformat(),
        }
    )


# ── GET /api/v1/energy/summary ────────────────────────────────────────────────


@energy_v1_bp.route("/api/v1/energy/summary", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(60, 60, "energy-summary")
def energy_summary():
    """Aggregate estimates across the caller's scope, built per-AC then summed.

    Sessions are fetched bounded (limit, default 1000) with asset specs in
    ONE join — one row per session, so joins can never fan totals out. Group
    aggregates key off the same effective labels as every other reader.
    """
    from app.api.v1.assets import InvalidFilter, _build_filters
    from app.api.v1.sessions import asset_role_scope

    try:
        from_date, to_date = _parse_range(request.args)
    except ValueError as exc:
        return error("INVALID_DATE", str(exc), 400)
    try:
        load_factor, lf_assumptions, _ = _load_factor(request.args)
    except ValueError as exc:
        return error("INVALID_LOAD_FACTOR", str(exc), 400)
    try:
        limit = min(1000, max(1, int(request.args.get("limit", 1000))))
    except ValueError:
        return error("INVALID_PAGINATION", "limit must be an integer.", 400)
    tariff, tariff_notes = _tariff()
    tz = em.campus_tzinfo()
    range_start, range_end = _range_bounds(from_date, to_date, tz)
    now = datetime.now(timezone.utc)

    user = get_current_user()
    try:
        where, params = _build_filters(request.args)
    except InvalidFilter as exc:
        return error("INVALID_FILTER", str(exc), 400)
    scope_where, scope_params, scope_label = asset_role_scope(user)
    where.extend(scope_where)
    params.extend(scope_params)
    where_sql = " AND ".join(where)

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT s.id, s.ac_id, s.started_at, s.ended_at,"
                " a.power_rating_kw, a.ac_capacity_ton,"
                f" {location_sql.label('room')} AS room,"
                f" {location_sql.label('department')} AS department,"
                f" {location_sql.label('block')} AS block"
                " FROM ac_usage_sessions s"
                f" JOIN acs a ON a.ac_id = s.ac_id {location_sql.LOCATION_JOIN}"
                f" {location_sql.EFFECTIVE_JOINS} WHERE {where_sql}"
                " ORDER BY s.started_at LIMIT %s",
                tuple(params) + (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("energy summary failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ENERGY_FAILED", "Unable to compute the energy summary.", 500)

    try:
        series, _, _ = wx.get_hourly_series()
        weather_available = True
    except wx.WeatherUnavailable as exc:
        series, weather_available, weather_error = None, False, str(exc)

    totals = {"runtime_hours": 0.0, "baseline_kwh": 0.0, "upper_bound_kwh": 0.0,
              "sessions": 0, "open_sessions": 0, "assets": 0, "excluded": 0}
    # Per-asset power basis tallies — an aggregate mixes rated and
    # capacity-derived inputs, so the response reports how many of each
    # instead of pretending one model version covers the whole fleet.
    power_basis_counts: dict[str, int] = {"rated": 0, "capacity-derived": 0}
    assets_seen: set[str] = set()
    groups: dict[str, dict[str, dict]] = {"room": {}, "department": {}, "building": {}}
    daily: dict[str, dict] = {}
    overlap_inputs: dict[str, list] = {}
    weather_covered = 0.0
    weather_temp_accum = 0.0

    # First pass: clip every session to the range (open sessions accrue to
    # min(now, range end)). Nothing is aggregated yet.
    clipped: list[dict] = []
    for row in rows:
        start, end = row["started_at"], row["ended_at"]
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end is not None and end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        is_open = end is None
        effective_end = min(end or now, range_end)
        clip_start = max(start, range_start)
        if effective_end <= clip_start:
            continue
        clipped.append({"row": row, "start": clip_start, "end": effective_end, "is_open": is_open})
        overlap_inputs.setdefault(row["ac_id"], []).append(
            {"id": str(row["id"]), "start": clip_start, "end": effective_end}
        )

    # Overlaps are detected per asset BEFORE aggregation, so corrupted
    # history can never inflate a total — the finding itself is the report.
    # Overlapped sessions still count in session tallies (they are real
    # records); only their runtime/kwh contributions are backed out.
    overlap_findings = []
    overlapped_ids: set[str] = set()
    for ac_id, items in overlap_inputs.items():
        for finding in em.detect_overlaps(items):
            overlap_findings.append({**finding, "ac_id": ac_id})
            overlapped_ids.add(finding["session_a"])
            overlapped_ids.add(finding["session_b"])

    for item in clipped:
        row = item["row"]
        clip_start, effective_end, is_open = item["start"], item["end"], item["is_open"]
        assets_seen.add(row["ac_id"])
        totals["sessions"] += 1
        if is_open:
            totals["open_sessions"] += 1
        hours = (effective_end - clip_start).total_seconds() / 3600.0
        resolution = em.resolve_power(row.get("power_rating_kw"), row.get("ac_capacity_ton"))
        power_kw = resolution["kw"]
        base = em.baseline_kwh(power_kw, hours, load_factor) if power_kw is not None else None
        upper = em.upper_bound_kwh(power_kw, hours) if power_kw is not None else None
        if power_kw is not None:
            power_basis_counts[resolution["basis"]] += 1
        if str(row["id"]) in overlapped_ids:
            totals["excluded"] += 1
            base = None
            upper = None
        elif base is None:
            totals["excluded"] += 1
        else:
            totals["runtime_hours"] += hours
            totals["baseline_kwh"] += base
            totals["upper_bound_kwh"] += upper
        if series is not None and weather_available and hours > 0:
            aligned = wx.align_session(clip_start, effective_end, series)
            if aligned["covered_hours"] > 0 and aligned["mean_temp_c"] is not None:
                weather_covered += aligned["covered_hours"]
                weather_temp_accum += aligned["mean_temp_c"] * aligned["covered_hours"]
        for level, label_key in (("room", "room"), ("department", "department"), ("building", "block")):
            label = row.get(label_key) or "Unknown"
            bucket = groups[level].setdefault(
                label, {"key": label, "runtime_hours": 0.0, "baseline_kwh": 0.0,
                        "sessions": 0, "assets": set()})
            bucket["sessions"] += 1
            bucket["assets"].add(row["ac_id"])
            if base is not None:
                bucket["runtime_hours"] += hours
                bucket["baseline_kwh"] += base
        for day, day_hours in em.split_at_midnight(clip_start, effective_end, tz):
            bucket = daily.setdefault(
                day, {"date": day, "runtime_hours": 0.0, "baseline_kwh": 0.0, "sessions": 0})
            bucket["runtime_hours"] += day_hours
            if base is not None and hours > 0:
                bucket["baseline_kwh"] += base * (day_hours / hours)
            bucket["sessions"] += 1

    totals["assets"] = len(assets_seen)

    mean_temp = (weather_temp_accum / weather_covered) if weather_covered > 0 else None
    band, band_note = em.classify_demand(mean_temp)
    cost = None
    if tariff is not None:
        cost = {"amount": round(totals["baseline_kwh"] * tariff["per_kwh"], 2),
                "currency": tariff["currency"], "tariff_per_kwh": tariff["per_kwh"],
                "basis": "estimated energy × configured tariff"}

    def _group_out(level):
        return [
            {"key": key, "units": None, "runtime_hours": entry["runtime_hours"],
             "baseline_kwh": entry["baseline_kwh"], "sessions": entry["sessions"],
             "assets": len(entry["assets"])}
            for key, entry in sorted(groups[level].items())
        ]

    return success(
        {
            "range": {"from": from_date.isoformat(), "to": to_date.isoformat()},
            "scope": scope_label,
            "model": {"version": em.MODEL_VERSION_BASELINE, "load_factor": load_factor,
                      "assumptions": lf_assumptions
                      + ["each session's input power is resolved per asset (rated first, capacity-derived only when rated is absent)"],
                      "power_basis_counts": power_basis_counts,
                      "derived_model_version": em.MODEL_VERSION_CAPACITY_DERIVED},
            "totals": totals,
            "daily": [daily[day] for day in sorted(daily)],
            "groups": {level: _group_out(level) for level in ("room", "department", "building")},
            "campus": {"runtime_hours": totals["runtime_hours"], "baseline_kwh": totals["baseline_kwh"]},
            "overlaps": overlap_findings,
            "cost": cost,
            "cost_unavailable_reason": None if tariff is not None else (tariff_notes[0] if tariff_notes else None),
            "weather": (
                {"available": True, "source": "Open-Meteo", "mean_temp_c": mean_temp,
                 "covered_hours": weather_covered, "demand_band": band, "demand_note": band_note}
                if weather_available else
                {"available": False, "reason": weather_error, "source": "Open-Meteo"}
            ),
            "calculated_at": now.isoformat(),
        }
    )


# ── GET /api/v1/weather/now ───────────────────────────────────────────────────


@energy_v1_bp.route("/api/v1/weather/now", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
@rate_limit(60, 60, "weather-now")
def weather_now():
    """Current campus conditions for dashboard weather cards.

    Always 200 when the contract holds: provider failure yields
    ``available: false`` (never a 500, never invented values), so dashboard
    rendering never depends on live provider availability.
    """
    return success(wx.current_conditions())
