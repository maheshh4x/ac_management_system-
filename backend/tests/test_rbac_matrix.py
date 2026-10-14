"""Phase 8.1 — RBAC matrix generated from ``app.url_map``.

For every v1 rule x every method x every role x an anonymous caller this test
asserts the *expected status*, and the expectation is derived rather than
hand-written:

  * ``require_permission`` publishes ``_requires_permissions`` on the view
    (app/auth/permissions.py), so the required permission is read from the
    route itself — a new route can never quietly skip the check;
  * ``ROLE_PERMISSIONS`` is the single source of truth for what a role holds,
    so granting a permission to a role updates the matrix automatically.

A role that lacks every permission the route requires must get **403**
(authorization runs before validation, so it is never a 400/404 either), an
anonymous caller must get **401**, and a role that holds the permission must
get neither — nor an ``INTERNAL_ERROR`` 5xx (a deliberate 5xx such as 503
``PHOTO_STORAGE_NOT_CONFIGURED`` is a legitimate answer and is tolerated).

Three things this test has to get right (each learned the hard way):

  * **Rules, not paths.** ``GET /api/v1/assets`` and ``POST /api/v1/assets``
    are two rules from two blueprints with different permissions; merging by
    path would assert the GET's permission for the POST.
  * **Auth-only routes.** ``require_auth`` sets
    ``_requires_permissions = ()`` when no ``require_permission`` follows, and
    an empty tuple means "no permission gate", not "grants nothing".
  * **One subject per rule.** ``POST /auth/register-profile`` inserts an
    auto-approved ``status=APPROVED`` profile for its caller, and
    ``profiles.role`` is authoritative over the JWT claim — so a subject
    shared across rules would have its role wiped halfway through the run
    and every later probe would 403 for an unrelated reason. Each rule gets
    its own subject, and the fixture deletes them all.

Plus one documented exception: ``_ROLE_SCOPED`` lists routes whose role check
lives in the view body (``patch_me`` refusing VIEWER) rather than in a
decorator, so the matrix expects their 403 instead of reporting it as a
violation. ``test_role_scoped_exceptions_cannot_go_stale`` keeps that list
honest.

Probe shape is otherwise side-effect free: path parameters are filled with
ids that cannot resolve (404) and mutations send ``{}`` (validation error).

``test_route_auth_coverage.py`` remains the tripwire that every rule carries
an auth marker at all; this file asserts what each role actually *gets*.
"""

from __future__ import annotations

import logging
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app
from app.auth.permissions import ROLE_PERMISSIONS, ROLES, has_permission
from tests.conftest import TEST_ISSUER, TEST_SECRET, TEST_SUPABASE_URL, make_token

_METHODS = ("GET", "POST", "PATCH", "PUT", "DELETE")

# Anonymous by design (02-api-contract.md): the OpenAPI document + QR landing.
_ANONYMOUS_OK = frozenset({"/api/v1/openapi.json", "/api/v1/public/assets/<ref>"})

_MISSING = "RBAC-DOES-NOT-EXIST"
_MISSING_UUID = "00000000-0000-4000-8000-000000000000"
_MUTATING = {"POST", "PATCH", "PUT"}

# Roles that a view denies *in code* instead of via require_permission(...),
# because the rule is business logic rather than a static permission (the
# same distinction app/auth/permissions.py draws for approval scoping).
# Each entry must keep an empty _requires_permissions: if someone later adds
# a real decorator here, test_role_scoped_routes_cannot_go_stale fails and
# this row has to be deleted rather than silently covering the change.
_ROLE_SCOPED = {
    ("/api/v1/me", "PATCH", "VIEWER"): "patch_me refuses to let a VIEWER edit a profile",
}


def _fill_path(rule: str) -> str:
    """Replace every ``<param>`` with an id that cannot resolve."""
    out = rule
    for uuid_slot in ("<ref>", "<profile_id>", "<notification_id>"):
        out = out.replace(uuid_slot, _MISSING_UUID)
    while "<" in out:
        start = out.index("<")
        end = out.index(">", start)
        out = out[:start] + _MISSING + out[end + 1 :]
    return out


def _probe_rules(app) -> list[tuple[str, str, list[str]]]:
    """[(path, endpoint, sorted methods)] — one entry per *rule*, never merged."""
    rows = []
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/v1/"):
            continue
        methods = sorted(m for m in rule.methods if m in _METHODS)
        if methods:
            rows.append((rule.rule, rule.endpoint, methods))
    return sorted(rows)


