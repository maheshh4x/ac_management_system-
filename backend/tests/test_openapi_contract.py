"""Phase 8.1 — contract test: ``openapi.yaml`` vs the running application.

Uses **schemathesis** to parse the hand-maintained spec and to validate real
responses, but never lets hypothesis generate traffic: every probe below is a
read-only GET issued by hand. A schema-driven fuzzing run would mutate the
shared test database (POST/PATCH/DELETE on live fixtures), which this suite
deliberately avoids — see ``acms-plan/phases/phase-8-testing-hardening.md``.

What is checked:

* the document parses as OpenAPI 3 and describes every implemented route
  **and no phantom route** (the existing ``test_openapi.py`` only covers the
  first direction);
* the document served at ``/api/v1/openapi.json`` is byte-for-byte the file;
* every parameter-less ``GET`` returns a **documented** status code whose body
  passes schemathesis' ``validate_response``;
* the same endpoints answer ``401`` without a token — as documented;
* the documented ``400`` for a malformed pagination argument really happens.

Successful JSON responses are additionally asserted against the envelope the
API guarantees everywhere: ``success`` plus ``data`` (or ``error``).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import TEST_SECRET, TEST_SUPABASE_URL, auth, make_token  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="needs a live database — set TEST_DATABASE_URL"
)

SPEC_FILE = Path(__file__).resolve().parents[1] / "openapi.yaml"
JSON_CONTENT_TYPE = "application/json"


@pytest.fixture()
def contract_app(db, monkeypatch):  # noqa: ARG001 - db pins the connection pool
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")

    from app import create_app
    from app.auth import jwt as jwt_mod
    from app.extensions import limiter

    jwt_mod.reset_caches()
    app = create_app()
    app.config["TESTING"] = True
    # Each probe carries a fresh subject, but the QR/report buckets are small
    # enough that a suite re-run inside the same minute could trip them.
    monkeypatch.setattr(limiter, "enabled", False)
    return app


@pytest.fixture()
def schema(contract_app):
    import schemathesis as st

    return st.openapi.from_wsgi("/api/v1/openapi.json", contract_app)


def _operations(schema) -> list:
    return [result.ok() for result in schema.get_all_operations()]


def _parameterless_gets(schema) -> list:
    return [op for op in _operations(schema) if op.method.lower() == "get" and "{" not in op.path]


def _documented(op) -> set[str]:
    if "default" in op.responses.status_codes:
        return {"*"}
    return {str(code) for code in op.responses.status_codes}


def _headers() -> dict:
    """A SUPER_ADMIN bearer token with no profile row (nothing is written)."""
    import uuid as _uuid

    return auth(make_token("SUPER_ADMIN", sub=str(_uuid.uuid4())))


def _assert_envelope(response) -> dict:
    """Assert the app-wide wire contract for a JSON response."""
    body = response.get_json()
    assert isinstance(body, dict), f"expected an envelope object, got {type(body).__name__}"
    assert set(body) >= {"success"}, f"envelope is missing 'success': {sorted(body)}"
    if response.status_code < 400:
        assert body["success"] is True, body
        assert "data" in body, f"success response has no 'data': {sorted(body)}"
    else:
        assert body["success"] is False, body
        error = body.get("error") or {}
        assert error.get("code") and error.get("message"), body
    return body


# ── the document ─────────────────────────────────────────────────────────────


def test_spec_parses_as_openapi3(schema):
    assert schema.raw_schema["openapi"].startswith("3."), schema.raw_schema["openapi"]
    ops = _operations(schema)
    assert len(ops) >= 60, f"only {len(ops)} operations loaded from the spec"
    for op in ops:
        assert op.path.startswith("/"), op.path
        assert op.method.islower(), op.method
        assert _documented(op), f"{op.label} documents no responses at all"


def test_served_spec_is_the_file(contract_app):
    served = contract_app.test_client().get("/api/v1/openapi.json")
    assert served.status_code == 200
    assert served.get_json() == yaml.safe_load(SPEC_FILE.read_text(encoding="utf-8"))


def test_spec_and_routes_match_in_both_directions(schema, contract_app):
    """No undocumented route, and no documented route that does not exist."""
    import re as _re

    def to_spec(rule: str) -> str:
        assert rule.startswith("/api/v1"), rule
        path = rule[len("/api/v1") :] or "/"
        return _re.sub(r"<[^>]+>", lambda m: "{" + m.group(0)[1:-1].split(":")[-1] + "}", path)

    implemented = set()
    for rule in contract_app.url_map.iter_rules():
        if not rule.rule.startswith("/api/v1"):
            continue
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            if method in rule.methods:
                implemented.add((method, to_spec(rule.rule)))

    spec = yaml.safe_load(SPEC_FILE.read_text(encoding="utf-8"))
    documented = {(method.upper(), path) for path, item in spec["paths"].items() for method in item}

    assert documented - implemented == set(), "documented but not implemented:\n" + "\n".join(
        f"{m} {p}" for m, p in sorted(documented - implemented)
    )
    assert implemented - documented == set(), "implemented but not documented:\n" + "\n".join(
        f"{m} {p}" for m, p in sorted(implemented - documented)
    )
    # schemathesis drops /openapi.json (the schema endpoint itself).
    assert ("GET", "/openapi.json") in implemented


# ── live responses ───────────────────────────────────────────────────────────


def test_every_parameterless_get_returns_a_documented_body(schema, contract_app):
    import schemathesis as st

    client = contract_app.test_client()
    headers = _headers()
    ops = _parameterless_gets(schema)
    assert ops, "no parameter-less GET operations found"

    checked_json = 0
    for op in ops:
        case = st.Case(operation=op, method=op.method, path=op.path)
        response = client.get("/api/v1" + op.path, headers=headers, query_string={"page": 1, "page_size": 1})
        assert str(response.status_code) in _documented(op), (
            f"{op.label} returned {response.status_code}, " f"documented: {sorted(_documented(op))}"
        )
        op.validate_response(response, case=case)  # raises on a schema mismatch

        if response.content_type and JSON_CONTENT_TYPE in response.content_type:
            _assert_envelope(response)
            checked_json += 1

    assert checked_json >= 10, f"only {checked_json} JSON bodies were contract-checked"


def test_unauthenticated_calls_are_documented_401(schema, contract_app):
    client = contract_app.test_client()
    for op in _parameterless_gets(schema):
        response = client.get("/api/v1" + op.path)
        assert response.status_code == 401, f"{op.label} answered {response.status_code} without a token"
        assert "401" in _documented(op), f"{op.label} does not document 401"
        if response.content_type and JSON_CONTENT_TYPE in response.content_type:
            body = _assert_envelope(response)
            assert body["error"]["code"] == "UNAUTHORIZED", body


@pytest.mark.parametrize("path", ["/assets", "/users", "/approvals", "/notifications"])
def test_malformed_pagination_is_a_documented_400(schema, contract_app, path):
    op = next(
        (o for o in _parameterless_gets(schema) if o.path == path),
        None,
    )
    assert op is not None, f"{path} is not in the spec"

    response = contract_app.test_client().get(
        "/api/v1" + path, headers=_headers(), query_string={"page": "not-a-number"}
    )
    assert response.status_code == 400, response.get_json()
    assert "400" in _documented(op), f"{path} does not document 400"

    body = _assert_envelope(response)
    assert body["error"]["code"] in {"INVALID_PAGINATION", "VALIDATION_ERROR"}, body
    assert re.match(r"^req_[0-9a-f]+$", body["error"]["request_id"] or ""), body["error"]
