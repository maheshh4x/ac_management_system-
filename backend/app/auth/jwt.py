"""JWT verification against Supabase Auth — Phase 1 hardened.

Verification modes (explicit allow-list of algorithms, never read unchecked
from the token header):
  * HS256 with ``SUPABASE_JWT_SECRET`` / ``JWT_SECRET`` (legacy Supabase JWT
    secret). ``SUPABASE_ANON_KEY`` is NEVER a signing secret: it is a public
    client-side key, present in the browser bundle.
  * Asymmetric (ES256/RS256) via JWKS: ``SUPABASE_JWKS_URL`` or derived from
    ``SUPABASE_URL`` as ``{SUPABASE_URL}/auth/v1/.well-known/jwks.json``.
    Keys are cached with a TTL; an unknown ``kid`` triggers one throttled
    refetch so key rotation keeps working.

Claims validated on every token: signature, ``exp`` (default), ``aud ==
"authenticated"``, ``iss == f"{SUPABASE_URL}/auth/v1"`` when ``SUPABASE_URL``
is configured, and ``sub`` must be a UUID.

Fail-closed: no verification key configured -> every authenticated request
gets 503 AUTH_NOT_CONFIGURED, in development too. ``APP_ENV=production``
refuses to boot without a key (see create_app).

Authorization: after signature verification the server-side ``profiles.role``
row is authoritative; the JWT ``app_metadata`` role claim is only a fallback
when no profile row exists (e.g. DB unreachable in tests). Client-supplied
bodies/headers can never grant a role. Profiles with status != 'APPROVED'
are rejected everywhere except a small exempt set (GET /me, register-profile)
so the UI can render the pending/rejected screen.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid as uuid_mod
from functools import wraps

import jwt
from flask import g, request

from app.core import error as error_response

logger = logging.getLogger(__name__)

_SUPABASE_ROLES = {"SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER"}
_EXPECTED_AUD = "authenticated"

# Explicit algorithm allow-lists. The token header's "alg" is an input to
# selection, never trusted by itself.
_HS_ALGS = ["HS256"]
_ASYM_ALGS = ["ES256", "RS256"]

_JWKS_TTL_SECONDS = 300

# Authenticated-but-not-yet-approved users may still call these endpoints so
# the UI can render the pending/rejected screen and self-register the profile.
_STATUS_EXEMPT_ENDPOINTS = {
    "users_v1.get_me",
    "users_v1.register_profile",
}


def _app_env() -> str:
    return (os.getenv("APP_ENV") or os.getenv("FLASK_ENV") or "development").lower()


def _jwt_secret() -> str | None:
    """HS256 verification secret. The public anon key is never used here."""
    return os.getenv("SUPABASE_JWT_SECRET") or os.getenv("JWT_SECRET")


def _supabase_url() -> str | None:
    url = os.getenv("SUPABASE_URL") or ""
    return url.rstrip("/") or None


def _jwks_url() -> str | None:
    explicit = os.getenv("SUPABASE_JWKS_URL")
    if explicit:
        return explicit
    base = _supabase_url()
    return f"{base}/auth/v1/.well-known/jwks.json" if base else None


def _issuer() -> str | None:
    base = _supabase_url()
    return f"{base}/auth/v1" if base else None


def auth_configured() -> bool:
    """True when at least one verification mechanism is configured."""
    return bool(_jwt_secret() or _jwks_url())


class _JwksProvider:
    """JWKS client with TTL caching and a throttled rotation refetch.

    pyjwt's PyJWKClient caches keys by ``kid`` for ``lifespan`` seconds and
    refetches on unknown ``kid``; we additionally throttle forced refetches so
    a flood of tokens with bogus ``kid`` values cannot DoS the JWKS endpoint.
    """

    def __init__(self, url: str, ttl: int = _JWKS_TTL_SECONDS):
        self._url = url
        self._ttl = ttl
        self._client: jwt.PyJWKClient | None = None
        # -inf: the first unknown kid always triggers one refetch.
        self._last_forced_refresh = float("-inf")

    def _get_client(self) -> jwt.PyJWKClient:
        if self._client is None:
            self._client = jwt.PyJWKClient(self._url, cache_keys=True, lifespan=max(self._ttl, 60))
        return self._client

    def signing_key(self, token: str):
        client = self._get_client()
        try:
            return client.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientError:
            # Possible key rotation: force one refetch per TTL window, retry.
            now = time.monotonic()
            if now - self._last_forced_refresh >= self._ttl:
                self._last_forced_refresh = now
                try:
                    client.fetch_data()
                except Exception:  # noqa: BLE001 - retry path handles failure
                    logger.warning("JWKS forced refetch failed url=%s", self._url)
            return client.get_signing_key_from_jwt(token)


_jwks_provider: _JwksProvider | None = None


def _get_jwks_provider() -> _JwksProvider | None:
    global _jwks_provider
    url = _jwks_url()
    if not url:
        return None
    if _jwks_provider is None or _jwks_provider._url != url:
        _jwks_provider = _JwksProvider(url)
    return _jwks_provider


_PROFILE_CACHE: dict[str, tuple[float, dict | None]] = {}
_PROFILE_CACHE_LOCK = threading.Lock()
_PROFILE_CACHE_TTL_SECONDS = 60.0


def invalidate_profile_cache(user_id: str | None = None) -> None:
    """Drop cached profile state. If user_id is None, clears all cached profiles."""
    with _PROFILE_CACHE_LOCK:
        if user_id:
            _PROFILE_CACHE.pop(user_id, None)
        else:
            _PROFILE_CACHE.clear()


def reset_caches() -> None:
    """Drop cached JWKS and profile state. Used by tests; safe to call anytime."""
    global _jwks_provider
    _jwks_provider = None
    invalidate_profile_cache()


def get_current_user():
    return getattr(g, "current_user", None)


def _lookup_profile(user_id: str):
    """Best-effort server-side profile lookup with in-memory TTL caching.
    Returns None when the table or the Phase-1.3 columns are not migrated yet
    (fail-open is acceptable here: without the DB the request will fail on data
    access anyway, and the JWT claims fallback below never grants a role the
    server didn't sign)."""
    now = time.monotonic()
    with _PROFILE_CACHE_LOCK:
        cached = _PROFILE_CACHE.get(user_id)
        if cached:
            timestamp, data = cached
            if now - timestamp < _PROFILE_CACHE_TTL_SECONDS:
                return data

    try:
        from app.config.database import get_db_connection

        conn = get_db_connection()
        try:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT id, email, name, role, college_id, department_id,"
                    " is_active, status, assigned_blocks"
                    " FROM profiles WHERE id = %s",
                    (user_id,),
                )
                row = cur.fetchone()
                keys = [
                    "id",
                    "email",
                    "name",
                    "role",
                    "college_id",
                    "department_id",
                    "is_active",
                    "status",
                    "assigned_blocks",
                ]
            except Exception as exc:
                if type(exc).__name__ == "UndefinedColumn":
                    # Pre-006 schema: no status column yet.
                    conn.rollback()
                    cur.execute(
                        "SELECT id, email, name, role, college_id, department_id, is_active"
                        " FROM profiles WHERE id = %s",
                        (user_id,),
                    )
                    row = cur.fetchone()
                    keys = ["id", "email", "name", "role", "college_id", "department_id", "is_active"]
                else:
                    raise
            cur.close()
        finally:
            conn.close()
        if not row:
            with _PROFILE_CACHE_LOCK:
                _PROFILE_CACHE[user_id] = (now, None)
            return None
        res = row if isinstance(row, dict) else dict(zip(keys, row, strict=False))
        with _PROFILE_CACHE_LOCK:
            _PROFILE_CACHE[user_id] = (now, res)
        return res
    except Exception as exc:  # noqa: BLE001 - table may not exist yet
        if type(exc).__name__ == "UndefinedTable":
            logger.debug("profile lookup skipped: profiles table not migrated yet")
        else:
            logger.warning(
                "profile lookup failed request_id=%s: %s",
                getattr(g, "request_id", "-"),
                exc,
            )
        return None


