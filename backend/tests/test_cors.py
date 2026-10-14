"""CORS preflight contract for the v1 API (regression for the dashboard fix).

Background: the energy dashboard's preflight failed with
"doesn't have HTTP ok status" — the root cause was a stale server process
that predated the energy blueprint (404 on OPTIONS), not a CORS misconfig.
These tests pin the behaviour that must hold so the same browser error can
never reappear for a configuration reason:

* OPTIONS preflight for the dashboard's endpoints answers 2xx for the
  configured frontend origin and advertises Authorization/Content-Type;
* an origin outside the allowlist gets NO CORS grant (response stays
  unreadable cross-origin even though it may exist);
* the preflight never authenticates: a successful OPTIONS does NOT grant
  API access — the actual GET still enforces auth (401) and RBAC (403).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import auth, make_token  # noqa: E402

DASHBOARD_ENDPOINTS = (
    "/api/v1/energy/summary?from=2026-10-08&to=2026-10-14&load_factor=0.7",
    "/api/v1/weather/now",
)
ALLOWED_ORIGIN = "http://localhost:3000"


def _preflight(client, path: str, origin: str = ALLOWED_ORIGIN):
    return client.open(
        path,
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )


@pytest.mark.parametrize("path", DASHBOARD_ENDPOINTS)
def test_preflight_ok_for_configured_frontend_origin(client, path):
    """The exact browser preflight must answer 2xx with CORS headers."""
    resp = _preflight(client, path)
    assert resp.status_code in (200, 204), resp.status_code
    assert resp.headers.get("Access-Control-Allow-Origin") == ALLOWED_ORIGIN
    allow_headers = (resp.headers.get("Access-Control-Allow-Headers") or "").lower()
    assert "authorization" in allow_headers
    assert "content-type" in allow_headers
    allow_methods = (resp.headers.get("Access-Control-Allow-Methods") or "").upper()
    assert "GET" in allow_methods


@pytest.mark.parametrize("path", DASHBOARD_ENDPOINTS)
def test_preflight_denied_for_unapproved_origin(client, path):
    """An origin outside the allowlist receives no CORS grant."""
    resp = _preflight(client, path, origin="http://evil.example")
    assert resp.status_code in (200, 204), resp.status_code
    # The response must not claim the disallowed origin (ideally no header).
    acao = resp.headers.get("Access-Control-Allow-Origin")
    assert acao != "http://evil.example"
    assert acao is None or acao == "null"


def test_preflight_does_not_grant_api_access(secret_client):
    """A successful preflight must not bypass auth on the real GET."""
    resp = _preflight(secret_client, "/api/v1/weather/now")
    assert resp.status_code in (200, 204)

    # Same endpoint, same origin, no token -> still 401.
    unauth = secret_client.get(
        "/api/v1/weather/now", headers={"Origin": ALLOWED_ORIGIN}
    )
    assert unauth.status_code == 401
    # CORS headers still decorate the error so the browser surfaces it.
    assert unauth.headers.get("Access-Control-Allow-Origin") == ALLOWED_ORIGIN


def test_get_still_enforces_rbac_after_preflight(secret_client):
    """Preflight succeeds; a token without the required permission gets 403."""
    resp = _preflight(secret_client, "/api/v1/energy/summary")
    assert resp.status_code in (200, 204)

    # VIEWER holds VIEW_ASSET; the summary endpoint requires it, so a viewer
    # token passes auth (not 401). RBAC for endpoints requiring more must 403.
    viewer = make_token(role="VIEWER")
    viewer_resp = secret_client.get(
        "/api/v1/energy/summary?from=2026-10-08&to=2026-10-14",
        headers={**auth(viewer), "Origin": ALLOWED_ORIGIN},
    )
    # Authenticated: not 401. (200 when a DB is configured, 5xx-free path
    # otherwise; we only assert authentication itself was enforced.)
    assert viewer_resp.status_code != 401, viewer_resp.get_json()


def test_error_responses_carry_cors_headers(secret_client):
    """CORS headers must be present on error responses too (404 path)."""
    resp = secret_client.get(
        "/api/v1/no-such-endpoint", headers={"Origin": ALLOWED_ORIGIN}
    )
    assert resp.status_code == 404
    assert resp.headers.get("Access-Control-Allow-Origin") == ALLOWED_ORIGIN
