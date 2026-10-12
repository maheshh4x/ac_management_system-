"""AC CSV/XLSX import parsers, validators and identity helpers (library).

Formerly the legacy ``/api/acs*`` blueprint. Phase 3 task 8 removed those
routes; this module now only hosts the parsing/validation machinery reused
by ``POST /api/v1/assets/import``.
"""

import csv
import difflib
import io
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from app.services import location_resolver

logger = logging.getLogger(__name__)

AC_COLUMNS = (
    "ac_id",
    "room_id",
    "ac_make_company",
    "ac_model_number",
    "ac_serial_number",
    "ac_capacity_ton",
    "ac_type",
    "manufacturing_year",
    "installation_year",
    "current_status",
    "ac_star_rating",
    "power_rating_kw",
    "power_factor",
    "installation_date",
    "last_maintenance_date",
    "next_service_date",
    "technician",
    "remarks",
)
REQUIRED_FIELDS = ("ac_id",)
OPTIONAL_FIELDS = tuple(field for field in AC_COLUMNS if field not in REQUIRED_FIELDS)
INTEGER_FIELDS = {"manufacturing_year", "installation_year", "ac_star_rating"}
NUMERIC_FIELDS = {"ac_capacity_ton", "power_rating_kw", "power_factor"}
DATE_FIELDS = {"installation_date", "last_maintenance_date", "next_service_date"}
# Plausible-year guard so out-of-range garbage fails validation with the
# normal "invalid <field>" message instead of an INTEGER overflow at write.
YEAR_FIELDS = {"manufacturing_year", "installation_year"}
YEAR_MIN, YEAR_MAX = 1800, 2100


# Unknown/sentinel cell values meaning "no value", as normalized keys
# (lowercase, every non-alphanumeric character removed): NA, N/A, N.A. ->
# "na"; NO DATA -> "nodata"; NOT AVAILABLE -> "notavailable";
# DATA NOT AVAILABLE / DATA NOT AVILABLE -> "datanot..."; UNKNOWN -> "unknown".
_UNKNOWN_VALUE_KEYS = frozenset(
    {"na", "nodata", "notavailable", "datanotavailable", "datanotavilable", "unknown"}
)
# Typo tolerance for the longer keys only ("DATA NOT AVilabe" in the wild),
# so short words like "na" can never fuzzy-match unrelated values.
_UNKNOWN_FUZZY_MIN = 6
_UNKNOWN_FUZZY_RATIO = 0.85


def _unknown_key(text):
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _is_unknown_value(text):
    """True when a string cell is an unknown/sentinel representation.

    Exact key match first; for values at least ``_UNKNOWN_FUZZY_MIN`` chars
    long, a difflib ratio >= ``_UNKNOWN_FUZZY_RATIO`` against the longer keys
    tolerates common misspellings (e.g. the reference workbook's
    ``DATA NOT AVilabe`` ~ ``data not available`` at 0.966) while real data
    (``unknown hall`` 0.78, ``naval base`` 0.67, ``nitttrc`` 0.31) stays.
    Punctuation-only cells ("-", "--") are unknown as well.
    """
    key = _unknown_key(text)
    if not key:
        return True
    if key in _UNKNOWN_VALUE_KEYS:
        return True
    if len(key) < _UNKNOWN_FUZZY_MIN:
        return False
    for candidate in _UNKNOWN_VALUE_KEYS:
        if len(candidate) < _UNKNOWN_FUZZY_MIN:
            continue
        # Length gate: ratio = 2*M/(a+b) >= 0.85 is impossible unless the
        # two keys are already nearly equal in length — skips difflib cheaply.
        if 2 * min(len(key), len(candidate)) < _UNKNOWN_FUZZY_RATIO * (len(key) + len(candidate)):
            continue
        if difflib.SequenceMatcher(None, key, candidate).ratio() >= _UNKNOWN_FUZZY_RATIO:
            return True
    return False


def _empty_to_none(value):
    """Blank cells and unknown/sentinel spellings normalize to None.

    Only fields whose schema allows NULL reach this — required-field rules
    (brand, department, block) run afterwards and still reject None.
    """
    if isinstance(value, str):
        value = value.strip()
        if _is_unknown_value(value):
            return None
        return value
    return value or None


