"""Centralized location resolution — the ONLY matching logic in the backend (§6).

Two permanently separate concepts live side by side here:

* SOURCE location     — exactly what an import/API supplied (``source_*``).
* CONFIRMED location  — canonical ids in the campus hierarchy
  (``colleges/blocks/floors/rooms/departments``) an asset is assigned in.

Resolution is PER HIERARCHY LEVEL and never all-or-nothing: an unresolvable
room leaves block/floor/department resolved (``PARTIAL``), preserves the
source values, and records why a level is unresolved.

Invariants
----------
* Per-field status is a pure function of the final id:
      id set                      -> RESOLVED
      id NULL + source present    -> UNRESOLVED (0 matches) /
                                      AMBIGUOUS (>1 matches)
      id NULL + source blank      -> MISSING
* Matching is deterministic only: exact canonical id, registered alias
  (``location_aliases``), scoped normalized name equality (``normalize``).
  Ambiguity is NEVER auto-assigned; there is no fuzzy matching.
* Matching priority per level (D-ID rules — documented here, enforced in
  ``resolve``):
      explicit canonical source id (block_id / floor_id / room_id), when it
          references an existing entity AND is consistent with the
          confirmed chain  >  registered alias  >  exact normalized
          canonical name  >  UNRESOLVED / AMBIGUOUS.
  D-ID-1: an explicit id that names an existing, scope-consistent entity is
      authoritative for its level; a disagreeing source NAME is recorded in
      the notes, never silently applied.
  D-ID-2: an explicit id that names NOTHING falls back to alias/name
      matching (which may still resolve, else UNRESOLVED / AMBIGUOUS); the
      unknown id is recorded in the notes for audit.
  D-ID-3: an explicit id that contradicts a MANUALLY confirmed ancestor scope
      is left UNRESOLVED with a clear note — manual pins always win.
  D-ID-4: an explicit id that contradicts any other confirmed scope applies
      anyway (canonical ids outrank names); the contradicted scope is
      cleared with a note and re-derives only when it carries no source of
      its own (name or id) — otherwise it stays UNRESOLVED for review, so a
      contradiction is never force-fitted into a chain.
* Re-import safety: a present-but-unresolvable (or ambiguous) source value
  NEVER downgrades a previously confirmed non-manual value — the previous
  id is kept (recorded in ``LocationResolution.kept`` and the notes) while
  the failed source stays stored for review. Only a later VALID match
  replaces a non-manual value; only an explicit manual clear removes a
  manual pin; blank source keeps confirmed values as before.
* Provenance priority along every hierarchy edge:
      manual == match == derived  >  kept-previous
  A confirmed child chain fixes non-manual parents; a kept-previous node
  drops when it contradicts confirmed ancestors; a manual node always wins
  over non-manual nodes (the DB composite FKs win over everything, so a
  contradiction with manual input is resolved in favour of the hierarchy).
  This is what makes re-imports safe (§11): blank source never downgrades a
  confirmed value, an incomplete import never clears a manual pin, and the
  resolver can never emit a chain the 014 composite FKs would reject.
* No level is ever invented from another level's business data (a room is
  never created or assigned from a department match).

All write paths go through :func:`upsert_parts` / :func:`apply_many` /
:func:`sync_acs` (the single-writer contract from migration 014):
asset_locations first, then the acs mirror columns, in the caller's
transaction.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass

logger = logging.getLogger(__name__)

LEVELS = ("college", "block", "floor", "department", "room")
RESOLVED = "RESOLVED"
UNRESOLVED = "UNRESOLVED"
MISSING = "MISSING"
AMBIGUOUS = "AMBIGUOUS"

OVERALL_ASSIGNED = "ASSIGNED"
OVERALL_PARTIAL = "PARTIAL"
OVERALL_PENDING = "PENDING"

# Provenance classes: "confirmed" nodes participate in chain authority;
# "prev" (kept from a previous record) always yields to them.
_CONFIRMED = frozenset({"manual", "match", "derive"})

# Source dict key (as produced by the canonical import lookup) -> asset_locations
# column. block_id / floor_id / room_id are explicit canonical source ids
# (migration 016): they participate in matching first (D-ID) and are
# preserved like every other source column.
SOURCE_FIELDS = {
    "college": "source_college_value",
    "block": "source_block_value",
    "block_id": "source_block_id_value",
    "floor": "source_floor_value",
    "floor_id": "source_floor_id_value",
    "department": "source_department_value",
    "room_type": "source_room_type_value",
    "room_number": "source_room_number_value",
    "room_id": "source_room_id_value",
}

# Source key per hierarchy level (room level is carried by room_number).
_LEVEL_SOURCE = {
    "college": "college",
    "block": "block",
    "floor": "floor",
    "department": "department",
    "room": "room_number",
}

# Explicit-id source key per level (college/department have no id key: the
# source dataset carries ids only for block, floor and room).
_LEVEL_ID_KEY = {
    "block": "block_id",
    "floor": "floor_id",
    "room": "room_id",
}


def normalize(value) -> str:
    """Controlled normalization: NFKD -> ascii -> lower -> strip non-alnum.

    Byte-identical to the historical ``routes.acs._match_key`` (which now
    delegates here), so whitespace, casing and punctuation variants collapse
    to one key while nothing fuzzy can match.
    """
    if value is None:
        return ""
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", value)


def clean_value(value):
    """Trim-only cleaning for caller-supplied source values (the importer
    applies its own sentinel handling — ``_empty_to_none`` — before calling
    in, so no fuzzy logic lives here)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value or None


# ─────────────────────────────────────────────────────────────────────────
# Campus directory (batch preloads — no N+1; §26)
# ─────────────────────────────────────────────────────────────────────────


