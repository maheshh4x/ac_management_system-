"""Phase 8.3 — tripwire: tests must never talk to a database other than
``TEST_DATABASE_URL``.

Why this exists
---------------
``app/config/database.py`` calls ``load_dotenv(..., override=True)`` on first
import, which *overwrites* ``DB_HOST``/``DB_PORT``/``DB_*`` in the environment
with the values in ``backend/.env``. In a real deployment that is harmless. In
the test suite it means the ordering of two lines decides whether a test run
reads the throwaway container or the **live** database:

  setenv(TEST_DATABASE_URL parts)  then  import app.config.database
      -> load_dotenv clobbers -> tests silently hit .env's database

That failure mode is silent: routes still answer, they just answer from the
wrong schema (observed in Phase 8 as ``column a.qr_version does not exist``,
because the live database was behind on its migrations).

``tests/conftest.py::db`` now imports the module *before* the setenv calls to
make the ordering deterministic, and this file proves it: the app's actual
connection is compared against TEST_DATABASE_URL, server side.

Nothing here writes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="needs a live database — set TEST_DATABASE_URL"
)


@pytest.fixture()
def db_target(db, monkeypatch):  # noqa: ARG001 — db fixes the env the way tests use it
    """The host/port/database the app's own pool actually connects to."""
    from app.config.database import get_db_connection

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT inet_server_addr()::text, inet_server_port(), current_database()")
            addr, server_port, name = cur.fetchone()
            params = conn.get_dsn_parameters()
    finally:
        conn.close()
    return {
        "addr": addr,
        "server_port": server_port,
        "database": name,
        "host_from_dsn": params.get("host"),
        "port_from_dsn": params.get("port"),
    }


def test_app_connects_to_the_test_database(db_target):
    """The *client* target (DSN host/port) must be TEST_DATABASE_URL.

    Note ``inet_server_port()`` is compared only for sanity: when the test
    database runs in Docker its own listening port (5432) differs from the
    published host port we dial (5433), so the DSN is the authoritative
    answer and the server port just has to be a real Postgres port.
    """
    expected = urlparse(os.environ["TEST_DATABASE_URL"])

    failures = []
    if db_target["database"] != (expected.path or "/postgres").lstrip("/"):
        failures.append(
            f"database {db_target['database']!r} != {(expected.path or '/postgres').lstrip('/')!r}"
        )
    if int(db_target["port_from_dsn"]) != (expected.port or 5432):
        failures.append(f"DSN port {db_target['port_from_dsn']} != {expected.port or 5432}")
    if db_target["server_port"] is None:
        failures.append("not connected over TCP (unix socket?) — DSN did not take effect")
    assert not failures, "the app is pointed at the wrong database:\n" + "\n".join(failures)


def test_app_is_not_using_the_dotenv_database(db_target):
    """``load_dotenv(override=True)`` must not have won over TEST_DATABASE_URL.

    .env points at the deployed database. A test run that reaches it would
    read (and potentially write) production data with test fixtures.
    """
    env_host = os.getenv("DB_HOST")  # already the TEST_DATABASE_URL host, per the db fixture
    expected = urlparse(os.environ["TEST_DATABASE_URL"]).hostname
    assert env_host == expected, f"DB_HOST={env_host!r} was not overridden with {expected!r}"

    # Server side proof: the peer we are actually talking to is the expected host.
    resolved = db_target["host_from_dsn"]
    assert resolved == expected, f"connected to {resolved!r}, expected {expected!r}"

    # Belt and braces: never the deployed pooler, whatever .env says.
    assert "supabase" not in (resolved or "").lower(), (
        f"a test run is connected to the deployed database ({resolved}); "
        "TEST_DATABASE_URL did not override backend/.env"
    )


def test_test_database_has_the_full_migration_set(db_target):
    """A schema behind the code produces confusing 500s instead of clear errors.

    The suite is written against migrations 001..012; if the throwaway
    database only has a subset (as the live one does), assertions about
    qr_version / audit redaction / report indexes fail far from their cause.
    """
    from app.config.database import get_db_connection

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            applied = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    # Mirror tests/test_migrate.py: numbered migrations only; the Supabase auth
    # trigger is applied out-of-band and is deliberately not in schema_migrations.
    expected = sorted(
        p.name
        for p in (Path(__file__).resolve().parents[1] / "migrations").glob("*.sql")
        if p.name[:3].isdigit()
    )
    missing = [m for m in expected if m not in applied]
    assert not missing, (
        f"test database is missing migrations {missing} (has {len(applied)}, "
        f"expected {len(expected)}) — run `python -m app.migrate up` against it"
    )
