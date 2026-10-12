"""Best-effort outbound email (Phase 7).

Flag-gated by ``EMAIL_ENABLED`` (default: **off**), so development and the
test suite never open an SMTP connection. Wired centrally from
``app.services.notify.notify()`` for the ``EMAIL_TYPES`` allow-list only —
every other notification stays in-app. The recipient lookup and the SMTP
send each open their own connection, so an email never participates in (or
observes) the caller's business transaction.

Every failure is logged and swallowed: an SMTP outage or a missing profile
must never turn a committed business action into a 500.

Backend-only configuration (never exposed to the browser):

===========  =========================================================
``EMAIL_ENABLED``   ``1/true/yes`` to send; anything else = off
``SMTP_HOST``       required when enabled; empty = no-op
``SMTP_PORT``       default 587 (465 switches to implicit TLS)
``SMTP_USER``       optional LOGIN
``SMTP_PASSWORD``   optional LOGIN password
``SMTP_FROM``       sender header; default ``noreply@acms.local``
``SMTP_STARTTLS``   default on for non-465 ports (``0`` disables)
``SMTP_TIMEOUT``    seconds, default 10
===========  =========================================================
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)

# Notification types that are mirrored to email. Deliberately a short
# allow-list: account decisions, job assignment and movement decisions are
# the ones a technician is expected to act on outside the portal.
EMAIL_TYPES = frozenset(
    {
        "ACCOUNT_APPROVED",
        "ACCOUNT_REJECTED",
        "JOB_ASSIGNED",
        "MOVEMENT_APPROVED",
        "MOVEMENT_REJECTED",
    }
)


def email_enabled() -> bool:
    return os.getenv("EMAIL_ENABLED", "").strip().lower() in {"1", "true", "yes"}


def send_notification_email(
    user_id,
    type: str,
    title: str,
    body: str | None = None,
    entity_type: str | None = None,
    entity_id=None,
) -> bool:
    """Mirror one notification to email when the type is allow-listed.

    Returns ``True`` only when SMTP accepted the message. Never raises.
    """
    if type not in EMAIL_TYPES or not email_enabled():
        return False
    try:
        to_email = _recipient_email(user_id)
        if not to_email:
            logger.info("email skipped: no address for user_id=%s", user_id)
            return False
        return smtp_send(to_email, title or type, body or "")
    except Exception:  # noqa: BLE001 - email is auxiliary by design
        logger.warning("email skipped after failure user_id=%s type=%s", user_id, type, exc_info=True)
        return False


def _recipient_email(user_id) -> str | None:
    """Look the recipient up on a dedicated connection (committed data only)."""
    from app.config.database import get_db_connection

    conn = None
    try:
        conn = get_db_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT email FROM profiles WHERE id = %s", (str(user_id),))
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
        if not row:
            return None
        value = row[0] if not isinstance(row, dict) else row.get("email")
        return str(value).strip() if value else None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - connection already returned
                pass


def smtp_send(to_email: str, subject: str, text: str) -> bool:
    """Hand one plain-text message to SMTP. Returns True when accepted."""
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        logger.info("email disabled: SMTP_HOST is not set (to=%s)", to_email)
        return False

    port = int(os.getenv("SMTP_PORT", "587") or 587)
    username = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("SMTP_FROM", "").strip() or "noreply@acms.local"
    timeout = int(os.getenv("SMTP_TIMEOUT", "10") or 10)
    starttls = os.getenv("SMTP_STARTTLS", "1").strip().lower() not in {"0", "false", "no"}

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(text)

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=timeout, context=ssl.create_default_context()) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            if starttls:
                smtp.starttls(context=ssl.create_default_context())
            if username:
                smtp.login(username, password)
            smtp.send_message(msg)

    logger.info("email accepted to=%s subject=%s", to_email, subject)
    return True