def load_directory(cursor) -> dict:
    """One SELECT for the campus chain + one for departments + one for
    aliases. Shape is a superset of the historical ``_load_campus_directory``
    (which delegates here): same college/block/floor/room entries, plus
    standalone departments (a department resolves even when no room uses it)
    and an alias index keyed (entity_type, normalized alias).
    """
    cursor.execute(
        "SELECT c.college_id, c.college_name, b.block_id, b.block_name, "
        "f.floor_id, f.floor_name, d.department_id, d.department_name, "
        "r.room_id, r.room_name, r.room_type, r.department_id AS room_department_id "
        "FROM colleges c "
        "LEFT JOIN blocks b ON b.college_id = c.college_id "
        "LEFT JOIN floors f ON f.block_id = b.block_id "
        "LEFT JOIN rooms r ON r.floor_id = f.floor_id "
        "LEFT JOIN departments d ON d.department_id = r.department_id"
    )
    colleges, blocks, floors, departments, rooms = {}, {}, {}, {}, []
    for row in cursor.fetchall():
        if row["college_id"]:
            colleges.setdefault(("name", normalize(row["college_name"])), []).append(row["college_id"])
            colleges.setdefault(("id", normalize(row["college_id"])), []).append(row["college_id"])
        if row["block_id"]:
            blocks.setdefault((row["college_id"], normalize(row["block_name"])), []).append(row)
            blocks.setdefault((row["college_id"], normalize(row["block_id"])), []).append(row)
        if row["floor_id"]:
            floors.setdefault((row["block_id"], normalize(row["floor_name"])), []).append(row)
            floors.setdefault((row["block_id"], normalize(row["floor_id"])), []).append(row)
        if row["room_id"]:
            rooms.append(row)

    # Departments independently of rooms: a department with zero rooms is
    # still canonical master data and must resolve deterministically.
    cursor.execute("SELECT department_id, department_name FROM departments")
    for row in cursor.fetchall():
        if row["department_id"]:
            departments.setdefault(normalize(row["department_name"]), set()).add(row["department_id"])
            departments.setdefault(normalize(row["department_id"]), set()).add(row["department_id"])

    aliases: dict[tuple[str, str], set[str]] = {}
    cursor.execute("SELECT entity_type, normalized_alias, entity_id " "FROM location_aliases WHERE is_active")
    for row in cursor.fetchall():
        aliases.setdefault((row["entity_type"], row["normalized_alias"]), set()).add(row["entity_id"])

    campus = {
        "colleges": colleges,
        "blocks": blocks,
        "floors": floors,
        "departments": departments,
        "rooms": rooms,
        "aliases": aliases,
    }
    _ensure_indexes(campus)
    return campus


def _ensure_indexes(campus: dict) -> dict:
    """Lazy id -> row indexes (id-exact matching + parent derivation)."""
    idx = campus.get("_by_id")
    if idx is not None:
        return idx
    idx = {"floors": {}, "blocks": {}, "rooms": {}}
    for rows in campus.get("floors", {}).values():
        for row in rows:
            idx["floors"].setdefault(row["floor_id"], row)
    for rows in campus.get("blocks", {}).values():
        for row in rows:
            idx["blocks"].setdefault(row["block_id"], row)
    for row in campus.get("rooms", []):
        idx["rooms"].setdefault(row["room_id"], row)
    # Normalized-id maps for explicit source ids (D-ID): an id supplied as
    # "room-102" still identifies "ROOM-102"; ids are PKs, so at most one
    # entity shares a normalized id and the lookup is unambiguous.
    idx["blocks_by_norm"] = {normalize(bid): row for bid, row in idx["blocks"].items() if bid}
    idx["floors_by_norm"] = {normalize(fid): row for fid, row in idx["floors"].items() if fid}
    idx["rooms_by_norm"] = {normalize(rid): row for rid, row in idx["rooms"].items() if rid}
    campus["_by_id"] = idx
    return idx


def _alias_ids(campus: dict, level: str, key: str) -> list[str]:
    aliases = campus.get("aliases") or {}
    return sorted(aliases.get((level, key), ()))


def _apply_explicit_id(level, src, ids, prov, manual, idx):
    """Validate an explicit canonical source id (D-ID rules).

    Returns ``(outcome, row, note)``:

    * ``("absent", None, None)`` — no explicit id supplied for this level.
    * ``("applied", row, None)`` — the id names an existing entity that is
      consistent with the confirmed ancestor scope (or there is no scope
      yet); the caller takes it as a ``match``-class id.
    * ``("invalid", None, note)`` — the id names nothing (D-ID-2): the
      caller falls back to alias/name matching and preserves the note.
    * ``("conflict", None, note)`` — the id exists but contradicts a
      MANUALLY confirmed ancestor scope (D-ID-3): terminal. (Contradictions
      with non-manual scopes are resolved by the caller in favour of the
      explicit id — D-ID-4 — because canonical ids outrank names.)
    """
    raw = src.get(_LEVEL_ID_KEY[level])
    if not raw:
        return "absent", None, None
    norm_map = {"block": "blocks_by_norm", "floor": "floors_by_norm", "room": "rooms_by_norm"}[level]
    row = (idx.get(norm_map) or {}).get(normalize(raw))
    if row is None:
        return "invalid", None, f"source {level}_id '{raw}' matches no campus {level}"
    if level == "block":
        scope = ids.get("college")
        if scope is not None and row.get("college_id") != scope and "college" in manual:
            return (
                "conflict",
                None,
                f"source block_id '{raw}' is not in the manually confirmed college",
            )
    elif level == "floor":
        scope = ids.get("block")
        if scope is not None and row.get("block_id") != scope and "block" in manual:
            return (
                "conflict",
                None,
                f"source floor_id '{raw}' is not in the manually confirmed block",
            )
    elif level == "room":
        if ids.get("floor") is not None and row.get("floor_id") != ids.get("floor") and "floor" in manual:
            return (
                "conflict",
                None,
                f"source room_id '{raw}' is not on the manually confirmed floor",
            )
        if ids.get("block") is not None and "block" in manual:
            floor_row = (idx.get("floors") or {}).get(row.get("floor_id")) or {}
            if floor_row.get("block_id") != ids.get("block"):
                return (
                    "conflict",
                    None,
                    f"source room_id '{raw}' is not in the manually confirmed block",
                )
    return "applied", row, None


