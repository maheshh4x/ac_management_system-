"""Route rate limiting (Phase 1.4) — adapter over flask-limiter.

Keeps the original ``@rate_limit(max_requests, window_seconds, key_prefix)``
call sites working while the enforcement moves to flask-limiter with a
Redis backend when REDIS_URL is configured (per-process memory otherwise,
development only). Identity key: authenticated user id, else ProxyFix-aware
remote address (see app.extensions).
"""

from __future__ import annotations

from app.extensions import limiter


def rate_limit(max_requests: int = 60, window_seconds: int = 60, key_prefix: str = ""):
    limit_string = f"{max_requests} per {window_seconds} second"

    def decorator(fn):
        # flask-limiter enforces per (key_func, limit, scope) — the prefix
        # keeps limits from different call sites from sharing a bucket.
        return limiter.limit(limit_string, scope=key_prefix or None)(fn)

    return decorator