def _normalize_value(field, value, row_number=None):
    value = _empty_to_none(value)
    if value is None:
        return None
    prefix = f"row {row_number}: " if row_number else ""
    try:
        if field == "ac_id":
            return str(value)
        if field in INTEGER_FIELDS:
            # Accept 2020, 2020.0 (Excel float), "2020" and "2020.0"; reject
            # anything else ("20X9", "twenty", "2020.5") as invalid <field>.
            if isinstance(value, str):
                text = value.strip()
                if re.fullmatch(r"[+-]?\d+(?:\.0+)?", text) is None:
                    raise ValueError(text)
                integer = int(float(text))
            elif isinstance(value, float):
                if not value.is_integer():
                    raise ValueError(str(value))
                integer = int(value)
            else:
                integer = int(value)
            if field in YEAR_FIELDS and not YEAR_MIN <= integer <= YEAR_MAX:
                raise ValueError(str(integer))
            return integer
        if field in NUMERIC_FIELDS:
            cleaned = re.sub(r"(?:ton|kw)\b", "", str(value), flags=re.IGNORECASE).strip()
            return Decimal(cleaned)
        if field in DATE_FIELDS:
            return date.fromisoformat(str(value)).isoformat()
    except (ValueError, InvalidOperation):
        raise ValueError(f"{prefix}invalid {field}") from None
    return value


def _coerce_ac_id(raw_id):
    """Excel S.No. arrives as a JSON number or string; ac_id is TEXT in
    PostgreSQL, so normalize early (105.0 -> "105") to keep
    later comparisons/inserts text=text."""
    if isinstance(raw_id, float) or (isinstance(raw_id, str) and "." in raw_id):
        try:
            f_val = float(raw_id)
            if f_val.is_integer():
                raw_id = int(f_val)
        except ValueError:
            pass
    return str(raw_id).strip()


def _canonical_payload(data, *, partial=False, row_number=None):
    if not isinstance(data, dict):
        raise ValueError("JSON object is required")
    if not partial and not _empty_to_none(data.get("ac_id")):
        raise ValueError("ac_id is required")
    values = {}
    for field in AC_COLUMNS:
        if field in data:
            values[field] = _normalize_value(field, data[field], row_number)
    if not partial:
        raw_id = data.get("ac_id")
        if raw_id is not None:
            values["ac_id"] = _coerce_ac_id(raw_id)
    return values


class RoomResolutionError(ValueError):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def _match_key(value):
    """Thin delegate to the centralized resolver's normalization (§6).

    Byte-identical output to the historical local implementation — a parity
    test pins both spellings against the same corpus.
    """
    return location_resolver.normalize(value)


def _upsert_ac_parts(values):
    """``(sql, params)`` for one ``INSERT ... ON CONFLICT (ac_id) DO UPDATE``.

    Shared by the single-row path (``_upsert_ac``) and the batched import
    writer, which mogrifies these into chained multi-statement round trips —
    both therefore execute byte-identical statements per row (per-row column
    sets included, so a blank cell still never erases an existing value).
    """
    columns = list(values)
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns if column != "ac_id")
    conflict_clause = (
        f"ON CONFLICT (ac_id) DO UPDATE SET {updates}, updated_at = NOW()"
        if updates
        else "ON CONFLICT (ac_id) DO NOTHING"
    )
    sql = (
        f"INSERT INTO acs ({', '.join(columns)}) VALUES ({placeholders}) "
        f"{conflict_clause} "
        "RETURNING ac_id"
    )
    return sql, tuple(values[column] for column in columns)


def _upsert_ac(cursor, values):
    sql, params = _upsert_ac_parts(values)
    cursor.execute(sql, params)
    result = cursor.fetchone()
    return result["ac_id"] if result else values["ac_id"]


class _BulkWriteError(Exception):
    """Unexpected database failure during the bulk import write.

    Logged server-side with the request id; the client only ever sees a
    generic message (the whole transaction is rolled back by the connection
    context manager when this propagates out of the ``with`` block).
    """