# ─────────────────────────────────────────────────────────────────────────
# Resolution model
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class LocationResolution:
    """Outcome of resolving one asset's source location — level by level."""

    ids: dict[str, str | None]
    statuses: dict[str, str]
    source: dict[str, str | None]
    notes: dict[str, str]
    overall: str
    manual_fields: frozenset[str] = frozenset()
    # Levels whose previously confirmed id was kept even though the new
    # source failed to resolve (re-import safety): the id stays RESOLVED,
    # the failed source stays stored + noted for review.
    kept: frozenset[str] = frozenset()

    @property
    def room_id(self) -> str | None:
        return self.ids.get("room")


def _overall_for(ids: dict[str, str | None]) -> str:
    if ids.get("room"):
        return OVERALL_ASSIGNED
    if any(ids.get(level) for level in ("college", "block", "floor", "department")):
        return OVERALL_PARTIAL
    return OVERALL_PENDING


def resolve(
    source: dict,
    campus: dict,
    *,
    previous_ids: dict | None = None,
    previous_manual: frozenset | set | None = None,
    room_outcome: tuple | None = None,
) -> LocationResolution:
    """Resolve one source-location dict against the campus directory.

    ``source`` keys: college, block, floor, department, room_type,
    room_number (values already sentinel-cleaned by the caller; blank/None
    = "no value"). ``previous_ids``/``previous_manual`` carry the confirmed
    state of an existing record so blank or unresolvable source values never
    downgrade it (§11).

    ``room_outcome`` lets a caller that already made the room decision
    (the legacy import path, whose pinned messages come from
    ``_resolve_room_local``) hand it over as ``(room_id, note, status)``:
    the room level adopts it verbatim — no second matching pass — while
    every other level still resolves normally around it.
    """
    idx = _ensure_indexes(campus)
    src = {key: clean_value(source.get(key)) for key in SOURCE_FIELDS}
    prev_ids = {level: (previous_ids or {}).get(level) for level in LEVELS}
    manual = set(previous_manual or ())

    ids: dict[str, str | None] = {level: None for level in LEVELS}
    prov: dict[str, str] = {level: "none" for level in LEVELS}
    notes: dict[str, str] = {}
    fail: dict[str, str] = {}  # level -> UNRESOLVED | AMBIGUOUS (id still NULL)
    kept: set[str] = set()  # levels kept from prev despite a failed new source (§4)

    def _take(level: str, candidate: str, how: str, note: str | None = None) -> None:
        ids[level] = candidate
        prov[level] = how
        if note:
            notes[level] = note

    def _yield_scope_to_explicit(level: str, row: dict, raw: str) -> None:
        """Clear a non-manual ancestor scope the applied explicit id
        contradicts (D-ID-4): the scope re-derives only when it carries no
        source of its own, otherwise it stays UNRESOLVED for review."""
        pairs: list[tuple[str, str | None]] = []
        if level == "block":
            pairs = [("college", row.get("college_id"))]
        elif level == "floor":
            pairs = [("block", row.get("block_id"))]
        elif level == "room":
            floor_row = (idx.get("floors") or {}).get(row.get("floor_id")) or {}
            pairs = [("floor", row.get("floor_id")), ("block", floor_row.get("block_id"))]
        for scope, canonical in pairs:
            if (
                canonical is not None
                and ids.get(scope) is not None
                and normalize(ids[scope]) != normalize(canonical)
                and scope not in manual
            ):
                notes[scope] = f"{scope} cleared: contradicted by explicit {level}_id '{raw}'"
                ids[scope] = None
                prov[scope] = "none"
                fail.pop(scope, None)

    def _explicit_override(level: str) -> None:
        """D-ID explicit-id pass for one level (runs after its name match).

        An applied id overwrites the name-match result; contradictions with
        non-manual scopes clear the scope (D-ID-4) while manual scopes are
        terminal (D-ID-3); unknown ids fall back to the name result (D-ID-2).
        """
        raw = src.get(_LEVEL_ID_KEY[level])
        if not raw or level in manual:
            return
        outcome, row, note = _apply_explicit_id(level, src, ids, prov, manual, idx)
        id_col = f"{level}_id"
        if outcome == "absent":
            return
        if outcome == "invalid":
            # D-ID-2: keep whatever name matching found; the unknown id is
            # preserved for audit under its own key (never clobbers the
            # level note the review section reads).
            notes[id_col] = note
            return
        if outcome == "conflict":
            # D-ID-3: terminal — a manual ancestor stands its ground.
            ids[level] = None
            prov[level] = "none"
            fail[level] = UNRESOLVED
            notes[level] = note
            return
        # D-ID-1: authoritative. Same entity as the name match = no-op.
        row_id = (row or {}).get(id_col)
        if ids.get(level) is not None and normalize(ids[level]) == normalize(row_id):
            return
        previous_hit = ids.get(level)
        _take(level, row_id, "match")
        fail.pop(level, None)
        if previous_hit is not None:
            notes[level] = (
                f"source {_LEVEL_SOURCE[level]} '{src[_LEVEL_SOURCE[level]]}' disagrees with"
                f" source {id_col} '{raw}'; canonical id applied"
            )
        else:
            notes.pop(level, None)
        _yield_scope_to_explicit(level, row or {}, raw)

    # ── 1. Manual pins are authoritative (§10/§11): skip matching entirely.
    for level in LEVELS:
        if level in manual and prev_ids.get(level):
            _take(level, prev_ids[level], "manual")
            if src.get(_LEVEL_SOURCE[level]):
                notes[level] = f"source '{src[_LEVEL_SOURCE[level]]}' ignored: manually confirmed {level}"
            idkey = _LEVEL_ID_KEY.get(level)
            if idkey and src.get(idkey) and normalize(src[idkey]) != normalize(prev_ids[level]):
                extra = f"source {level}_id '{src[idkey]}' ignored: manually confirmed {level}"
                notes[level] = f"{notes[level]}; {extra}" if notes.get(level) else extra

    # ── 2. Source matching, deterministic and scoped.
    # 2a. college (global)
    if ids["college"] is None and src["college"]:
        key = normalize(src["college"])
        hits: list[str] = list(_alias_ids(campus, "college", key))
        hits += campus.get("colleges", {}).get(("id", key), [])
        hits += campus.get("colleges", {}).get(("name", key), [])
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1:
            _take("college", hits[0], "match")
        elif len(hits) > 1:
            fail["college"] = AMBIGUOUS
            notes["college"] = f"college '{src['college']}' is ambiguous"
        else:
            fail["college"] = UNRESOLVED
            notes["college"] = f"no campus college matches '{src['college']}'"

    # 2b. block (scoped to a resolved college exactly like the historical
    #     matcher: once scoped, only that college's blocks are visible)
    if ids["block"] is None and src["block"]:
        key = normalize(src["block"])
        scope = ids["college"]
        hits = []
        if scope is None:
            for cand in _alias_ids(campus, "block", key):
                hits.append(cand)
            for (_cid, bkey), rows in campus.get("blocks", {}).items():
                if bkey == key:
                    hits += [r["block_id"] for r in rows]
        else:
            for cand in _alias_ids(campus, "block", key):
                row = idx["blocks"].get(cand)
                if row and row.get("college_id") == scope:
                    hits.append(cand)
            for r in campus.get("blocks", {}).get((scope, key), []):
                hits.append(r["block_id"])
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1:
            _take("block", hits[0], "match")
        elif len(hits) > 1:
            fail["block"] = AMBIGUOUS
            notes["block"] = f"multiple campus blocks match '{src['block']}'"
        else:
            fail["block"] = UNRESOLVED
            notes["block"] = f"no campus block matches block_name '{src['block']}'"
    _explicit_override("block")

    # 2c. floor (scoped to a resolved block; when block AND college sources
    #     are simply absent a campus-wide unique floor is allowed and
    #     back-derives its own block)
    if ids["floor"] is None and src["floor"]:
        key = normalize(src["floor"])
        scope = ids["block"]
        hits = []
        if scope is not None:
            for cand in _alias_ids(campus, "floor", key):
                row = idx["floors"].get(cand)
                if row and row["block_id"] == scope:
                    hits.append(cand)
            for r in campus.get("floors", {}).get((scope, key), []):
                hits.append(r["floor_id"])
        elif src["block"] is None and src["college"] is None:
            hits += _alias_ids(campus, "floor", key)
            for (_bid, fkey), rows in campus.get("floors", {}).items():
                if fkey == key:
                    hits += [r["floor_id"] for r in rows]
        hits = list(dict.fromkeys(hits))
        if len(hits) == 1:
            _take("floor", hits[0], "match")
        elif len(hits) > 1:
            fail["floor"] = AMBIGUOUS
            notes["floor"] = f"multiple floors match '{src['floor']}'" + (
                f" in block '{src['block']}'" if scope else ""
            )
        elif scope is not None:
            fail["floor"] = UNRESOLVED
            notes["floor"] = f"no floor '{src['floor']}' in block '{src['block']}'"
        elif src["block"] or src["college"]:
            fail["floor"] = UNRESOLVED
            notes["floor"] = f"floor '{src['floor']}' cannot be resolved while block is unresolved"
        else:
            fail["floor"] = UNRESOLVED
            notes["floor"] = f"no floor matches '{src['floor']}'"
    _explicit_override("floor")

    # 2d. department (global — departments are not block-scoped)
    if ids["department"] is None and src["department"]:
        key = normalize(src["department"])
        hits = set(_alias_ids(campus, "department", key))
        hits |= set(campus.get("departments", {}).get(key, set()))
        hits = sorted(hits)
        if len(hits) == 1:
            _take("department", hits[0], "match")
        elif len(hits) > 1:
            fail["department"] = AMBIGUOUS
            notes["department"] = f"multiple departments match '{src['department']}'"
        else:
            fail["department"] = UNRESOLVED
            notes["department"] = f"no campus department matches '{src['department']}'"

    # 2e. room (scoped like the historical matcher; room-type tie-break and
    #     name-or-id key are the same candidate filter as before)
    if room_outcome is not None:
        # External decider (legacy import): adopt its verdict verbatim.
        room_id, room_note, room_status = room_outcome
        if room_id:
            _take("room", room_id, "match")
        else:
            if src["room_number"]:
                fail["room"] = room_status or UNRESOLVED
            if room_note:
                notes["room"] = room_note
    elif ids["room"] is None and src["room_number"]:
        key = normalize(src["room_number"])
        room_type_key = normalize(src["room_type"])
        scope_block = ids["block"]
        floor_id = ids["floor"]
        department_id = ids["department"]

        def _matched(candidates, extra):
            typed = [
                r for r in candidates if r.get("room_type") and normalize(r["room_type"]) == room_type_key
            ]
            if typed:
                candidates = typed
            return list(dict.fromkeys(extra + [c["room_id"] for c in candidates]))

        hits: list[str] = []
        skip_note = None
        if scope_block is not None and src["floor"] and floor_id is None:
            # Source floor exists but matched nothing: room candidates would
            # have to cross floors — never guess, leave the room for review.
            skip_note = f"room '{src['room_number']}' cannot be resolved while floor is unresolved"
        elif scope_block is not None:
            alias_hits = [
                cand
                for cand in _alias_ids(campus, "room", key)
                if (idx["rooms"].get(cand) or {}).get("block_id") == scope_block
            ]
            candidates = [
                room
                for room in campus.get("rooms", [])
                if room["block_id"] == scope_block
                and (floor_id is None or room["floor_id"] == floor_id)
                and (department_id is None or room.get("room_department_id") in (None, department_id))
                and (normalize(room["room_name"]) == key or normalize(room["room_id"]) == key)
            ]
            hits = _matched(candidates, alias_hits)
        elif floor_id is not None and src["block"] is None:
            # Block simply absent but the floor matched uniquely: scope the
            # room candidates to that floor — still deterministic (§6).
            alias_hits = [
                cand
                for cand in _alias_ids(campus, "room", key)
                if (idx["rooms"].get(cand) or {}).get("floor_id") == floor_id
            ]
            candidates = [
                room
                for room in campus.get("rooms", [])
                if room["floor_id"] == floor_id
                and (department_id is None or room.get("room_department_id") in (None, department_id))
                and (normalize(room["room_name"]) == key or normalize(room["room_id"]) == key)
            ]
            hits = _matched(candidates, alias_hits)
        elif src["block"] is None and src["floor"] is None:
            # No scope at all: only a campus-wide unique room name/id match
            # is deterministic; it derives floor/block/college afterwards.
            candidates = [
                room
                for room in campus.get("rooms", [])
                if (department_id is None or room.get("room_department_id") in (None, department_id))
                and (normalize(room["room_name"]) == key or normalize(room["room_id"]) == key)
            ]
            hits = _matched(candidates, _alias_ids(campus, "room", key))
        if skip_note:
            fail["room"] = UNRESOLVED
            notes["room"] = skip_note
        elif len(hits) == 1:
            _take("room", hits[0], "match")
        elif len(hits) > 1:
            fail["room"] = AMBIGUOUS
            notes["room"] = f"room reference '{src['room_number']}' is ambiguous ({len(hits)} matches)"
        elif scope_block is None and src["block"]:
            fail["room"] = UNRESOLVED
            notes["room"] = f"room '{src['room_number']}' cannot be resolved while block is unresolved"
        elif scope_block is None and src["floor"] and ids["floor"] is None:
            fail["room"] = UNRESOLVED
            notes["room"] = f"room '{src['room_number']}' cannot be resolved while floor is unresolved"
        else:
            fail["room"] = UNRESOLVED
            notes["room"] = f"no room matches '{src['room_number']}'"
    elif ids["room"] is None and any(src.values()):
        # §9: a room TYPE without a room NUMBER is metadata, not a room;
        # likewise a room-less row with other known location data.
        notes["room"] = "room_number not provided"
    if room_outcome is None:
        # Legacy callers adopt their own verdict verbatim (above); everyone
        # else gets the explicit-id pass for room_id.
        _explicit_override("room")

    # ── 3. Blank source keeps the previously confirmed value (§11) — only
    #     into empty slots; matching above had first refusal. A present but
    #     UNRESOLVED/AMBIGUOUS source likewise never downgrades a confirmed
    #     value (§4): the previous id is kept and the failed source stays
    #     stored + noted for review.
    for level in LEVELS:
        if level in manual or ids[level] is not None:
            continue
        if src.get(_LEVEL_SOURCE[level]):
            if prev_ids.get(level) and fail.get(level) in (UNRESOLVED, AMBIGUOUS):
                _take(level, prev_ids[level], "prev")
                kept.add(level)
                notes[level] = (notes.get(level) + "; " if notes.get(level) else "") + (
                    f"kept previously confirmed {level} '{prev_ids[level]}'"
                )
            continue  # source present but unresolved: recorded via fail/notes
        if prev_ids.get(level):
            _take(level, prev_ids[level], "prev")

    # ── 4. Derivation / authority / drop passes until stable. Priority:
    #     manual == match == derived  >  prev-kept (see module docstring).
    for _ in range(6):
        changed = _derive_parents(ids, prov, idx, notes, src)
        changed = _enforce_edges(ids, prov, idx, notes, src) or changed
        changed = _drop_inconsistent(ids, prov, idx, src, fail, notes) or changed
        if not changed:
            break

    # ── 5. statuses (pure function of ids) + overall. Reconcile the        #     invariant first: any pass above may have cleared an id after      #     matching, and id NULL + source present must never surface as      #     MISSING.
    for level in LEVELS:
        idkey = _LEVEL_ID_KEY.get(level)
        has_source = bool(src.get(_LEVEL_SOURCE[level]) or (idkey and src.get(idkey)))
        if not ids[level] and has_source and level not in fail:
            fail[level] = UNRESOLVED
    statuses = {}
    for level in LEVELS:
        if ids[level]:
            statuses[level] = RESOLVED
        else:
            statuses[level] = fail.get(level, MISSING)
    if statuses["room"] == MISSING and "room" not in notes and any(src.values()):
        notes["room"] = "room_number not provided"

    return LocationResolution(
        ids=ids,
        statuses=statuses,
        source=src,
        notes=notes,
        overall=_overall_for(ids),
        manual_fields=frozenset(manual),
        kept=frozenset(kept),
    )