def _verify_token(token: str) -> dict:
    """Verify signature + standard claims. Raises jwt.InvalidTokenError."""
    decode_kwargs = {
        "audience": _EXPECTED_AUD,
        "options": {"require": ["exp", "iat", "sub", "aud"], "verify_aud": True},
    }
    issuer = _issuer()
    if issuer:
        decode_kwargs["issuer"] = issuer
        decode_kwargs["options"]["require"].append("iss")
        decode_kwargs["options"]["verify_iss"] = True
    else:
        # Without SUPABASE_URL the expected issuer is unknowable; log once so
        # the gap is visible instead of silently unverified.
        logger.warning("SUPABASE_URL not set: JWT issuer claim is not verified")

    secret = _jwt_secret()
    if secret:
        claims = jwt.decode(token, secret, algorithms=_HS_ALGS, **decode_kwargs)
    else:
        provider = _get_jwks_provider()
        if provider is None:
            raise jwt.InvalidTokenError("no verification key configured")
        key = provider.signing_key(token)
        # Allow-list stays asymmetric only: an HS256 token "signed" with the
        # RSA public key (confusion attack) is rejected by algorithms=... .
        claims = jwt.decode(token, key.key, algorithms=_ASYM_ALGS, **decode_kwargs)

    try:
        uuid_mod.UUID(str(claims.get("sub", "")))
    except (ValueError, AttributeError, TypeError):
        raise jwt.InvalidTokenError("sub claim is not a UUID") from None
    return claims


