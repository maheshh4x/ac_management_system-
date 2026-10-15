"""
database.py — psycopg2 connection pool for the AC Management System.

Uses ThreadedConnectionPool so that bulk import (370 rows) re-uses
a small set of connections instead of opening one per row.

TRANSACTION CONTRACT (explicit — nothing auto-commits):
  - ``with get_db_connection() as conn:`` → commit on clean exit,
    rollback if an exception leaves the block.
  - Plain usage ``conn = get_db_connection(); ...; conn.close()`` → the
    caller MUST call ``conn.commit()`` on success. ``close()`` never
    commits: any leftover uncommitted work is rolled back before the
    connection is returned to its owning pool (exactly once).
"""

import logging
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from psycopg2 import pool

# D9: override=False — real process env wins over .env. With override=True,
# any standalone CLI that exported DB_* (e.g. pointing at the TEST database)
# was silently re-pointed at the live cluster at import, and migrations ran
# against production. When the process env is empty (deploys, plain local
# runs) the .env values still fill in exactly as before.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

logger = logging.getLogger(__name__)

_pool: pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()

# Bounded pool-acquisition permits (deployment-reliability audit, P0).
# psycopg2's ThreadedConnectionPool.getconn() blocks indefinitely when every
# pooled connection is checked out, so one slow dependency could park a
# gthread worker until Gunicorn's 30 s timeout killed it — surfacing as
# multi-second p95 latency with no actionable error. A counting semaphore
# sized to maxconn bounds that wait: a request that cannot get a connection
# within DB_POOL_ACQUIRE_TIMEOUT_S fails fast with PoolAcquireTimeout
# (mapped by the existing generic handlers to the endpoint's usual 5xx
# envelope — response semantics unchanged) instead of hanging a worker.
# Permits are released exactly once alongside the connection, on both the
# clean-return and the discard (close=True) paths.
_pool_permits: threading.Semaphore | None = None
_pool_permits_size: int | None = None


class PoolAcquireTimeout(TimeoutError):
    """No pooled DB connection became available within the acquire budget."""


def _acquire_timeout_s() -> float:
    try:
        return float(os.getenv("DB_POOL_ACQUIRE_TIMEOUT_S", "5"))
    except ValueError:
        return 5.0


def _get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        with _pool_lock:
            if _pool is None or _pool.closed:
                statement_timeout_ms = int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "30000"))
                options = f"-c statement_timeout={statement_timeout_ms}" f" -c application_name=acms-api"
                minconn = int(os.getenv("DB_POOL_MIN", "2"))
                maxconn = int(os.getenv("DB_POOL_MAX", "16"))
                keepalives = int(os.getenv("DB_KEEPALIVES", "1"))
                keepalives_idle = int(os.getenv("DB_KEEPALIVES_IDLE", "30"))
                keepalives_interval = int(os.getenv("DB_KEEPALIVES_INTERVAL", "10"))
                keepalives_count = int(os.getenv("DB_KEEPALIVES_COUNT", "3"))
                _pool = pool.ThreadedConnectionPool(
                    minconn=minconn,
                    maxconn=maxconn,
                    host=os.getenv("DB_HOST"),
                    port=os.getenv("DB_PORT"),
                    database=os.getenv("DB_NAME"),
                    user=os.getenv("DB_USER"),
                    password=os.getenv("DB_PASSWORD"),
                    sslmode=os.getenv("DB_SSLMODE", "prefer"),
                    options=options,
                    keepalives=keepalives,
                    keepalives_idle=keepalives_idle,
                    keepalives_interval=keepalives_interval,
                    keepalives_count=keepalives_count,
                )
                logger.info(
                    "PostgreSQL connection pool created (min=%s, max=%s, keepalives=%s)",
                    minconn,
                    maxconn,
                    keepalives,
                )
                global _pool_permits, _pool_permits_size
                if _pool_permits is None or _pool_permits_size != maxconn:
                    _pool_permits = threading.Semaphore(maxconn)
                    _pool_permits_size = maxconn
    return _pool


class _PooledConnection:
    """
    Thin wrapper so that:
      - ``with get_db_connection() as conn:`` works (context-manager)
      - ``conn = get_db_connection(); conn.close()`` works (plain usage)
    The underlying psycopg2 connection is returned to the pool on exit/close.
    """

    def __init__(self, owner_pool, conn, permit=None):
        self._owner_pool = owner_pool
        self._conn = conn
        self._returned = False
        # The semaphore permit held for this checkout (None when acquisition
        # is unbudgeted); released exactly once in _return_to_pool / the
        # discard path below.
        self._permit = permit

    def _return_to_pool(self):
        if self._returned:
            return
        self._returned = True
        self._owner_pool.putconn(self._conn)
        if self._permit is not None:
            self._permit.release()
            self._permit = None

    # ── delegate all attribute access to the real connection ──────────────────
    def __getattr__(self, name):
        return getattr(self._conn, name)

    # ── context-manager support ───────────────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._returned:  # already closed: do not touch the returned conn
            return False
        if exc_type is None:
            try:
                self._conn.commit()  # successful request → commit
            except Exception:
                self._conn.rollback()
                raise
        else:
            self._conn.rollback()  # unexpected exception → rollback
        self._return_to_pool()
        return False

    # ── plain-usage close ─────────────────────────────────────────────────────
    def close(self):
        """Return the connection to its owning pool exactly once.

        NEVER commits implicitly — a cleanup path must not decide to persist
        a half-finished request. Any leftover uncommitted transaction is
        rolled back so the next borrower always starts clean.
        """
        if self._returned:
            return
        try:
            self._conn.rollback()
        except Exception:
            # Broken connection: don't hand it back to the pool.
            logger.warning(
                "rollback during connection cleanup failed; discarding connection",
                exc_info=True,
            )
            self._returned = True
            self._owner_pool.putconn(self._conn, close=True)
            if self._permit is not None:
                self._permit.release()
                self._permit = None
            return
        self._return_to_pool()

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)


def get_db_connection() -> _PooledConnection:
    """Return a pooled connection.  Can be used as a context manager or
    by calling .close() explicitly when done.

    Waits at most DB_POOL_ACQUIRE_TIMEOUT_S (default 5 s; <= 0 blocks
    indefinitely, the pre-audit behavior) for a free connection, then
    raises PoolAcquireTimeout. Never retries: callers surface the error
    through their existing handler, and clients must not retry timed-out
    mutations (a timed-out request may still be running server-side).
    """
    owner_pool = _get_pool()
    permits = _pool_permits
    budget = _acquire_timeout_s()
    held = None
    if permits is not None and budget > 0:
        if not permits.acquire(timeout=budget):
            raise PoolAcquireTimeout(
                f"no DB connection available within {budget:g}s "
                "(pool exhausted; see DB_POOL_MAX / WEB_CONCURRENCY sizing)"
            )
        held = permits
    try:
        conn = owner_pool.getconn()
    except Exception:
        if held is not None:
            held.release()
        raise
    conn.autocommit = False
    return _PooledConnection(owner_pool, conn, held)
