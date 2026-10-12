"""Phase 8.1 / 8.3 — SQL-injection probes on string filters, and pagination bounds.

Two independent guarantees are asserted here:

**Injection.** Every list route is swept with hostile payloads in each of the
string filters it can read, plus hostile payloads *as path segments*. Nothing
in this codebase should ever build SQL by concatenating a request value —
every filter is a ``%s`` bind (see ``_build_filters`` in assets.py and the
query builders in maintenance/movements/system/users). The sweep proves it
from the outside: a concatenation bug surfaces as a 500 or as a leaked
``psycopg2``/``syntax error`` string, both of which fail the test. The sweep
also re-checks table existence and row counts afterwards, so a payload that
did manage to run DDL would be caught even if no single response looked wrong.

``q='`` is additionally checked for *equivalence* with a literal that cannot
match anything: a parameterised ``ILIKE '%''%`` and an ``ILIKE '%zzz%'`` both
return zero rows, whereas a concatenated ``WHERE q = ''`` would either throw
or return every row.

**Bounds.** ``page``/``page_size`` are clamped (``max(1, ...)`` /
``min(200, ...)``) and rejected with ``INVALID_PAGINATION`` when they are not
integers — asserted on every paginated route, not just one, because the
parsing is duplicated per blueprint and a regression in one of them would
otherwise go unnoticed. ``sort`` is a whitelist in assets.py, and the QR batch
sheet is capped in code; both are asserted too.

Rate limiting: the limiter keys authenticated traffic on ``user:{id}``, so
each probe deliberately uses a *fresh JWT subject*. That keeps ~800 probes
from tripping ``qr-batch``/``public-asset`` buckets and reading a 429 as a
result. No profile row is created for these subjects (register-profile is
never called), so there is nothing to clean up.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.config.database import get_db_connection
from tests.conftest import TEST_ISSUER, TEST_SECRET, TEST_SUPABASE_URL, make_token

_METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")

# String filters that end up inside a WHERE clause somewhere in the API.
_STR_PARAMS = (
    "q",
    "search",
    "status",
    "block",
    "department",
    "manufacturer",
    "lifecycle",
    "priority",
    "assigned_to",
    "action",
    "entity_type",
    "user",
    "actor",
    "from",
    "to",
    "sort",
    "cursor",
    "ids",
)

# Deliberately nasty, but written so the intent is obvious when one fails.
_PROBES = (
    "'",
    '"',
    "\\",
    "'; DROP TABLE acs; --",
    "' OR '1'='1",
    "%' OR '1'='1",
    "1' UNION SELECT NULL --",
    "x'); DROP TABLE profiles; --",
    "admin'--",
    "||pg_sleep(0)||",
)

_LEAK_MARKERS = (
    "Traceback (most recent call last)",
    "psycopg2",
    "SQLSTATE",
    "syntax error at or near",
)

# The three free-text search filters, probed with every payload.
_FREE_TEXT = (
    ("/api/v1/maintenance", "q"),
    ("/api/v1/audit", "q"),
    ("/api/v1/assets", "search"),
    ("/api/v1/users", "search"),
)

# Routes that answer with a ``pagination`` sibling object.
_PAGINATED = (
    "/api/v1/assets",
    "/api/v1/maintenance",
    "/api/v1/movements",
    "/api/v1/audit",
    "/api/v1/users",
    "/api/v1/approvals",
    "/api/v1/notifications",
)

# /api/v1/notifications parses page_size with min(100, ...) rather than 200.
_PAGE_SIZE_CAP = {"/api/v1/notifications": 100}
_DEFAULT_CAP = 200

# Real table names (``asset_movements``, not ``movements``) — a wrong name here
# would make the "table survived the sweep" check vacuous, so it is asserted
# against the live catalog by _snapshot().
_WATCHED_TABLES = ("acs", "profiles", "audit_logs", "maintenance_work_orders", "asset_movements")


@pytest.fixture()
def sweep_app(db, monkeypatch):  # noqa: ARG001 — db points the pool at TEST_DATABASE_URL
    """A client configured the way the rest of the suite authenticates.

    JWT env has to be pinned *before* the first verification attempt: with
    ``SUPABASE_URL`` present and no ``JWT_SECRET`` the app selects its JWKS
    backend, cannot resolve a key for a locally signed HS256 token, and
    answers 503 to everything — which would make this whole sweep measure
    configuration rather than SQL handling.
    """
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    app = create_app()
    app.config["TESTING"] = True
    app.config["RATELIMIT_ENABLED"] = False
    return app


def _get_rules(app) -> list[str]:
    """Every v1 path that accepts GET (rules are unique per path+methods)."""
    seen: dict[str, set[str]] = {}
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/v1/"):
            continue
        methods = {m for m in rule.methods if m in _METHODS}
        if "GET" in methods:
            seen.setdefault(rule.rule, set()).update(methods)
    return sorted(seen)


def _fill(rule: str, value: str | None = None) -> str:
    """Fill every ``<param>`` — with ``value`` when given (path injection)."""
    out = rule
    while "<" in out:
        start = out.index("<")
        end = out.index(">", start)
        slot = value if value is not None else "RBAC-DOES-NOT-EXIST"
        out = out[:start] + slot + out[end + 1 :]
    return out


def _authed(app, path: str, params: dict[str, str]):
    """One probe as SUPER_ADMIN under a throwaway subject (see docstring)."""
    token = make_token("SUPER_ADMIN", sub=str(uuid.uuid4()), iss=TEST_ISSUER)
    return app.test_client().open(path, query_string=params, headers={"Authorization": f"Bearer {token}"})


def _leak(body: str) -> list[str]:
    return [m for m in _LEAK_MARKERS if m in body]


def _body_text(resp) -> str:
    """Response body as text, tolerant of the binary endpoints.

    ``get_data(as_text=True)`` hard-fails on ``application/pdf`` (label
    sheets) and PNG QR images, which several of these routes legitimately
    return — a decode error would abort the sweep instead of being recorded
    as "no SQL leakage here".
    """
    return resp.get_data().decode("utf-8", errors="replace")


def _snapshot() -> dict:
    """{table: (exists, row_count)} for the tables a probe could damage."""
    from app.config.database import get_db_connection

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            out = {}
            for table in _WATCHED_TABLES:
                cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{table}",))
                exists = cur.fetchone()[0]
                count = None
                if exists:
                    cur.execute(f"SELECT count(*) FROM {table}")  # fixed table-name literal, not user input
                    count = cur.fetchone()[0]
                out[table] = (exists, count)
            return out
    finally:
        conn.close()


def _diff(before: dict, after: dict) -> list[str]:
    problems = []
    for table, (exists, count) in before.items():
        if not exists:
            problems.append(f"{table} was missing before the sweep even started")
            continue
        now_exists, now_count = after.get(table, (False, None))
        if not now_exists:
            problems.append(f"{table} no longer exists after the sweep")
        elif now_count != count:
            problems.append(f"{table}: {count} -> {now_count} rows during the sweep")
    return problems


def test_query_string_filters_absorb_injection_payloads(sweep_app, db):  # noqa: ARG001
    """Every GET route x every string filter x hostile payloads stays a 2xx/4xx.

    The row/table snapshot is taken *before* the sweep and re-checked inside
    the same test, so a payload that managed to run DDL is caught even if no
    individual response looked wrong.
    """
    before = _snapshot()
    failures: list[str] = []
    probed = 0

    rules = _get_rules(sweep_app)
    for ri, rule in enumerate(rules):
        url = _fill(rule)
        for pi, param in enumerate(_STR_PARAMS):
            # Two payloads per (route, param), rotating so that across the
            # sweep every payload lands on every filter at least once.
            for step in (0, 1):
                payload = _PROBES[(ri + pi + step) % len(_PROBES)]
                resp = _authed(sweep_app, url, {param: payload})
                probed += 1
                body = _body_text(resp)
                if resp.status_code >= 500:
                    failures.append(f"{param}={payload!r} on {rule} -> {resp.status_code}: {body[:200]}")
                elif _leak(body):
                    failures.append(f"{param}={payload!r} on {rule} leaked {_leak(body)}")

    assert probed > 500, f"only {probed} probes issued — the sweep did not really run"

    failures += _diff(before, _snapshot())
    assert not failures, "SQL-injection probe failures:\n" + "\n".join(failures[:25])


def test_free_text_filters_treat_an_apostrophe_as_a_literal(sweep_app, db):  # noqa: ARG001
    """A hostile free-text filter must match nothing, never the whole table.

    A parameterised ``ILIKE '%<value>%'`` returns zero rows for
    ``' OR '1'='1``; a concatenated query would raise a syntax error (500) or
    silently return every row. Both outcomes fail here.
    """
    failures: list[str] = []
    for route, param in _FREE_TEXT:
        literal = _authed(sweep_app, route, {param: "zzz-no-such-value-zzz"})
        hostile = _authed(sweep_app, route, {param: "' OR '1'='1"})
        for label, resp in (("literal", literal), ("hostile", hostile)):
            if resp.status_code >= 500:
                failures.append(f"{route}?{param} ({label}) -> {resp.status_code}")
                continue
            body = resp.get_json() or {}
            items = body.get("data")
            if not isinstance(items, list):
                failures.append(f"{route}?{param} ({label}): unexpected envelope keys {sorted(body)}")
                continue
            if items:
                failures.append(
                    f"{route}?{param}={label!r} filter returned {len(items)} rows — "
                    "a value that matches nothing should return an empty page"
                )
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize(
    "route",
    ("/api/v1/assets", "/api/v1/assets/facets", "/api/v1/assets/export", "/api/v1/dashboard/summary"),
)
def test_status_filter_survives_a_trailing_backslash(route, sweep_app, db):  # noqa: ARG001
    """Phase 8 regression: ``?status=\\`` used to answer 500.

    ``_build_filters`` binds the raw value on the status fallback (an exact
    ILIKE against ``operational_status`` since 013 dropped the free-text column;
    unlike every other LIKE in the API, which wraps it in ``%...%``), so a
    trailing backslash made the pattern end with the escape character and
    Postgres rejected the query with SQLSTATE 22025 — an unhandled 500 on all
    four routes that share that builder.
    """
    for value in ("\\", "a\\", "%\\", "UNDER_MAINTENANCE\\"):
        resp = _authed(sweep_app, route, {"status": value})
        assert (
            resp.status_code < 500
        ), f"{route}?status={value!r} -> {resp.status_code}: {_body_text(resp)[:200]}"


def test_path_segments_absorb_injection_payloads(sweep_app, db):  # noqa: ARG001
    """Hostile payloads used as ``<ref>``/``<id>`` path segments never 500."""
    from urllib.parse import quote

    before = _snapshot()
    failures: list[str] = []
    for rule in _get_rules(sweep_app):
        if "<" not in rule:
            continue
        for payload in _PROBES[:6]:
            resp = _authed(sweep_app, _fill(rule, quote(payload, safe="")), {})
            body = _body_text(resp)
            if resp.status_code >= 500:
                failures.append(f"GET {rule} with segment {payload!r} -> {resp.status_code}: {body[:200]}")
            elif _leak(body):
                failures.append(f"GET {rule} with segment {payload!r} leaked {_leak(body)}")
    failures += _diff(before, _snapshot())
    assert not failures, "path-segment probe failures:\n" + "\n".join(failures)


def _page_of(resp) -> tuple[list, dict]:
    """``(items, pagination)`` for either envelope shape.

    Most list endpoints answer with ``paginated()``: ``data`` is the item list
    and ``pagination`` is a sibling. ``/api/v1/notifications`` nests
    ``{items, unread_count, pagination}`` inside ``data`` instead, so paging
    assertions have to understand both rather than assume one.
    """
    body = resp.get_json() or {}
    data = body.get("data")
    if isinstance(data, dict) and "items" in data:
        return list(data.get("items") or []), (data.get("pagination") or {})
    return (list(data) if isinstance(data, list) else []), (body.get("pagination") or {})


@pytest.mark.parametrize("route", _PAGINATED)
def test_pagination_clamps_out_of_range_values(route, sweep_app, db):  # noqa: ARG001
    """``page``/``page_size`` are clamped into range rather than rejected."""
    cases = (
        ({"page": "0"}, "page", 1),
        ({"page": "-7"}, "page", 1),
        ({"page_size": "0"}, "page_size", 1),
        ({"page_size": "-5"}, "page_size", 1),
    )
    failures = []
    for params, key, expected in cases:
        resp = _authed(sweep_app, route, params)
        if resp.status_code != 200:
            failures.append(f"{route}?{key}={params[key]} -> {resp.status_code}")
            continue
        got = _page_of(resp)[1].get(key)
        if got != expected:
            failures.append(f"{route}?{key}={params[key]} -> pagination.{key}={got}, expected {expected}")
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("route", _PAGINATED)
def test_pagination_caps_page_size(route, sweep_app, db):  # noqa: ARG001
    """A huge page_size is capped, never passed through to the query."""
    cap = _PAGE_SIZE_CAP.get(route, _DEFAULT_CAP)
    resp = _authed(sweep_app, route, {"page_size": "100000"})
    assert resp.status_code == 200, f"{route}?page_size=100000 -> {resp.status_code}"
    pagination = _page_of(resp)[1]
    assert (
        pagination.get("page_size") == cap
    ), f"{route}: page_size={pagination.get('page_size')}, expected cap {cap}"


@pytest.mark.parametrize("route", _PAGINATED)
def test_pagination_rejects_non_integers(route, sweep_app, db):  # noqa: ARG001
    """Non-integer paging parameters are a 400, not a 500 from int()."""
    for key in ("page", "page_size"):
        for value in ("abc", "1.5", "1e5", "", " "):
            resp = _authed(sweep_app, route, {key: value})
            if resp.status_code == 200:
                # Empty/blank falls back to the default — acceptable.
                if value.strip():
                    pytest.fail(f"{route}?{key}={value!r} -> 200, expected 400")
                continue
            assert resp.status_code == 400, f"{route}?{key}={value!r} -> {resp.status_code}"
            body = resp.get_json() or {}
            assert (body.get("error") or {}).get(
                "code"
            ) == "INVALID_PAGINATION", (
                f"{route}?{key}={value!r} error code {(body.get('error') or {}).get('code')!r}"
            )


@pytest.mark.parametrize("route", _PAGINATED)
def test_far_beyond_the_last_page_returns_empty_not_error(route, sweep_app, db):  # noqa: ARG001
    """``page`` past the end is an empty page, not a negative OFFSET or 500."""
    resp = _authed(sweep_app, route, {"page": "99999999", "page_size": "10"})
    assert resp.status_code == 200, f"{route}?page=99999999 -> {resp.status_code}"
    items, pagination = _page_of(resp)
    assert items == [], f"{route}: expected an empty page, got {len(items)} rows"
    assert pagination.get("page") == 99999999
    assert pagination.get("total_pages", 0) <= pagination.get("page", 0)


@pytest.fixture()
def paging_corpus(db):  # noqa: ARG001
    """Two extra assets so ``page_size=1`` has a second page to reach.

    The suite used to lean on ~20 ambient assets that happened to be in the
    shared database; a freshly migrated one has none, which silently turned
    the offset-paging regression check into a skip.
    """
    created = [f"PAGE-AC-{uuid.uuid4().hex[:8]}" for _ in range(2)]
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO acs (ac_id, room_id) VALUES (%s, NULL)",
                [(row,) for row in created],
            )
        conn.commit()
    finally:
        conn.close()
    yield created
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE entity_id = ANY(%s::text[])", (created,))
            cur.execute("DELETE FROM acs WHERE ac_id = ANY(%s::text[])", (created,))
        conn.commit()
    finally:
        conn.close()


@pytest.mark.parametrize("route", _PAGINATED)
def test_offset_paging_actually_advances(route, sweep_app, paging_corpus):  # noqa: ARG001
    """``page=2`` must return *different* rows from ``page=1``.

    Phase 8 regression: ``list_audit`` documented classic offset paging while
    its query had no ``OFFSET`` at all, so ``page`` was echoed into the
    envelope and every page returned the first rows. The audit UI paginates by
    offset, so "page 2 of N" was silently showing page 1.
    """
    first_items, first_p = _page_of(_authed(sweep_app, route, {"page": "1", "page_size": "1"}))
    second_items, second_p = _page_of(_authed(sweep_app, route, {"page": "2", "page_size": "1"}))

    total = first_p.get("total") or 0
    if total < 2:
        pytest.skip(f"{route} only has {total} rows — not enough to page through")
    assert first_p.get("page") == 1
    assert second_p.get("page") == 2
    assert first_items, f"{route}: page 1 came back empty despite total={total}"
    assert second_items, f"{route}: page 2 came back empty despite total={total}"
    assert (
        first_items != second_items
    ), f"{route}: page 2 repeated page 1 — offset paging is not applied to the query"


def test_sort_parameter_is_a_whitelist(sweep_app, db):  # noqa: ARG001
    """assets accepts SORT_KEYS values only; anything else is ignored safely."""
    failures = []
    for value in ("evil", "ac_id; DROP TABLE acs --", "1=1", "*", "a.ac_id"):
        resp = _authed(sweep_app, "/api/v1/assets", {"sort": value})
        if resp.status_code != 200:
            failures.append(f"?sort={value!r} -> {resp.status_code}")
    for value in ("ac_id", "-ac_id", "ac_make_company", "-installation_date"):
        resp = _authed(sweep_app, "/api/v1/assets", {"sort": value})
        if resp.status_code != 200:
            failures.append(f"?sort={value!r} (valid) -> {resp.status_code}")
    assert not failures, "\n".join(failures)


def test_qr_batch_accepts_a_large_ids_list_and_stays_capped(sweep_app, db):  # noqa: ARG001
    """The label sheet never exceeds the four-sheet / 96-label ceiling.

    The ``LIMIT`` is appended where the query is built rather than living in
    ``_BATCH_SELECT``, so the source of ``qr_batch`` is inspected: the bound
    must be present and must be the shared cap (not an unbounded scan and not
    a stray literal). The ceiling itself cannot be *reached* at runtime here —
    the shared fixture only holds 20 assets — so the constant and the query
    text carry that assertion, and the runtime probe below just proves a
    200-element ``ids`` list is accepted rather than rejected.
    """
    import inspect

    from app.api.v1.qr import _MAX_BATCH_LABELS, qr_batch

    assert _MAX_BATCH_LABELS == 96, "the plan caps a single sheet at 3 x 8 x 4 labels"
    source = inspect.getsource(qr_batch)
    assert "LIMIT %s" in source, "the label query must be bounded by a bind"
    assert "_MAX_BATCH_LABELS" in source, "the bound must be the shared cap, not a hard-coded literal"

    ids = ",".join(f"INJECT-{i}" for i in range(200))
    resp = _authed(sweep_app, "/api/v1/assets/qr/batch", {"ids": ids})
    assert resp.status_code in (200, 404), f"?ids=<200 refs> -> {resp.status_code}"
    if resp.status_code == 200:
        assert resp.mimetype in ("application/pdf", "application/octet-stream")


def test_openapi_document_still_serves(sweep_app, db):  # noqa: ARG001
    """Sanity: the sweep left the app able to serve its contract document."""
    resp = _authed(sweep_app, "/api/v1/openapi.json", {})
    assert resp.status_code == 200
    assert (resp.get_json() or {}).get("openapi")