# Multi-row INSERTs are chunked so a very large file stays below the
# 65 535 bind-parameter limit (500 rows x <=19 columns = 9 500 params).
BULK_UPSERT_CHUNK = 500


def _load_room_directory(cursor):
    """One SELECT that replaces the per-row room queries of the old import.

    Returns every room id (for ``room_id`` lookups) plus an index keyed by
    ``(normalized department, normalized room)`` for local resolution.
    """
    cursor.execute(
        "SELECT r.room_id, r.room_name, d.department_name "
        "FROM rooms r LEFT JOIN departments d ON d.department_id = r.department_id"
    )
    room_ids = set()
    room_index = {}
    for row in cursor.fetchall():
        room_ids.add(row["room_id"])
        key = (_match_key(row["department_name"]), _match_key(row["room_name"]))
        room_index.setdefault(key, []).append(row["room_id"])
    return room_ids, room_index


def _load_campus_directory(cursor):
    """Thin delegate to the centralized resolver's directory preload (§6).

    Returns the historical shape (colleges/blocks/floors/departments/rooms)
    plus the resolver's additions: standalone departments, the alias index
    and id-row indexes. One call, batch preloads, no N+1 (§26).
    """
    return location_resolver.load_directory(cursor)


def _pick_one(matches, what, value, row_number=None, context=""):
    """Exactly-one resolved reference â€” zero or many are clear user errors."""
    seen = []
    for m in matches:
        ident = m if isinstance(m, str) else (m.get("block_id") or m.get("floor_id") or m.get("room_id") or m)
        if ident not in [
            s if isinstance(s, str) else (s.get("block_id") or s.get("floor_id") or s.get("room_id") or s)
            for s in seen
        ]:
            seen.append(m)
    prefix = f"row {row_number}: " if row_number else ""
    if not seen:
        raise ValueError(f"{prefix}no {what} matches '{value}'{context}")
    if len(seen) > 1:
        raise ValueError(f"{prefix}multiple {what}s match '{value}'{context}")
    return seen[0]


def _resolve_room_best_effort(lookup, campus, row_number=None):
    """Best-effort room resolution for the canonical format.

    Returns (room_id, None) when exactly one existing room unambiguously
    matches the supplied college/block/floor/department/room references, or
    (None, reason) when it cannot — unresolved/ambiguous rooms are never an
    import failure and never a guess; the AC is stored with room_id NULL.

    Only block_name + department_name are required input (validated earlier);
    college/floor narrow the scope when present. Matching is deterministic
    normalized equality against existing campus master data only.

    Thin delegate over the centralized resolver (§6): the resolution itself
    comes from ``location_resolver.resolve`` (id → alias → scoped normalized
    name), while the early-return order and every reason quoted from
    ``location_resolver.room_failure_note`` are byte-identical to the
    historical implementation — the Phase-3 import tests pin them.
    """
    source = {
        "college": _empty_to_none(lookup.get("college_name")),
        "block": _empty_to_none(lookup.get("block_name")),
        "block_id": _empty_to_none(lookup.get("block_id")),
        "floor": _empty_to_none(lookup.get("floor_name")),
        "floor_id": _empty_to_none(lookup.get("floor_id")),
        "department": _empty_to_none(lookup.get("department_name")),
        "room_type": _empty_to_none(lookup.get("room_type")),
        "room_number": _empty_to_none(lookup.get("room_number")),
        "room_id": _empty_to_none(lookup.get("room_id")),
    }
    res = location_resolver.resolve(source, campus)
    # Single early-return chain in room_failure_note(): byte-identical order
    # and messages to the historical implementation (Phase-3 import tests
    # pin them).
    note = location_resolver.room_failure_note(source, res)
    if note is not None:
        return None, note
    return res.ids["room"], None