@pytest.fixture()
def matrix_app(db):  # noqa: ARG001 — depends on db so env/pool are set first
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    app = create_app()
    app.config["TESTING"] = True
    # The matrix issues hundreds of probes through a couple of shared
    # rate-limit buckets; a 429 here would say nothing about authorization.
    app.config["RATELIMIT_ENABLED"] = False
    return app


@pytest.fixture()
def matrix_subjects(db, monkeypatch):  # noqa: ARG001
    """Register/teardown the throwaway subjects (see module docstring)."""
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    subjects: list[str] = []
    yield subjects
    if not subjects:
        return
    try:
        from app.config.database import get_db_connection

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                # Two traps hid here, each silently swallowed by `pass`:
                # 1. psycopg2 adapts a Python list to a text[] literal, and
                #    Postgres has no uuid = text operator — hence the cast.
                # 2. audit_logs.user_id references profiles, so
                #    POST /auth/register-profile (which audits every probe)
                #    must have its own audit rows removed before the profile.
                # Getting this wrong orphaned one profile per matrix run in
                # the test database; see the warning log if it recurs.
                cur.execute("DELETE FROM audit_logs WHERE user_id = ANY(%s::uuid[])", (subjects,))
                cur.execute("DELETE FROM notifications WHERE user_id = ANY(%s::uuid[])", (subjects,))
                cur.execute("DELETE FROM profiles WHERE id = ANY(%s::uuid[])", (subjects,))
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — teardown must never mask a real failure
        # ...but it must not be silent either: a swallowed error here is
        # exactly how orphaned profile rows accumulated in the test database
        # during Phase 8 (register-profile leaves a row per matrix run).
        logging.getLogger(__name__).warning("rbac matrix subject cleanup failed", exc_info=True)


def _probe(app, url: str, method: str, subject: str, role: str):
    headers = {"Authorization": f"Bearer {make_token(role, sub=subject, iss=TEST_ISSUER)}"}
    kwargs = {"json": {}} if method in _MUTATING else {}
    return app.test_client().open(url, method=method, headers=headers, **kwargs)


def test_role_matrix(matrix_app, matrix_subjects):
    """Anonymous -> 401; role without the permission -> 403; holder -> neither."""
    anonymous = matrix_app.test_client()
    failures: list[str] = []
    probed = 0

    for path, endpoint, methods in _probe_rules(matrix_app):
        view = matrix_app.view_functions[endpoint]
        required = tuple(getattr(view, "_requires_permissions", ()) or ())
        is_public = path in _ANONYMOUS_OK or not getattr(view, "_requires_auth", False)
        url = _fill_path(path)
        subject = str(uuid.uuid4())  # fresh per rule — see module docstring
        matrix_subjects.append(subject)

        for method in methods:
            kwargs = {"json": {}} if method in _MUTATING else {}

            if not is_public:
                status = anonymous.open(url, method=method, **kwargs).status_code
                probed += 1
                if status != 401:
                    failures.append(f"anonymous {method} {path} -> {status}, expected 401")

            if not getattr(view, "_requires_auth", False):
                continue  # public route, anonymous already covered

            for role in ROLES:
                resp = _probe(matrix_app, url, method, subject, role)
                status = resp.status_code
                probed += 1

                # Documented in-code role guard: 403 is the correct answer.
                if (path, method, role) in _ROLE_SCOPED:
                    if status != 403:
                        failures.append(
                            f"{role} {method} {path} -> {status}, expected the in-code 403 "
                            f"({_ROLE_SCOPED[(path, method, role)]})"
                        )
                    continue

                # Empty tuple == no permission gate (auth-only route).
                granted = not required or any(has_permission(role, p) for p in required)
                if not granted:
                    if status != 403:
                        needs = "|".join(required)
                        failures.append(f"{role} {method} {path} -> {status}, expected 403 (needs {needs})")
                elif status in (401, 403):
                    failures.append(
                        f"{role} {method} {path} -> {status}, expected to be allowed "
                        f"(needs {'|'.join(required) or 'auth only'}, holds {sorted(ROLE_PERMISSIONS[role])})"
                    )
                elif status >= 500:
                    # A deliberate "dependency unavailable" answer (e.g. 503
                    # PHOTO_STORAGE_NOT_CONFIGURED when storage is off) carries
                    # its own code and is a real response; only INTERNAL_ERROR
                    # means the view actually crashed.
                    body = resp.get_json() or {}
                    code = (body.get("error") or {}).get("code")
                    if code == "INTERNAL_ERROR":
                        failures.append(
                            f"{role} {method} {path} -> {status} ({code}), crashed on an authorized probe"
                        )

    assert probed > 200, f"matrix only issued {probed} probes — enumeration looks broken"
    assert failures == [], "RBAC matrix violations:\n" + "\n".join(failures)