def room_failure_note(source, res: LocationResolution) -> str | None:
    """First resolution failure in the canonical early-return order.

    Shared by ``routes.acs._resolve_room_best_effort`` (whose historical
    reasons the Phase-3 import tests pin byte-for-byte) and by the
    importer's pending-location warning, so both quote exactly one
    implementation of "why the room could not be assigned" in the
    original order: college → block → floor → department (ambiguous only)
    → room. Returns None when the room resolved.
    """
    src = {key: clean_value(source.get(key)) for key in SOURCE_FIELDS}
    if src["college"] and res.statuses["college"] != RESOLVED:
        return res.notes.get("college")
    if res.statuses["block"] != RESOLVED:
        if src.get("block_id"):
            return (
                res.notes.get("block")
                or res.notes.get("block_id")
                or f"source block_id '{src['block_id']}' did not resolve"
            )
        if src["block"] is None:
            return f"no campus block matches block_name '{src['block']}'"
        return res.notes.get("block")
    if src["floor"] and res.statuses["floor"] != RESOLVED:
        return res.notes.get("floor")
    if src.get("floor_id") and not src["floor"] and res.statuses["floor"] != RESOLVED:
        return (
            res.notes.get("floor")
            or res.notes.get("floor_id")
            or f"source floor_id '{src['floor_id']}' did not resolve"
        )
    if res.statuses["department"] == AMBIGUOUS:
        return res.notes.get("department")
    if src["room_number"] is None and not src.get("room_id"):
        return "room_number not provided"
    if res.statuses["room"] != RESOLVED:
        return res.notes.get("room") or res.notes.get("room_id") or f"no room matches '{src['room_number']}'"
    return None