def _resolve_room_local(room_id, department_name, room_name, room_ids, room_index, row_number=None):
    """In-memory equivalent of ``_resolve_room`` â€” identical messages, zero SQL."""
    room_id = _empty_to_none(room_id)
    department_name = _empty_to_none(department_name)
    room_name = _empty_to_none(room_name)
    prefix = f"row {row_number}: " if row_number else ""
    if room_id:
        if room_id not in room_ids:
            raise ValueError(f"{prefix}room_id '{room_id}' does not reference an existing room")
        return room_id
    if not department_name and not room_name:
        return None
    if not department_name or not room_name:
        raise ValueError(f"{prefix}Department and Location are both required to resolve a room")
    matches = room_index.get((_match_key(department_name), _match_key(room_name)), [])
    if not matches:
        raise RoomResolutionError(
            "unmatched", f"{prefix}no room matches Department '{department_name}' and Location '{room_name}'"
        )
    if len(matches) > 1:
        raise RoomResolutionError(
            "ambiguous",
            f"{prefix}multiple rooms match Department '{department_name}' and Location '{room_name}'",
        )
    return matches[0]


def _validate_import_row(values, row_number=None):
    """Centralized AC-import validation. Returns a user-facing error message
    or None.

    Required AC data: ac_id (enforced by ``_canonical_payload``) and a
    non-empty company/brand. Everything else is optional; values that are
    present must satisfy the existing DB CHECK rules (capacity > 0,
    star rating 1-5, power factor 0-1 â€” all NULL-tolerant by design).
    """
    prefix = f"row {row_number}: " if row_number else ""
    brand = values.get("ac_make_company")
    if brand is None or (isinstance(brand, str) and not brand.strip()):
        return f"{prefix}Brand/company is required."
    stars = values.get("ac_star_rating")
    if stars is not None and not 1 <= stars <= 5:
        return f"{prefix}ac_star_rating must be between 1 and 5"
    capacity = values.get("ac_capacity_ton")
    if capacity is not None and capacity <= 0:
        return f"{prefix}ac_capacity_ton must be greater than 0"
    power_factor = values.get("power_factor")
    if power_factor is not None and not 0 <= power_factor <= 1:
        return f"{prefix}power_factor must be between 0 and 1"
    return None


# Keys that must survive blank-stripping: identifiers, the two required data
# fields, and the location bookkeeping written by the importer itself.
_IMPORT_KEEP_KEYS = {"ac_id", "ac_make_company", "room_id", "asset_code", "location_status"}


def _drop_blank_optional_fields(values):
    """Blank/missing optional spreadsheet cells mean ""no update"" â€” they must
    never erase existing database values during duplicate/update imports
    (e.g. an omitted Serial Number must not wipe a stored serial)."""
    return {key: value for key, value in values.items() if value is not None or key in _IMPORT_KEEP_KEYS}


def _validate_canonical_row(lookup, values, row_number=None):
    """Required-field validation for the canonical campus-chain format.

    FINAL BUSINESS RULE: exactly three fields are required â€” block_name,
    department_name, ac_make_company. Every other AC/campus field is optional
    and must never reject the row when absent.
    """
    prefix = f"row {row_number}: " if row_number else ""
    for field in ("block_name", "department_name"):
        value = _empty_to_none(lookup.get(field))
        if value is None or (isinstance(value, str) and not value.strip()):
            return f"{prefix}{field} is required"
    return _validate_import_row(values, row_number)  # brand + range rules


def _attribute_type(value):
    """Deterministic typing for extensible attributes (no semantic guessing)."""
    try:
        float(str(value))
        return "NUMBER"
    except (TypeError, ValueError):
        return "TEXT"


def _bulk_upsert_attributes(cursor, rows):
    """Bulk upsert into asset_attributes. rows: (asset_uuid, name, value, type, source)."""
    statement = (
        "INSERT INTO asset_attributes (asset_uuid, attribute_name, attribute_value, attribute_type, source) "
        "VALUES {values} ON CONFLICT (asset_uuid, attribute_name) DO UPDATE SET "
        "attribute_value = EXCLUDED.attribute_value, attribute_type = EXCLUDED.attribute_type, "
        "source = EXCLUDED.source, updated_at = NOW()"
    )
    single_row = "(%s, %s, %s, %s, %s)"
    statements = 0
    for start in range(0, len(rows), BULK_UPSERT_CHUNK):
        batch = rows[start : start + BULK_UPSERT_CHUNK]
        cursor.execute(
            statement.format(values=", ".join([single_row] * len(batch))),
            tuple(value for row in batch for value in row),
        )
        statements += 1
    return statements


