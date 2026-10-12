"""Standard API response envelopes + pagination (Plan #21, #28, #29)."""

from __future__ import annotations

from flask import g, jsonify


def success(data=None, status=200):
    payload = {"success": True, "data": data}
    rid = getattr(g, "request_id", None)
    if rid:
        payload["request_id"] = rid
    return jsonify(payload), status


def paginated(items, page: int, page_size: int, total: int, status=200, next_cursor: str | None = None):
    total_pages = max(1, -(-total // page_size)) if page_size else 1
    payload = {
        "success": True,
        "data": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
    }
    if next_cursor is not None:
        payload["pagination"]["next_cursor"] = next_cursor
    rid = getattr(g, "request_id", None)
    if rid:
        payload["request_id"] = rid
    return jsonify(payload), status


def _json_safe(value):
    """Reduce ``details`` to JSON primitives, lists, dicts, datetimes or UUIDs.

    Pydantic's ``ValidationError.errors()`` stores the original exception in
    ``ctx`` for a ``value_error`` (e.g. ``{"error": ValueError("role must be
    a known role")}``). Feeding that to ``jsonify`` raised ``TypeError`` *inside*
    ``error()``, so a bad ``role`` on ``POST /approvals/{id}/approve`` or
    ``PATCH /users/{id}`` returned 500 INTERNAL_ERROR instead of 400 — Phase 8
    finding. Anything unrecognised is rendered as its string form rather than
    allowed to escape as a crash.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def error(code: str, message: str, status=400, details=None):
    payload = {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "request_id": getattr(g, "request_id", None),
        },
    }
    if details is not None:
        payload["error"]["details"] = _json_safe(details)
    return jsonify(payload), status
