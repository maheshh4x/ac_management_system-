"""Phase 8.1 — unit tests for ``app.services.email``.

The mail path is flag-gated (``EMAIL_ENABLED`` off by default) and every
failure is swallowed, so none of it was ever exercised: ``_recipient_email``
and both SMTP branches sat uncovered. Nothing here opens a socket —
``smtplib.SMTP`` / ``smtplib.SMTP_SSL`` are replaced by recording fakes — and
no real address, host or password is used anywhere.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.services import email as email_mod  # noqa: E402

SMTP_VARS = (
    "EMAIL_ENABLED",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "SMTP_STARTTLS",
    "SMTP_TIMEOUT",
)


@pytest.fixture(autouse=True)
def clean_smtp_env(monkeypatch):
    """Start every test from a known mail configuration (.env must not leak)."""
    for var in SMTP_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def smtp(monkeypatch):
    """Replace the SMTP client with a recorder; yields the class it created."""

    class FakeSMTP:
        instances: list = []

        def __init__(self, host, port, timeout=None, **kwargs):
            self.host = host
            self.port = port
            self.timeout = timeout
            self.kwargs = kwargs
            self.sent = []
            self.tls = False
            self.logged_in = None
            type(self).instances.append(self)  # subclasses keep their own list

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            self.tls = True

        def login(self, user, password):
            self.logged_in = (user, password)

        def send_message(self, msg):
            self.sent.append(msg)

    class FakeSMTPSSL(FakeSMTP):
        instances = []

        def __init__(self, host, port, timeout=None, context=None, **kwargs):
            super().__init__(host, port, timeout=timeout, **kwargs)
            self.context = context

    monkeypatch.setattr(email_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP, FakeSMTPSSL


@pytest.fixture()
def profile_with_email(db):  # noqa: ARG001 - db pins the database target
    pid = str(uuid.uuid4())
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status)"
                " VALUES (%s, %s, 'P8 Email', 'VIEWER', 'APPROVED')",
                (pid, f"{pid[:8]}@acms.test"),
            )
        conn.commit()
    finally:
        conn.close()
    yield pid
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM profiles WHERE id = %s", (pid,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def profile_without_email(db):  # noqa: ARG001
    pid = str(uuid.uuid4())
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status)"
                " VALUES (%s, NULL, 'P8 NoEmail', 'VIEWER', 'APPROVED')",
                (pid,),
            )
        conn.commit()
    finally:
        conn.close()
    yield pid
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM profiles WHERE id = %s", (pid,))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("0", False),
        ("no", False),
        ("", False),
        ("maybe", False),
    ],
)
def test_email_enabled_parses_the_flag(monkeypatch, value, expected):
    monkeypatch.setenv("EMAIL_ENABLED", value)
    assert email_mod.email_enabled() is expected


def test_send_is_off_unless_expactly_enabled(monkeypatch):
    monkeypatch.delenv("EMAIL_ENABLED", raising=False)
    assert email_mod.send_notification_email(uuid.uuid4(), "ACCOUNT_APPROVED", "hi") is False


def test_non_allowlisted_types_are_never_emailed(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")
    assert email_mod.send_notification_email(uuid.uuid4(), "SOMETHING_ELSE", "hi") is False
    # The allow-list is deliberately short and closed.
    assert "SOMETHING_ELSE" not in email_mod.EMAIL_TYPES
    assert email_mod.EMAIL_TYPES == {
        "ACCOUNT_APPROVED",
        "ACCOUNT_REJECTED",
        "JOB_ASSIGNED",
        "MOVEMENT_APPROVED",
        "MOVEMENT_REJECTED",
    }


def test_lookup_returns_the_profile_address(profile_with_email):
    assert email_mod._recipient_email(profile_with_email) == f"{profile_with_email[:8]}@acms.test"


def test_lookup_returns_none_without_a_row():
    assert email_mod._recipient_email(uuid.uuid4()) is None


def test_lookup_returns_none_for_a_blank_address(profile_without_email):
    assert email_mod._recipient_email(profile_without_email) is None


def test_notification_email_is_delivered_over_smtp(monkeypatch, smtp, profile_with_email):
    fake, _ = smtp
    monkeypatch.setenv("EMAIL_ENABLED", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_USER", "mailer")
    monkeypatch.setenv("SMTP_PASSWORD", "not-a-real-password")
    monkeypatch.setenv("SMTP_FROM", "noreply@acms.test")
    monkeypatch.setenv("SMTP_TIMEOUT", "3")

    ok = email_mod.send_notification_email(
        profile_with_email, "ACCOUNT_APPROVED", "You are approved", "welcome back"
    )
    assert ok is True
    assert len(fake.instances) == 1
    client = fake.instances[0]
    assert (client.host, client.port, client.timeout) == ("smtp.test.local", 2525, 3)
    assert client.logged_in == ("mailer", "not-a-real-password")
    assert client.tls is True  # non-465 ports starttls by default
    assert len(client.sent) == 1
    msg = client.sent[0]
    assert msg["To"] == f"{profile_with_email[:8]}@acms.test"
    assert msg["Subject"] == "You are approved"
    assert msg["From"] == "noreply@acms.test"
    assert "welcome back" in msg.get_content()


def test_a_recipient_without_an_address_is_skipped(monkeypatch, smtp, profile_without_email):
    fake, _ = smtp
    monkeypatch.setenv("EMAIL_ENABLED", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")

    assert email_mod.send_notification_email(profile_without_email, "JOB_ASSIGNED", "job") is False
    assert fake.instances == [], "no message may be composed for a missing address"


def test_smtp_send_is_a_noop_without_a_host(smtp):
    fake, _ = smtp
    assert email_mod.smtp_send("someone@acms.test", "subject", "body") is False
    assert fake.instances == []


def test_port_465_uses_implicit_tls_and_skips_starttls(monkeypatch, smtp):
    fake, fake_ssl = smtp
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")
    monkeypatch.setenv("SMTP_PORT", "465")

    assert email_mod.smtp_send("someone@acms.test", "s", "b") is True
    assert len(fake_ssl.instances) == 1
    assert fake.instances == []
    assert fake_ssl.instances[0].tls is False
    assert fake_ssl.instances[0].sent


def test_starttls_can_be_disabled_and_login_skipped_without_a_user(monkeypatch, smtp):
    fake, _ = smtp
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")
    monkeypatch.setenv("SMTP_STARTTLS", "0")

    assert email_mod.smtp_send("someone@acms.test", "s", "b") is True
    assert fake.instances[0].tls is False
    assert fake.instances[0].logged_in is None


def test_default_from_address_is_used_when_unset(monkeypatch, smtp):
    fake, _ = smtp
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")

    assert email_mod.smtp_send("someone@acms.test", "s", "b") is True
    assert fake.instances[0].sent[0]["From"] == "noreply@acms.local"


def test_a_broken_lookup_never_raises(monkeypatch):
    """Email is auxiliary: a database failure must degrade to False."""

    def boom(_user_id):
        raise RuntimeError("database gone")

    monkeypatch.setenv("EMAIL_ENABLED", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")
    monkeypatch.setattr(email_mod, "_recipient_email", boom)
    assert email_mod.send_notification_email(uuid.uuid4(), "ACCOUNT_REJECTED", "no") is False


def test_an_smtp_failure_never_raises(monkeypatch, smtp, profile_with_email):
    class Exploding:
        def __init__(self, *args, **kwargs):
            raise OSError("connection refused")

    monkeypatch.setattr(email_mod.smtplib, "SMTP", Exploding)
    monkeypatch.setenv("EMAIL_ENABLED", "1")
    monkeypatch.setenv("SMTP_HOST", "smtp.test.local")

    assert email_mod.send_notification_email(profile_with_email, "JOB_ASSIGNED", "job") is False