def _derive_parents(ids, prov, idx, notes, src) -> bool:
    """Fill NULL parent slots from a confirmed child's canonical chain.

    A slot whose SOURCE value is present but matched nothing is never
    filled: its status must stay UNRESOLVED so the review section keeps
    seeing the unmatched text (a registered alias then resolves it on the
    next pass), and the historical all-or-nothing delegate must keep
    quoting its own failure instead of a derived parent. Both the source
    NAME and an explicit source ID count as "present" here.
    """
    changed = False

    def fill(level: str, value: str | None, via: str) -> None:
        nonlocal changed
        idkey = _LEVEL_ID_KEY.get(level)
        has_source = bool(src.get(_LEVEL_SOURCE[level]) or (idkey and src.get(idkey)))
        if value and ids.get(level) is None and not has_source:
            ids[level] = value
            prov[level] = "derive"
            notes.setdefault(level, f"derived from {via} chain")
            changed = True

    room_id = ids.get("room")
    if room_id:
        room = idx["rooms"].get(room_id)
        if room:
            fill("floor", room.get("floor_id"), "room")
            fill("department", room.get("room_department_id"), "room")
    if ids.get("floor"):
        floor = idx["floors"].get(ids["floor"])
        if floor:
            fill("block", floor.get("block_id"), "floor")
    if ids.get("block"):
        block = idx["blocks"].get(ids["block"])
        if block:
            fill("college", block.get("college_id"), "block")
    return changed


