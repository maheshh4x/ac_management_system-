"""POST /api/v1/assets/import — multipart bulk import (Phase 3 task 5).

Reuses the battle-tested parsers/validators from ``app.routes.acs`` (the
legacy endpoint's helpers) behind auth + permission + envelope contracts:

* extensions ``.xlsx``/``.csv`` only, content sniffed (magic bytes),
* max 10 MB, formulas are never evaluated (openpyxl ``data_only=True``),
* ``dry_run=true`` returns a row-level classification report and writes
  nothing,
* commit runs in ONE transaction: row statements are chained into chunked
  multi-statement round trips (one SAVEPOINT per chunk) so hundreds of rows
  cost a handful of database round trips; a failing chunk rolls back to its
  savepoint and falls back to per-row SAVEPOINT isolation, so a single bad
  row still never poisons the file, and returns counts,
* a client-supplied ``Idempotency-Key`` makes retries after a timeout safe:
  a concurrent duplicate is rejected with 409, a completed one replays the
  first report instead of importing again.
"""

from __future__ import annotations

import hashlib
import io
import logging
import threading
import time

from flask import Blueprint, g, request
from psycopg2.extras import RealDictCursor
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.auth.jwt import require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, success
from app.routes import acs as acs_parsers
from app.services import location_resolver
from app.services.status_map import derive_operational_status

assets_import_v1_bp = Blueprint("assets_import_v1", __name__)

logger = logging.getLogger(__name__)

MAX_IMPORT_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".xlsx", ".csv"}
_XLSX_MAGIC = b"PK\x03\x04"

# Batched write geometry: a chunk's row statements are chained client-side
# (cursor.mogrify) into ONE round trip wrapped in its own SAVEPOINT. 100 rows
# keeps a chunk message well under 1 MB while cutting sequential round trips
# ~60x (per-statement network RTT dominated the old per-row loop: ~930
# statements for a 300-row file ≈ 39 s against a ~42 ms remote database).
IMPORT_WRITE_CHUNK = 100

# Idempotent retry guard (timeout-safe). A commit whose client supplies an
# ``Idempotency-Key`` header is checked here BEFORE any database work:
#   * key still in flight       -> 409 IMPORT_IN_PROGRESS (never a second run),
#   * key completed in the TTL  -> replay the first report (``replayed: true``),
#   * otherwise                 -> run the import and register the key.
# Data-level idempotency (ac_id unique + ON CONFLICT upserts) still protects
# across processes/restarts; this in-process cache only avoids repeating work
# and hands a retrying client the FIRST attempt's authoritative result.
IDEMPOTENCY_TTL_SECONDS = 10 * 60
IDEMPOTENCY_MAX_ENTRIES = 32
_idempotency_lock = threading.Lock()
_inflight_imports: set[str] = set()
_completed_imports: dict[str, tuple[float, str, dict]] = {}  # key -> (at, sha256, report)


def _idempotency_prune(now: float) -> None:
    """Called with ``_idempotency_lock`` held: drop expired + oldest entries."""
    expired = [
        key for key, (at, _sha, _report) in _completed_imports.items() if now - at > IDEMPOTENCY_TTL_SECONDS
    ]
    for key in expired:
        del _completed_imports[key]
    while len(_completed_imports) > IDEMPOTENCY_MAX_ENTRIES:
        oldest = min(_completed_imports, key=lambda key: _completed_imports[key][0])
        del _completed_imports[oldest]


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _sniff(data: bytes, ext: str) -> str | None:
    """Content sniffing: the magic bytes must match the declared extension.
    Returns an error message or None."""
    if ext == ".xlsx":
        if not data.startswith(_XLSX_MAGIC):
            return "File content is not a valid .xlsx workbook (missing ZIP signature)."
    else:  # .csv — must be text, definitely not a ZIP container
        if data.startswith(_XLSX_MAGIC):
            return "File content looks like a workbook, but the extension is .csv."
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return "CSV must be UTF-8 encoded."
    return None


def _classify_row(ac_id: str | None, existing: set[str], seen: dict[str, int]):
    """Decide the action for one parsed row against DB state + in-file dupes."""
    if ac_id and ac_id in seen:
        return "duplicate"
    if ac_id and ac_id in existing:
        return "update"
    return "create"


