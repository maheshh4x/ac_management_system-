"""Gunicorn configuration (Phase 1.2, deploy target for Phase 9).

Used by:  gunicorn -c gunicorn.conf.py wsgi:app
(gunicorn is POSIX-only; on Windows run Flask's dev server via run.py for
local development, or deploy via Docker/WSL.)
"""
import multiprocessing
import os

bind = os.getenv("BIND", "0.0.0.0:5000")
workers = int(os.getenv("WEB_CONCURRENCY", str(multiprocessing.cpu_count() * 2 + 1)))
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