def _enforce_edges(ids, prov, idx, notes, src) -> bool:
    """Confirmed children fix non-manual parents on every hierarchy edge.

    Mirrors the composite FKs: (room, floor), (floor, block),
    (block, college) plus the room's canonical department. Manual parents
    are never overwritten here — a contradiction with a confirmed child
    drops the child instead (see _drop_inconsistent), except the room edge
    where the DB FK itself decides (child side is authoritative because the
    parent would then be re-derived from the room below).

    Filling an empty parent slot is skipped when that level's SOURCE value
    exists but matched nothing (see _derive_parents): the unmatched text
    stays visible as UNRESOLVED for review instead of being silently
    answered by a chain the source never mentioned. Explicit source ids
    count as source here too: a contradicted explicit id must surface in
    review, not be steamrolled by a child's chain.
    """
    changed = False

    def set_parent(level: str, value: str | None, via: str, why: str) -> None:
        nonlocal changed
        if ids.get(level) != value:
            if prov.get(level) == "manual":
                return  # manual wins; child-side handling drops the child
            ids[level] = value
            prov[level] = "derive" if value else "none"
            notes.setdefault(level, why)
            changed = True

    # room -> floor (composite FK authority: the room's floor is canonical)
    if ids.get("room") and prov.get("room") in _CONFIRMED:
        room = idx["rooms"].get(ids["room"])
        if room and room.get("floor_id") and ids.get("floor") != room["floor_id"]:
            if prov.get("floor") == "manual":
                # both manual: DB FK decides — room side wins, floor pin goes
                ids["floor"] = room["floor_id"]
                prov["floor"] = "derive"
                notes["floor"] = "manual floor contradicts manual room; room chain applied"
                changed = True
            elif not src.get("floor_id"):
                set_parent("floor", room["floor_id"], "room", "room's canonical floor applied")
            # else: an explicit floor id the confirmed room contradicts stays
            # for review (D-ID-4) — never steamrolled by the child's chain.

    # room -> department (canonical room department wins when present)
    if ids.get("room") and prov.get("room") in _CONFIRMED:
        room = idx["rooms"].get(ids["room"])
        if (
            room
            and room.get("room_department_id")
            and ids.get("department")
            and ids["department"] != room["room_department_id"]
            and prov.get("department") != "manual"
        ):
            set_parent(
                "department", room["room_department_id"], "room", "room's canonical department applied"
            )

    # floor -> block (composite FK authority)
    if ids.get("floor") and prov.get("floor") in _CONFIRMED:
        floor = idx["floors"].get(ids["floor"])
        if floor and floor.get("block_id") and ids.get("block") != floor["block_id"]:
            if prov.get("block") != "manual" and not src.get("block") and not src.get("block_id"):
                set_parent("block", floor["block_id"], "floor", "floor's canonical block applied")

    # block -> college (composite FK authority; block without college must
    # leave college NULL or the (block, college) FK rejects the row)
    if ids.get("block") and prov.get("block") in _CONFIRMED:
        block = idx["blocks"].get(ids["block"])
        if block is not None:
            canonical = block.get("college_id")
            if ids.get("college") != canonical:
                if prov.get("college") == "manual":
                    # Manual college cannot beat the FK: canonical applies.
                    ids["college"] = canonical
                    prov["college"] = "derive" if canonical else "none"
                    notes["college"] = "manual college contradicts block chain; canonical applied"
                    changed = True
                elif canonical is None and ids.get("college"):
                    ids["college"] = None
                    prov["college"] = "none"
                    notes.setdefault("college", "block has no college; college cleared")
                    changed = True
                elif canonical and ids.get("college") is None:
                    if not src.get("college"):
                        set_parent("college", canonical, "block", "block's canonical college applied")
                    # else: source college exists but matched nothing —
                    # stays UNRESOLVED for review (composite FK tolerates
                    # college NULL: the (block, college) FK is skipped).
                elif canonical and ids.get("college") != canonical:
                    if prov.get("college") in _CONFIRMED:
                        # Two confirmed sources disagree — the block chain
                        # (child side) is the physical truth.
                        ids["college"] = canonical
                        prov["college"] = "derive"
                        notes.setdefault("college", "canonical block chain applied")
                        changed = True
                    else:
                        # college was kept-previous: replace with canonical
                        ids["college"] = canonical
                        prov["college"] = "derive"
                        notes.setdefault("college", "canonical block chain applied")
                        changed = True
    return changed


