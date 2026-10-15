"""Regression tests for gunicorn sizing defaults (reliability audit, P0).

Importing gunicorn.conf.py executes it, so each case loads it in a fresh
module namespace with a controlled environment. Verifies:
- the computed default worker count is capped (never cpu_count*2+1 on big
  machines), keeping workers x DB_POOL_MAX within a safe envelope;
- an explicit WEB_CONCURRENCY is always honored (no silent clamping);
- oversized explicit sizing prints a warning instead of failing silently.
"""

import importlib.util
import sys
from pathlib import Path

CONF_PATH = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"


def load_conf(monkeypatch, env: dict):
    for var in ("WEB_CONCURRENCY", "WEB_THREADS", "DB_POOL_MAX", "BIND", "LOG_LEVEL"):
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # gunicorn is intentionally absent on Windows (POSIX-only); the conf
    # file itself needs only os + multiprocessing.
    sys.modules.pop("gunicorn_conf_under_test", None)
    spec = importlib.util.spec_from_file_location("gunicorn_conf_under_test", CONF_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_workers_are_capped(monkeypatch, capsys):
    module = load_conf(monkeypatch, {})
    import multiprocessing

    formula = multiprocessing.cpu_count() * 2 + 1
    assert module.workers == min(formula, 3)
    assert module.workers <= 3


def test_explicit_concurrency_is_honored(monkeypatch, capsys):
    module = load_conf(monkeypatch, {"WEB_CONCURRENCY": "4", "DB_POOL_MAX": "10"})
    assert module.workers == 4


def test_oversized_product_warns(monkeypatch, capsys):
    load_conf(monkeypatch, {"WEB_CONCURRENCY": "8", "DB_POOL_MAX": "16"})
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "128" in out


def test_safe_product_is_quiet(monkeypatch, capsys):
    load_conf(monkeypatch, {"WEB_CONCURRENCY": "2", "DB_POOL_MAX": "10"})
    out = capsys.readouterr().out
    assert "WARNING" not in out
