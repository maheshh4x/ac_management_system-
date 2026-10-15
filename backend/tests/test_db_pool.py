"""Regression tests for the connection-pool race (CR-ITEM-1.1 … 1.3).

Uses a stub pool so no live database is required. Verifies:
- QA-1.1: concurrent first requests create exactly one pool
- QA-1.2: every connection is returned to its owning pool
- QA-1.3: double close / context-exit returns the connection only once
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

import app.config.database as dbmod  # noqa: E402


class _FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakePool:
    instances = []

    def __init__(self, *args, **kwargs):
        self.returned = []
        self.lock = threading.Lock()
        self.closed = False
        _FakePool.instances.append(self)

    def getconn(self):
        return _FakeConn()

    def putconn(self, conn):
        with self.lock:
            # Mimic psycopg2: reject connections this pool never handed out.
            self.returned.append(conn)


@pytest.fixture()
def stub_pool(monkeypatch):
    _FakePool.instances.clear()
    monkeypatch.setattr(dbmod.pool, "ThreadedConnectionPool", _FakePool)
    dbmod._pool = None
    dbmod._pool_permits = None
    dbmod._pool_permits_size = None
    yield _FakePool
    dbmod._pool = None
    dbmod._pool_permits = None
    dbmod._pool_permits_size = None


def test_concurrent_init_creates_single_pool(stub_pool):
    barrier = threading.Barrier(16)
    pools_seen = []

    def worker():
        barrier.wait()
        pools_seen.append(dbmod._get_pool())

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(stub_pool.instances) == 1
    assert all(p is stub_pool.instances[0] for p in pools_seen)


def test_connection_returns_to_owning_pool(stub_pool):
    owner = dbmod._get_pool()
    wrapper = dbmod.get_db_connection()
    assert wrapper._owner_pool is owner
    wrapper.close()
    assert len(owner.returned) == 1


def test_double_close_is_idempotent(stub_pool):
    dbmod._get_pool()
    wrapper = dbmod.get_db_connection()
    owner = wrapper._owner_pool
    wrapper.close()
    wrapper.close()  # plain double-close
    with wrapper:  # context-manager exit after close
        pass
    assert len(owner.returned) == 1


def test_context_manager_returns_once(stub_pool):
    dbmod._get_pool()
    with dbmod.get_db_connection() as conn:
        owner = conn._owner_pool
    assert len(owner.returned) == 1


# ── Transaction contract (industrial hardening) ─────────────────────────────


def test_close_rolls_back_instead_of_committing(stub_pool):
    dbmod._get_pool()
    wrapper = dbmod.get_db_connection()
    raw = wrapper._conn
    wrapper.close()  # no explicit commit → cleanup must roll back, never commit
    assert raw.commits == 0
    assert raw.rollbacks == 1


def test_context_manager_commits_only_on_success(stub_pool):
    dbmod._get_pool()
    wrapper = dbmod.get_db_connection()
    raw = wrapper._conn
    with wrapper:
        pass
    assert raw.commits == 1
    assert raw.rollbacks == 0


def test_context_manager_rolls_back_on_exception(stub_pool):
    dbmod._get_pool()
    wrapper = dbmod.get_db_connection()
    raw = wrapper._conn
    with pytest.raises(ValueError):
        with wrapper:
            raise ValueError("simulated request failure")
    assert raw.commits == 0
    assert raw.rollbacks == 1


def test_close_after_with_block_does_not_touch_returned_connection(stub_pool):
    dbmod._get_pool()
    wrapper = dbmod.get_db_connection()
    raw = wrapper._conn
    with wrapper:
        pass
    wrapper.close()  # already returned: must not run a second rollback/commit
    assert raw.commits == 1
    assert raw.rollbacks == 0


# ── Bounded pool acquisition (deployment-reliability audit, P0) ─────────────
# getconn() on an exhausted psycopg2 pool blocks indefinitely; the wrapper
# must fail fast with PoolAcquireTimeout and must recycle the permit so the
# pool recovers after checkout holders return their connections.


def test_acquire_timeout_fails_fast(monkeypatch, stub_pool):
    """Exhausted pool: the third checkout fails within the budget, not never."""
    import time

    monkeypatch.setenv("DB_POOL_MAX", "2")
    monkeypatch.setenv("DB_POOL_ACQUIRE_TIMEOUT_S", "0.2")
    dbmod._pool = None
    dbmod._pool_permits = None
    held = [dbmod.get_db_connection(), dbmod.get_db_connection()]
    start = time.monotonic()
    with pytest.raises(dbmod.PoolAcquireTimeout):
        dbmod.get_db_connection()
    assert time.monotonic() - start < 5
    for conn in held:
        conn.close()


def test_acquire_timeout_recovers_after_release(monkeypatch, stub_pool):
    monkeypatch.setenv("DB_POOL_MAX", "1")
    monkeypatch.setenv("DB_POOL_ACQUIRE_TIMEOUT_S", "5")
    dbmod._pool = None
    dbmod._pool_permits = None
    first = dbmod.get_db_connection()
    import threading as _threading

    outcome = {}

    def waiter():
        try:
            with dbmod.get_db_connection():
                outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    t = _threading.Thread(target=waiter)
    t.start()
    _threading.Event().wait(0.3)
    first.close()  # returning the only connection must release its permit
    t.join(timeout=10)
    assert outcome.get("ok") is True, outcome


def test_acquire_timeout_nonpositive_disables_budget(monkeypatch, stub_pool):
    """DB_POOL_ACQUIRE_TIMEOUT_S<=0 preserves the pre-audit behavior: no
    fast failure, and checkout/close must not corrupt the semaphore (no
    permit is taken, so none may be released)."""
    monkeypatch.setenv("DB_POOL_MAX", "2")
    monkeypatch.setenv("DB_POOL_ACQUIRE_TIMEOUT_S", "0")
    dbmod._pool = None
    dbmod._pool_permits = None
    conns = [dbmod.get_db_connection() for _ in range(3)]  # beyond maxconn: no error
    for conn in conns:
        conn.close()
    assert dbmod._pool_permits._value == 2  # untouched: nothing leaked or minted
