"""Cache-behavior verification for the latency work (no live database).

- Profile cache (``app.auth.jwt``): TTL hit avoids the DB round trip,
  expiry/invalidation re-queries, and a role change becomes visible after
  invalidation (stale-then-fresh authorization consistency).
- Signed-URL cache (``app.api.v1.campus``): cache hit avoids the external
  Storage HTTP call; failures degrade to metadata without URLs.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import jwt as jwt_mod  # noqa: E402
import app.api.v1.campus as campus_mod  # noqa: E402


class _FakeCursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, *args, **kwargs):
        self._conn.queries += 1

    def fetchone(self):
        return self._conn.row

    def close(self):
        pass


class _FakeConn:
    def __init__(self, row):
        self.row = row
        self.queries = 0

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self)

    def close(self):
        pass


PROFILE_ROW = (
    "11111111-1111-1111-1111-111111111111",
    "a@example.test",
    "A",
    "VIEWER",
    None,
    None,
    True,
    "APPROVED",
    None,
)


@pytest.fixture()
def stub_profile_db(monkeypatch):
    conn = _FakeConn(PROFILE_ROW)
    monkeypatch.setattr("app.config.database.get_db_connection", lambda: conn)
    jwt_mod.invalidate_profile_cache()
    yield conn
    jwt_mod.invalidate_profile_cache()


def test_profile_cache_hit_avoids_second_query(stub_profile_db):
    assert jwt_mod._lookup_profile("u-1") is not None
    assert jwt_mod._lookup_profile("u-1") is not None
    assert stub_profile_db.queries == 1


def test_profile_cache_expiry_requeries(monkeypatch, stub_profile_db):
    jwt_mod._lookup_profile("u-1")
    monkeypatch.setattr(jwt_mod, "_PROFILE_CACHE_TTL_SECONDS", 0.0)
    # TTL zero: every lookup re-queries.
    jwt_mod._lookup_profile("u-1")
    assert stub_profile_db.queries == 2


def test_profile_invalidation_reveals_role_change(stub_profile_db):
    first = jwt_mod._lookup_profile("u-1")
    assert first["role"] == "VIEWER"
    promoted = list(PROFILE_ROW)
    promoted[3] = "TECHNICIAN"
    stub_profile_db.row = tuple(promoted)
    # Still cached: stale role served until invalidated.
    assert jwt_mod._lookup_profile("u-1")["role"] == "VIEWER"
    jwt_mod.invalidate_profile_cache("u-1")
    assert jwt_mod._lookup_profile("u-1")["role"] == "TECHNICIAN"


def test_signed_url_cache_avoids_repeat_http(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://unit-test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append(path)
        return 200, b'{"signedURL": "/object/sign/bucket/p.png?token=t"}'

    monkeypatch.setattr(campus_mod, "_storage_request", fake_request)
    campus_mod._SIGNED_URL_CACHE.clear()
    first = campus_mod._sign_read_url("p.png")
    second = campus_mod._sign_read_url("p.png")
    assert first == second and first is not None
    assert len(calls) == 1
    campus_mod._SIGNED_URL_CACHE.clear()


def test_signed_url_failure_degrades_to_none(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://unit-test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")

    def failing_request(method, path, **kwargs):
        raise OSError("storage down")

    monkeypatch.setattr(campus_mod, "_storage_request", failing_request)
    campus_mod._SIGNED_URL_CACHE.clear()
    assert campus_mod._sign_read_url("p.png") is None


def test_signed_url_unconfigured_returns_none_without_http(monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    called = []
    monkeypatch.setattr(campus_mod, "_storage_request", lambda *a, **k: called.append(1))
    assert campus_mod._sign_read_url("p.png") is None
    assert called == []
