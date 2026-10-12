"""Industrial upgrade smoke tests (Plan #40, #41). No live DB required for most."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import auth, make_token  # noqa: E402

# `client` and `secret_client` fixtures come from tests/conftest.py.


def test_health_and_request_id(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert r.headers.get("X-Request-ID")


def test_ready_shape(client):
    r = client.get("/ready")
    assert r.status_code in (200, 503)
    assert "request_id" in r.get_json().get("error", {}) or r.get_json().get("success") is True


def test_qr_payload_roundtrip():
    from app.services import qr_service

    payload = qr_service.qr_payload(asset_uuid="123e4567-e89b-12d3-a456-426614174000")
    assert "/a/123e4567" in payload
    parsed = qr_service.parse_qr_payload(payload)
    assert parsed["asset_uuid"] == "123e4567-e89b-12d3-a456-426614174000"
    assert qr_service.parse_qr_payload("NITTTR-AC-001")["asset_code"] == "NITTTR-AC-001"


def test_qr_png_generates():
    from app.services import qr_service

    blob = qr_service.qr_png_bytes("http://localhost:3000/a/abc")
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"


def test_rbac_matrix():
    from app.auth.permissions import has_permission

    assert has_permission("VIEWER", "VIEW_ASSET")
    assert not has_permission("VIEWER", "CREATE_ASSET")
    assert not has_permission("TECHNICIAN", "APPROVE_MOVEMENT")
    assert has_permission("FACILITY_MANAGER", "APPROVE_MOVEMENT")
    assert has_permission("SUPER_ADMIN", "VIEW_AUDIT")
    # Phase 1 permission additions (02-api-contract.md).
    assert has_permission("FACILITY_MANAGER", "APPROVE_USERS")
    assert not has_permission("VIEWER", "APPROVE_USERS")
    assert has_permission("TECHNICIAN", "COMPLETE_MOVEMENT")
    assert has_permission("FACILITY_MANAGER", "EXPORT_DATA")


def test_asset_schema_rejects_bad_capacity():
    from pydantic import ValidationError

    from app.schemas.v1_schemas import AssetCreate

    with pytest.raises(ValidationError):
        AssetCreate(ac_id="X1", ac_capacity_ton=-2)


def test_protected_read_requires_auth(client):
    r = client.get("/api/v1/assets/does-not-exist-xyz")
    assert r.status_code == 401
    body = r.get_json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert "request_id" in body["error"]


def test_authenticated_missing_asset_envelope(secret_client):
    r = secret_client.get("/api/v1/assets/does-not-exist-xyz", headers=auth(make_token("VIEWER")))
    # DB unreachable in CI -> 500 envelope; DB reachable but missing -> 404 envelope.
    assert r.status_code in (404, 500)
    body = r.get_json()
    assert body["success"] is False
    assert body["error"]["code"]
    assert "request_id" in body["error"]
