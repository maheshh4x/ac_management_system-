"""Open-Meteo campus weather client (read-only, cached, never load-bearing).

Single place where campus coordinates live (env-overridable, defaults are
the NITTTR Chennai campus). One process-wide TTL cache serves every
consumer, so a dashboard with N assets still issues at most one provider
request per TTL window — never one request per AC, never one per refresh.

Failure policy: timeouts, HTTP errors and malformed payloads all surface
as :class:`WeatherUnavailable`. Callers fall back to the baseline energy
model; the dashboard renders an explicit unavailable/stale state. A failed
refresh keeps serving the last good series flagged ``stale=True`` rather
than inventing values.

Every hourly record carries ``is_forecast`` (hour start vs provider fetch
time): forecast values are never presented as historical measurements.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)


class WeatherUnavailable(Exception):
    """The provider could not supply usable data (timeout/error/malformed)."""


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def config() -> dict:
    """ campus weather configuration — the ONLY place coordinates live. """
    return {
        "latitude": _env_float("WEATHER_LATITUDE", 13.0878),
        "longitude": _env_float("WEATHER_LONGITUDE", 80.2785),
        "timezone": os.getenv("WEATHER_TIMEZONE", "Asia/Kolkata"),
        "timeout_s": _env_float("WEATHER_TIMEOUT_S", 10.0),
        "cache_ttl_s": _env_int("WEATHER_CACHE_TTL_S", 600),
        "past_days": _env_int("WEATHER_PAST_DAYS", 7),
    }


def request_url(cfg: dict | None = None) -> str:
    """The provider URL (variables requested by the energy specification)."""
    cfg = cfg or config()
    variables = ",".join(
        [
            "temperature_2m",
            "relative_humidity_2m",
            "dew_point_2m",
            "apparent_temperature",
            "rain",
        ]
    )
    return (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={cfg['latitude']}&longitude={cfg['longitude']}"
        f"&hourly={variables}"
        f"&timezone={cfg['timezone'].replace('/', '%2F')}"
        f"&past_days={cfg['past_days']}"
    )


def _http_get(url: str, timeout_s: float):
    """Single network seam — monkeypatched in tests, never called per-AC."""
    response = requests.get(url, timeout=timeout_s)
    response.raise_for_status()
    return response.json()


def _parse_series(payload: dict, fetched_at: datetime) -> tuple[list[dict], dict]:
    """Validate + normalize the provider payload into UTC hourly records."""
    if not isinstance(payload, dict):
        raise WeatherUnavailable("provider returned a non-object payload")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise WeatherUnavailable("provider payload has no hourly block")
    times = hourly.get("time")
    temps = hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temps, list) or not times:
        raise WeatherUnavailable("provider payload is missing time/temperature series")
    n = len(times)
    for key in ("relative_humidity_2m", "dew_point_2m", "apparent_temperature", "rain"):
        values = hourly.get(key)
        if values is not None and (not isinstance(values, list) or len(values) != n):
            raise WeatherUnavailable(f"provider series {key!r} has wrong length")

    offset_s = payload.get("utc_offset_seconds", 0)
    try:
        offset_s = int(offset_s)
    except (TypeError, ValueError):
        offset_s = 0

    def _num(values, index):
        if values is None:
            return None
        value = values[index]
        return value if isinstance(value, (int, float)) else None

    series = []
    for index, stamp in enumerate(times):
        try:
            # Provider stamps are campus-local ISO ("YYYY-MM-DDTHH:MM");
            # the offset converts them to true UTC instants.
            naive = datetime.fromisoformat(str(stamp))
        except ValueError:
            raise WeatherUnavailable(f"provider time {stamp!r} is not ISO-8601") from None
        aware = naive.replace(tzinfo=timezone(timedelta(seconds=offset_s)))
        start_utc = aware.astimezone(timezone.utc)
        series.append(
            {
                "time_utc": start_utc,
                "temp_c": _num(temps, index),
                "humidity_pct": _num(hourly.get("relative_humidity_2m"), index),
                "dew_point_c": _num(hourly.get("dew_point_2m"), index),
                "apparent_c": _num(hourly.get("apparent_temperature"), index),
                "rain_mm": _num(hourly.get("rain"), index),
                "is_forecast": start_utc > fetched_at,
            }
        )
    meta = {
        "source": "Open-Meteo",
        "fetched_at": fetched_at,
        "hours": len(series),
    }
    return series, meta


_cache: dict = {}
_cache_lock = threading.Lock()


def get_hourly_series(*, force_refresh: bool = False) -> tuple[list[dict], dict, bool]:
    """Return ``(series, meta, stale)`` — cached, TTL-bounded, failure-safe.

    ``stale`` is True only when a failed refresh falls back to the previous
    series. Raises :class:`WeatherUnavailable` when there is nothing usable
    at all (first fetch fails).
    """
    cfg = config()
    now = time.time()
    with _cache_lock:
        cached = _cache.get("series")
        if cached and not force_refresh and now - _cache.get("at", 0) < cfg["cache_ttl_s"]:
            meta = dict(_cache["meta"])
            meta["served_from_cache"] = True
            return list(cached), meta, False
    try:
        fetched_at = datetime.now(timezone.utc)
        payload = _http_get(request_url(cfg), cfg["timeout_s"])
        series, meta = _parse_series(payload, fetched_at)
    except WeatherUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - timeouts, DNS, HTTP errors, bad JSON
        logger.warning("weather provider request failed: %s", exc)
        with _cache_lock:
            if _cache.get("series"):
                meta = dict(_cache["meta"])
                meta["served_from_cache"] = True
                return list(_cache["series"]), meta, True
        raise WeatherUnavailable(f"weather provider unreachable: {exc}") from exc
    with _cache_lock:
        _cache["series"] = series
        _cache["at"] = now
        _cache["meta"] = meta
    meta = dict(meta)
    meta["served_from_cache"] = False
    return series, meta, False


def reset_cache() -> None:
    """Test hook: drop the cached series."""
    with _cache_lock:
        _cache.clear()


def align_session(
    start: datetime, end: datetime, series: list[dict]
) -> dict:
    """Attribute a session interval to hourly records with partial-hour math.

    Returns ``{covered_hours, total_hours, missing: [iso...], mean_temp_c,
    mean_humidity_pct}``. Means are duration-weighted over covered hours
    only; hours with a null temperature still count as covered for runtime
    but not for the mean. ``sum(parts) == total`` always holds — verified
    by tests, never assumed.
    """
    from app.services import energy_models as em

    total = max(0.0, (end - start).total_seconds() / 3600.0)
    covered = 0.0
    temp_accum = 0.0
    temp_hours = 0.0
    hum_accum = 0.0
    hum_hours = 0.0
    missing = []
    for point in series:
        hour_start = point["time_utc"]
        hour_end = hour_start + timedelta(hours=1)
        part = em.overlap_hours(start, end, hour_start, hour_end)
        if part <= 0:
            continue
        covered += part
        if point.get("temp_c") is not None:
            temp_accum += point["temp_c"] * part
            temp_hours += part
        if point.get("humidity_pct") is not None:
            hum_accum += point["humidity_pct"] * part
            hum_hours += part
        if point.get("temp_c") is None:
            missing.append(hour_start.isoformat())
    return {
        "covered_hours": covered,
        "total_hours": total,
        "missing": missing,
        "mean_temp_c": (temp_accum / temp_hours) if temp_hours > 0 else None,
        "mean_humidity_pct": (hum_accum / hum_hours) if hum_hours > 0 else None,
    }


def current_conditions(*, now: datetime | None = None) -> dict:
    """Current campus conditions for the dashboard weather card.

    Never raises: provider failure yields ``available: False`` with a
    reason, and a failed refresh serves the last good series as stale.
    """
    from app.services import energy_models as em

    moment = now or datetime.now(timezone.utc)
    try:
        series, meta, stale = get_hourly_series()
    except WeatherUnavailable as exc:
        return {"available": False, "reason": str(exc), "source": "Open-Meteo"}
    current = None
    for point in series:
        if point["time_utc"] <= moment:
            current = point
        else:
            break
    if current is None:
        return {"available": False, "reason": "no current hour in provider series", "source": "Open-Meteo"}
    band, explanation = em.classify_demand(current.get("temp_c"))
    return {
        "available": True,
        "stale": stale,
        "source": "Open-Meteo",
        "observed_at": current["time_utc"].isoformat(),
        "fetched_at": meta["fetched_at"].isoformat(),
        "temp_c": current.get("temp_c"),
        "humidity_pct": current.get("humidity_pct"),
        "dew_point_c": current.get("dew_point_c"),
        "apparent_c": current.get("apparent_c"),
        "rain_mm": current.get("rain_mm"),
        "demand_band": band,
        "demand_note": explanation,
    }
