"""Campus CSV import core (batched, single transaction).

Formerly the legacy ``/api/campus/import`` route; the Phase 3 task 8 v1
endpoint (``app.api.v1.campus_import``) now calls :func:`run_campus_import`
and wraps the report in the standard envelope. Behaviour is unchanged:

parse + validate every row locally, then write blocks/floors/departments/rooms
with one multi-row statement per table in a single transaction — any
unexpected DB failure rolls back everything and only a generic message
reaches the client (detail stays in the server log with the request id).

Hierarchy and upsert semantics: College → Block → Floor → Department → Room;
in-file duplicates keep the last occurrence, matching the old sequential
per-row upsert outcome.
"""

import csv
import io
import logging
import re
from collections import OrderedDict

from psycopg2.extras import RealDictCursor

from app.config.database import get_db_connection

logger = logging.getLogger(__name__)

# Multi-row INSERTs are chunked to stay far below the 65 535 bind-parameter
# limit even for pathologically large campus files.
BULK_CHUNK = 500

MAX_IMPORT_BYTES = 10 * 1024 * 1024  # 10 MB (parity with the asset import)


class CampusImportError(Exception):
    """User-facing import failure (message is safe to return to the client)."""

    def __init__(self, message: str, status: int = 400, code: str = "INVALID_REQUEST"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _value(row, *names):
    for name in names:
        if name in row:
            value = row[name].strip() if isinstance(row[name], str) else row[name]
            return value or None
    return None


def _header(value):
    return "_".join(str(value).strip().lower().replace("-", " ").split())


def _floor_number(value):
    match = re.search(r"-?\d+", value or "")
    return int(match.group()) if match else None


def _derived_id(prefix, value):
    return f"{prefix}-{re.sub(r'[^A-Z0-9]+', '-', value.upper()).strip('-')}"


def _bulk_upsert(cursor, table, columns, rows, conflict_target=None, custom_updates=None):
    """Batched INSERT ... ON CONFLICT DO UPDATE.

    Returns the number of statements executed (chunked by BULK_CHUNK).
    custom_updates overrides the SET expression per column, e.g.
    {"college_id": "college_id = COALESCE(blocks.college_id, EXCLUDED.college_id)"}.
    """
    columns = list(columns)
    column_sql = ", ".join(columns)
    single_row = "(" + ", ".join(["%s"] * len(columns)) + ")"
    conflict = ""
    if conflict_target:
        updates = ", ".join(
            (custom_updates or {}).get(column, f"{column} = EXCLUDED.{column}")
            for column in columns
            if column != conflict_target
        )
        conflict = f" ON CONFLICT ({conflict_target}) DO UPDATE SET {updates}, updated_at = NOW()"
    statements = 0
    for start in range(0, len(rows), BULK_CHUNK):
        batch = rows[start : start + BULK_CHUNK]
        values_sql = ", ".join([single_row] * len(batch))
        params = tuple(value for row in batch for value in row)
        cursor.execute(f"INSERT INTO {table} ({column_sql}) VALUES {values_sql}{conflict}", params)
        statements += 1
    return statements


def run_campus_import(upload, request_id, *, dry_run: bool = False) -> dict:
    """Parse a campus CSV and (unless ``dry_run``) write it in one transaction.

    Returns the report dict (``total_rows``, ``imported``, ``failed``,
    ``errors``, entity counts, ``dry_run``). Raises :class:`CampusImportError`
    for user-facing failures; any unexpected exception propagates AFTER being
    logged with the request id, so callers only need a generic envelope.
    """
    if upload is None or not upload.filename or not upload.filename.lower().endswith(".csv"):
        raise CampusImportError("A CSV file is required", 400, "FILE_REQUIRED")

    data = upload.read()
    if len(data) > MAX_IMPORT_BYTES:
        raise CampusImportError("The file exceeds the 10 MB import limit.", 413, "FILE_TOO_LARGE")
    if not data:
        raise CampusImportError("The uploaded file is empty.", 400, "FILE_EMPTY")

    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        if not reader.fieldnames:
            raise CampusImportError("CSV headers are required", 400, "NO_HEADERS")
        rows = [{_header(key): value for key, value in row.items() if key is not None} for row in reader]
    except UnicodeDecodeError:
        raise CampusImportError("CSV must be UTF-8 encoded", 400, "INVALID_FILE_CONTENT") from None

    # Phase 1 — parse and validate every row locally (no DB round-trips).
    errors = []
    blocks, floors, departments, rooms = (OrderedDict() for _ in range(4))
    imported = 0
    for row_number, row in enumerate(rows, start=2):
        try:
            block_id = _value(row, "building_id", "block_id")
            block_name = _value(row, "building_name", "block_name")
            floor_name = _value(row, "floor", "floor_name")
            room_id = _value(row, "room_id")
            room_name = _value(row, "room_name")
            department_name = _value(row, "department", "department_name")
            missing = [
                field
                for field, value in (
                    ("building_id", block_id),
                    ("building_name", block_name),
                    ("floor", floor_name),
                    ("room_id", room_id),
                    ("room_name", room_name),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"row {row_number}: missing required campus fields: {', '.join(missing)}")

            floor_id = f"{block_id}-FLR-{re.sub(r'[^A-Z0-9]+', '-', floor_name.upper()).strip('-')}"
            department_id = _derived_id("DEPT", department_name) if department_name else None
            blocks[block_id] = (block_id, block_name)
            floors[floor_id] = (floor_id, block_id, floor_name, _floor_number(floor_name))
            if department_id:
                departments[department_id] = (department_id, department_name)
            rooms[room_id] = (room_id, floor_id, department_id, room_name)
            imported += 1
        except ValueError as error:
            # Intentionally user-facing validation text.
            errors.append({"row": row_number, "message": str(error)})
        except Exception:
            logger.exception("campus import row %s failed request_id=%s", row_number, request_id)
            errors.append({"row": row_number, "message": f"Row {row_number} could not be processed."})

    report = {
        "dry_run": dry_run,
        "total_rows": len(rows),
        "imported": imported,
        "failed": len(errors),
        "errors": errors,
        "blocks": len(blocks),
        "floors": len(floors),
        "departments": len(departments),
        "rooms": len(rooms),
    }
    if dry_run:
        return report

    # Phase 2 — one preload SELECT + one batched statement per table in a
    # single transaction (commit on clean exit). An unexpected DB failure
    # rolls back everything and only a generic message reaches the client;
    # the detail stays in the server log with the request id.
    try:
        with get_db_connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cursor:
            # Single-institute deployment: link imported blocks to the one
            # existing college. NULL when the DB is (still) multi- or un-seeded.
            cursor.execute("SELECT college_id FROM colleges LIMIT 2")
            college_rows = cursor.fetchall()
            default_college_id = college_rows[0]["college_id"] if len(college_rows) == 1 else None

            if blocks:
                if default_college_id:
                    _bulk_upsert(
                        cursor,
                        "blocks",
                        ("block_id", "block_name", "college_id"),
                        [
                            (block_id, block_name, default_college_id)
                            for block_id, block_name in blocks.values()
                        ],
                        conflict_target="block_id",
                        custom_updates={
                            "college_id": "college_id = COALESCE(blocks.college_id, EXCLUDED.college_id)"
                        },
                    )
                else:
                    _bulk_upsert(
                        cursor,
                        "blocks",
                        ("block_id", "block_name"),
                        list(blocks.values()),
                        conflict_target="block_id",
                    )
            if floors:
                _bulk_upsert(
                    cursor,
                    "floors",
                    ("floor_id", "block_id", "floor_name", "floor_number"),
                    list(floors.values()),
                    conflict_target="floor_id",
                )
            if departments:
                _bulk_upsert(
                    cursor,
                    "departments",
                    ("department_id", "department_name"),
                    list(departments.values()),
                    conflict_target="department_id",
                )
            if rooms:
                _bulk_upsert(
                    cursor,
                    "rooms",
                    ("room_id", "floor_id", "department_id", "room_name"),
                    list(rooms.values()),
                    conflict_target="room_id",
                )
    except Exception:
        logger.exception("campus import write failed request_id=%s", request_id)
        raise

    return report
