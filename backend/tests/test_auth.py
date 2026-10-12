"""Auth/RBAC hardening tests: anon-key isolation, role matrix, fail-closed.

Phase 1.2 removed the anonymous demo mode and the AUTH_DISABLED bypass from
all non-test code paths. Tests authenticate against a test-only JWT secret
(see conftest.py).
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.auth import jwt as jwt_mod  # noqa: E402
from tests.conftest import (  # noqa: E402
    TEST_SECRET,
    auth,
    make_token,
)

AUDIT = "/api/v1/audit"


def test_anon_key_is_not_used_to_verify_tokens(secret_client, monkeypatch):
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    # A token claiming SUPER_ADMIN but signed with the anon key is rejected.
    forged = make_token("SUPER_ADMIN", secret="public-anon-key")
    r = secret_client.get(AUDIT, headers=auth(forged))
    assert r.status_code == 401
    assert r.get_json()["error"]["code"] == "INVALID_TOKEN"


def test_anon_key_is_not_picked_up_as_secret(monkeypatch):
    for var in ("SUPABASE_JWT_SECRET", "JWT_SECRET", "SUPABASE_URL", "SUPABASE_JWKS_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "public-anon-key")
    assert jwt_mod._jwt_secret() is None
    assert jwt_mod._jwks_url() is None
    assert jwt_mod.auth_configured() is False


# ── fail closed: no verification key configured ─────────────────────────────


def test_no_secret_everything_closed(client):
    # No token at all -> 401, in development too.
    assert client.get("/api/v1/assets").status_code == 401
    assert client.get(AUDIT).status_code == 401
    assert client.post("/api/v1/assets", json={"ac_id": "X"}).status_code == 401
    # A token that cannot be verified (no key) -> 503, never silently accepted.
    r = client.get("/api/v1/assets", headers=auth(make_token("SUPER_ADMIN")))
    assert r.status_code == 503
    assert r.get_json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


def test_legacy_routes_locked(client):
    # Note: the legacy guard rejects anonymous /api/* requests before routing,
    # so removed and surviving legacy paths alike answer 401 here. Proof that
    # a legacy path is really GONE requires an authenticated call (see
    # test_campus_import.test_legacy_campus_import_removed).
    assert client.get("/api/acs").status_code == 401
    assert client.delete("/api/acs/X").status_code == 401
    assert client.post("/api/campus/import").status_code == 401
    assert client.get("/api/db-test").status_code == 401
    assert client.get("/api/dashboard").status_code == 401
    # The v1 replacement exists and is auth-locked.
    assert client.post("/api/v1/campus/import").status_code == 401
    # Public probes stay open.
    assert client.get("/health").status_code == 200


def test_legacy_assets_and_hierarchy_routes_removed(db_admin_client):
    """Phase 3 task 8: /api/acs* and the hierarchy routes are gone — only v1
    serves assets/campus now. Authenticated so the legacy guard (which
    rejects anonymous /api/* requests before routing) cannot mask the 404."""
    assert db_admin_client.get("/api/acs").status_code == 404
    assert db_admin_client.post("/api/acs", json={}).status_code == 404
    assert db_admin_client.get("/api/acs/ANY").status_code == 404
    assert db_admin_client.put("/api/acs/ANY", json={}).status_code == 404
    assert db_admin_client.delete("/api/acs/ANY").status_code == 404
    for path in ("/api/blocks", "/api/floors", "/api/rooms", "/api/departments", "/api/colleges"):
        assert db_admin_client.get(path).status_code == 404, path
    # ...while the v1 replacements are alive.
    assert db_admin_client.get("/api/v1/assets?page_size=1").status_code == 200
    assert db_admin_client.get("/api/v1/campus/tree").status_code == 200


def test_legacy_dashboard_and_db_test_routes_removed(db_admin_client):
    """Phase 8.6: the pre-v1 /api/dashboard and /api/db-test blueprints are
    gone (the FE never called them). Authenticated so the legacy guard —
    which rejects anonymous /api/* requests before routing — cannot mask
    the 404 the way it masks anonymous probes above."""
    assert db_admin_client.get("/api/dashboard").status_code == 404
    assert db_admin_client.get("/api/db-test").status_code == 404


def test_auth_disabled_env_is_ignored(client, monkeypatch):
    # The Phase 1.1 flag no longer exists: setting it changes nothing.
    monkeypatch.setenv("AUTH_DISABLED", "true")
    assert client.get(AUDIT).status_code == 401


# ── RBAC role matrix (with the test-only secret configured) ─────────────────


def test_role_matrix_viewer_technician_manager_admin(secret_client):
    # GET /api/v1/audit requires VIEW_AUDIT (SUPER_ADMIN only).
    assert secret_client.get(AUDIT, headers=auth(make_token("VIEWER"))).status_code == 403
    assert secret_client.get(AUDIT, headers=auth(make_token("TECHNICIAN"))).status_code == 403
    assert secret_client.get(AUDIT, headers=auth(make_token("FACILITY_MANAGER"))).status_code == 403
    assert secret_client.get(AUDIT, headers=auth(make_token("SUPER_ADMIN"))).status_code in (200, 404, 500)


def test_write_permission_matrix(secret_client):
    # POST /api/v1/assets requires CREATE_ASSET (FACILITY_MANAGER+).
    assert (
        secret_client.post("/api/v1/assets", json={}, headers=auth(make_token("VIEWER"))).status_code == 403
    )
    assert (
        secret_client.post("/api/v1/assets", json={}, headers=auth(make_token("TECHNICIAN"))).status_code
        == 403
    )
    # Authorized role passes RBAC and reaches payload validation.
    r = secret_client.post("/api/v1/assets", json={}, headers=auth(make_token("SUPER_ADMIN")))
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_token_rejected(secret_client):
    assert secret_client.get(AUDIT, headers=auth("garbage.token.value")).status_code == 401
    forged = make_token("SUPER_ADMIN", secret="wrong-secret")
    assert secret_client.get(AUDIT, headers=auth(forged)).status_code == 401


# ── production boot refuses unsafe configuration ────────────────────────────


def test_production_boot_refuses_without_key(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    for var in ("SUPABASE_JWT_SECRET", "JWT_SECRET", "SUPABASE_JWKS_URL", "SUPABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    jwt_mod.reset_caches()
    with pytest.raises(RuntimeError, match="unsafe production configuration"):
        create_app()


def test_production_boot_refuses_debug(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("FLASK_DEBUG", "1")
    with pytest.raises(RuntimeError, match="unsafe production configuration"):
        create_app()


def test_production_boot_refuses_wildcard_cors(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="unsafe production configuration"):
        create_app()


def test_production_boot_ok_with_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.delenv("FLASK_DEBUG", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    app = create_app()
    assert app is not None
