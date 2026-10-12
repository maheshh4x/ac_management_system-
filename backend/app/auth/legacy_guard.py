"""Centralized auth/permission lockdown for legacy ``/api/*`` routes.

Every non-v1 ``/api/*`` route must be covered by ``LEGACY_ROUTE_PERMISSIONS``
below (matched on path prefix + HTTP method) or explicitly allow-listed in
``PUBLIC_PATH_PREFIXES``. Any rule that matches neither fails closed with 403
and is logged — so adding a new legacy route without a mapping is an outage,
not a silent exposure. ``tests/test_route_auth_coverage.py`` enumerates
``app.url_map`` and fails if coverage is incomplete.

Method mapping (02-api-contract.md): GET -> VIEW_ASSET, POST/PUT -> CREATE/
EDIT or the domain permission, DELETE -> DECOMMISSION_ASSET; imports ->
IMPORT_DATA; /api/db-test -> MANAGE_SETTINGS.
"""

from __future__ import annotations

import logging

from flask import g, request

from app.auth.jwt import authenticate_request, get_current_user
from app.auth.permissions import has_permission
from app.core import error as error_response

logger = logging.getLogger(__name__)

# Anonymous allow-list. Everything else under /api/* requires auth.
PUBLIC_PATH_PREFIXES = (
    "/health",
    "/ready",
    "/api/health",
    "/api/v1/openapi.json",
    "/api/v1/public/",
)

_AUTHENTICATED = "_AUTHENTICATED"

# (path prefix, method) -> permission required. More specific prefixes win.
LEGACY_ROUTE_PERMISSIONS: tuple[tuple[str, str, str], ...] = (
    ("/api/db-test", "GET", "MANAGE_SETTINGS"),
    ("/api/campus/import", "POST", "IMPORT_DATA"),
    ("/api/acs/import", "POST", "IMPORT_DATA"),
    ("/api/acs", "GET", "VIEW_ASSET"),
    ("/api/acs", "POST", "CREATE_ASSET"),
    ("/api/acs", "PUT", "EDIT_ASSET"),
    ("/api/acs", "DELETE", "DECOMMISSION_ASSET"),
    ("/api/maintenance", "GET", "VIEW_ASSET"),
    ("/api/maintenance", "POST", "MANAGE_MAINTENANCE"),
    ("/api/maintenance", "PUT", "MANAGE_MAINTENANCE"),
    ("/api/maintenance", "DELETE", "MANAGE_MAINTENANCE"),
    ("/api/colleges", "GET", "VIEW_ASSET"),
    ("/api/blocks", "GET", "VIEW_ASSET"),
    ("/api/floors", "GET", "VIEW_ASSET"),
    ("/api/departments", "GET", "VIEW_ASSET"),
    ("/api/rooms", "GET", "VIEW_ASSET"),
    ("/api/dashboard", "GET", "VIEW_ASSET"),
)


def legacy_required_permission(path: str, method: str) -> str | None:
    """Return the permission a legacy route requires, or None when uncovered."""
    best: tuple[int, str] | None = None
    for prefix, m, permission in LEGACY_ROUTE_PERMISSIONS:
        if m == method and (path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "?")):
            if path.startswith(prefix):
                score = len(prefix)
                if best is None or score > best[0]:
                    best = (score, permission)
    return best[1] if best else None


def register_legacy_guard(app):
    """Install a before_request guard enforcing LEGACY_ROUTE_PERMISSIONS."""

    @app.before_request
    def _enforce_legacy_auth():
        path = request.path
        if not path.startswith("/api/"):
            return None
        if path.startswith("/api/v1/"):
            return None  # v1 routes carry their own decorators
        if any(path == p or path.startswith(p) for p in PUBLIC_PATH_PREFIXES):
            return None

        required = legacy_required_permission(path, request.method)
        if required is None:
            # Fail closed: an unmapped legacy route is a bug, loudly.
            logger.error(
                "legacy route without auth mapping request_id=%s path=%s method=%s",
                getattr(g, "request_id", "-"),
                path,
                request.method,
            )
            return error_response("FORBIDDEN", "This endpoint is not available.", 403)

        resp = authenticate_request()
        if resp is not None:
            return resp
        if required == _AUTHENTICATED:
            return None

        user = get_current_user() or {}
        if not has_permission(user.get("role"), required):
            return error_response("FORBIDDEN", "You do not have permission to perform this action.", 403)
        return None
