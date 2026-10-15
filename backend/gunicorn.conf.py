"""Gunicorn configuration (Phase 1.2, deploy target for Phase 9).

Used by:  gunicorn -c gunicorn.conf.py wsgi:app
(gunicorn is POSIX-only; on Windows run Flask's dev server via run.py for
local development, or deploy via Docker/WSL.)
"""
import multiprocessing
import os

bind = os.getenv("BIND", "0.0.0.0:5000")


def _default_workers() -> int:
    """Conservative default: min(formula, cap).

    The historic default (cpu_count*2+1) is a CPU-bound heuristic; this API
    is I/O-bound and every worker process owns a full DB pool of
    DB_POOL_MAX connections, so total connections = workers x DB_POOL_MAX
    must stay below the database's max_connections (Supabase pooler). The
    cap applies ONLY to the computed default — an explicit WEB_CONCURRENCY
    is always honored (with a warning below when the product looks unsafe).
    """
    try:
        formula = multiprocessing.cpu_count() * 2 + 1
    except NotImplementedError:
        formula = 3
    return min(formula, 3)


workers = int(os.getenv("WEB_CONCURRENCY", str(_default_workers())))
# The API is I/O-bound: every request waits on 1-10 PostgreSQL round trips
# (measured: /me = 1, /dashboard/summary = 5, /assets/facets = 2 statements;
# ~43 ms remote RTT each). The default *sync* worker class serves one
# request at a time per worker, so the dashboard's parallel burst
# (/me + /assets + /summary + /facets + /trend) queued behind each other —
# the observed multi-second /me latency (5234 ms) with all other endpoints
# showing 300-1100 ms. gthread workers overlap requests during DB waits;
# per-worker connection pools are unchanged (keep
# WEB_CONCURRENCY x DB_POOL_MAX below the database's max_connections).
worker_class = "gthread"
threads = int(os.getenv("WEB_THREADS", "8"))
timeout = 30
graceful_timeout = 30
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()

# Fail-visible sizing guard: workers x DB_POOL_MAX must stay below the
# database's max_connections (see deployment-checklist.md). Gunicorn runs
# this file at startup, so the warning lands in the deploy logs — it never
# changes behavior, only visibility. Explicit WEB_CONCURRENCY is honored.
try:
    _pool_max = int(os.getenv("DB_POOL_MAX", "16"))
    if workers * _pool_max > 60:
        print(
            f"WARNING: WEB_CONCURRENCY({workers}) x DB_POOL_MAX({_pool_max}) = "
            f"{workers * _pool_max} connections may exceed the database limit; "
            "see deployment-checklist.md (pool sizing).",
            flush=True,
        )
except ValueError:
    pass
