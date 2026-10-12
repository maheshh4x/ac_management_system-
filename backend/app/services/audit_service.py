"""Append-only audit log writer (Plan #17). Safe no-op when table missing.

Phase 7: value payloads are redacted before they ever reach the table
(``redact_values``) so ``old_values``/``new_values`` can never carry
password-like PII; the read endpoint re-applies the same redaction as
defence in depth for rows written before this hardening.
"""

from __future__ import annotations

import json
import logging
import re

from flask import g, request
from psycopg2.errors import UndefinedTable

logger = logging.getLogger(__name__)

# Password-like keys that must never appear in audit payloads. Matched
# case-insensitively as substrings of the key (covers password_hash,
# refresh_token, api_key, client_secret, ...).
_SENSITIVE_KEY = re.compile(
    r"password|passwd|pwd|secret|token|api[_-]?key|authorization|credential",
    re.IGNORECASE,
)
REDACTED = "***REDACTED***"


def redact_values(values):
    """Recursively replace password-like values with ``***REDACTED***``.

    Accepts dicts/lists (native JSONB) or JSON strings; non-JSON strings are
    returned unchanged. Pure function — safe on untrusted read payloads.
    """
    if isinstance(values, str):
        stripped = values.strip()
        if not stripped or stripped[0] not in "[{":
            return values
        try:
            return json.dumps(_redact_structure(json.loads(stripped)))
        except (ValueError, TypeError):
            return values
    return _redact_structure(values)


def _redact_structure(values):
    if values is None:
        return None
    if isinstance(values, dict):
        return {
            k: (REDACTED if isinstance(k, str) and _SENSITIVE_KEY.search(k) else _redact_structure(v))
            for k, v in values.items()
        }
    if isinstance(values, list):
        return [_redact_structure(v) for v in values]
    return values


def audit(action: str, entity_type: str, entity_id=None, old_values=None, new_values=None):
    """Best-effort audit insert — must NEVER break the business request, but
    failures must be loud: audited mutations are compliance-relevant, so a
    lost record is a warning-level event with the request id attached."""
    try:
        from app.config.database import get_db_connection

        user = getattr(g, "current_user", None) or {}
        with get_db_connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_logs
                    (user_id, action, entity_type, entity_id, old_values, new_values, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    str(user.get("id")) if user.get("id") else None,
                    action,
                    entity_type,
                    str(entity_id) if entity_id is not None else None,
                    _to_json(old_values),
                    _to_json(new_values),
                    (request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:64],
                    (request.headers.get("User-Agent") or "")[:512],
                ),
            )
    except UndefinedTable:  # audit_logs not migrated yet
        logger.debug("audit skipped (%s %s): audit_logs table not migrated", action, entity_type)
    except Exception:  # never break the main request on audit failure
        logger.warning(
            "audit write failed request_id=%s action=%s entity_type=%s entity_id=%s",
            getattr(g, "request_id", "-"),
            action,
            entity_type,
            entity_id,
            exc_info=True,
        )


def _to_json(value):
    """Serialize an audit payload — redacting password-like fields first so
    sensitive values never reach the table (Phase 7 PII rule)."""
    if value is None:
        return None
    try:
        return json.dumps(_redact_structure(value), default=str)
    except Exception:
        return str(value)
