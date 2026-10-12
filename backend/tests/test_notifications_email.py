"""Phase 7: notifications ``?since=`` polling filter and the email mirror.

Two independent concerns live here:

* ``GET /api/v1/notifications?since=`` — server-side incremental polling
  (the FE keeps its 60 s timer, it just asks for "newer than X").
* ``app/services/email.py`` — flag-gated (``EMAIL_ENABLED``, default off),
  best-effort mirror of the ``EMAIL_TYPES`` allow-list wired into
  ``app.services.notify.notify()``. Never raises, never blocks a request.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import TEST_SECRET, TEST_SUPABASE_URL, TEST_USER_ID, AuthClient, make_token  # noqa: E402

NOTIFICATIONS = "/api/v1/notifications"


def _authed_client(monkeypatch, role="VIEWER", sub=TEST_USER_ID):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    app = create_app()
    app.config["TESTING"] = True
    return AuthClient(app.test_client(), make_token(role, sub=sub))


@pytest.fixture()
def notify_client(db, monkeypatch):  # noqa: ARG001
    """Signed-in client + a profile row (notifications.user_id FK) + rows."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status, is_active)"
                " VALUES (%s, 'p7-notify@acms.test', 'P7 Notify', 'VIEWER', 'APPROVED', TRUE)"
                " ON CONFLICT (id) DO UPDATE SET role = 'VIEWER', status = 'APPROVED', is_active = TRUE",
                (TEST_USER_ID,),
            )
            for offset, title in ((2, "Old"), (0, "New")):
                cur.execute(
                    "INSERT INTO notifications (user_id, type, title, body)"
                    " VALUES (%s, 'P7_TEST', %s, 'body')",
                    (TEST_USER_ID, f"P7 {title}"),
                )
                # Backdate the first row past midnight so ?since= today and
                # ?since=<now-5m> both have something to exclude.
                if offset:
                    cur.execute(
                        "UPDATE notifications SET created_at = now() - interval '2 days'" " WHERE title = %s",
                        (f"P7 {title}",),
                    )
        conn.commit()
    finally:
        conn.close()
    yield _authed_client(monkeypatch, "VIEWER")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM notifications WHERE user_id = %s AND type = 'P7_TEST'", (TEST_USER_ID,))
            cur.execute("DELETE FROM profiles WHERE id = %s", (TEST_USER_ID,))
        conn.commit()
    finally:
        conn.close()


def _data(resp):
    return resp.get_json()["data"]


def _error_code(resp):
    return (resp.get_json() or {}).get("error", {}).get("code")


# ── ?since= polling ──────────────────────────────────────────────────────────


def test_notifications_requires_auth(client):
    assert client.get(NOTIFICATIONS).status_code == 401


def test_notifications_returns_both_rows_by_default(notify_client):
    r = notify_client.get(NOTIFICATIONS)
    assert r.status_code == 200, r.get_json()
    data = _data(r)
    titles = [row["title"] for row in data["items"]]
    assert "P7 New" in titles and "P7 Old" in titles
    assert data["pagination"]["total"] >= 2
    assert data["unread_count"] >= 2


def test_notifications_since_excludes_older_rows(notify_client):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    r = notify_client.get(f"{NOTIFICATIONS}?since={cutoff}")
    assert r.status_code == 200, r.get_json()
    titles = [row["title"] for row in _data(r)["items"]]
    assert "P7 New" in titles
    assert "P7 Old" not in titles  # written two hours ago
    assert _data(r)["pagination"]["total"] == len(titles)


def test_notifications_since_accepts_plain_date(notify_client):
    # A bare date becomes midnight in the database's session zone (UTC); the
    # local date is a day ahead of it until UTC catches up.
    today = datetime.now(timezone.utc).date().isoformat()
    r = notify_client.get(f"{NOTIFICATIONS}?since={today}")
    assert r.status_code == 200, r.get_json()
    titles = [row["title"] for row in _data(r)["items"]]
    assert "P7 New" in titles
    assert "P7 Old" not in titles


def test_notifications_since_rejects_garbage(notify_client):
    r = notify_client.get(f"{NOTIFICATIONS}?since=last-tuesday")
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_DATE"


def test_notifications_since_combines_with_unread(notify_client):
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    r = notify_client.get(f"{NOTIFICATIONS}?since={cutoff}&unread=1")
    assert r.status_code == 200, r.get_json()
    assert all(not row["is_read"] for row in _data(r)["items"])


# ── email mirror ─────────────────────────────────────────────────────────────


