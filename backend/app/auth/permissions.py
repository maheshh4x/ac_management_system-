"""Server-side RBAC (Plan #4, #5). Backend enforces every permission.

Phase 1 additions (02-api-contract.md "Permission additions"):
  APPROVE_USERS, COMPLETE_MOVEMENT, ASSIGN_MAINTENANCE, EXPORT_DATA.
Manager = SUPER_ADMIN minus MANAGE_USERS, MANAGE_SETTINGS,
DECOMMISSION_ASSET, VIEW_AUDIT, plus APPROVE_USERS (scoped to
TECHNICIAN/VIEWER inside the approval endpoints — scoping is business
logic, not a static permission).
VIEW_PUBLIC_ASSET is not a role permission: the public asset endpoint is
anonymous by design and separately rate-limited.
"""

from __future__ import annotations

from functools import wraps

from flask import g

from app.auth.jwt import get_current_user
from app.core import error as error_response

ROLES = ("SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER")

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "SUPER_ADMIN": {
        "VIEW_ASSET",
        "CREATE_ASSET",
        "EDIT_ASSET",
        "DECOMMISSION_ASSET",
        "MANAGE_CAMPUS",
        "MANAGE_MAINTENANCE",
        "CREATE_MOVEMENT",
        "APPROVE_MOVEMENT",
        "COMPLETE_MOVEMENT",
        "ASSIGN_MAINTENANCE",
        "VIEW_REPORTS",
        "EXPORT_DATA",
        "MANAGE_USERS",
        "APPROVE_USERS",
        "VIEW_AUDIT",
        "MANAGE_SETTINGS",
        "IMPORT_DATA",
    },
    "FACILITY_MANAGER": {
        "VIEW_ASSET",
        "CREATE_ASSET",
        "EDIT_ASSET",
        "MANAGE_CAMPUS",
        "MANAGE_MAINTENANCE",
        "CREATE_MOVEMENT",
        "APPROVE_MOVEMENT",
        "COMPLETE_MOVEMENT",
        "ASSIGN_MAINTENANCE",
        "VIEW_REPORTS",
        "EXPORT_DATA",
        "APPROVE_USERS",
        "IMPORT_DATA",
    },
    "TECHNICIAN": {"VIEW_ASSET", "CREATE_MOVEMENT", "MANAGE_MAINTENANCE", "COMPLETE_MOVEMENT"},
    "VIEWER": {"VIEW_ASSET"},
}


def has_permission(role: str | None, permission: str) -> bool:
    if not role:
        return False
    return permission in ROLE_PERMISSIONS.get(role, set())


def require_permission(*permissions: str):
    """Enforce that the verified role grants one of `permissions`.

    Must be applied after require_auth. Role checks are server side: only
    g.current_user built by require_auth is consulted — client-supplied
    roles in bodies/headers are never trusted.
    """

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            user = get_current_user()
            if user is None:
                return error_response("UNAUTHORIZED", "Authentication required.", 401)
            if not user.get("role"):
                return error_response(
                    "FORBIDDEN",
                    "You do not have permission to perform this action.",
                    403,
                )
            role = user.get("role")
            if not any(has_permission(role, p) for p in permissions):
                return error_response(
                    "FORBIDDEN",
                    "You do not have permission to perform this action.",
                    403,
                )
            g.permission_checked = permissions
            return fn(*args, **kwargs)

        wrapper._requires_auth = True
        wrapper._requires_permissions = tuple(permissions)
        return wrapper

    return decorator
