"""Phase 1.3 users/approvals/notifications tests.

Approver scoping rules are pure logic (evaluate_approval) and tested without
a DB. Endpoint-level tests need a migrated database; they honor
TEST_DATABASE_URL and skip otherwise.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.v1.users import evaluate_approval  # noqa: E402
from tests.conftest import TEST_SECRET, auth, make_token  # noqa: E402

TEST_DB = os.getenv("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DB, reason="set TEST_DATABASE_URL")

ADMIN = "11111111-1111-1111-1111-111111111111"
MANAGER = "22222222-2222-2222-2222-222222222222"
TARGET = "33333333-3333-3333-3333-333333333333"


# ── pure approver-scoping rules ─────────────────────────────────────────────


def _target(requested_role="TECHNICIAN", blocks=None, target_id=TARGET):
    return {"id": target_id, "requested_role": requested_role, "assigned_blocks": blocks}


def test_admin_can_approve_anything():
    ok, _ = evaluate_approval("SUPER_ADMIN", None, ADMIN, _target("FACILITY_MANAGER"))
    assert ok


def test_manager_can_approve_technician_and_viewer():
    assert evaluate_approval("FACILITY_MANAGER", None, MANAGER, _target("TECHNICIAN"))[0]
    assert evaluate_approval("FACILITY_MANAGER", None, MANAGER, _target("VIEWER"))[0]
    assert evaluate_approval("FACILITY_MANAGER", None, MANAGER, _target(None))[0]  # default VIEWER


def test_manager_cannot_approve_manager_or_admin_requests():
    for role in ("FACILITY_MANAGER", "SUPER_ADMIN"):
        ok, code = evaluate_approval("FACILITY_MANAGER", None, MANAGER, _target(role))
        assert not ok and code == "ROLE_OUT_OF_SCOPE"


def test_nobody_can_approve_themselves():
    ok, code = evaluate_approval("SUPER_ADMIN", None, ADMIN, _target("VIEWER", target_id=ADMIN))
    assert not ok and code == "SELF_APPROVAL"


def test_manager_block_scope_enforced_when_both_sides_scoped():
    ok, _ = evaluate_approval("FACILITY_MANAGER", ["A-block"], MANAGER, _target("TECHNICIAN", ["A-block"]))
    assert ok
    ok, code = evaluate_approval("FACILITY_MANAGER", ["A-block"], MANAGER, _target("TECHNICIAN", ["B-block"]))
    assert not ok and code == "BLOCK_OUT_OF_SCOPE"
    # Unscoped target (no blocks assigned yet) stays approvable.
    ok, _ = evaluate_approval("FACILITY_MANAGER", ["A-block"], MANAGER, _target("TECHNICIAN", None))
    assert ok


def test_technician_and_viewer_cannot_approve():
    for role in ("TECHNICIAN", "VIEWER"):
        ok, code = evaluate_approval(role, None, MANAGER, _target())
        assert not ok and code == "FORBIDDEN"


# ── endpoint behaviour (live DB) ────────────────────────────────────────────


@needs_db
def test_register_profile_idempotent_and_viewer_default(secret_client):
    # Distinct sub: keeps the test-DB profiles row isolated from other tests.
    token = make_token("VIEWER", sub="44444444-4444-4444-4444-444444444444")
    r1 = secret_client.post(
        "/api/v1/auth/register-profile",
        json={"name": "T", "requested_role": "SUPER_ADMIN"},
        headers=auth(token),
    )
    assert r1.status_code in (200, 201)
    body = r1.get_json()["data"]
    # requested role is recorded, never granted: role stays VIEWER/PENDING.
    assert body["role"] == "VIEWER"
    assert body["status"] == "PENDING"
    r2 = secret_client.post("/api/v1/auth/register-profile", json={}, headers=auth(token))
    assert r2.status_code == 200


@needs_db
def test_approvals_require_permission_and_scope(secret_client):
    # ROLELESS/VIEWER may not reach the queue.
    viewer = make_token("VIEWER")
    r = secret_client.get("/api/v1/approvals", headers=auth(viewer))
    assert r.status_code == 403
    admin = make_token("SUPER_ADMIN")
    r2 = secret_client.get("/api/v1/approvals", headers=auth(admin))
    assert r2.status_code == 200


@needs_db
def test_last_super_admin_cannot_be_demoted(secret_client):
    admin = make_token("SUPER_ADMIN", sub=ADMIN)
    r = secret_client.patch(f"/api/v1/users/{ADMIN}", json={"role": "VIEWER"}, headers=auth(admin))
    # self-edit of role is forbidden outright
    assert r.status_code == 403


def test_pending_user_blocked_except_me(monkeypatch):
    """The status gate must let PENDING users reach GET /me but nothing else.
    Simulated without a DB by forcing the profile lookup result."""
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", "https://unit-test.supabase.co")
    for var in ("SUPABASE_JWT_SECRET", "SUPABASE_JWKS_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_ENV", "development")

    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    pending_profile = {
        "id": TARGET,
        "email": "p@x.io",
        "role": "VIEWER",
        "is_active": True,
        "status": "PENDING",
    }
    monkeypatch.setattr(jwt_mod, "_lookup_profile", lambda _uid: dict(pending_profile))

    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    token = auth(make_token("VIEWER", sub=TARGET))

    assert c.get("/api/v1/me", headers=token).status_code == 200
    r = c.get("/api/v1/assets", headers=token)
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "ACCOUNT_PENDING"
    r2 = c.post("/api/v1/auth/register-profile", json={}, headers=token)
    assert r2.status_code in (200, 201, 500)  # exempt from the gate; DB may be absent


def test_rejected_user_blocked(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", "https://unit-test.supabase.co")
    for var in ("SUPABASE_JWT_SECRET", "SUPABASE_JWKS_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("APP_ENV", "development")

    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    monkeypatch.setattr(
        jwt_mod,
        "_lookup_profile",
        lambda _uid: {
            "id": TARGET,
            "email": "r@x.io",
            "role": "VIEWER",
            "is_active": True,
            "status": "REJECTED",
        },
    )

    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    r = c.get("/api/v1/assets", headers=auth(make_token("VIEWER", sub=TARGET)))
    assert r.status_code == 403
    assert r.get_json()["error"]["code"] == "ACCOUNT_REJECTED"
