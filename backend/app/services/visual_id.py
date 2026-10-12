"""Visual Asset ID — the ONE centralized human-readable asset identifier (§VIS).

A Visual Asset ID is DERIVED, never stored: ``generate_visual_asset_id``
renders an asset's current effective/resolved location plus a short UUID
suffix, e.g. ``NITTTR-MKB-1F-ERD-IH-C5-C87C``. The UUID stays the immutable
technical identity (database keys, relationships, API references, QR target,
routing, audit); the Visual ID is display only and changes whenever the
underlying location data changes.

Normalization rules (deterministic, no campus names hardcoded, no fragile
slicing — every rule below is data-shape based):
  * tokenize on non-alphanumerics (spaces, punctuation, parens, hyphens
    collapse identically; casing is folded by upper-casing the output);
  * drop generic level nouns (block/floor/room/...) and filler words
    (of/the/and/...) — they carry no identity;
  * several tokens -> initials (short tokens contribute whole, so codes and
    digits survive: "CE08" -> "CE08", "lab-1" -> "L1", "S&P" -> "SP");
  * one token -> uppercased whole when short (<=6 chars, so "ERD",
    "VESTAR", "HOSTEL"/"Hostel" all spell identically), else first 3;
  * floors: a numeric token wins ("Floor 01" -> "1F"), else ordinal words
    ("First" -> "1F", "Ground" -> "GF", case-insensitive), else name code + F;
  * rooms are paren-aware: "Innovation Hub (Cabin 5)" -> main initials
    ("IH") + "-" + parenthetical compacted ("Cabin 5" -> "C5") = "IH-C5".
Missing levels contribute NOTHING (no NULL/UNDEFINED/empty segments, no
"--"); a row with no location at all falls back to "AC-<suffix>".

UUID suffix: last N hex chars (default 8, ``VISUAL_ID_SUFFIX_LEN``; the
hyphens are stripped first). Sizing: 303 assets need ~2**32 space to be
safe — 4 hex chars collide with ~50% probability at n=303
(303*302/(2*65536)), 8 chars with ~0.001%. Run
``backend/scripts/check_visual_id_collisions.py`` against a database to
verify; if it ever reports a collision, raise the env var (no code change).

This module is the ONLY place Visual IDs are computed: API serialization,
exports and the batch QR labels all call ``visual_id_for_row``. The
frontend displays the server string verbatim (no second implementation).
"""

from __future__ import annotations

import os
import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

# Generic level nouns + filler words: identity-free in every campus.
STOPWORDS = frozenset(
    {
        "block",
        "floor",
        "room",
        "building",
        "hall",
        "house",
        "center",
        "centre",
        "tower",
        "wing",
        "complex",
        "campus",
        "of",
        "the",
        "and",
        "for",
        "a",
        "an",
        "to",
        "in",
        "on",
    }
)

# Floor ordinal words (case-insensitive) -> code letter/digit.
FLOOR_ORDINALS = {
    "ground": "G",
    "first": "1",
    "second": "2",
    "third": "3",
    "fourth": "4",
    "fifth": "5",
    "sixth": "6",
    "seventh": "7",
    "eighth": "8",
    "ninth": "9",
    "tenth": "10",
    "eleventh": "11",
    "twelfth": "12",
}

# Literal spellings that mean "no value" (import sentinels can leak these as
# text through direct API writes); they must never become ID segments.
_BLANK_LITERALS = frozenset({"", "null", "none", "n/a", "na", "-", "—"})

_DEFAULT_SUFFIX_LEN = 8


def _clean(value) -> str | None:
    """Trimmed text, or None for blanks/placeholders (never invent)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.strip().lower() in _BLANK_LITERALS:
        return None
    return text


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(value)


def _initials(tokens: list[str]) -> str:
    """Initials, keeping short tokens whole so codes/digits survive."""
    return "".join(t.upper() if len(t) <= 2 else t[0].upper() for t in tokens)


def _abbr_name(value) -> str | None:
    """Generic name code: initials of significant tokens, short singles kept."""
    text = _clean(value)
    if text is None:
        return None
    tokens = [t for t in _tokens(text) if t.lower() not in STOPWORDS] or _tokens(text)[:1]
    if not tokens:
        return None
    if len(tokens) == 1:
        upper = tokens[0].upper()
        return upper if len(upper) <= 6 else upper[:3]
    return _initials(tokens)


def _abbr_floor(value) -> str | None:
    """Floor code: numeric token wins, else ordinal word, else name code + F."""
    text = _clean(value)
    if text is None:
        return None
    tokens = _tokens(text)
    for token in tokens:
        if token.isdigit():
            return f"{int(token)}F"
    for token in tokens:
        mapped = FLOOR_ORDINALS.get(token.lower())
        if mapped is not None:
            return f"{mapped}F"
    base = _abbr_name(text)
    return f"{base}F" if base else None


def _abbr_room(text) -> str | None:
    """Paren-aware room code: main initials + compacted parentheticals."""
    cleaned = _clean(text)
    if cleaned is None:
        return None
    parts = [p for p in re.split(r"[()]", cleaned) if p.strip()]
    if not parts:
        return None
    code = _abbr_name(parts[0])
    if code is None:
        return None
    for paren in parts[1:]:
        compact = _abbr_name(paren)
        if compact:
            code += f"-{compact}"
    return code


def _suffix_len(explicit: int | None = None) -> int:
    if explicit is not None:
        try:
            return max(4, int(explicit))
        except (TypeError, ValueError):
            pass
    try:
        return max(4, int(os.getenv("VISUAL_ID_SUFFIX_LEN", str(_DEFAULT_SUFFIX_LEN))))
    except (TypeError, ValueError):
        return _DEFAULT_SUFFIX_LEN


def generate_visual_asset_id(
    *,
    college=None,
    block=None,
    floor=None,
    department=None,
    room_name=None,
    room_type=None,
    room_id=None,
    room_number=None,
    asset_uuid=None,
    suffix_len: int | None = None,
) -> str:
    """Render the Visual Asset ID for one asset's effective location.

    Segment order: COLLEGE-BLOCK-FLOOR-DEPARTMENT-ROOM-SUFFIX; absent
    segments vanish (never placeholders). The room prefers the canonical
    name, then the (effective) type, then the id, then the source number —
    mirroring what the detail page displays.
    """
    room_text = _clean(room_name) or _clean(room_type) or _clean(room_id) or _clean(room_number)
    segments = [
        _abbr_name(college),
        _abbr_name(block),
        _abbr_floor(floor),
        _abbr_name(department),
        _abbr_room(room_text) if room_text else None,
    ]
    compact = re.sub(r"[^0-9a-fA-F]", "", str(asset_uuid or ""))
    suffix = compact[-_suffix_len(suffix_len) :].upper() or None
    located = [s for s in segments if s]
    if not located and not suffix:
        return "AC-UNRESOLVED"
    return "-".join((located or ["AC"]) + ([suffix] if suffix else []))


def visual_id_for_row(row: dict) -> str:
    """Visual ID for one API/asset row (effective flats + canonical room)."""
    return generate_visual_asset_id(
        college=row.get("college_name"),
        block=row.get("block_name"),
        floor=row.get("floor_name"),
        department=row.get("department_name"),
        room_name=row.get("canon_room_name"),
        room_type=row.get("room_type"),
        room_id=row.get("room_id"),
        room_number=row.get("source_room_number_value"),
        asset_uuid=row.get("asset_uuid"),
    )