def _kept_source_warnings(row_number: int, row_res, skip: set[str] | None = None) -> list[dict]:
    """Audit warnings for sources that failed but did not downgrade (§4).

    A kept level stays RESOLVED, so it emits no pending_location warning —
    without this entry the failed source would be invisible in the import
    report (it remains stored in its source_* column + noted for review
    regardless).
    """
    skip = skip or set()
    out = []
    for level in location_resolver.LEVELS:
        if level in row_res.kept and level not in skip:
            out.append(
                {
                    "row": row_number,
                    "kind": "unresolved_source",
                    "message": (
                        f"row {row_number}: {level} source did not resolve"
                        f" ({row_res.notes.get(level)}); kept previously confirmed location"
                    ),
                }
            )
    return out


def _load_previous_location(cursor, ac_ids, into: dict) -> None:
    """Batched load of confirmed locations for ac_ids (§11, no N+1).

    Fills ``into[ac_id] = {"ids": {...}, "manual": frozenset(...)}`` for
    every id not already present; ids without an asset_locations row are
    simply absent (the import's first write then creates theirs).
    """
    missing = [ac_id for ac_id in ac_ids if ac_id not in into]
    if not missing:
        return
    cursor.execute(
        "SELECT ac_id, " + ", ".join(f"{level}_id" for level in location_resolver.LEVELS) + ", manual_fields "
        "FROM asset_locations WHERE ac_id = ANY(%s)",
        (sorted(missing),),
    )
    for record in cursor.fetchall():
        into[record["ac_id"]] = {
            "ids": {level: record[f"{level}_id"] for level in location_resolver.LEVELS},
            "manual": frozenset(record["manual_fields"] or ()),
        }


