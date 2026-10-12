"""Campus hierarchy endpoints (Phase 3 task 6).

GET  /api/v1/campus/tree                 — colleges -> blocks -> floors -> rooms
POST/PATCH/DELETE /api/v1/campus/blocks  — MANAGE_CAMPUS; delete refused (409)
POST/PATCH/DELETE /api/v1/campus/floors  — delete refused while rooms exist (409)
POST/PATCH/DELETE /api/v1/campus/rooms   — delete refused while ACs exist (409)
POST/PATCH/DELETE /api/v1/campus/departments — delete refused while rooms exist (409)
PATCH /api/v1/campus/blocks/<id>/layout  — JSON schema-validated, size-capped
POST/DELETE /api/v1/campus/blocks/<id>/photos — signed upload via private
    Supabase Storage bucket (server-side service key; never in the browser).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Blueprint, g, request
from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor
from pydantic import ValidationError

from app.auth.jwt import require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, paginated, success
from app.schemas.v1_schemas import (
    BlockCreate,
    BlockPatch,
    DepartmentCreate,
    DepartmentPatch,
    FloorCreate,
    FloorPatch,
    LocationAliasCreate,
    RoomCreate,
    RoomPatch,
)
from app.services import location_resolver, location_sql
from app.services.audit_service import audit

campus_v1_bp = Blueprint("campus_v1", __name__)

logger = logging.getLogger(__name__)

LAYOUT_MAX_BYTES = 256 * 1024  # 256 KB cap on block layout documents
LAYOUT_MAX_DEPTH = 12
PHOTO_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per photo
PHOTO_EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
PHOTO_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF")


def _storage_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_ROLE_KEY"))


def _storage_headers() -> dict:
    return {
        "Authorization": f"Bearer {os.getenv('SUPABASE_SERVICE_ROLE_KEY')}",
        "apikey": os.getenv("SUPABASE_SERVICE_ROLE_KEY", ""),
    }


def _storage_request(
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    json_body: dict | None = None,
    content_type: str | None = None,
    timeout: int = 15,
) -> tuple[int, bytes]:
    """Minimal Supabase Storage REST call via stdlib (no extra dependency).

    Returns (status_code, response_body). Raises on transport errors.
    """
    url = f"{os.getenv('SUPABASE_URL')}/storage/v1{path}"
    headers = dict(_storage_headers())
    data = body
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type
        headers["x-upsert"] = "true"
    request_obj = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request_obj, timeout=timeout) as response:  # noqa: S310 - server-side URL from config
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def _sign_read_url(path: str) -> str | None:
    """Signed READ url for a stored object (1 h). None when storage is not
    configured or Supabase errors — the tree still returns photo metadata."""
    if not _storage_configured() or not path:
        return None
    bucket = os.getenv("BUILDING_PHOTOS_BUCKET", "building-photos")
    try:
        status, body = _storage_request(
            "POST", f"/object/sign/{bucket}/{path}", json_body={"expiresIn": 3600}
        )
        if status != 200:
            logger.warning("sign photo url failed status=%s", status)
            return None
        signed = (json.loads(body or b"{}") or {}).get("signedURL")
        if not signed:
            return None
        return f"{os.getenv('SUPABASE_URL')}/storage/v1{signed}"
    except (URLError, ValueError, OSError):
        logger.exception("sign photo url failed")
        return None


def _serialize_photos(photos) -> list[dict]:
    out = []
    for photo in photos or []:
        entry = dict(photo)
        entry["signed_url"] = _sign_read_url(entry.get("path", ""))
        out.append(entry)
    return out


def _find_child_count(cur, table: str, column: str, value: str) -> int:
    cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = %s", (value,))
    return cur.fetchone()["n"]


# ── tree ────────────────────────────────────────────────────────────────────


@campus_v1_bp.route("/api/v1/campus/tree", methods=["GET"])
@require_auth
@require_permission("VIEW_ASSET")
def campus_tree():
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT college_id, college_name FROM colleges ORDER BY college_name")
            colleges = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT block_id, college_id, block_name, layout, photos, created_at, updated_at"
                " FROM blocks ORDER BY block_name"
            )
            blocks = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT floor_id, block_id, floor_name, floor_number FROM floors ORDER BY floor_number NULLS LAST, floor_name"
            )
            floors = [dict(r) for r in cur.fetchall()]
            cur.execute(
                "SELECT room_id, floor_id, department_id, room_name, room_type FROM rooms ORDER BY room_name"
            )
            rooms = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT department_id, department_name FROM departments ORDER BY department_name")
            departments = [dict(r) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("campus tree failed request_id=%s", getattr(g, "request_id", "-"))
        return error("CAMPUS_TREE_FAILED", "Unable to load the campus tree.", 500)

    rooms_by_floor: dict[str, list] = {}
    for room in rooms:
        rooms_by_floor.setdefault(room["floor_id"], []).append(room)
    floors_by_block: dict[str, list] = {}
    for floor in floors:
        floor["rooms"] = rooms_by_floor.pop(floor["floor_id"], [])
        floors_by_block.setdefault(floor["block_id"], []).append(floor)
    blocks_by_college: dict[str, list] = {}
    for block in blocks:
        block["floors"] = floors_by_block.pop(block["block_id"], [])
        # JSONB comes back already parsed by psycopg2; be defensive anyway.
        if isinstance(block.get("layout"), str):
            try:
                block["layout"] = json.loads(block["layout"])
            except ValueError:
                block["layout"] = None
        block["photos"] = _serialize_photos(block.get("photos"))
        blocks_by_college.setdefault(block.get("college_id") or "", []).append(block)
    for college in colleges:
        college["blocks"] = blocks_by_college.pop(college["college_id"], [])
    # Orphaned blocks (no college) still surface so nothing disappears.
    orphans = [b for group in blocks_by_college.values() for b in group]

    return success(
        {
            "colleges": colleges,
            "orphan_blocks": orphans,
            "departments": departments,
        }
    )


# ── generic CRUD helpers ────────────────────────────────────────────────────


def _derived_id(prefix: str, value: str) -> str:
    return f"{prefix}-{re.sub(r'[^A-Z0-9]+', '-', value.upper()).strip('-')}"


def _create_row(cur, table: str, id_column: str, explicit_id: str | None, values: dict, derived_id: str):
    row_id = explicit_id or derived_id
    payload = {id_column: row_id, **values}
    columns = ", ".join(payload)
    placeholders = ", ".join(["%s"] * len(payload))
    cur.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) RETURNING *",
        tuple(payload.values()),
    )
    return dict(cur.fetchone())


def _patch_row(cur, table: str, id_column: str, row_id: str, fields: dict):
    cur.execute(f"SELECT * FROM {table} WHERE {id_column} = %s", (row_id,))
    before = cur.fetchone()
    if before is None:
        return None, None
    sets = ", ".join(f"{k} = %s" for k in fields)
    cur.execute(
        f"UPDATE {table} SET {sets}, updated_at = NOW() WHERE {id_column} = %s RETURNING *",
        tuple(fields.values()) + (row_id,),
    )
    return dict(before), dict(cur.fetchone())


def _delete_guarded(cur, table: str, id_column: str, row_id: str, children: list[tuple[str, str]]):
    """Delete with the plan's 409 guards: refuse while children exist.
    children: [(table, foreign_key_column), ...] checked in order."""
    cur.execute(f"SELECT 1 FROM {table} WHERE {id_column} = %s", (row_id,))
    if cur.fetchone() is None:
        return "NOT_FOUND", None
    for child_table, fk in children:
        n = _find_child_count(cur, child_table, fk, row_id)
        if n:
            return "HAS_CHILDREN", {"child": child_table, "count": n}
    cur.execute(f"DELETE FROM {table} WHERE {id_column} = %s", (row_id,))
    return "DELETED", None


@campus_v1_bp.route("/api/v1/campus/blocks", methods=["POST"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def create_block():
    try:
        payload = BlockCreate(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid block payload.", 400, exc.errors())
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # Validate the parent like create_floor/create_room do: without
            # this an unknown college_id hit blocks_college_id_fkey and came
            # back as a 500 (Phase 8 finding) instead of a 400.
            if payload.college_id:
                cur.execute("SELECT 1 FROM colleges WHERE college_id = %s", (payload.college_id,))
                if cur.fetchone() is None:
                    cur.close()
                    return error("INVALID_COLLEGE", f"college_id '{payload.college_id}' does not exist.", 400)
            row = _create_row(
                cur,
                "blocks",
                "block_id",
                payload.block_id,
                {
                    k: v
                    for k, v in {
                        "block_name": payload.block_name,
                        "college_id": payload.college_id,
                    }.items()
                    if v is not None
                },
                _derived_id("BLOCK", payload.block_name),
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except pg_errors.UniqueViolation:
        logger.info("duplicate block id request_id=%s", getattr(g, "request_id", "-"))
        return error("DUPLICATE_ID", "A block with this ID or name already exists.", 409)
    except Exception:
        logger.exception("create block failed request_id=%s", getattr(g, "request_id", "-"))
        return error("BLOCK_CREATE_FAILED", "Unable to create the block.", 500)
    audit("CREATE_BLOCK", "block", row["block_id"], None, payload.model_dump(mode="json"))
    return success(row, 201)


@campus_v1_bp.route("/api/v1/campus/blocks/<block_id>", methods=["PATCH"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def update_block(block_id):
    try:
        payload = BlockPatch(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid block payload.", 400, exc.errors())
    fields = {k: v for k, v in payload.model_dump(mode="json").items() if v is not None}
    if not fields:
        return error("NO_FIELDS", "No fields provided for update.", 400)
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if "college_id" in fields:
                cur.execute("SELECT 1 FROM colleges WHERE college_id = %s", (fields["college_id"],))
                if cur.fetchone() is None:
                    cur.close()
                    return error("INVALID_COLLEGE", "college_id does not reference an existing college.", 400)
            before, after = _patch_row(cur, "blocks", "block_id", block_id, fields)
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("update block failed request_id=%s", getattr(g, "request_id", "-"))
        return error("BLOCK_UPDATE_FAILED", "Unable to update the block.", 500)
    if after is None:
        return error("BLOCK_NOT_FOUND", "Block not found.", 404)
    audit("UPDATE_BLOCK", "block", block_id, before, fields)
    return success(after)


@campus_v1_bp.route("/api/v1/campus/blocks/<block_id>", methods=["DELETE"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def delete_block(block_id):
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            status, detail = _delete_guarded(cur, "blocks", "block_id", block_id, [("floors", "block_id")])
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("delete block failed request_id=%s", getattr(g, "request_id", "-"))
        return error("BLOCK_DELETE_FAILED", "Unable to delete the block.", 500)
    if status == "NOT_FOUND":
        return error("BLOCK_NOT_FOUND", "Block not found.", 404)
    if status == "HAS_CHILDREN":
        return error(
            "BLOCK_HAS_FLOORS",
            f"Cannot delete: the block still contains {detail['count']} floor(s).",
            409,
            detail,
        )
    audit("DELETE_BLOCK", "block", block_id, None, None)
    return success({"block_id": block_id, "deleted": True})


# ── floors ──────────────────────────────────────────────────────────────────


@campus_v1_bp.route("/api/v1/campus/floors", methods=["POST"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def create_floor():
    try:
        payload = FloorCreate(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid floor payload.", 400, exc.errors())
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT 1 FROM blocks WHERE block_id = %s", (payload.block_id,))
            if cur.fetchone() is None:
                cur.close()
                return error("INVALID_BLOCK", f"block_id '{payload.block_id}' does not exist.", 400)
            values = {
                "block_id": payload.block_id,
                "floor_name": payload.floor_name,
            }
            if payload.floor_number is not None:
                values["floor_number"] = payload.floor_number
            row = _create_row(
                cur,
                "floors",
                "floor_id",
                payload.floor_id,
                values,
                f"{payload.block_id}-{_derived_id('FLR', payload.floor_name)}",
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except pg_errors.UniqueViolation:
        logger.info("duplicate floor id request_id=%s", getattr(g, "request_id", "-"))
        return error("DUPLICATE_ID", "A floor with this ID or name already exists.", 409)
    except Exception:
        logger.exception("create floor failed request_id=%s", getattr(g, "request_id", "-"))
        return error("FLOOR_CREATE_FAILED", "Unable to create the floor.", 500)
    audit("CREATE_FLOOR", "floor", row["floor_id"], None, payload.model_dump(mode="json"))
    return success(row, 201)


@campus_v1_bp.route("/api/v1/campus/floors/<floor_id>", methods=["PATCH"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def update_floor(floor_id):
    try:
        payload = FloorPatch(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid floor payload.", 400, exc.errors())
    fields = {k: v for k, v in payload.model_dump(mode="json").items() if v is not None}
    if not fields:
        return error("NO_FIELDS", "No fields provided for update.", 400)
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if "block_id" in fields:
                cur.execute("SELECT 1 FROM blocks WHERE block_id = %s", (fields["block_id"],))
                if cur.fetchone() is None:
                    cur.close()
                    return error("INVALID_BLOCK", "block_id does not reference an existing block.", 400)
            before, after = _patch_row(cur, "floors", "floor_id", floor_id, fields)
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("update floor failed request_id=%s", getattr(g, "request_id", "-"))
        return error("FLOOR_UPDATE_FAILED", "Unable to update the floor.", 500)
    if after is None:
        return error("FLOOR_NOT_FOUND", "Floor not found.", 404)
    audit("UPDATE_FLOOR", "floor", floor_id, before, fields)
    return success(after)


@campus_v1_bp.route("/api/v1/campus/floors/<floor_id>", methods=["DELETE"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def delete_floor(floor_id):
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            status, detail = _delete_guarded(cur, "floors", "floor_id", floor_id, [("rooms", "floor_id")])
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("delete floor failed request_id=%s", getattr(g, "request_id", "-"))
        return error("FLOOR_DELETE_FAILED", "Unable to delete the floor.", 500)
    if status == "NOT_FOUND":
        return error("FLOOR_NOT_FOUND", "Floor not found.", 404)
    if status == "HAS_CHILDREN":
        return error(
            "FLOOR_HAS_ROOMS",
            f"Cannot delete: the floor still contains {detail['count']} room(s).",
            409,
            detail,
        )
    audit("DELETE_FLOOR", "floor", floor_id, None, None)
    return success({"floor_id": floor_id, "deleted": True})


# ── rooms ───────────────────────────────────────────────────────────────────


@campus_v1_bp.route("/api/v1/campus/rooms", methods=["POST"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def create_room():
    try:
        payload = RoomCreate(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid room payload.", 400, exc.errors())
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT 1 FROM floors WHERE floor_id = %s", (payload.floor_id,))
            if cur.fetchone() is None:
                cur.close()
                return error("INVALID_FLOOR", f"floor_id '{payload.floor_id}' does not exist.", 400)
            if payload.department_id:
                cur.execute("SELECT 1 FROM departments WHERE department_id = %s", (payload.department_id,))
                if cur.fetchone() is None:
                    cur.close()
                    return error(
                        "INVALID_DEPARTMENT",
                        f"department_id '{payload.department_id}' does not exist.",
                        400,
                    )
            values = {"floor_id": payload.floor_id, "room_name": payload.room_name}
            if payload.department_id:
                values["department_id"] = payload.department_id
            if payload.room_type:
                values["room_type"] = payload.room_type
            row = _create_row(
                cur,
                "rooms",
                "room_id",
                payload.room_id,
                values,
                f"{payload.floor_id}-{_derived_id('RM', payload.room_name)}",
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except pg_errors.UniqueViolation:
        logger.info("duplicate room id request_id=%s", getattr(g, "request_id", "-"))
        return error("DUPLICATE_ID", "A room with this ID or name already exists on this floor.", 409)
    except Exception:
        logger.exception("create room failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ROOM_CREATE_FAILED", "Unable to create the room.", 500)
    audit("CREATE_ROOM", "room", row["room_id"], None, payload.model_dump(mode="json"))
    return success(row, 201)


@campus_v1_bp.route("/api/v1/campus/rooms/<room_id>", methods=["PATCH"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def update_room(room_id):
    try:
        payload = RoomPatch(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid room payload.", 400, exc.errors())
    fields = {k: v for k, v in payload.model_dump(mode="json").items() if v is not None}
    if not fields:
        return error("NO_FIELDS", "No fields provided for update.", 400)
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if "floor_id" in fields:
                cur.execute("SELECT 1 FROM floors WHERE floor_id = %s", (fields["floor_id"],))
                if cur.fetchone() is None:
                    cur.close()
                    return error("INVALID_FLOOR", "floor_id does not reference an existing floor.", 400)
            if "department_id" in fields and fields["department_id"]:
                cur.execute("SELECT 1 FROM departments WHERE department_id = %s", (fields["department_id"],))
                if cur.fetchone() is None:
                    cur.close()
                    return error(
                        "INVALID_DEPARTMENT",
                        "department_id does not reference an existing department.",
                        400,
                    )
            before, after = _patch_row(cur, "rooms", "room_id", room_id, fields)
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("update room failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ROOM_UPDATE_FAILED", "Unable to update the room.", 500)
    if after is None:
        return error("ROOM_NOT_FOUND", "Room not found.", 404)
    audit("UPDATE_ROOM", "room", room_id, before, fields)
    return success(after)


@campus_v1_bp.route("/api/v1/campus/rooms/<room_id>", methods=["DELETE"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def delete_room(room_id):
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            status, detail = _delete_guarded(cur, "rooms", "room_id", room_id, [("acs", "room_id")])
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("delete room failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ROOM_DELETE_FAILED", "Unable to delete the room.", 500)
    if status == "NOT_FOUND":
        return error("ROOM_NOT_FOUND", "Room not found.", 404)
    if status == "HAS_CHILDREN":
        return error(
            "ROOM_HAS_ASSETS",
            f"Cannot delete: the room still contains {detail['count']} AC asset(s).",
            409,
            detail,
        )
    audit("DELETE_ROOM", "room", room_id, None, None)
    return success({"room_id": room_id, "deleted": True})


# ── departments ─────────────────────────────────────────────────────────────


@campus_v1_bp.route("/api/v1/campus/departments", methods=["POST"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def create_department():
    try:
        payload = DepartmentCreate(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid department payload.", 400, exc.errors())
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            row = _create_row(
                cur,
                "departments",
                "department_id",
                payload.department_id,
                {"department_name": payload.department_name},
                _derived_id("DEPT", payload.department_name),
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except pg_errors.UniqueViolation:
        logger.info("duplicate department id request_id=%s", getattr(g, "request_id", "-"))
        return error("DUPLICATE_ID", "A department with this ID or name already exists.", 409)
    except Exception:
        logger.exception("create department failed request_id=%s", getattr(g, "request_id", "-"))
        return error("DEPARTMENT_CREATE_FAILED", "Unable to create the department.", 500)
    audit("CREATE_DEPARTMENT", "department", row["department_id"], None, payload.model_dump(mode="json"))
    return success(row, 201)


@campus_v1_bp.route("/api/v1/campus/departments/<department_id>", methods=["PATCH"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def update_department(department_id):
    try:
        payload = DepartmentPatch(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid department payload.", 400, exc.errors())
    fields = {"department_name": payload.department_name}
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            before, after = _patch_row(cur, "departments", "department_id", department_id, fields)
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("update department failed request_id=%s", getattr(g, "request_id", "-"))
        return error("DEPARTMENT_UPDATE_FAILED", "Unable to update the department.", 500)
    if after is None:
        return error("DEPARTMENT_NOT_FOUND", "Department not found.", 404)
    audit("UPDATE_DEPARTMENT", "department", department_id, before, fields)
    return success(after)


@campus_v1_bp.route("/api/v1/campus/departments/<department_id>", methods=["DELETE"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def delete_department(department_id):
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            status, detail = _delete_guarded(
                cur, "departments", "department_id", department_id, [("rooms", "department_id")]
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("delete department failed request_id=%s", getattr(g, "request_id", "-"))
        return error("DEPARTMENT_DELETE_FAILED", "Unable to delete the department.", 500)
    if status == "NOT_FOUND":
        return error("DEPARTMENT_NOT_FOUND", "Department not found.", 404)
    if status == "HAS_CHILDREN":
        return error(
            "DEPARTMENT_HAS_ROOMS",
            f"Cannot delete: {detail['count']} room(s) still reference this department.",
            409,
            detail,
        )
    audit("DELETE_DEPARTMENT", "department", department_id, None, None)
    return success({"department_id": department_id, "deleted": True})


# ── location review / aliases / re-resolve (permanent location model) ────────


# Preserved source columns per hierarchy level (the room level is carried by
# room_number, exactly like resolver._LEVEL_SOURCE). Levels that also carry
# an explicit source id (migration 016) list both: a row needs review when
# it has ANY unmatched source for the level, name or id.
_LEVEL_SOURCE_COLS = {
    "college": ("source_college_value",),
    "block": ("source_block_value", "source_block_id_value"),
    "floor": ("source_floor_value", "source_floor_id_value"),
    "department": ("source_department_value",),
    "room": ("source_room_number_value", "source_room_id_value"),
}

# Canonical master table per level, for alias-target validation.
_LEVEL_TARGET_TABLE = {
    "college": "colleges",
    "block": "blocks",
    "floor": "floors",
    "department": "departments",
    "room": "rooms",
}


def _review_conditions(level: str | None = None) -> str:
    """SQL: a row needs review when a level HAS preserved source text but no
    confirmed id — UNRESOLVED or AMBIGUOUS, exactly the rows an alias, a
    manual confirmation or a re-resolve can finish (§13). Source and status
    always agree through the resolver's invariant, so the id check is the
    canonical form and works even for rows written before status columns
    existed."""
    levels = (level,) if level else location_resolver.LEVELS

    def _level_clause(lvl: str) -> str:
        present = " OR ".join(f"al.{col} IS NOT NULL" for col in _LEVEL_SOURCE_COLS[lvl])
        return f"(al.{lvl}_id IS NULL AND ({present}))"

    return "(" + " OR ".join(_level_clause(lvl) for lvl in levels) + ")"


@campus_v1_bp.route("/api/v1/campus/location/review", methods=["GET"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def location_review():
    """Assets whose source text still has no confirmed id (§13).

    Powers the Location review section on the campus page: one row per asset
    with the same nested ``location`` payload as GET /assets/<ref> (confirmed
    ids, preserved source, per-field statuses), so an admin sees exactly what
    the import said and what the resolver could not match. Optional
    ``?level=`` narrows to one hierarchy level; pagination mirrors the assets
    list.
    """
    level = request.args.get("level") or None
    if level is not None and level not in location_resolver.LEVELS:
        return error("INVALID_LEVEL", "level must be one of: college, block, floor, department, room.", 400)
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = min(200, max(1, int(request.args.get("page_size", 50))))
    except ValueError:
        return error("INVALID_PAGINATION", "page and page_size must be integers.", 400)

    # Same read shape as the assets list (import inside the view, the pattern
    # system.py uses for the assets helpers).
    from app.api.v1.assets import BASE_SELECT, _serialize

    where_sql = f"WHERE {_review_conditions(level)} AND COALESCE(a.is_active, TRUE) = TRUE"
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(f"SELECT COUNT(*) AS total {location_sql.ASSET_JOINS} {where_sql}")
            total = cur.fetchone()["total"]
            offset = (page - 1) * page_size
            cur.execute(
                f"SELECT {BASE_SELECT} {location_sql.ASSET_JOINS} {where_sql}"
                " ORDER BY a.ac_id LIMIT %s OFFSET %s",
                (page_size, offset),
            )
            items = [_serialize(r) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("location review failed request_id=%s", getattr(g, "request_id", "-"))
        return error("LOCATION_REVIEW_FAILED", "Unable to load the location review queue.", 500)
    return paginated(items, page, page_size, total)


@campus_v1_bp.route("/api/v1/campus/location/aliases", methods=["GET"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def list_location_aliases():
    """List location aliases (§6.1). Filters: ``?entity_type=``, ``?q=``
    (substring on the alias text), ``?include_inactive=true``."""
    entity_type = request.args.get("entity_type") or None
    if entity_type is not None and entity_type not in location_resolver.LEVELS:
        return error(
            "INVALID_LEVEL", "entity_type must be one of: college, block, floor, department, room.", 400
        )
    needle = (request.args.get("q") or "").strip()
    include_inactive = (request.args.get("include_inactive") or "").lower() in ("1", "true", "yes")
    where: list[str] = []
    params: list = []
    if entity_type is not None:
        where.append("entity_type = %s")
        params.append(entity_type)
    if needle:
        where.append("alias ILIKE %s")
        params.append(f"%{needle}%")
    if not include_inactive:
        where.append("is_active")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT id, entity_type, entity_id, alias, normalized_alias, source,"
                f" is_active, created_at, updated_at FROM location_aliases {where_sql}"
                " ORDER BY entity_type, alias LIMIT 500",
                tuple(params),
            )
            rows = [dict(r) for r in cur.fetchall()]
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("list aliases failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ALIAS_LIST_FAILED", "Unable to list location aliases.", 500)
    return success({"aliases": rows})


@campus_v1_bp.route("/api/v1/campus/location/aliases", methods=["POST"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def create_location_alias():
    """Create (or reactivate) one alias: text variant -> canonical entity.

    201 for a new row, 200 when an inactive alias was reactivated or the
    identical alias already pointed at the same entity (idempotent). An
    ACTIVE alias pointing at a different entity is a 409 — an alias is never
    silently repointed, because that would move every future resolution.
    """
    try:
        payload = LocationAliasCreate(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid alias payload.", 400, exc.errors())
    normalized = location_resolver.normalize(payload.alias)
    if not normalized:
        return error("VALIDATION_ERROR", "alias must contain at least one letter or digit.", 400)
    table = _LEVEL_TARGET_TABLE[payload.entity_type]
    id_col = f"{payload.entity_type}_id"
    created = False
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(f"SELECT 1 FROM {table} WHERE {id_col} = %s", (payload.entity_id,))
            if cur.fetchone() is None:
                cur.close()
                return error(
                    "INVALID_LOCATION_TARGET",
                    f"{payload.entity_type} '{payload.entity_id}' does not exist.",
                    400,
                )
            cur.execute(
                "SELECT * FROM location_aliases WHERE entity_type = %s AND normalized_alias = %s",
                (payload.entity_type, normalized),
            )
            existing = cur.fetchone()
            if existing is not None and existing["is_active"] and existing["entity_id"] != payload.entity_id:
                cur.close()
                return error(
                    "ALIAS_EXISTS",
                    "This alias already points at a different entity.",
                    409,
                    {"entity_id": existing["entity_id"]},
                )
            if existing is None:
                cur.execute(
                    "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias)"
                    " VALUES (%s, %s, %s, %s) RETURNING *",
                    (payload.entity_type, payload.entity_id, payload.alias, normalized),
                )
                row = dict(cur.fetchone())
                created = True
            else:
                # Reactivate an inactive alias (or refresh the identical one).
                cur.execute(
                    "UPDATE location_aliases SET entity_id = %s, alias = %s, is_active = TRUE,"
                    " updated_at = NOW() WHERE id = %s RETURNING *",
                    (payload.entity_id, payload.alias, existing["id"]),
                )
                row = dict(cur.fetchone())
            # Make the next step obvious (§11): count review rows at this
            # level whose unmatched source normalizes EXACTLY to the new
            # alias — those are the rows a re-resolve will finish. This is a
            # count only; nothing is re-resolved implicitly.
            cols = _LEVEL_SOURCE_COLS[payload.entity_type]
            cur.execute(
                f"SELECT DISTINCT {', '.join(f'al.{col}' for col in cols)}"
                " FROM asset_locations al JOIN acs a ON a.ac_id = al.ac_id"
                f" WHERE {_review_conditions(payload.entity_type)}"
                " AND COALESCE(a.is_active, TRUE) = TRUE"
            )
            matching = 0
            for record in cur.fetchall():
                for col in cols:
                    value = record[col]
                    if value is not None and location_resolver.normalize(value) == normalized:
                        matching += 1
                        break
            row["matching_review_rows"] = matching
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except pg_errors.UniqueViolation:
        # Concurrent create of the same alias — same outcome as the 409 above.
        return error("ALIAS_EXISTS", "This alias already exists.", 409)
    except Exception:
        logger.exception("create alias failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ALIAS_CREATE_FAILED", "Unable to create the alias.", 500)
    audit(
        "CREATE_LOCATION_ALIAS",
        "location_alias",
        str(row["id"]),
        None,
        {"entity_type": payload.entity_type, "entity_id": payload.entity_id, "alias": payload.alias},
    )
    return success(row, 201 if created else 200)


@campus_v1_bp.route("/api/v1/campus/location/aliases/<alias_id>", methods=["DELETE"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def delete_location_alias(alias_id):
    """Deactivate an alias (soft delete): the resolver's directory only loads
    ``is_active`` rows, so it stops resolving immediately while the row and
    its history stay for the audit trail.

    The path segment stays a plain string so auth/permission always run
    first (a numeric converter would 404 the RBAC probes before
    authentication); non-numeric ids answer 404 here.
    """
    try:
        alias_pk = int(alias_id)
    except (TypeError, ValueError):
        return error("ALIAS_NOT_FOUND", "Alias not found.", 404)
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "SELECT id, entity_type, entity_id, alias, normalized_alias FROM location_aliases WHERE id = %s",
                (alias_pk,),
            )
            before = cur.fetchone()
            if before is None:
                cur.close()
                return error("ALIAS_NOT_FOUND", "Alias not found.", 404)
            cur.execute(
                "UPDATE location_aliases SET is_active = FALSE, updated_at = NOW() WHERE id = %s", (alias_pk,)
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("delete alias failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ALIAS_DELETE_FAILED", "Unable to delete the alias.", 500)
    audit("DEACTIVATE_LOCATION_ALIAS", "location_alias", str(alias_pk), dict(before), None)
    return success({"id": alias_pk, "is_active": False, "deleted": True})


@campus_v1_bp.route("/api/v1/campus/location/re-resolve", methods=["POST"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def re_resolve_locations():
    """Re-run the centralized resolver over stored source values (§12).

    Body: ``{"limit": 1..2000}`` (default 500). Batch-limited and idempotent:
    only rows whose ids/statuses actually change are written, so a second run
    reports ``updated: 0`` unless an alias or master-data change gave the
    resolver something new. Manual confirmations are passed through and never
    touched; each row keeps its own source_ref provenance while resolved_by
    records this actor. Write order is the 014 contract: asset_locations
    first, then the acs mirror.
    """
    body = request.get_json(silent=True) or {}
    limit = body.get("limit", 500)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 2000:
        return error("VALIDATION_ERROR", "limit must be an integer between 1 and 2000.", 400)
    user_id = (getattr(g, "current_user", None) or {}).get("id") or "api"

    id_cols = ", ".join(f"al.{level}_id" for level in location_resolver.LEVELS)
    status_cols = ", ".join(f"al.{level}_status" for level in location_resolver.LEVELS)
    source_cols = ", ".join(f"al.{col}" for col in location_resolver.SOURCE_FIELDS.values())
    scanned = 0
    updated = 0
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            campus = location_resolver.load_directory(cur)
            cur.execute(
                "SELECT al.ac_id, al.source_ref, al.manual_fields,"
                f" {id_cols}, {status_cols}, {source_cols}"
                " FROM asset_locations al JOIN acs a ON a.ac_id = al.ac_id"
                f" WHERE COALESCE(a.is_active, TRUE) = TRUE AND {_review_conditions(None)}"
                " ORDER BY al.ac_id LIMIT %s",
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            scanned = len(rows)
            pending = []
            for row in rows:
                ids = {level: row[f"{level}_id"] for level in location_resolver.LEVELS}
                source = {key: row[col] for key, col in location_resolver.SOURCE_FIELDS.items()}
                res = location_resolver.resolve(
                    source,
                    campus,
                    previous_ids=ids,
                    previous_manual=frozenset(row["manual_fields"] or ()),
                )
                changed = any(
                    res.ids[level] != ids[level] or res.statuses[level] != row[f"{level}_status"]
                    for level in location_resolver.LEVELS
                )
                if changed:
                    pending.append((row["ac_id"], res, row["source_ref"]))
            updated = len(pending)
            if pending:
                # Preserve each row's source provenance; record who re-resolved.
                by_ref: dict[str, list] = {}
                for ac_id, res, ref in pending:
                    by_ref.setdefault(ref or "unknown", []).append((ac_id, res))
                for ref, part in by_ref.items():
                    location_resolver.apply_many(cur, part, source_ref=ref, resolved_by=user_id)
                location_resolver.sync_acs(cur, [(ac_id, res) for ac_id, res, _ in pending])
                conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception:
        logger.exception("location re-resolve failed request_id=%s", getattr(g, "request_id", "-"))
        return error("LOCATION_RE_RESOLVE_FAILED", "Unable to re-resolve locations.", 500)
    audit("RE_RESOLVE_LOCATIONS", "location", None, None, {"scanned": scanned, "updated": updated})
    return success({"scanned": scanned, "updated": updated})


# ── block layout ────────────────────────────────────────────────────────────


def _validate_layout(value) -> str | None:
    """JSON-schema-lite validation: object at top level, bounded depth,
    JSON-serialisable primitives only, size-capped."""
    if not isinstance(value, dict):
        return "layout must be a JSON object"
    encoded = json.dumps(value)
    if len(encoded.encode("utf-8")) > LAYOUT_MAX_BYTES:
        return f"layout exceeds the {LAYOUT_MAX_BYTES // 1024} KB size limit"

    def walk(node, depth):
        if depth > LAYOUT_MAX_DEPTH:
            return f"layout nesting exceeds {LAYOUT_MAX_DEPTH} levels"
        if isinstance(node, dict):
            for key, child in node.items():
                if not isinstance(key, str):
                    return "layout keys must be strings"
                problem = walk(child, depth + 1)
                if problem:
                    return problem
        elif isinstance(node, list):
            for child in node:
                problem = walk(child, depth + 1)
                if problem:
                    return problem
        elif not isinstance(node, (str, int, float, bool, type(None))):
            return f"unsupported value type in layout: {type(node).__name__}"
        return None

    return walk(value, 0)


@campus_v1_bp.route("/api/v1/campus/blocks/<block_id>/layout", methods=["PATCH"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def update_block_layout(block_id):
    body = request.get_json(silent=True)
    layout = body.get("layout") if isinstance(body, dict) else body
    problem = _validate_layout(layout)
    if problem:
        return error("INVALID_LAYOUT", problem, 400)
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute(
                "UPDATE blocks SET layout = %s, updated_at = NOW() WHERE block_id = %s RETURNING block_id",
                (json.dumps(layout), block_id),
            )
            row = cur.fetchone()
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("update block layout failed request_id=%s", getattr(g, "request_id", "-"))
        return error("LAYOUT_UPDATE_FAILED", "Unable to save the layout.", 500)
    if row is None:
        return error("BLOCK_NOT_FOUND", "Block not found.", 404)
    audit("UPDATE_BLOCK_LAYOUT", "block", block_id, None, {"bytes": len(json.dumps(layout))})
    return success({"block_id": block_id, "layout": layout})


# ── block photos (private storage; signed URLs) ─────────────────────────────


@campus_v1_bp.route("/api/v1/campus/blocks/<block_id>/photos", methods=["POST"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def upload_block_photo(block_id):
    if not _storage_configured():
        return error(
            "PHOTO_STORAGE_NOT_CONFIGURED",
            "Photo storage is not configured on the server (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY).",
            503,
        )
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return error("FILE_REQUIRED", "An image file is required.", 400)
    filename = (upload.filename or "").lower()
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ""
    mime = PHOTO_EXTENSIONS.get(ext)
    if mime is None:
        return error("UNSUPPORTED_FILE_TYPE", "Only .png, .jpg, .jpeg and .webp images are supported.", 400)
    data = upload.read()
    if not data:
        return error("FILE_EMPTY", "The uploaded image is empty.", 400)
    if len(data) > PHOTO_MAX_BYTES:
        return error("FILE_TOO_LARGE", "Images must be 5 MB or smaller.", 413)
    if not data.startswith(PHOTO_MAGIC):
        return error("INVALID_FILE_CONTENT", "File content is not a supported image.", 400)

    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT photos FROM blocks WHERE block_id = %s", (block_id,))
            block_row = cur.fetchone()
            if block_row is None:
                cur.close()
                return error("BLOCK_NOT_FOUND", "Block not found.", 404)
            photos = list(block_row["photos"] or [])
            photo_id = str(uuid.uuid4())
            path = f"blocks/{block_id}/{photo_id}{ext}"
            bucket = os.getenv("BUILDING_PHOTOS_BUCKET", "building-photos")
            status, body = _storage_request(
                "POST",
                f"/object/{bucket}/{path}",
                body=data,
                content_type=mime,
                timeout=30,
            )
            if status not in (200, 201):
                logger.warning("photo upload failed status=%s body=%s", status, body[:300] if body else b"")
                cur.close()
                return error("PHOTO_UPLOAD_FAILED", "The photo could not be stored.", 502)
            entry = {
                "id": photo_id,
                "path": path,
                "name": upload.filename,
                "is_cover": not photos,
            }
            photos.append(entry)
            cur.execute(
                "UPDATE blocks SET photos = %s, updated_at = NOW() WHERE block_id = %s RETURNING block_id",
                (json.dumps(photos), block_id),
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("photo upload failed request_id=%s", getattr(g, "request_id", "-"))
        return error("PHOTO_UPLOAD_FAILED", "The photo could not be stored.", 500)

    audit("ADD_BLOCK_PHOTO", "block", block_id, None, {"photo_id": photo_id})
    entry["signed_url"] = _sign_read_url(path)
    return success(entry, 201)


@campus_v1_bp.route("/api/v1/campus/blocks/<block_id>/photos/<photo_id>", methods=["DELETE"])
@require_auth
@require_permission("MANAGE_CAMPUS")
def delete_block_photo(block_id, photo_id):
    removed_entry: dict | None = None
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT photos FROM blocks WHERE block_id = %s", (block_id,))
            block_row = cur.fetchone()
            if block_row is None:
                cur.close()
                return error("BLOCK_NOT_FOUND", "Block not found.", 404)
            photos = list(block_row["photos"] or [])
            removed_entry = next((p for p in photos if p.get("id") == photo_id), None)
            if removed_entry is None:
                cur.close()
                return error("PHOTO_NOT_FOUND", "Photo not found.", 404)
            remaining = [p for p in photos if p.get("id") != photo_id]
            cur.execute(
                "UPDATE blocks SET photos = %s, updated_at = NOW() WHERE block_id = %s",
                (json.dumps(remaining), block_id),
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("photo delete failed request_id=%s", getattr(g, "request_id", "-"))
        return error("PHOTO_DELETE_FAILED", "The photo could not be removed.", 500)

    # Best-effort removal from object storage (metadata row is already gone).
    if _storage_configured() and removed_entry.get("path"):
        bucket = os.getenv("BUILDING_PHOTOS_BUCKET", "building-photos")
        try:
            status, _ = _storage_request("DELETE", f"/object/{bucket}/{removed_entry['path']}")
            if status not in (200, 204, 404):
                logger.warning("photo storage cleanup failed status=%s", status)
        except (URLError, OSError):
            logger.warning("photo storage cleanup failed", exc_info=True)

    audit("DELETE_BLOCK_PHOTO", "block", block_id, {"photo_id": photo_id}, None)
    return success({"block_id": block_id, "photo_id": photo_id, "deleted": True})
