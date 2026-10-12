"""Structured API errors (Plan #28). Never leak str(e) to clients."""

from __future__ import annotations

import logging

from flask import g

from app.core import error as error_response

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def register_error_handlers(app):
    @app.errorhandler(ApiError)
    def _handle_api_error(exc: ApiError):
        logger.warning(
            "api_error request_id=%s code=%s status=%s msg=%s",
            getattr(g, "request_id", "-"),
            exc.code,
            exc.status,
            exc.message,
        )
        return error_response(exc.code, exc.message, exc.status, exc.details)

    @app.errorhandler(404)
    def _handle_404(_exc):
        return error_response("NOT_FOUND", "Resource not found.", 404)

    @app.errorhandler(405)
    def _handle_405(_exc):
        return error_response("METHOD_NOT_ALLOWED", "Method not allowed.", 405)

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception):  # noqa: BLE001
        logger.exception(
            "unhandled_error request_id=%s path=%s",
            getattr(g, "request_id", "-"),
            getattr(exc, "__dict__", ""),
        )
        return error_response("INTERNAL_ERROR", "An unexpected error occurred.", 500)