def authenticate_request():
    """Authenticate the current request. Returns None on success (with
    ``g.current_user`` populated) or a Flask error response.

    This is the single entry point used by the require_auth decorator and the
    legacy-route guard, so both enforce exactly the same policy.
    """
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else None

    if not token:
        return error_response("UNAUTHORIZED", "Authentication required.", 401)

    if not auth_configured():
        # Fail closed in every environment: without a verification key no
        # token can be checked, so no request may proceed.
        return error_response(
            "AUTH_NOT_CONFIGURED",
            "Authentication is not configured on the server.",
            503,
        )

    try:
        claims = _verify_token(token)
    except jwt.ExpiredSignatureError:
        return error_response("TOKEN_EXPIRED", "Session has expired. Please log in again.", 401)
    except jwt.InvalidTokenError as exc:
        # Sanitized cause only (pyjwt messages never contain the token):
        # distinguishes expired/issuer/audience/signature failures in logs.
        logger.warning(
            "token verification failed request_id=%s reason=%s",
            getattr(g, "request_id", "-"),
            f"{type(exc).__name__}: {exc}",
        )
        return error_response("INVALID_TOKEN", "Invalid authentication token.", 401)
    except Exception:  # noqa: BLE001 - JWKS network failures etc.
        logger.warning(
            "token verification backend failure request_id=%s",
            getattr(g, "request_id", "-"),
            exc_info=True,
        )
        return error_response("AUTH_UNAVAILABLE", "Authentication service unavailable.", 503)

    user_id = claims.get("sub")
    email = claims.get("email")
    profile = _lookup_profile(user_id) if user_id else None

    role = None
    if profile and profile.get("role") in _SUPABASE_ROLES:
        role = profile["role"]
    if not role:
        meta = claims.get("app_metadata") or {}
        if meta.get("role") in _SUPABASE_ROLES:
            role = meta["role"]

    if profile and profile.get("is_active") is False:
        return error_response("USER_INACTIVE", "User account is inactive.", 403)

    if profile is not None:
        status = profile.get("status") or "APPROVED"
        if status != "APPROVED" and request.endpoint not in _STATUS_EXEMPT_ENDPOINTS:
            if status == "PENDING":
                return error_response("ACCOUNT_PENDING", "Your account is awaiting approval.", 403)
            return error_response("ACCOUNT_REJECTED", "Your account registration was rejected.", 403)

    g.current_user = {
        "id": user_id,
        "email": email or (profile or {}).get("email"),
        "role": role,
        "profile": profile,
        "claims": claims,
    }
    return None


def require_auth(fn):
    """Decorator: authenticated user required. Sets g.current_user."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        resp = authenticate_request()
        if resp is not None:
            return resp
        return fn(*args, **kwargs)

    wrapper._requires_auth = True
    wrapper._requires_permissions = getattr(fn, "_requires_permissions", ())
    return wrapper
