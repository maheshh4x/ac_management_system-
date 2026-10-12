"""Asset writes via /api/v1 with Pydantic validation + audit (Plan #21-#23)."""

from __future__ import annotations

import logging

from flask import Blueprint, g, request
from psycopg2 import errors as pg_errors
from psycopg2.extras import RealDictCursor
from pydantic import ValidationError

from app.api.v1.assets import BASE_SELECT, _serialize
from app.auth.jwt import require_auth
from app.auth.permissions import require_permission
from app.config.database import get_db_connection
from app.core import error, success
from app.schemas.v1_schemas import AssetCreate, AssetLocationPatch, AssetUpdate
from app.services import location_resolver, location_sql
from app.services.audit_service import audit
from app.services.status_map import derive_operational_status

assets_write_v1_bp = Blueprint("assets_write_v1", __name__)

logger = logging.getLogger(__name__)


def _duplicate_serial_conflict(cur, serial: str, exclude_ac_id: str | None = None):
    """Return the conflicting asset_code when ``serial`` already exists
    (Phase 3 task 2: duplicate non-empty serials are a 409 with the code)."""
    if not serial:
        return None
    cur.execute(
        "SELECT asset_code, ac_id FROM acs WHERE ac_serial_number = %s AND ac_id <> COALESCE(%s, '') LIMIT 1",
        (serial, exclude_ac_id),
    )
    row = cur.fetchone()
    return (row["asset_code"] or row["ac_id"]) if row else None


@assets_write_v1_bp.route("/api/v1/assets", methods=["POST"])
@require_auth
@require_permission("CREATE_ASSET")
def create_asset():
    try:
        payload = AssetCreate(**(request.get_json(silent=True) or {}))
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid asset payload.", 400, exc.errors())
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            if payload.room_id:
                cur.execute("SELECT 1 FROM rooms WHERE room_id = %s", (payload.room_id,))
                if cur.fetchone() is None:
                    cur.close()
                    return error("INVALID_ROOM", f"room_id '{payload.room_id}' does not exist.", 400)
            conflict_code = _duplicate_serial_conflict(cur, payload.ac_serial_number)
            if conflict_code:
                cur.close()
                return error(
                    "SERIAL_EXISTS",
                    f"Serial number '{payload.ac_serial_number}' is already used by asset '{conflict_code}'.",
                    409,
                    {"conflicting_asset_code": conflict_code},
                )
            cur.execute(
                """INSERT INTO acs (ac_id, room_id, ac_make_company, ac_model_number, ac_serial_number,
                                    ac_capacity_ton, ac_type, operational_status,
                                    installation_date, last_maintenance_date,
                                    manufacturing_year, installation_year,
                                    ac_star_rating, power_rating_kw, power_factor,
                                    next_service_date, technician, remarks,
                                    asset_code, lifecycle_status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE')
                   ON CONFLICT (ac_id) DO NOTHING RETURNING ac_id""",
                (
                    payload.ac_id,
                    payload.room_id,
                    payload.ac_make_company,
                    payload.ac_model_number,
                    payload.ac_serial_number,
                    str(payload.ac_capacity_ton) if payload.ac_capacity_ton is not None else None,
                    payload.ac_type,
                    derive_operational_status(payload.current_status),
                    payload.installation_date.isoformat() if payload.installation_date else None,
                    payload.last_maintenance_date.isoformat() if payload.last_maintenance_date else None,
                    payload.manufacturing_year,
                    payload.installation_year,
                    payload.ac_star_rating,
                    str(payload.power_rating_kw) if payload.power_rating_kw is not None else None,
                    str(payload.power_factor) if payload.power_factor is not None else None,
                    payload.next_service_date.isoformat() if payload.next_service_date else None,
                    payload.technician,
                    payload.remarks,
                    payload.ac_id,
                ),
            )
            row = cur.fetchone()
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("create asset failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ASSET_CREATE_FAILED", "Unable to create the asset.", 500)
    if not row:
        return error("ASSET_EXISTS", "An asset with this ac_id already exists.", 409)
    audit("CREATE_ASSET", "asset", payload.ac_id, None, payload.model_dump(mode="json"))
    return success({"ac_id": row["ac_id"]}, 201)


