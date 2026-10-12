"""Read-path statement budgets — performance regression tests (issue-2 fix).

Why: on a remote database every sequential ``cursor.execute`` pays one full
network round trip, so the *statement count* of a request is the multiplier
on its latency. The dashboard page fires /me + /assets + /dashboard/summary +
/assets/facets + /reports/maintenance-trend together; before this fix the
summary ran 10 statements and facets 7 (≈ 891 ms / 671–1109 ms observed in
production logs).

These tests pin each hot endpoint's total statement count — profile lookup
(``require_auth`` → ``_lookup_profile``) included — so the consolidation
cannot silently regress. Values are pinned by the existing suite
(test_assets_v1, test_dashboard_all_filter, test_reports_*): this file only
counts round trips and reports wall time.
"""

from __future__ import annotations

import time

import pytest

import app.api.v1.assets as assets_mod
import app.api.v1.campus as campus_mod
import app.api.v1.reports as reports_mod
import app.api.v1.system as system_mod
import app.config.database as db_mod

# ── statement counting (test-local instrumentation) ────────────────────────

COUNTER = {"n": 0}


class _CountingCursor:
    def __init__(self, cursor):
        self._c = cursor

    def execute(self, *args, **kwargs):
        COUNTER["n"] += 1
        return self._c.execute(*args, **kwargs)

    def __enter__(self):
        self._c.__enter__()
        return self

    def __exit__(self, *exc):
        return self._c.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._c, name)

    def __iter__(self):
        return iter(self._c)


class _CountingConn:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        return _CountingCursor(self._conn.cursor(*args, **kwargs))

    def commit(self):
        COUNTER["n"] += 1
        return self._conn.commit()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture()
def count_stmts(monkeypatch):
    """Count every statement the request path issues (incl. the auth lookup).

    ``assets``/``system``/``campus`` bind ``get_db_connection`` at import
    time (patch their module attributes); ``jwt._lookup_profile`` imports it
    late from ``app.config.database`` (patch the origin module).
    """
    real = db_mod.get_db_connection

    def wrapper():
        return _CountingConn(real())

    monkeypatch.setattr(db_mod, "get_db_connection", wrapper)
    monkeypatch.setattr(assets_mod, "get_db_connection", wrapper)
    monkeypatch.setattr(system_mod, "get_db_connection", wrapper)
    monkeypatch.setattr(campus_mod, "get_db_connection", wrapper)
    monkeypatch.setattr(reports_mod, "get_db_connection", wrapper)
    COUNTER["n"] = 0
    return COUNTER


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


def _measure(client, count_stmts, path: str) -> tuple[int, float]:
    COUNTER["n"] = 0
    start = time.perf_counter()
    r = client.get(path)
    wall_ms = (time.perf_counter() - start) * 1000
    assert r.status_code == 200, (path, r.status_code, r.get_json())
    return COUNTER["n"], wall_ms


# ── budgets (statements include the 1-profile auth lookup) ─────────────────


def test_me_is_a_single_profile_lookup(client, count_stmts):
    statements, wall = _measure(client, count_stmts, "/api/v1/me")
    print(f"\n/api/v1/me: {statements} stmts, {wall:.1f} ms")
    # JWT decode + one indexed profile read — nothing else may run.
    assert statements <= 1, statements


def test_assets_list_budget(client, count_stmts):
    statements, wall = _measure(client, count_stmts, "/api/v1/assets?page_size=10")
    print(f"/api/v1/assets: {statements} stmts, {wall:.1f} ms")
    # profile + COUNT + page fetch.
    assert statements <= 3, statements


def test_facets_budget(client, count_stmts):
    statements, wall = _measure(client, count_stmts, "/api/v1/assets/facets")
    print(f"/api/v1/assets/facets: {statements} stmts, {wall:.1f} ms")
    # profile + ONE consolidated UNION ALL over the seven facets.
    assert statements <= 2, statements


def test_dashboard_summary_budget(client, count_stmts):
    statements, wall = _measure(client, count_stmts, "/api/v1/dashboard/summary")
    print(f"/api/v1/dashboard/summary: {statements} stmts, {wall:.1f} ms")
    # profile + KPIs + MTTR + dimensions (UNION ALL) + overdue + my_jobs.
    assert statements <= 6, statements


def test_campus_tree_budget(client, count_stmts):
    statements, wall = _measure(client, count_stmts, "/api/v1/campus/tree")
    print(f"/api/v1/campus/tree: {statements} stmts, {wall:.1f} ms")
    # profile + colleges + blocks + floors + rooms + departments.
    assert statements <= 6, statements


def test_maintenance_trend_budget(client, count_stmts):
    statements, wall = _measure(client, count_stmts, "/api/v1/reports/maintenance-trend")
    print(f"/api/v1/reports/maintenance-trend: {statements} stmts, {wall:.1f} ms")
    # profile + the grouped trend queries (rows/stats/priority/meta) — this
    # endpoint was never in the slow list; the budget only pins its shape.
    assert statements <= 5, statements