def _room_error_details(pairs_by_reference):
    """Group unresolved room references: [(reference, row list)] -> count summary,
    resistant to flooding (row list capped, full count kept)."""
    return [
        {"reference": reference, "rows": rows_list[:50], "count": len(rows_list)}
        for reference, rows_list in sorted(pairs_by_reference.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]


def _bulk_upsert(cursor, columns, rows):
    """Multi-row ``INSERT ... ON CONFLICT (ac_id) DO UPDATE``.

    Replaces one statement per row with one statement per chunk. Returns the
    number of statements executed (used by the production perf log).
    """
    columns = list(columns)
    column_sql = ", ".join(columns)
    single_row = "(" + ", ".join(["%s"] * len(columns)) + ")"
    updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns if column != "ac_id")
    conflict_clause = (
        f"ON CONFLICT (ac_id) DO UPDATE SET {updates}, updated_at = NOW()"
        if updates
        else "ON CONFLICT (ac_id) DO NOTHING"
    )
    statements = 0
    for start in range(0, len(rows), BULK_UPSERT_CHUNK):
        batch = rows[start : start + BULK_UPSERT_CHUNK]
        values_sql = ", ".join([single_row] * len(batch))
        params = tuple(value for row in batch for value in (row[column] for column in columns))
        cursor.execute(f"INSERT INTO acs ({column_sql}) VALUES {values_sql} {conflict_clause}", params)
        statements += 1
    return statements


CSV_ALIASES = {
    "ac_id": ("ac_id", "ac_identifier", "asset_id", "s_no", "serial_no"),
    "room_id": ("room_id",),
    # Aliases are compared AFTER _header() normalization, so raw headers such
    # as "Brand / Company", "AC Make" and "AC Manufacturer" collapse to the
    # same snake_case keys (reference file: ACMS 301.xlsx). New variants are
    # appended so the precedence of the existing aliases never changes.
    "ac_make_company": (
        "brand",
        "company",
        "name_of_the_brand",
        "manufacturer",
        "ac_make_company",
        "brand_company",
        "make",
        "ac_make",
        "ac_manufacturer",
    ),
    "ac_model_number": ("model", "model_number", "ac_model_number"),
    "ac_serial_number": ("serial_number", "serial", "ac_serial_number"),
    "ac_capacity_ton": ("capacity_ton", "capacity", "tonnage", "ac_capacity_ton"),
    "ac_type": ("ac_type", "type", "unit_type"),
    "current_status": ("status", "current_status"),
    "manufacturing_year": ("manufacturing_year", "manufacture_year"),
    "ac_star_rating": ("ac_star_rating", "star_rating", "stars"),
    "power_rating_kw": ("power_rating_kw", "power_rating", "power_kw"),
    "power_factor": ("power_factor",),
    "installation_year": ("installation_year", "year_of_purchase", "purchase_year"),
    "installation_date": ("installation_date",),
    "last_maintenance_date": ("last_maintenance_date",),
    "next_service_date": ("next_service_date", "next_maintenance_date"),
    "department_name": ("department", "department_name"),
    "room_name": ("location", "room_name"),
    "technician": ("technician",),
    "remarks": ("remarks",),
    # Canonical 22-column campus-chain format. These are lookup/reference fields
    # used to resolve Collegeâ†’Blockâ†’Floorâ†’Departmentâ†’Room during import (never
    # stored on the AC row itself), plus two extended AC attributes stored via
    # the extensible asset_attributes mechanism.
    "college_name": ("college_name",),
    "block_name": ("block_name",),
    "block_id": ("block_id",),
    "floor_name": ("floor_name",),
    "floor_id": ("floor_id",),
    "room_number": ("room_number",),
    "room_type": ("room_type",),
    "unit_number": ("unit_number",),
    "last_maintenance_type": ("last_maintenance_type", "maintenance_type"),
}

