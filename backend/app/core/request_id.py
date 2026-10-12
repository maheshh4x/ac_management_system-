"""Request-ID middleware (Plan #30)."""

from __future__ import annotations

import uuid

from flask import g, request


def register_request_id(app):
    @app.before_request
    def _assign_request_id():
        rid = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        g.request_id = rid

    @app.after_request
    def _attach_request_id(response):
        rid = getattr(g, "request_id", None)
        if rid:
            response.headers["X-Request-ID"] = rid
        return response
