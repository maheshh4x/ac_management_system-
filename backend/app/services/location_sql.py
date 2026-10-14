"""Effective-location SQL — the only SQL shape for READING locations (§7).

Read-side counterpart to ``location_resolver``: where the resolver defines
how locations are WRITTEN (asset_locations first, acs mirror after), this
module defines how READS see them:

* Confirmed canonical ids live in ``asset_locations`` (one row per ac_id).
* The legacy mirror chain (``acs.room_id -> rooms -> floors -> blocks``)
  is only a fallback for rows written before migration 014, so every
  consumer keeps working on old data without a second code path.
* An unresolved level still shows its preserved ``source_*`` text:
  effective label = canonical name -> canonical id -> source value.
* Permission scoping binds canonical ids only (after the COALESCE chain),
  never source text — a block a manager is not assigned can never become
  visible through a source string.

Every reader (assets list/detail/facets/export, dashboard summary,
reports) composes its joins and location filters from here, so effective-
location semantics change in exactly one place and no query can drift.
"""

from __future__ import annotations

# The per-asset resolution record: PRIMARY KEY ac_id -> at most one row, so
# the LEFT JOIN can never multiply asset rows.
LOCATION_JOIN = "LEFT JOIN asset_locations al ON al.ac_id = a.ac_id"

# Effective hierarchy chain: each level prefers the confirmed id from
# asset_locations and falls back to the id derived from the mirrored room.
# Alias contract for every query built from this module:
#   a = acs, al = asset_locations, r = rooms, f = floors, b = blocks,
#   d = departments, c = colleges.
EFFECTIVE_JOINS = (
    "LEFT JOIN rooms r ON r.room_id = COALESCE(al.room_id, a.room_id) "
    "LEFT JOIN floors f ON f.floor_id = COALESCE(al.floor_id, r.floor_id) "
    "LEFT JOIN blocks b ON b.block_id = COALESCE(al.block_id, f.block_id) "
    "LEFT JOIN departments d ON d.department_id = COALESCE(al.department_id, r.department_id) "
    "LEFT JOIN colleges c ON c.college_id = COALESCE(al.college_id, b.college_id)"
)

# Full FROM for asset-centric queries (list, facets, detail, export,
# dashboard summary): one join shape, applied everywhere.
ASSET_JOINS = f"FROM acs a {LOCATION_JOIN} {EFFECTIVE_JOINS}"

# Work-order-centric chain (reports): same effective COALESCEs, restricted
# to the levels those queries actually use.
WO_JOINS = (
    "FROM maintenance_work_orders w LEFT JOIN acs a ON a.ac_id = w.ac_id "
    "LEFT JOIN asset_locations al ON al.ac_id = a.ac_id "
    "LEFT JOIN rooms r ON r.room_id = COALESCE(al.room_id, a.room_id) "
    "LEFT JOIN floors f ON f.floor_id = COALESCE(al.floor_id, r.floor_id) "
    "LEFT JOIN blocks b ON b.block_id = COALESCE(al.block_id, f.block_id)"
)

# Effective room id expression (aliased as ``room_id`` by BASE_SELECT): the
# confirmed id when a record exists, the mirror otherwise.
ROOM_ID_SQL = "COALESCE(al.room_id, a.room_id)"

# level -> (id SQL, name SQL, preserved source SQL)
_LEVEL_SQL = {
    "college": ("c.college_id", "c.college_name", "al.source_college_value"),
    "block": ("b.block_id", "b.block_name", "al.source_block_value"),
    "floor": ("f.floor_id", "f.floor_name", "al.source_floor_value"),
    "department": ("d.department_id", "d.department_name", "al.source_department_value"),
    "room": (ROOM_ID_SQL, "r.room_name", "al.source_room_number_value"),
}

# level -> (id row key, name row key, source row key, status row key) for
# the nested payload built from an already-fetched row. Names come from the
# raw canonical columns (canon_*): the flat fields in BASE_SELECT carry the
# EFFECTIVE text (canonical -> id -> source), which must never pose as the
# canonical name here. Rows built without the canon_* columns (older tests,
# hand-rolled selects) fall back to the flat key.
_ROW_KEYS = {
    "college": ("college_id", "canon_college_name", "source_college_value", "college_status"),
    "block": ("block_id", "canon_block_name", "source_block_value", "block_status"),
    "floor": ("floor_id", "canon_floor_name", "source_floor_value", "floor_status"),
    "department": (
        "department_id",
        "canon_department_name",
        "source_department_value",
        "department_status",
    ),
    "room": ("room_id", "canon_room_name", "source_room_number_value", "room_status"),
}


