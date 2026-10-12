"""POST /api/v1/campus/import — bulk campus CSV import (Phase 3 task 8).

v1 replacement for the legacy ``/api/campus/import``: same shared core
(``app.routes.campus_import.run_campus_import``) and permission
(``IMPORT_DATA``), wrapped in auth + the standard envelope. ``dry_run=true``
validates and reports without writing.
"""

from __future__ import annotations

import logging

from flask import Blueprint, g, request

from app.auth.jwt import require_auth
from app.auth.permissions import require_permission
from app.core import error, success
from app.routes import campus_import as campus_core

campus_import_v1_bp = Blueprint("campus_import_v1", __name__)

logger = logging.getLogger(__name__)


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@campus_import_v1_bp.route("/api/v1/campus/import", methods=["POST"])
@require_auth
@require_permission("IMPORT_DATA")
def import_campus():
    request_id = getattr(g, "request_id", "-")
    dry_run = _is_true(request.args.get("dry_run") or request.form.get("dry_run"))

    try:
        report = campus_core.run_campus_import(request.files.get("file"), request_id, dry_run=dry_run)
    except campus_core.CampusImportError as exc:
        return error(exc.code, exc.message, exc.status)
    except Exception:
        # The shared core already logged the detail with this request id.
        logger.exception("v1 campus import failed request_id=%s", request_id)
        return error("IMPORT_FAILED", "Unable to import campus CSV", 500)

    return success(report, 200 if dry_run else 201)
