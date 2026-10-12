"""Shared pytest fixtures (Phase 1).

After Phase 1.2 there is no anonymous demo mode and no AUTH_DISABLED bypass
outside tests. Tests authenticate by injecting a *test-only* JWT secret
(JWT_SECRET=<test value>) and signing HS256 tokens against it — the fixture
below is the sanctioned way to simulate a logged-in user in this suite.

Tests that need a live database must call ``db_required()`` and honor
TEST_DATABASE_URL (throwaway Postgres, e.g. `docker run -p 5432 postgres:16`).
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import jwt as pyjwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEST_SECRET = "unit-test-jwt-secret"
TEST_SUPABASE_URL = "https://unit-test.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"
TEST_USER_ID = "11111111-1111-1111-1111-111111111111"

os.environ.setdefault("APP_ENV", "development")


@pytest.fixture()
def client(monkeypatch):
    """App client with NO verification key configured (fully fail-closed)."""
    for var in ("SUPABASE_JWT_SECRET", "JWT_SECRET", "SUPABASE_JWKS_URL", "SUPABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture()
def secret_client(monkeypatch):
    """App client with the test-only HS256 secret configured."""
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
    return app.test_client()


def make_token(
    role="VIEWER",
    secret=TEST_SECRET,
    sub=TEST_USER_ID,
    iss=TEST_ISSUER,
    aud="authenticated",
    alg="HS256",
    key=None,
    **extra_claims,
):
    """Build a fully-claimed test token (iss/aud/exp/sub are required)."""
    now = int(time.time())
    claims = {
        "sub": sub,
        "email": f"{str(sub)[:8]}@acms.test",
        "aud": aud,
        "iss": iss,
        "iat": now - 10,
        "exp": now + 3600,
        "app_metadata": {"role": role} if role else {},
    }
    claims.update(extra_claims)
    return pyjwt.encode(claims, key if key is not None else secret, algorithm=alg)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def db_required():
    return pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"),
        reason="needs a live database — set TEST_DATABASE_URL",
    )


class AuthClient:
    """Flask test-client wrapper that attaches a Bearer token to every call."""

    def __init__(self, client, token):
        self._client = client
        self._token = token

    def _merge(self, kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Authorization", f"Bearer {self._token}")
        kwargs["headers"] = headers
        return kwargs

    def get(self, url, **kwargs):
        return self._client.get(url, **self._merge(kwargs))

    def post(self, url, **kwargs):
        return self._client.post(url, **self._merge(kwargs))

    def put(self, url, **kwargs):
        return self._client.put(url, **self._merge(kwargs))

    def patch(self, url, **kwargs):
        return self._client.patch(url, **self._merge(kwargs))

    def delete(self, url, **kwargs):
        return self._client.delete(url, **self._merge(kwargs))


def _point_at(url: str, monkeypatch) -> None:
    """Set ``DB_*`` from a Postgres URL (importing app.config.database first)."""
    from urllib.parse import urlparse

    u = urlparse(url)
    monkeypatch.setenv("DB_HOST", u.hostname or "localhost")
    monkeypatch.setenv("DB_PORT", str(u.port or 5432))
    monkeypatch.setenv("DB_NAME", (u.path or "/postgres").lstrip("/"))
    monkeypatch.setenv("DB_USER", u.username or "postgres")
    monkeypatch.setenv("DB_PASSWORD", u.password or "")
    monkeypatch.setenv("DB_SSLMODE", "disable")


@pytest.fixture(autouse=True)
def _never_reach_the_dotenv_database(monkeypatch):
    """Pin every test to TEST_DATABASE_URL, whether or not it asks for ``db``.

    ``app/config/database.py`` runs ``load_dotenv(..., override=True)`` on
    first import, which plants the *live* ``DB_*`` values in ``os.environ``.
    A test that touches the database without requesting the ``db`` fixture
    used to inherit them and silently query the deployed database — Phase 8
    found exactly that: the ``secret_client``-only users tests were reading
    production, and ``register-profile`` writes to whichever database it
    lands on. Pinning unconditionally closes that hole for every test.

    Import order matters: ``app.config.database`` must be imported *before*
    any ``DB_*`` setenv below, because its module-level ``load_dotenv`` would
    otherwise overwrite them afterwards. See tests/test_db_target.py.
    """
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        return
    from app.config import database as dbmod  # noqa: F401  — import order is the point

    _point_at(url, monkeypatch)


@pytest.fixture()
def db(_never_reach_the_dotenv_database):
    """Point the app at the throwaway TEST_DATABASE_URL (skip without one).

    Start one with: docker run -e POSTGRES_PASSWORD=postgres -p 5432:5432 postgres:16
    then: $env:TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/postgres"
    """
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("needs a live database — set TEST_DATABASE_URL")

    from app.config import database as dbmod

    if dbmod._pool is not None:
        try:
            dbmod._pool.closeall()
        except Exception:
            pass
        dbmod._pool = None
    _seed_shared_test_data()
    yield


def _seed_shared_test_data():
    """Idempotent baseline every DB test expects (non-fatal if the schema is
    not migrated yet — those tests fail with their own clear errors anyway).

    Campus chain the import tests resolve against (003 seeds the college):
    NITTTR Chennai -> Main Academic Block -> Floor 1 ->
      {ROOM-102 CSE Computer Lab 1 / Computer Science,
       ROOM-105 HOD Cabin / Administration}

    NOTE: deliberately NO profile row for TEST_USER_ID here — profiles.role is
    authoritative over the JWT claim (app/auth/jwt.py), and the role-matrix
    tests rely on tokens WITHOUT a profile row. test_audit creates its own
    short-lived profile for the audit_logs user FK.
    """
    statements = (
        "INSERT INTO blocks (block_id, college_id, block_name) VALUES "
        "('MAIN-ACADEMIC-BLOCK', 'NITTTR-CHENNAI', 'Main Academic Block') "
        "ON CONFLICT (block_id) DO NOTHING",
        "INSERT INTO departments (department_id, department_name) VALUES "
        "('DEPT-COMPUTER-SCIENCE', 'Computer Science') ON CONFLICT (department_id) DO NOTHING",
        "INSERT INTO departments (department_id, department_name) VALUES "
        "('DEPT-ADMINISTRATION', 'Administration') ON CONFLICT (department_id) DO NOTHING",
        "INSERT INTO floors (floor_id, block_id, floor_name, floor_number) VALUES "
        "('MAIN-ACADEMIC-BLOCK-FLR-1', 'MAIN-ACADEMIC-BLOCK', 'Floor 1', 1) "
        "ON CONFLICT (floor_id) DO NOTHING",
        "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type) VALUES "
        "('ROOM-102', 'MAIN-ACADEMIC-BLOCK-FLR-1', 'DEPT-COMPUTER-SCIENCE', "
        "'CSE Computer Lab 1', 'Lab') ON CONFLICT (room_id) DO NOTHING",
        "INSERT INTO rooms (room_id, floor_id, department_id, room_name) VALUES "
        "('ROOM-105', 'MAIN-ACADEMIC-BLOCK-FLR-1', 'DEPT-ADMINISTRATION', 'HOD Cabin') "
        "ON CONFLICT (room_id) DO NOTHING",
    )
    try:
        from app.config.database import get_db_connection

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                for statement in statements:
                    cur.execute(statement)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        logging.getLogger(__name__).warning("shared test-data seed skipped", exc_info=True)


@pytest.fixture()
def db_admin_client(db, monkeypatch):
    """Authed SUPER_ADMIN client bound to the test database."""
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
    return AuthClient(app.test_client(), make_token("SUPER_ADMIN"))