def label(level: str) -> str:
    """SQL expression: effective display text for one level.

    Canonical name first, then the canonical id, then the preserved source
    value — an unresolved level still shows exactly what the import
    supplied instead of vanishing into NULL.
    """
    id_sql, name_sql, source_sql = _LEVEL_SQL[level]
    return f"COALESCE(NULLIF({name_sql}, ''), {id_sql}, {source_sql})"


def label_or(level: str, fallback_sql: str) -> str:
    """:func:`label` with an extra final fallback (facet buckets)."""
    id_sql, name_sql, source_sql = _LEVEL_SQL[level]
    return f"COALESCE(NULLIF({name_sql}, ''), {id_sql}, {source_sql}, {fallback_sql})"


def id_sql(level: str) -> str:
    """Canonical id expression for one level (facet grouping keys).

    Grouping facets by the canonical id — not the display label — keeps two
    distinct canonical entities that share a display name in separate
    buckets instead of silently merging them.
    """
    return _LEVEL_SQL[level][0]


def room_type_label() -> str:
    """Effective room-type text: the canonical type, else the preserved
    source (descriptive metadata — never a physical room)."""
    return "COALESCE(NULLIF(r.room_type, ''), al.source_room_type_value)"


def filter_match(level: str, value: str) -> tuple[str, list]:
    """Filter clause for one level: canonical id, canonical name OR the
    preserved source text (three bound ILIKE params via :func:`filter_params`).

    Source fallback is what makes reviewable rows findable: typing the
    unresolved block text surfaces exactly the assets the location review
    section needs. Permission scoping never uses this — only the canonical
    ``b.block_id = ANY(...)`` forms.
    """
    id_sql, name_sql, source_sql = _LEVEL_SQL[level]
    clause = f"({id_sql} ILIKE %s OR {name_sql} ILIKE %s OR {source_sql} ILIKE %s)"
    return clause, filter_params(value)


def escape_like(value: str) -> str:
    """Escape LIKE metacharacters (D6): the escape char first, then `%`/`_`_.

    Backslash is PostgreSQL's default LIKE escape, so no ESCAPE clause is
    needed and the pinned clause text is unchanged — only param values gain
    backslashes. A user typing ``100%`` now matches the literal text instead
    of acting as a wildcard.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def fold_sql(column_sql: str) -> str:
    """SQL form of the shared match key (Option A, D1/D3): lower FIRST, then
    strip every non-alphanumeric run — the same order as
    ``location_resolver.normalize`` (lower-then-strip), which makes the two
    ASCII-exact. (Stripping first would eat uppercase letters: ``[^a-z0-9]``
    matches ``'A'``.) Backed by the 017 match-key indexes; see
    migrations/017_match_key_indexes.sql.
    """
    return f"(REGEXP_REPLACE(LOWER({column_sql}), '[^a-z0-9]+', '', 'g'))"


def filter_params(value: str) -> list:
    pattern = f"%{escape_like(value)}%"
    return [pattern, pattern, pattern]


def location_object(row: dict) -> dict | None:
    """Nested ``location`` payload for one asset row (list + detail share it).

    Returns None for rows that carry no location columns (work-order
    history entries etc.), so section payloads never gain a fake location.
    Per-field status is the stored resolution state, reconciled with the id
    invariant (id set -> RESOLVED) for rows that predate migration 014.
    """
    if "room_status" not in row:
        return None
    levels: dict[str, dict] = {}
    for level, (id_key, name_key, source_key, status_key) in _ROW_KEYS.items():
        level_id = row.get(id_key)
        status = row.get(status_key)
        if status is None:
            status = "RESOLVED" if level_id else "MISSING"
        entry = {
            # name is the RAW canonical name (canon_*): None when unresolved.
            # The effective display text (canonical -> id -> source) lives in
            # the flat fields; source text lives in "source". Never mixed.
            "id": level_id,
            "name": row.get(name_key),
            "status": status,
            "source": row.get(source_key),
        }
        if level == "room":
            entry["type"] = row.get("canon_room_type", row.get("room_type"))
        levels[level] = entry
    return {
        "status": row.get("location_status"),
        "levels": levels,
        "manual_fields": list(row.get("manual_fields") or []),
    }
