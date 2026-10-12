"""Legacy free-text status -> constrained operational_status (Phase 3.1).

Single source of truth for the vocabulary mapping in 01-assessment §4 —
used by the write endpoints, the import commit path and anywhere else that
accepts a raw ``current_status`` value.
"""

from __future__ import annotations

OPERATIONAL_STATUSES = ("WORKING", "FAULT", "MAINTENANCE", "OFFLINE", "UNKNOWN")

_WORKING = {"working"}
_FAULT = {"not working", "damaged", "fault"}
_MAINTENANCE = {"under maintenance", "maintenance"}
_OFFLINE = {"inactive", "offline"}


def derive_operational_status(current_status: str | None) -> str:
    """Map any incoming status text to the constrained vocabulary."""
    key = (current_status or "").strip().lower()
    if key in _WORKING:
        return "WORKING"
    if key in _FAULT:
        return "FAULT"
    if key in _MAINTENANCE:
        return "MAINTENANCE"
    if key in _OFFLINE:
        return "OFFLINE"
    return "UNKNOWN"


def normalize_status_filter(value: str | None) -> str | None:
    """Resolve a list-filter status argument (Title Case UI values, upper-case
    canonical values, or legacy free text) to operational_status, or None when
    the caller passed an unknown token (callers then fall back to an exact,
    case-insensitive ILIKE against operational_status)."""
    key = (value or "").strip()
    if not key:
        return None
    canonical = key.upper().replace(" ", "_")
    if canonical in OPERATIONAL_STATUSES:
        return canonical
    derived = derive_operational_status(key)
    # A value the mapper knows (e.g. "Working", "not working") resolves
    # canonically; genuinely unknown text returns UNKNOWN only when it does
    # not match any legacy word — in that case let the caller LIKE-match it.
    if key.lower() in _WORKING | _FAULT | _MAINTENANCE | _OFFLINE:
        return derived
    return None