def _drop_inconsistent(ids, prov, idx, src, fail, notes) -> bool:
    """Drop nodes that contradict confirmed ancestors.

    Kept-previous nodes always drop (low priority). Confirmed nodes drop
    only against a MANUAL ancestor (manual > match) — except a manual node
    itself, which never drops; its contradiction is resolved on the parent
    side (or loudly by the composite FK if both sides are manual).
    """
    changed = False

    def drop(level: str, why: str) -> None:
        nonlocal changed
        notes[level] = why
        ids[level] = None
        prov[level] = "none"
        idkey = _LEVEL_ID_KEY.get(level)
        if src.get(_LEVEL_SOURCE[level]) or (idkey and src.get(idkey)):
            fail[level] = UNRESOLVED  # source exists but nothing confirmed
        else:
            fail.pop(level, None)
        changed = True

    # room vs confirmed/manual floor or block
    if ids.get("room") and prov.get("room") != "manual":
        room = idx["rooms"].get(ids["room"])
        if room:
            if prov.get("floor") == "manual" and room.get("floor_id") != ids.get("floor"):
                drop("room", "room dropped: conflicts with manually confirmed floor")
            elif prov.get("block") == "manual" and (
                (idx["floors"].get(room.get("floor_id")) or {}).get("block_id") != ids.get("block")
            ):
                drop("room", "room dropped: conflicts with manually confirmed block")
            elif prov.get("room") == "prev":
                if ids.get("floor") and room.get("floor_id") != ids["floor"]:
                    drop("room", "kept room dropped: conflicts with confirmed floor")
                elif ids.get("block") and room.get("floor_id"):
                    floor = idx["floors"].get(room["floor_id"])
                    if floor and floor.get("block_id") != ids["block"]:
                        drop("room", "kept room dropped: conflicts with confirmed block")

    # floor vs confirmed/manual block
    if ids.get("floor") and prov.get("floor") != "manual":
        floor = idx["floors"].get(ids["floor"])
        if floor:
            if prov.get("block") == "manual" and floor.get("block_id") != ids.get("block"):
                drop("floor", "floor dropped: conflicts with manually confirmed block")
            elif prov.get("floor") == "prev" and ids.get("block") and floor.get("block_id") != ids["block"]:
                drop("floor", "kept floor dropped: conflicts with confirmed block")

    # block vs confirmed/manual college
    if ids.get("block") and prov.get("block") != "manual":
        block = idx["blocks"].get(ids["block"])
        if block:
            if prov.get("college") == "manual" and block.get("college_id") not in (None, ids.get("college")):
                drop("block", "block dropped: conflicts with manually confirmed college")
            elif (
                prov.get("block") == "prev"
                and ids.get("college")
                and block.get("college_id")
                not in (
                    None,
                    ids["college"],
                )
            ):
                drop("block", "kept block dropped: conflicts with confirmed college")

    # department vs confirmed/manual room
    if ids.get("department") and prov.get("department") != "manual":
        room = idx["rooms"].get(ids.get("room")) if ids.get("room") else None
        if room and room.get("room_department_id") and ids["department"] != room["room_department_id"]:
            if prov.get("department") == "manual":
                pass  # unreachable (guard above), kept for symmetry
            elif prov.get("department") == "prev" or prov.get("room") == "manual":
                drop("department", "department dropped: conflicts with confirmed room")

    # Kept-previous parents contradicting a confirmed child chain drop: an
    # explicit id applies globally, so a stale kept parent can disagree with
    # a newly confirmed child (the child is authoritative; the edge passes
    # above only fix parents that carry no source of their own).
    if ids.get("room") and prov.get("room") in _CONFIRMED:
        room = idx["rooms"].get(ids["room"])
        if room:
            if prov.get("floor") == "prev" and ids.get("floor") and room.get("floor_id") != ids["floor"]:
                drop("floor", "kept floor dropped: conflicts with confirmed room")
            room_floor = (idx.get("floors") or {}).get(room.get("floor_id")) or {}
            if (
                prov.get("block") == "prev"
                and ids.get("block")
                and room_floor.get("block_id")
                and room_floor["block_id"] != ids["block"]
            ):
                drop("block", "kept block dropped: conflicts with confirmed room")
    if ids.get("floor") and prov.get("floor") in _CONFIRMED:
        floor = idx["floors"].get(ids["floor"])
        if (
            floor
            and prov.get("block") == "prev"
            and ids.get("block")
            and floor.get("block_id") != ids["block"]
        ):
            drop("block", "kept block dropped: conflicts with confirmed floor")

    # college kept-previous vs canonical block chain already handled by
    # _enforce_edges (parents are re-derived, children dropped).
    return changed