@pytest.fixture()
def email_mod(db, monkeypatch):  # noqa: ARG001 - db pins the env to the test DB
    from app.services import email as mod

    monkeypatch.delenv("EMAIL_ENABLED", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    return mod


@pytest.fixture(autouse=True)
def _notify_profile(db):  # noqa: ARG001
    """notifications.user_id FK: notify() tests need a real profile row."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status, is_active)"
                " VALUES (%s, 'p7-email@acms.test', 'P7 Email', 'VIEWER', 'APPROVED', TRUE)"
                " ON CONFLICT (id) DO UPDATE SET role = 'VIEWER', status = 'APPROVED', is_active = TRUE",
                (TEST_USER_ID,),
            )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM notifications WHERE user_id = %s AND type LIKE %s",
                (TEST_USER_ID, "ACCOUNT\\_%"),
            )
            cur.execute(
                "DELETE FROM notifications WHERE user_id = %s AND title IN ('Welcome', 'In-app only')",
                (TEST_USER_ID,),
            )
            cur.execute("DELETE FROM profiles WHERE id = %s", (TEST_USER_ID,))
        conn.commit()
    finally:
        conn.close()


def _recipient(monkeypatch, mod, address="user@acms.test"):
    monkeypatch.setattr(mod, "_recipient_email", lambda user_id: address)


def test_email_off_by_default(email_mod, monkeypatch):
    """No EMAIL_ENABLED => never touches SMTP, always returns False."""
    calls = []
    monkeypatch.setattr(email_mod, "smtp_send", lambda *a, **k: calls.append(a) or True)
    assert email_mod.send_notification_email(TEST_USER_ID, "JOB_ASSIGNED", "t", "b") is False
    assert calls == []


def test_email_disabled_flag_wins(email_mod, monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "0")
    calls = []
    monkeypatch.setattr(email_mod, "smtp_send", lambda *a, **k: calls.append(a) or True)
    assert email_mod.send_notification_email(TEST_USER_ID, "JOB_ASSIGNED", "t", "b") is False
    assert calls == []


def test_email_only_sends_for_allow_listed_types(email_mod, monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "1")
    _recipient(monkeypatch, email_mod)
    calls = []
    monkeypatch.setattr(email_mod, "smtp_send", lambda *a, **k: calls.append(a) or True)

    assert email_mod.send_notification_email(TEST_USER_ID, "JOB_ASSIGNED", "Assigned", "do it") is True
    assert email_mod.send_notification_email(TEST_USER_ID, "P7_TEST", "nope", "x") is False
    assert len(calls) == 1
    to, subject, body = calls[0]
    assert to == "user@acms.test"
    assert subject == "Assigned"
    assert body == "do it"


def test_email_skips_when_recipient_unknown(email_mod, monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "1")
    _recipient(monkeypatch, email_mod, address=None)
    calls = []
    monkeypatch.setattr(email_mod, "smtp_send", lambda *a, **k: calls.append(a) or True)
    assert email_mod.send_notification_email(TEST_USER_ID, "JOB_ASSIGNED", "t", "b") is False
    assert calls == []


def test_email_never_raises(email_mod, monkeypatch):
    """SMTP failure is swallowed — the business request must not see it."""
    monkeypatch.setenv("EMAIL_ENABLED", "1")
    _recipient(monkeypatch, email_mod)

    def _boom(*a, **k):
        raise ConnectionRefusedError("smtp down")

    monkeypatch.setattr(email_mod, "smtp_send", _boom)
    assert email_mod.send_notification_email(TEST_USER_ID, "JOB_ASSIGNED", "t", "b") is False


def test_notify_mirrors_allow_listed_types(email_mod, monkeypatch):
    """notify() hands every notification to the email hook; the hook decides."""
    sent = []
    monkeypatch.setattr(email_mod, "send_notification_email", lambda *a, **k: sent.append(a) or True)
    from app.services import notify as notify_mod

    notify_mod.notify(TEST_USER_ID, "ACCOUNT_APPROVED", "Welcome", "body")
    notify_mod.notify(TEST_USER_ID, "P7_TEST", "In-app only", "body")
    assert [(a[0], a[1]) for a in sent] == [
        (TEST_USER_ID, "ACCOUNT_APPROVED"),
        (TEST_USER_ID, "P7_TEST"),
    ]


def test_notify_survives_email_hook_failure(email_mod, monkeypatch, caplog):
    import logging

    def _boom(*a, **k):
        raise RuntimeError("hook exploded")

    monkeypatch.setattr(email_mod, "send_notification_email", _boom)
    from app.services import notify as notify_mod

    with caplog.at_level(logging.WARNING, logger="app.services.notify"):
        notify_mod.notify(TEST_USER_ID, "ACCOUNT_APPROVED", "Welcome", "body")
    assert "email mirror failed" in caplog.text


def test_smtp_send_without_host_is_a_noop(email_mod, monkeypatch):
    """SMTP_HOST unset => no connection attempt, clean False."""
    monkeypatch.setenv("SMTP_HOST", "")
    assert email_mod.smtp_send("a@b.c", "subject", "body") is False


def test_email_types_are_a_small_allow_list(email_mod):
    # The allow-list stays deliberately short: account decisions, job
    # assignment and movement decisions only.
    assert email_mod.EMAIL_TYPES == {
        "ACCOUNT_APPROVED",
        "ACCOUNT_REJECTED",
        "JOB_ASSIGNED",
        "MOVEMENT_APPROVED",
        "MOVEMENT_REJECTED",
    }
    assert email_mod.email_enabled() is False


def test_smtp_send_uses_configured_host(email_mod, monkeypatch):
    """Full path: flag on + host set => one message handed to smtplib."""
    monkeypatch.setenv("EMAIL_ENABLED", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.test")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_STARTTLS", raising=False)

    captured = {"messages": []}

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            captured["starttls"] = True

        def send_message(self, msg):
            captured["messages"].append(msg)

        def login(self, user, password):
            captured["login"] = user

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    _recipient(monkeypatch, email_mod)

    assert email_mod.send_notification_email(TEST_USER_ID, "MOVEMENT_APPROVED", "Approved", "ok") is True
    assert captured["host"] == "smtp.example.test"
    assert captured["port"] == 2525
    assert captured.get("starttls") is True
    assert len(captured["messages"]) == 1
    message = captured["messages"][0]
    assert message["Subject"] == "Approved"
    assert message["To"] == "user@acms.test"
    assert message["From"] == "noreply@example.test"
    assert "ok" in message.get_content()