# Lookup-only import fields: resolved against campus master data or used for
# identity generation â€” never written as acs columns.
IMPORT_LOOKUP_FIELDS = (
    "department_name",
    "room_name",
    "college_name",
    "block_name",
    "block_id",
    "floor_name",
    "floor_id",
    "room_number",
    "room_type",
)
# Extended canonical fields preserved as typed asset_attributes (they have no
# typed acs column and must NOT end up in unknown_columns).
IMPORT_EXTENDED_ATTRIBUTE_FIELDS = ("unit_number", "last_maintenance_type")
# A file is in canonical campus-chain mode when any of these canonical-only
# columns are present.
CANONICAL_FORMAT_MARKERS = {
    "college_name",
    "room_number",
    "room_type",
    "unit_number",
    "last_maintenance_type",
}


def _header(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower()).strip("_")


def _read_import_rows(upload):
    filename = upload.filename.lower()
    if filename.endswith(".xlsx"):
        workbook = load_workbook(upload, read_only=True, data_only=True)
        sheet = workbook.active
        values = list(sheet.values)
        workbook.close()
        headers = [_header(value) for value in (values[0] if values else [])]
        rows = [
            {headers[index]: value for index, value in enumerate(row) if index < len(headers)}
            for row in values[1:]
        ]
        return headers, rows
    if filename.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(upload.read().decode("utf-8-sig")))
        headers = [_header(value) for value in (reader.fieldnames or [])]
        return headers, [
            {_header(key): value for key, value in row.items() if key is not None} for row in reader
        ]
    raise ValueError("Only CSV and XLSX files are supported")


def _is_blank_row(row):
    """True when a parsed row has no real content: every cell is null,
    empty or whitespace-only.

    Excel reports a physical max_row far beyond the data (trailing formatted
    rows) and users insert separator rows — those are not records and must
    never be validated, counted or written, for CSV and XLSX alike.
    """
    return all(value is None or (isinstance(value, str) and not value.strip()) for value in row.values())


def _csv_payload(row, row_number):
    values = {}
    for field, aliases in CSV_ALIASES.items():
        source = next((alias for alias in aliases if alias in row), None)
        if source is not None:
            values[field] = row[source]
    lookup = {key: values.pop(key, None) for key in IMPORT_LOOKUP_FIELDS + IMPORT_EXTENDED_ATTRIBUTE_FIELDS}
    # ac_id is deliberately NOT required at parse time: canonical-format rows
    # get their identity generated during import (see _derive_ac_id). Legacy-
    # mode rows are checked for ac_id later by the campus-chain-free flow.
    values = _canonical_payload(values, partial=True, row_number=row_number)
    if values.get("ac_id") is not None:
        values["ac_id"] = _coerce_ac_id(values["ac_id"])
    return values, lookup


def _text_id(value):
    """Uppercase, slugged component for generated identities (deterministic)."""
    return re.sub(r"[^A-Z0-9]+", "-", str(value).strip().upper()).strip("-")


def _derive_ac_id(room_id, unit_number=None, serial_number=None, location_ref=None):
    """Project AC ID generation policy for imports: generated deterministically
    from the resolved location plus the unit's discriminator, so a second
    import of the same logical asset maps to the same AC (update, not duplicate).

    AC-<room_id>-U<unit_number>   (preferred; unit number present)
    AC-<room_id>-S<serial_number> (serial fallback)
    AC-<room_id>                  (room-sole-unit fallback)
    For unassigned assets (no resolvable room) the deterministic location
    token is derived from the resolved block/department reference:
    AC-<block>-<dept>-Uâ€¦ / -Sâ€¦ / bare.
    """
    location = room_id if room_id else f"LOC-{_text_id(location_ref or 'UNSCOPED')}"
    unit = _text_id(unit_number) if _empty_to_none(unit_number) else None
    if unit:
        return f"AC-{location}-U{unit}"
    serial = _text_id(serial_number) if _empty_to_none(serial_number) else None
    if serial:
        return f"AC-{location}-S{serial}"
    return f"AC-{location}"