def test_every_role_is_granted_something(matrix_app):
    """The matrix is vacuous if some role is never allowed a single gated route."""
    granted_to: dict[str, int] = {role: 0 for role in ROLES}
    gated = 0
    for _path, endpoint, _methods in _probe_rules(matrix_app):
        required = tuple(getattr(matrix_app.view_functions[endpoint], "_requires_permissions", ()) or ())
        if not required:
            continue
        gated += 1
        for role in ROLES:
            if any(has_permission(role, p) for p in required):
                granted_to[role] += 1
    assert gated >= 20, f"only {gated} permission-gated routes — marker discovery is broken"
    assert all(v > 0 for v in granted_to.values()), f"roles never granted a route: {granted_to}"


def test_route_and_permission_markers_agree_for_every_v1_rule(matrix_app):
    """Every v1 rule is either auth+permission gated, auth-only, or public."""
    buckets = {"gated": 0, "auth_only": 0, "public": 0}
    problems = []
    for path, endpoint, _methods in _probe_rules(matrix_app):
        view = matrix_app.view_functions[endpoint]
        if not getattr(view, "_requires_auth", False):
            buckets["public"] += 1
            if path not in _ANONYMOUS_OK:
                problems.append(f"{path} carries no auth marker but is not allow-listed")
        elif tuple(getattr(view, "_requires_permissions", ()) or ()):
            buckets["gated"] += 1
        else:
            buckets["auth_only"] += 1
    assert not problems, "\n".join(problems)
    assert buckets["public"] == len(_ANONYMOUS_OK), f"public routes = {buckets}"
    assert buckets["gated"] > buckets["auth_only"], f"marker buckets look wrong: {buckets}"


def test_denied_responses_use_the_error_envelope(matrix_app, matrix_subjects):
    """A 403 must be the plain error envelope, never a partial payload."""
    checked = 0
    for path, endpoint, methods in _probe_rules(matrix_app):
        required = tuple(getattr(matrix_app.view_functions[endpoint], "_requires_permissions", ()) or ())
        if not required:
            continue
        denied = [r for r in ROLES if not any(has_permission(r, p) for p in required)]
        if not denied:
            continue
        subject = str(uuid.uuid4())
        matrix_subjects.append(subject)
        r = _probe(matrix_app, _fill_path(path), methods[0], subject, denied[0])
        checked += 1
        assert r.status_code == 403, f"{denied[0]} {methods[0]} {path} -> {r.status_code}"
        body = r.get_json()
        assert body is not None, f"{denied[0]} {methods[0]} {path}: 403 without a JSON body"
        assert body.get("success") is False, f"{denied[0]} {methods[0]} {path}: missing success=false"
        err = body.get("error") or {}
        assert err.get("code") == "FORBIDDEN", f"{denied[0]} {methods[0]} {path}: code {err.get('code')!r}"
        assert err.get("request_id"), f"{denied[0]} {methods[0]} {path}: no request_id in the 403"
    assert checked >= 10, f"only {checked} denied routes exercised — enumeration looks broken"


def test_public_v1_routes_are_exactly_the_documented_two(matrix_app):
    """Only the OpenAPI document and the QR landing may ever be anonymous."""
    public = set()
    for path, endpoint, _methods in _probe_rules(matrix_app):
        view = matrix_app.view_functions[endpoint]
        if not getattr(view, "_requires_auth", False):
            public.add(path)
    assert public == _ANONYMOUS_OK


def test_role_scoped_exceptions_cannot_go_stale(matrix_app):
    """Every ``_ROLE_SCOPED`` row must still describe a *decorator-less* guard.

    The table exists so the matrix can expect 403 from a route whose check
    lives in the view body rather than in ``require_permission``. If someone
    gives such a route a real decorator the row becomes a silent exemption
    that hides a regression, so it has to be removed instead.
    """
    by_path = {path: endpoint for path, endpoint, _m in _probe_rules(matrix_app)}
    problems = []
    for path, method, role in _ROLE_SCOPED:
        if role not in ROLES:
            problems.append(f"{path} {method}: {role!r} is not a known role")
        endpoint = by_path.get(path)
        if endpoint is None:
            problems.append(f"{path} no longer exists — drop the _ROLE_SCOPED row")
            continue
        required = tuple(getattr(matrix_app.view_functions[endpoint], "_requires_permissions", ()) or ())
        if required:
            problems.append(
                f"{path} {method} now declares require_permission{required} — "
                "the in-code guard moved into a decorator, drop the _ROLE_SCOPED row"
            )
    assert not problems, "\n".join(problems)
