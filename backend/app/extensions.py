"""Shared Flask extension instances (flask-limiter).

Rate limiting policy (Phase 1.4): Redis-backed when ``REDIS_URL`` is set
(correct across gunicorn workers); in-memory only for development. A
production deployment without REDIS_URL boots but logs a loud warning —
fail-open with a warning is deliberate here because the limiter is defense
in depth, not the auth boundary.

Client identity: the authenticated user id when a token was verified, else
the remote address *after* ProxyFix (the trusted-proxy count from
``TRUSTED_PROXIES`` decides the real client IP). Raw X-Forwarded-For from
the request is never read directly.
"""

from __future__ import annotations

import logging
import os

from flask import g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logger = logging.getLogger(__name__)


def _rate_limit_key() -> str:
    user = getattr(g, "current_user", None) or {}
    if user.get("id"):
        return f"user:{user['id']}"
    return get_remote_address() or "unknown"


limiter = Limiter(key_func=_rate_limit_key, default_limits=[], headers_enabled=True)


def init_limiter(app) -> None:
    redis_url = os.getenv("REDIS_URL")
    env = (os.getenv("APP_ENV") or "development").lower()
    if redis_url:
        app.config["RATELIMIT_STORAGE_URI"] = redis_url
    else:
        app.config["RATELIMIT_STORAGE_URI"] = "memory://"
        if env == "production":
            logger.warning(
                "REDIS_URL not set: rate limiting is per-process only. "
                "Behind multiple gunicorn workers each worker gets its own buckets."
            )
    limiter.init_app(app)