@assets_write_v1_bp.route("/api/v1/assets/<ac_id>", methods=["PATCH"])
@require_auth
@require_permission("EDIT_ASSET")
def update_asset(ac_id):
    raw = request.get_json(silent=True) or {}
    try:
        payload = AssetUpdate(**raw)
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid asset payload.", 400, exc.errors())
    # Explicit nulls clear the column; omitted keys stay untouched. (A bare
    # "drop every None" would make it impossible for the UI to clear a field.)
    provided = [k for k in AssetUpdate.model_fields if k in raw]
    fields = {k: v for k, v in payload.model_dump(mode="json").items() if k in provided}
    if not fields:
        return error("NO_FIELDS", "No fields provided for update.", 400)
    if "current_status" in fields:
        # current_status stays an accepted request key, but its column was
        # dropped (013): derive the constrained vocabulary, then remove it from
        # the SET map. Explicit null -> UNKNOWN, as before.
        fields["operational_status"] = derive_operational_status(fields.pop("current_status"))
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # Resolve asset_uuid / asset_code / ac_id — the FE uses asset_uuid
            # as its stable URL key (same triple as GET /api/v1/assets/<ref>).
            cur.execute(
                "SELECT * FROM acs WHERE asset_uuid::text = %s OR asset_code = %s OR ac_id = %s LIMIT 1",
                (ac_id, ac_id, ac_id),
            )
            before = cur.fetchone()
            if not before:
                cur.close()
                return error("ASSET_NOT_FOUND", "Asset not found.", 404)
            target_ac_id = before["ac_id"]
            if "room_id" in fields and fields["room_id"]:
                cur.execute("SELECT 1 FROM rooms WHERE room_id = %s", (fields["room_id"],))
                if cur.fetchone() is None:
                    cur.close()
                    return error("INVALID_ROOM", "room_id does not reference an existing room.", 400)
            if "ac_serial_number" in fields and fields["ac_serial_number"]:
                conflict_code = _duplicate_serial_conflict(
                    cur, fields["ac_serial_number"], exclude_ac_id=target_ac_id
                )
                if conflict_code:
                    cur.close()
                    return error(
                        "SERIAL_EXISTS",
                        f"Serial number '{fields['ac_serial_number']}' is already used by asset '{conflict_code}'.",
                        409,
                        {"conflicting_asset_code": conflict_code},
                    )
            # Lifecycle consistency (mirrors the acs_lifecycle_consistency CHECK
            # in migration 004): an active asset cannot jump to DECOMMISSIONED
            # via a bare field update — use the decommission endpoint.
            if fields.get("lifecycle_status") == "DECOMMISSIONED" and before.get("is_active", True):
                cur.close()
                return error(
                    "VALIDATION_ERROR",
                    "Cannot set lifecycle_status to DECOMMISSIONED on an active asset; use POST /api/v1/assets/<ac_id>/decommission.",
                    400,
                )
            sets = ", ".join(f"{k} = %s" for k in fields)
            cur.execute(
                f"UPDATE acs SET {sets}, updated_at = NOW() WHERE ac_id = %s",
                tuple(fields.values()) + (target_ac_id,),
            )
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("update asset failed request_id=%s", getattr(g, "request_id", "-"))
        return error("ASSET_UPDATE_FAILED", "Unable to update the asset.", 500)
    audit("UPDATE_ASSET", "asset", target_ac_id, None, fields)
    return success({"ac_id": target_ac_id})


@assets_write_v1_bp.route("/api/v1/assets/<ac_id>/decommission", methods=["POST"])
@require_auth
@require_permission("DECOMMISSION_ASSET")
def decommission_asset(ac_id):
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            # Same uuid/code/ac_id resolution as GET and PATCH.
            cur.execute(
                "UPDATE acs SET lifecycle_status='DECOMMISSIONED', is_active=FALSE,"
                " decommissioned_at=NOW(), updated_at=NOW()"
                " WHERE asset_uuid::text = %s OR asset_code = %s OR ac_id = %s",
                (ac_id, ac_id, ac_id),
            )
            if cur.rowcount == 0:
                cur.close()
                return error("ASSET_NOT_FOUND", "Asset not found.", 404)
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("decommission asset failed request_id=%s", getattr(g, "request_id", "-"))
        return error("DECOMMISSION_FAILED", "Unable to decommission the asset.", 500)
    audit("DECOMMISSION_ASSET", "asset", ac_id, {"is_active": True}, {"is_active": False})
    return success({"ac_id": ac_id, "lifecycle_status": "DECOMMISSIONED"})


# ── manual location confirmation (permanent location model, §10) ─────────────

# Canonical master table per provided id column (all PKs are <level>_id).
_LEVEL_ID_TABLES = {
    "college_id": ("colleges", "college_id"),
    "block_id": ("blocks", "block_id"),
    "floor_id": ("floors", "floor_id"),
    "department_id": ("departments", "department_id"),
    "room_id": ("rooms", "room_id"),
}