@assets_import_v1_bp.route("/api/v1/assets/import", methods=["POST"])
@require_auth
@require_permission("IMPORT_DATA")
def import_assets():
    request_id = getattr(g, "request_id", "-")
    dry_run = _is_true(request.args.get("dry_run") or request.form.get("dry_run"))

    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return error("FILE_REQUIRED", "A CSV or XLSX file is required.", 400)

    filename = secure_filename(upload.filename) or "upload"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        return error("UNSUPPORTED_FILE_TYPE", "Only .xlsx and .csv files are supported.", 400)

    # Size cap without buffering the whole upload first.
    try:
        upload.stream.seek(0, io.SEEK_END)
        size = upload.stream.tell()
        upload.stream.seek(0)
    except (AttributeError, OSError):
        size = None
    if size is not None and size > MAX_IMPORT_BYTES:
        return error("FILE_TOO_LARGE", "The file exceeds the 10 MB import limit.", 413)

    data = upload.read()
    if len(data) > MAX_IMPORT_BYTES:
        return error("FILE_TOO_LARGE", "The file exceeds the 10 MB import limit.", 413)
    if not data:
        return error("FILE_EMPTY", "The uploaded file is empty.", 400)

    sniff_error = _sniff(data, ext)
    if sniff_error:
        return error("INVALID_FILE_CONTENT", sniff_error, 400)

    # Reuse the legacy parser through a FileStorage wrapper (same code path
    # the well-tested /api/acs/import uses).
    wrapped = FileStorage(stream=io.BytesIO(data), filename=filename)
    try:
        headers, rows = acs_parsers._read_import_rows(wrapped)
    except ValueError as exc:
        return error("INVALID_FILE_CONTENT", str(exc), 400)
    except UnicodeDecodeError:
        return error("INVALID_FILE_CONTENT", "CSV must be UTF-8 encoded.", 400)
    except Exception:
        logger.exception("import parse failed request_id=%s", request_id)
        return error("PARSE_FAILED", "The file could not be parsed.", 400)
    if not headers:
        return error("NO_HEADERS", "Import headers are required.", 400)

    # Blank padding rows (Excel's phantom max_row, user separator rows) are
    # not records: drop them before validation while keeping every survivor's
    # ORIGINAL spreadsheet row number so errors and report rows still point
    # at the row the user sees in Excel.
    physical_rows = len(rows)
    indexed_rows = [
        (row_number, row) for row_number, row in enumerate(rows, 2) if not acs_parsers._is_blank_row(row)
    ]
    blank_rows_ignored = physical_rows - len(indexed_rows)
    rows = [row for _, row in indexed_rows]

    available = set(headers)
    recognized = sorted(
        field
        for field, aliases in acs_parsers.CSV_ALIASES.items()
        if any(alias in available for alias in aliases)
    )
    recognized_aliases = {alias for aliases in acs_parsers.CSV_ALIASES.values() for alias in aliases}
    ignored = sorted(set(headers) - recognized_aliases)
    missing_fields = [field for field in acs_parsers.OPTIONAL_FIELDS if field not in recognized]
    canonical_mode = bool(set(headers) & acs_parsers.CANONICAL_FORMAT_MARKERS)

    # ── Phase 1: parse + validate locally (no writes, no row round-trips) ──
    errors: list[dict] = []
    warnings: list[dict] = []
    report_rows: list[dict] = []
    prepared: list[tuple[int, dict, dict, dict]] = []
    for row_number, row in indexed_rows:
        try:
            values, lookup = acs_parsers._csv_payload(row, row_number)
            values.setdefault("asset_code", values.get("ac_id"))
            values = acs_parsers._drop_blank_optional_fields(values)
            prepared.append((row_number, values, lookup, row))
        except ValueError as exc:
            errors.append({"row": row_number, "message": str(exc)})
            report_rows.append({"row": row_number, "action": "invalid", "message": str(exc)})
        except Exception:
            logger.exception("import row %s payload failed request_id=%s", row_number, request_id)
            message = f"Row {row_number} could not be processed."
            errors.append({"row": row_number, "message": message})
            report_rows.append({"row": row_number, "action": "invalid", "message": message})

    # ── Idempotent retry guard (commits only, see IDEMPOTENCY_TTL_SECONDS) ──
    # Checked before any DB work so a replayed retry is instant; registration
    # happens in the same critical section, so a concurrent duplicate of the
    # same key observes 409 instead of racing us through the import.
    idem_header = (request.headers.get("Idempotency-Key") or "").strip()[:128]
    cache_key = None
    digest = None
    registered = False
    if idem_header and not dry_run:
        digest = hashlib.sha256(data).hexdigest()
        user_id = str((getattr(g, "current_user", None) or {}).get("id") or "-")
        cache_key = f"{user_id}:{idem_header}"
        with _idempotency_lock:
            _idempotency_prune(time.monotonic())
            if cache_key in _inflight_imports:
                return error(
                    "IMPORT_IN_PROGRESS",
                    "An identical import is already being processed. Wait for it"
                    " to finish, then verify before retrying.",
                    409,
                )
            completed = _completed_imports.get(cache_key)
            if completed is not None and completed[1] == digest:
                # Same key AND same bytes: the first attempt already finished —
                # return its report instead of importing a second time.
                replayed = dict(completed[2])
                replayed["replayed"] = True
                logger.info(
                    "v1 import replay request_id=%s created=%s updated=%s",
                    request_id,
                    replayed.get("created"),
                    replayed.get("updated"),
                )
                return success(replayed, 201)
            _inflight_imports.add(cache_key)
            registered = True

    try:
        conn = get_db_connection()
    except Exception:
        if registered:
            with _idempotency_lock:
                _inflight_imports.discard(cache_key)
        logger.exception("import db connect failed request_id=%s", request_id)
        return error("IMPORT_FAILED", "Unable to import the file.", 500)

    committed = False
    report: dict = {}
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # ── Phase 2: preload references (file-size-independent queries) ──
            room_ids, room_index = acs_parsers._load_room_directory(cursor)
            # Batched existence probe: only the ids this file actually carries
            # (one indexed query) instead of scanning the whole acs table.
            explicit_ids = sorted({values["ac_id"] for _, values, _, _ in prepared if values.get("ac_id")})
            existing_ids: set[str] = set()
            if explicit_ids:
                cursor.execute("SELECT ac_id FROM acs WHERE ac_id = ANY(%s)", (explicit_ids,))
                existing_ids = {record["ac_id"] for record in cursor.fetchall()}
            # Both formats resolve against the same centralized directory
            # (§6): the canonical format for its full chain, the legacy
            # format for department names and the chain derived from the
            # room its own matcher decides. Batch preloads only — no N+1.
            campus = acs_parsers._load_campus_directory(cursor)
            # Confirmed state of this file's existing assets (§11): one
            # indexed query; blank or unresolvable source values replayed
            # against it can never downgrade what is already confirmed.
            previous_by_ac: dict[str, dict] = {}
            _load_previous_location(cursor, existing_ids, previous_by_ac)

            # ── Phase 3: validate + resolve location (memory only) ──
            entry_by_row: dict[int, dict] = {}
            valid: list[tuple[int, dict]] = []
            attr_by_ac: dict[str, dict] = {}
            pending_location = 0
            generated = 0
            generated_ids: set[str] = set()
            resolve_cache: dict = {}
            merged: dict[str, dict] = {}
            seen_ids: dict[str, int] = {}
            rows_by_ac: dict[str, list[int]] = {}
            # Per-ac location bookkeeping (asset_locations): the per-field
            # resolution chosen for the row plus every source dict this file
            # carried for that id, in file order (duplicates replay over the
            # previous outcome; existing assets replay over DB state).
            loc_by_ac: dict[str, location_resolver.LocationResolution] = {}
            sources_by_ac: dict[str, list[dict]] = {}
            unmatched: set[str] = set()
            ambiguous: set[str] = set()
            unmatched_rows: dict[str, list[int]] = {}
            ambiguous_rows: dict[str, list[int]] = {}

            for row_number, values, lookup, raw_row in prepared:
                if canonical_mode:
                    violation = acs_parsers._validate_canonical_row(lookup, values, row_number)
                    if violation:
                        errors.append({"row": row_number, "message": violation})
                        report_rows.append({"row": row_number, "action": "invalid", "message": violation})
                        continue
                    row_source = {
                        "college": acs_parsers._empty_to_none(lookup.get("college_name")),
                        "block": acs_parsers._empty_to_none(lookup.get("block_name")),
                        "block_id": acs_parsers._empty_to_none(lookup.get("block_id")),
                        "floor": acs_parsers._empty_to_none(lookup.get("floor_name")),
                        "floor_id": acs_parsers._empty_to_none(lookup.get("floor_id")),
                        "department": acs_parsers._empty_to_none(lookup.get("department_name")),
                        "room_type": acs_parsers._empty_to_none(lookup.get("room_type")),
                        "room_number": acs_parsers._empty_to_none(lookup.get("room_number")),
                        # room_id is an acs-column field (not a lookup field),
                        # so it is read from values, not lookup — and honoured
                        # by the resolver's explicit-id pass, not discarded.
                        "room_id": acs_parsers._empty_to_none(values.get("room_id")),
                    }
                    try:
                        # Request-scoped memo: identical campus references
                        # resolve once instead of once per row (§26). The
                        # resolution is a pure function of these six source
                        # values plus the confirmed state, which is exactly
                        # the memo key. Confirmed state applies here when
                        # the id is already known (explicit ids); generated
                        # identities are replayed after their probe below.
                        early_ac = str(values.get("ac_id") or "").strip()
                        prev = previous_by_ac.get(early_ac)
                        row_res = location_resolver.resolve_memoized(
                            row_source,
                            campus,
                            previous_ids=prev["ids"] if prev else None,
                            previous_manual=prev["manual"] if prev else None,
                            cache=resolve_cache,
                        )
                        resolved_room = row_res.ids.get("room")
                    except Exception:
                        logger.exception("import row %s resolve failed request_id=%s", row_number, request_id)
                        message = f"Row {row_number} could not be processed."
                        errors.append({"row": row_number, "message": message})
                        report_rows.append({"row": row_number, "action": "invalid", "message": message})
                        continue
                    if resolved_room is not None:
                        values["room_id"] = resolved_room
                    else:
                        # Unresolvable/blank source never writes room_id (§11):
                        # an existing asset keeps its confirmed room (the
                        # column is simply not part of the statement — same
                        # blank-never-erases convention as every other
                        # field); a new insert still lands NULL as before.
                        values.pop("room_id", None)
                        pending_location += 1
                        location_note = location_resolver.room_failure_note(row_source, row_res) or (
                            "location could not be resolved"
                        )
                        warnings.append(
                            {
                                "row": row_number,
                                "kind": "pending_location",
                                "message": (
                                    f"row {row_number}: {location_note}; asset stored with pending location"
                                ),
                            }
                        )
                    # Overall status is id-derived: ASSIGNED with a room,
                    # PENDING with nothing resolved, PARTIAL in between (§5).
                    values["location_status"] = row_res.overall
                    # Sources that failed without downgrading stay visible in
                    # the report (§4); resolved rows emit no pending warning.
                    warnings.extend(_kept_source_warnings(row_number, row_res))
                    if not str(values.get("ac_id") or "").strip():
                        values["ac_id"] = acs_parsers._derive_ac_id(
                            resolved_room,
                            lookup.get("unit_number"),
                            values.get("ac_serial_number"),
                            location_ref=f"{lookup.get('block_name')}-{lookup.get('department_name')}",
                        )
                        generated += 1
                        generated_ids.add(values["ac_id"])
                    if acs_parsers._empty_to_none(values.get("asset_code")) is None:
                        values["asset_code"] = values["ac_id"]
                else:
                    if not str(values.get("ac_id") or "").strip():
                        message = "ac_id is required"
                        errors.append({"row": row_number, "message": message})
                        report_rows.append({"row": row_number, "action": "invalid", "message": message})
                        continue
                    violation = acs_parsers._validate_import_row(values, row_number)
                    if violation:
                        errors.append({"row": row_number, "message": violation})
                        report_rows.append({"row": row_number, "action": "invalid", "message": violation})
                        continue
                    try:
                        resolved = acs_parsers._resolve_room_local(
                            values.get("room_id"),
                            lookup["department_name"],
                            lookup["room_name"],
                            room_ids,
                            room_index,
                            row_number,
                        )
                        room_outcome = (resolved, None, None)
                        if resolved is None:
                            pending_location += 1
                            warnings.append(
                                {
                                    "row": row_number,
                                    "kind": "pending_location",
                                    "message": (
                                        f"row {row_number}: no department/location provided;"
                                        " asset stored with pending location"
                                    ),
                                }
                            )
                    except acs_parsers.RoomResolutionError as exc:
                        pair = f"{lookup.get('department_name')} | {lookup.get('room_name')}"
                        if exc.kind == "ambiguous":
                            ambiguous.add(pair)
                            ambiguous_rows.setdefault(pair, []).append(row_number)
                            legacy_status = location_resolver.AMBIGUOUS
                        else:
                            unmatched.add(pair)
                            unmatched_rows.setdefault(pair, []).append(row_number)
                            legacy_status = location_resolver.UNRESOLVED
                        room_outcome = (None, str(exc), legacy_status)
                        pending_location += 1
                        warnings.append({"row": row_number, "kind": exc.kind, "message": str(exc)})
                    except ValueError as exc:
                        # Half-specified location or nonexistent explicit
                        # room_id: keep pending, never guess. The reference is
                        # the raw (unresolved) value, exactly like legacy.
                        reference = str(
                            values.get("room_id")
                            or f"{lookup.get('department_name')} | {lookup.get('room_name')}"
                        )
                        unmatched.add(reference)
                        unmatched_rows.setdefault(reference, []).append(row_number)
                        room_outcome = (None, str(exc), location_resolver.UNRESOLVED)
                        pending_location += 1
                        warnings.append({"row": row_number, "kind": "unmatched", "message": str(exc)})

                    # Per-field location record for the legacy format too
                    # (§4/§6): the decision above is adopted verbatim as the
                    # room outcome — its pinned messages stay in the legacy
                    # path — while department matching and the canonical
                    # chain around it come from the centralized resolver.
                    row_source = {
                        "college": None,
                        "block": None,
                        "block_id": None,
                        "floor": None,
                        "floor_id": None,
                        "department": acs_parsers._empty_to_none(lookup["department_name"]),
                        "room_type": None,
                        "room_number": acs_parsers._empty_to_none(lookup["room_name"]),
                        # The legacy matcher already honours an explicit
                        # room_id; preserve it as source metadata too.
                        "room_id": acs_parsers._empty_to_none(values.get("room_id")),
                    }
                    try:
                        early_ac = str(values.get("ac_id") or "").strip()
                        prev = previous_by_ac.get(early_ac)
                        row_res = location_resolver.resolve_memoized(
                            row_source,
                            campus,
                            previous_ids=prev["ids"] if prev else None,
                            previous_manual=prev["manual"] if prev else None,
                            room_outcome=room_outcome,
                            cache=resolve_cache,
                        )
                    except Exception:
                        logger.exception(
                            "import row %s location record failed request_id=%s", row_number, request_id
                        )
                        message = f"Row {row_number} could not be processed."
                        errors.append({"row": row_number, "message": message})
                        report_rows.append({"row": row_number, "action": "invalid", "message": message})
                        continue
                    if room_outcome[0] is not None:
                        values["room_id"] = room_outcome[0]
                    else:
                        # Never write an unresolvable reference over a
                        # confirmed room (§11): the column is simply omitted
                        # from the statement, like every blank field.
                        values.pop("room_id", None)
                    values["location_status"] = row_res.overall
                    # Kept non-room levels never warn otherwise (legacy room
                    # warnings above are room-only); a failed room outcome
                    # already warned, so skip it here to avoid doubling.
                    skip = {"room"} if room_outcome[0] is None else set()
                    warnings.extend(_kept_source_warnings(row_number, row_res, skip))

                ac_id = values["ac_id"]
                # asset_locations outcome for this id: an in-file duplicate
                # chains onto the previous row's outcome, so a later row
                # with blank location data can never erase an earlier row's
                # resolution — the resolver's keep-previous rule (§11)
                # applied to in-file state, mirroring `merged`'s
                # later-wins-per-column semantics for location columns.
                previous_outcome = loc_by_ac.get(ac_id)
                if previous_outcome is not None:
                    row_res = location_resolver.resolve(
                        row_source,
                        campus,
                        previous_ids=previous_outcome.ids,
                        previous_manual=previous_outcome.manual_fields,
                    )
                loc_by_ac[ac_id] = row_res
                sources_by_ac.setdefault(ac_id, []).append(row_source)
                action = _classify_row(ac_id, existing_ids, seen_ids)
                rows_by_ac.setdefault(ac_id, []).append(row_number)
                if action == "duplicate":
                    merged[ac_id].update(values)  # later row wins per column
                    report_rows.append(
                        {
                            "row": row_number,
                            "ac_id": ac_id,
                            "action": "duplicate",
                            "merged_into_row": seen_ids[ac_id],
                        }
                    )
                else:
                    seen_ids[ac_id] = row_number
                    merged[ac_id] = dict(values)
                    action = "update" if ac_id in existing_ids else "create"
                    report_rows.append({"row": row_number, "ac_id": ac_id, "action": action})
                    valid.append((row_number, merged[ac_id]))

                # Unknown columns + extended fields become typed attributes.
                extras = attr_by_ac.setdefault(ac_id, {})
                for header_name in ignored:
                    raw_value = acs_parsers._empty_to_none(raw_row.get(header_name))
                    if raw_value is not None:
                        extras[header_name] = raw_value
                for extended_field in acs_parsers.IMPORT_EXTENDED_ATTRIBUTE_FIELDS:
                    ext_value = acs_parsers._empty_to_none(lookup.get(extended_field))
                    if ext_value is not None:
                        extras[extended_field] = str(ext_value)

            # Generated identities only became known during resolution — one
            # batched probe flips provisional "create" labels to "update" when
            # such an asset already exists (equivalent to what the old
            # full-table scan saw, without transferring the whole table).
            if generated_ids:
                cursor.execute("SELECT ac_id FROM acs WHERE ac_id = ANY(%s)", (sorted(generated_ids),))
                known_ids = {record["ac_id"] for record in cursor.fetchall()}
                if known_ids:
                    for entry in report_rows:
                        if entry.get("action") == "create" and entry.get("ac_id") in known_ids:
                            entry["action"] = "update"
                    # These identities only became known now, so their
                    # confirmed locations were not applied during the row
                    # loop: load them (one query) and replay this file's
                    # rows over them in file order (§11 — a generated
                    # identity that already exists must not be downgraded
                    # by source values it never had).
                    _load_previous_location(cursor, known_ids, previous_by_ac)
                    for ac_id in sorted(known_ids):
                        sources = sources_by_ac.get(ac_id)
                        prev = previous_by_ac.get(ac_id)
                        if not sources or prev is None:
                            continue  # nothing confirmed to protect
                        outcome = None
                        for src in sources:
                            outcome = location_resolver.resolve(
                                src,
                                campus,
                                previous_ids=prev["ids"] if outcome is None else outcome.ids,
                                previous_manual=(
                                    prev["manual"] if outcome is None else outcome.manual_fields
                                ),
                            )
                        loc_by_ac[ac_id] = outcome

            # Row-level classification -> counts. Derived from the final
            # per-row actions so validation failures and write failures stay
            # consistent by construction (no incremental counters to drift).
            entry_by_row = {entry["row"]: entry for entry in report_rows}

            def _counts() -> dict:
                return {
                    "imported": sum(1 for e in report_rows if e["action"] != "invalid"),
                    "created": sum(1 for e in report_rows if e["action"] == "create"),
                    "updated": sum(1 for e in report_rows if e["action"] == "update"),
                    "duplicates": sum(1 for e in report_rows if e["action"] == "duplicate"),
                    "invalid": sum(1 for e in report_rows if e["action"] == "invalid"),
                }

            report = {
                "dry_run": dry_run,
                "total_rows": len(rows),
                "physical_rows": physical_rows,
                "blank_rows_ignored": blank_rows_ignored,
                **_counts(),
                "pending_location": pending_location,
                "failed": len(errors),
                "errors": errors,
                "warnings": warnings,
                "rows": report_rows,
                "missing_fields": missing_fields,
                "recognized_columns": recognized,
                "unknown_columns": ignored,
                "ignored_columns": ignored,
                "unmatched_rooms": sorted(unmatched),
                "ambiguous_rooms": sorted(ambiguous),
                "unmatched_room_details": acs_parsers._room_error_details(unmatched_rows),
                "ambiguous_room_details": acs_parsers._room_error_details(ambiguous_rows),
                "canonical_format": canonical_mode,
                "generated_identities": generated,
                "attributes_recorded": sum(len(extras) for extras in attr_by_ac.values()),
            }

            if dry_run:
                conn.rollback()
                return success(report)

            # ── Phase 5: commit — ONE transaction, batched statements ──
            # Every row's statement is chained client-side (mogrify) into one
            # round trip per chunk, each chunk wrapped in its own SAVEPOINT. A
            # failing chunk rolls back to its savepoint and re-runs row-by-row
            # with the original per-row SAVEPOINT logic, so one bad row still
            # only poisons itself; systemic failures still abort the whole file
            # (the outer finally rolls everything back — no partial writes).
            payloads: list[tuple[int, dict]] = []
            for row_number, values in valid:
                payload = dict(values)
                if "current_status" in payload:
                    # CSV "status"/"current_status" stays an accepted input
                    # header; the dropped column (013) is not a write target.
                    payload["operational_status"] = derive_operational_status(payload.pop("current_status"))
                payloads.append((row_number, payload))

            attrs_written = 0
            failed_ids: set[str] = set()

            def _mark_write_failed(row_number: int, ac_id: str) -> None:
                # One write covers this ac_id and every in-file duplicate
                # merged into it: all of those rows failed, none landed.
                affected = rows_by_ac.get(ac_id) or [row_number]
                for failed_row in affected:
                    entry = entry_by_row.get(failed_row)
                    if entry is not None:
                        entry["action"] = "invalid"
                        entry["message"] = "write failed"
                    errors.append({"row": failed_row, "message": f"Row {failed_row} could not be written."})
                failed_ids.add(ac_id)

            def _fallback_row(row_number: int, payload: dict) -> None:
                # The original per-row path: SAVEPOINT + upsert + attributes,
                # so a failing row rolls back only itself.
                nonlocal attrs_written
                ac_id = payload["ac_id"]
                cursor.execute("SAVEPOINT import_row")
                try:
                    acs_parsers._upsert_ac(cursor, payload)
                    extras = attr_by_ac.get(ac_id)
                    if extras:
                        cursor.execute(
                            "SELECT asset_uuid FROM acs WHERE ac_id = %s",
                            (ac_id,),
                        )
                        uuid_row = cursor.fetchone()
                        if uuid_row:
                            attr_rows = [
                                (
                                    uuid_row["asset_uuid"],
                                    name,
                                    str(value),
                                    acs_parsers._attribute_type(value),
                                    "import",
                                )
                                for name, value in extras.items()
                            ]
                            acs_parsers._bulk_upsert_attributes(cursor, attr_rows)
                            attrs_written += len(attr_rows)
                    cursor.execute("RELEASE SAVEPOINT import_row")
                except Exception:
                    cursor.execute("ROLLBACK TO SAVEPOINT import_row")
                    logger.exception("import row %s write failed request_id=%s", row_number, request_id)
                    _mark_write_failed(row_number, ac_id)

            for start in range(0, len(payloads), IMPORT_WRITE_CHUNK):
                chunk = payloads[start : start + IMPORT_WRITE_CHUNK]
                savepoint = f"import_chunk_{start // IMPORT_WRITE_CHUNK}"
                chunk_ac_ids = [payload["ac_id"] for _, payload in chunk]
                attr_ids = sorted(ac_id for ac_id in chunk_ac_ids if attr_by_ac.get(ac_id))
                sent = False
                try:
                    statements = [f"SAVEPOINT {savepoint}"]
                    for _row_number, payload in chunk:
                        sql, params = acs_parsers._upsert_ac_parts(payload)
                        statements.append(cursor.mogrify(sql, params).decode("utf-8"))
                    if not attr_ids:
                        statements.append(f"RELEASE SAVEPOINT {savepoint}")
                    sent = True  # from here on a failure may have executed rows
                    cursor.execute("; ".join(statements))
                    chunk_attrs = 0
                    if attr_ids:
                        # Attributes ride inside the same chunk savepoint: the
                        # asset rows and their attributes commit or roll back
                        # together, exactly like the old per-row write.
                        cursor.execute(
                            "SELECT ac_id, asset_uuid FROM acs WHERE ac_id = ANY(%s)",
                            (attr_ids,),
                        )
                        uuid_by_ac = {record["ac_id"]: record["asset_uuid"] for record in cursor.fetchall()}
                        attr_rows = [
                            (
                                uuid_by_ac[ac_id],
                                name,
                                str(value),
                                acs_parsers._attribute_type(value),
                                "import",
                            )
                            for ac_id in chunk_ac_ids
                            if ac_id in uuid_by_ac
                            for name, value in (attr_by_ac.get(ac_id) or {}).items()
                        ]
                        if attr_rows:
                            acs_parsers._bulk_upsert_attributes(cursor, attr_rows)
                            chunk_attrs = len(attr_rows)
                        cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
                    attrs_written += chunk_attrs  # counted only after the release
                except Exception:
                    if sent:
                        try:
                            cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        except Exception:
                            logger.exception(
                                "import chunk savepoint rollback failed request_id=%s", request_id
                            )
                    logger.exception(
                        "import chunk starting at row %s failed; retrying row-by-row request_id=%s",
                        chunk[0][0],
                        request_id,
                    )
                    for row_number, payload in chunk:
                        _fallback_row(row_number, payload)

            # ── asset_locations first, then the acs mirror (single-writer
            # contract from migration 014): one batched upsert + one batched
            # mirror UPDATE for exactly the rows that landed — failed ids
            # keep their previous record untouched, and the mirror trigger
            # sees a matching record so it only performs its status check.
            loc_rows = [
                (payload["ac_id"], loc_by_ac[payload["ac_id"]])
                for _, payload in payloads
                if payload["ac_id"] not in failed_ids and payload["ac_id"] in loc_by_ac
            ]
            if loc_rows:
                location_resolver.apply_many(
                    cursor, loc_rows, source_ref=f"import:{filename}", resolved_by="import"
                )
                location_resolver.sync_acs(cursor, loc_rows)

            # Recompute counts from the final classification (write failures
            # mutated the row actions above) and the actually-landed attributes.
            report.update(_counts())
            report["failed"] = len(errors)
            report["attributes_recorded"] = attrs_written
            conn.commit()
            committed = True
            if registered:
                with _idempotency_lock:
                    _completed_imports[cache_key] = (time.monotonic(), digest, dict(report))
    except Exception:
        logger.exception("import failed request_id=%s", request_id)
        return error("IMPORT_FAILED", "Import failed. No data was written.", 500)
    finally:
        try:
            if not committed:
                conn.rollback()
        finally:
            conn.close()
        if registered:
            # Always release the key (success stored the report above; a
            # failure stores nothing so the retry runs the import fresh).
            with _idempotency_lock:
                _inflight_imports.discard(cache_key)

    logger.info(
        "v1 import dry_run=%s rows=%s created=%s updated=%s duplicates=%s invalid=%s"
        " pending=%s failed=%s request_id=%s",
        dry_run,
        len(rows),
        report["created"],
        report["updated"],
        report["duplicates"],
        report["invalid"],
        pending_location,
        report["failed"],
        request_id,
    )
    return success(report, 201 if not dry_run else 200)