# ─────────────────────────────────────────────────────────────────────────
# Batch helper (memoized — identical inputs share one resolution; §26)
# ─────────────────────────────────────────────────────────────────────────


def resolve_memoized(
    source, campus, *, previous_ids=None, previous_manual=None, room_outcome=None, cache: dict
):
    """Batch helper: identical inputs share one resolution (§26)."""
    key = (
        tuple(source.get(k) for k in sorted(SOURCE_FIELDS)),
        tuple(sorted((previous_ids or {}).items())),
        tuple(sorted(previous_manual or ())),
        room_outcome,
    )
    hit = cache.get(key)
    if hit is not None:
        return hit
    result = resolve(
        source,
        campus,
        previous_ids=previous_ids,
        previous_manual=previous_manual,
        room_outcome=room_outcome,
    )
    cache[key] = result
    return result


# ─────────────────────────────────────────────────────────────────────────
# Writers (single-writer contract — asset_locations first, acs mirror after)
# ─────────────────────────────────────────────────────────────────────────

# Insertable columns in a stable order (ac_id first; manual_fields deliberately
# omitted from the import path so re-imports never touch manual pins).
_RESOLUTION_COLUMNS = (
    ["ac_id"]
    + [f"{level}_id" for level in LEVELS]
    + list(SOURCE_FIELDS.values())
    + [f"{level}_status" for level in LEVELS]
    + ["source_ref", "resolved_by", "resolution_notes"]
)


def resolution_values(ac_id: str, res: LocationResolution, *, source_ref: str, resolved_by: str):
    """(columns, params) for one asset_locations row.

    Source columns whose value is blank are OMITTED entirely so a blank cell
    never erases previously preserved source metadata (the project's import
    convention), while ids/statuses/notes always carry the fresh resolution.
    """
    columns = []
    params = []
    source_key_by_col = {v: k for k, v in SOURCE_FIELDS.items()}
    for col in _RESOLUTION_COLUMNS:
        if col == "ac_id":
            columns.append(col)
            params.append(ac_id)
        elif col.endswith("_id") and col[:-3] in LEVELS:
            columns.append(col)
            params.append(res.ids.get(col[:-3]))
        elif col in source_key_by_col:
            value = res.source.get(source_key_by_col[col])
            if value is None:
                continue  # blank never erases
            columns.append(col)
            params.append(value)
        elif col.endswith("_status"):
            columns.append(col)
            params.append(res.statuses.get(col[: -len("_status")], MISSING))
        elif col == "source_ref":
            columns.append(col)
            params.append(source_ref)
        elif col == "resolved_by":
            columns.append(col)
            params.append(resolved_by)
        elif col == "resolution_notes":
            columns.append(col)
            params.append(json.dumps({k: v for k, v in res.notes.items() if v}))
    return columns, params


def upsert_parts(
    ac_id: str, res: LocationResolution, *, source_ref: str, resolved_by: str
) -> tuple[str, tuple]:
    """``(sql, params)`` for one idempotent asset_locations upsert."""
    columns, params = resolution_values(ac_id, res, source_ref=source_ref, resolved_by=resolved_by)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in columns if col != "ac_id")
    sql = (
        f"INSERT INTO asset_locations ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT (ac_id) DO UPDATE SET {updates}, updated_at = NOW()"
    )
    return sql, tuple(params)


def apply_many(
    cursor,
    rows: list[tuple[str, LocationResolution]],
    *,
    source_ref: str,
    resolved_by: str,
    chunk: int = 100,
) -> int:
    """Upsert many resolutions in chunked, mogrified round trips.

    ``rows``: [(ac_id, resolution), ...]. Returns rows written. Callers
    manage their own transaction/savepoints; acs.room_id is written by the
    import's own acs upsert or by :func:`sync_acs` afterwards.
    """
    written = 0
    for start in range(0, len(rows), chunk):
        part = rows[start : start + chunk]
        statements = []
        for ac_id, res in part:
            sql, params = upsert_parts(ac_id, res, source_ref=source_ref, resolved_by=resolved_by)
            statements.append(cursor.mogrify(sql, params).decode("utf-8"))
        cursor.execute("; ".join(statements))
        written += len(part)
    return written


def sync_acs(cursor, rows: list[tuple[str, LocationResolution]]) -> int:
    """Mirror room_id + overall location_status onto acs for these rows.

    Runs AFTER the asset_locations write: the mirror trigger then sees a
    matching record and only performs its status check.
    """
    if not rows:
        return 0
    pairs = [(ac_id, res.ids.get("room"), res.overall) for ac_id, res in rows]
    for start in range(0, len(pairs), 500):
        part = pairs[start : start + 500]
        values_sql = ", ".join(["(%s, %s, %s)"] * len(part))
        flat = [item for triple in part for item in triple]
        cursor.execute(
            "UPDATE acs a SET room_id = v.room_id, location_status = v.status "
            f"FROM (VALUES {values_sql}) AS v(ac_id, room_id, status) "
            "WHERE a.ac_id = v.ac_id "
            "AND (a.room_id IS DISTINCT FROM v.room_id "
            "     OR a.location_status IS DISTINCT FROM v.status)",
            tuple(flat),
        )
    return len(pairs)