@assets_write_v1_bp.route("/api/v1/assets/<ac_id>/location", methods=["PATCH"])
@require_auth
@require_permission("EDIT_ASSET")
def patch_asset_location(ac_id):
    """Confirm location levels manually, one level at a time (§10).

    A provided id pins that level as manually confirmed — imports and
    re-resolves can never auto-change it afterwards; an explicit null
    un-pins and clears it so the stored source may re-resolve; an omitted key
    is untouched. The centralized resolver re-derives every other level from
    the stored source plus the confirmed chain (confirming a room fills its
    floor/block/college), the record is written first and the acs mirror
    follows — the migration 014 single-writer contract. The composite FKs
    are the chain authority: a contradiction between confirmed levels comes
    back 400, never 500.
    """
    raw = request.get_json(silent=True) or {}
    try:
        payload = AssetLocationPatch(**raw)
    except ValidationError as exc:
        return error("VALIDATION_ERROR", "Invalid location payload.", 400, exc.errors())
    provided = [key for key in _LEVEL_ID_TABLES if key in raw]
    if not provided:
        return error("NO_FIELDS", "No location levels provided for update.", 400)
    dumped = payload.model_dump(mode="json")
    fields = {key: dumped[key] for key in provided}
    note = dumped.get("note")
    user_id = (getattr(g, "current_user", None) or {}).get("id") or "api"
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor(cursor_factory=RealDictCursor)
            # Same uuid/code/ac_id resolution as GET and asset PATCH.
            cur.execute(
                "SELECT ac_id FROM acs WHERE asset_uuid::text = %s OR asset_code = %s OR ac_id = %s LIMIT 1",
                (ac_id, ac_id, ac_id),
            )
            row = cur.fetchone()
            if row is None:
                cur.close()
                return error("ASSET_NOT_FOUND", "Asset not found.", 404)
            target = row["ac_id"]
            # Every provided id must be a real canonical entity; hierarchy
            # consistency itself is enforced by the composite FKs below.
            for key, value in fields.items():
                if value is None:
                    continue
                table, column = _LEVEL_ID_TABLES[key]
                cur.execute(f"SELECT 1 FROM {table} WHERE {column} = %s", (value,))
                if cur.fetchone() is None:
                    cur.close()
                    return error(
                        "INVALID_LOCATION_LEVEL",
                        f"{key} '{value}' does not reference an existing {key[: -len('_id')]}.",
                        400,
                    )
            cur.execute("SELECT * FROM asset_locations WHERE ac_id = %s", (target,))
            current = cur.fetchone()
            ids = {level: current[f"{level}_id"] if current else None for level in location_resolver.LEVELS}
            manual = set(current["manual_fields"] or ()) if current else set()
            source = (
                {key: current[col] for key, col in location_resolver.SOURCE_FIELDS.items()}
                if current
                else {key: None for key in location_resolver.SOURCE_FIELDS}
            )
            # The source's provenance stays untouched; only resolved_by
            # changes hands (blank source columns are never rewritten).
            source_ref = (current["source_ref"] if current else None) or f"manual:{user_id}"
            before_state = {"ids": dict(ids), "manual_fields": sorted(manual)}
            for key, value in fields.items():
                level = key[: -len("_id")]
                ids[level] = value
                if value is None:
                    manual.discard(level)
                else:
                    manual.add(level)
            campus = location_resolver.load_directory(cur)
            res = location_resolver.resolve(source, campus, previous_ids=ids, previous_manual=manual)
            # Record the manual decision itself (the resolver only notes the
            # manual levels that contradicted a source value).
            for key, value in fields.items():
                level = key[: -len("_id")]
                res.notes.setdefault(
                    level, "manually confirmed via API" if value else "manually cleared via API"
                )
            if note:
                res.notes.setdefault("resolution_note", note)
            # Composite FKs (room,floor) (floor,block) (block,college): a
            # contradiction between confirmed levels must be a 400, so the
            # write runs inside a savepoint.
            cur.execute("SAVEPOINT location_patch")
            try:
                location_resolver.apply_many(cur, [(target, res)], source_ref=source_ref, resolved_by=user_id)
                # The generic upsert deliberately omits manual_fields so
                # re-imports can never touch pins — the manual path writes
                # them itself, before the acs mirror follows.
                cur.execute(
                    "UPDATE asset_locations SET manual_fields = %s, updated_at = NOW() WHERE ac_id = %s",
                    (sorted(manual), target),
                )
                location_resolver.sync_acs(cur, [(target, res)])
            except pg_errors.ForeignKeyViolation:
                cur.execute("ROLLBACK TO SAVEPOINT location_patch")
                cur.close()
                return error(
                    "INVALID_LOCATION_CHAIN",
                    "The confirmed levels contradict each other"
                    " (for example a floor that does not belong to the given block).",
                    400,
                )
            # Read back the exact same shape as GET /assets/<ref>.
            cur.execute(f"SELECT {BASE_SELECT} {location_sql.ASSET_JOINS} WHERE a.ac_id = %s", (target,))
            location = _serialize(dict(cur.fetchone()))["location"]
            cur.close()
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logger.exception("update asset location failed request_id=%s", getattr(g, "request_id", "-"))
        return error("LOCATION_UPDATE_FAILED", "Unable to update the asset location.", 500)
    audit(
        "UPDATE_ASSET_LOCATION",
        "asset",
        target,
        before_state,
        {"levels": fields, **({"note": note} if note else {})},
    )
    return success({"ac_id": target, "location": location})
