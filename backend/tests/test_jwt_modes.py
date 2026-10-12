"""JWT verification modes (Phase 1.1): HS256 + JWKS, strict claims.

Covers: expired token, wrong issuer, wrong audience, alg=none,
HS256-signed-with-the-RSA-public-key confusion attack, and JWKS key rotation.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import jwt as pyjwt
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import (  # noqa: E402
    TEST_ISSUER,
    TEST_SECRET,
    TEST_SUPABASE_URL,
    TEST_USER_ID,
    auth,
    make_token,
)

ME = "/api/v1/me"


@pytest.fixture()
def hs256_client(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


class _FakeProvider:
    """JWKS provider stub: resolves any kid to the given public key."""

    def __init__(self, public_key):
        self.public_key = public_key

    def signing_key(self, _token):
        return SimpleNamespace(key=self.public_key)


@pytest.fixture()
def jwks_client(monkeypatch):
    """App in JWKS (asymmetric) mode with an injectable provider."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("SUPABASE_JWKS_URL", "https://unit-test.supabase.co/auth/v1/.well-known/jwks.json")
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.setenv("APP_ENV", "development")

    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    monkeypatch.setattr(jwt_mod, "_get_jwks_provider", lambda: _FakeProvider(public_pem))

    app = create_app()
    app.config["TESTING"] = True
    return app.test_client(), private_key, public_pem


# ── HS256 mode ───────────────────────────────────────────────────────────────


def test_hs256_valid_token_accepted(hs256_client):
    r = hs256_client.get(ME, headers=auth(make_token("VIEWER")))
    assert r.status_code == 200
    assert r.get_json()["data"]["id"] == TEST_USER_ID


def test_hs256_wrong_secret_rejected(hs256_client):
    r = hs256_client.get(ME, headers=auth(make_token("SUPER_ADMIN", secret="wrong-secret")))
    assert r.status_code == 401
    assert r.get_json()["error"]["code"] == "INVALID_TOKEN"


def test_expired_token_rejected(hs256_client):
    token = make_token("VIEWER", exp=int(time.time()) - 60)
    r = hs256_client.get(ME, headers=auth(token))
    assert r.status_code == 401
    assert r.get_json()["error"]["code"] == "TOKEN_EXPIRED"


def test_wrong_issuer_rejected(hs256_client):
    r = hs256_client.get(ME, headers=auth(make_token("VIEWER", iss="https://evil.example.com/auth/v1")))
    assert r.status_code == 401


def test_wrong_audience_rejected(hs256_client):
    r = hs256_client.get(ME, headers=auth(make_token("VIEWER", aud="service_role")))
    assert r.status_code == 401


def test_alg_none_rejected(hs256_client):
    import time as _t

    none_token = pyjwt.encode(
        {
            "sub": TEST_USER_ID,
            "aud": "authenticated",
            "iss": TEST_ISSUER,
            "iat": int(_t.time()),
            "exp": int(_t.time()) + 300,
            "app_metadata": {"role": "SUPER_ADMIN"},
        },
        None,
        algorithm="none",
    )
    r = hs256_client.get(ME, headers=auth(none_token))
    assert r.status_code == 401


def test_non_uuid_sub_rejected(hs256_client):
    r = hs256_client.get(ME, headers=auth(make_token("VIEWER", sub="not-a-uuid")))
    assert r.status_code == 401
    assert r.get_json()["error"]["code"] == "INVALID_TOKEN"


# ── JWKS (asymmetric) mode ──────────────────────────────────────────────────


def test_rs256_valid_token_accepted(jwks_client):
    client, private_key, _ = jwks_client
    token = pyjwt.encode(
        {
            "sub": TEST_USER_ID,
            "email": "mgr@acms.test",
            "aud": "authenticated",
            "iss": TEST_ISSUER,
            "iat": int(time.time()) - 5,
            "exp": int(time.time()) + 600,
            "app_metadata": {"role": "FACILITY_MANAGER"},
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "unit-key-1"},
    )
    r = client.get(ME, headers=auth(token))
    assert r.status_code == 200
    assert r.get_json()["data"]["role"] == "FACILITY_MANAGER"


def test_confusion_attack_hs256_signed_with_public_key(jwks_client):
    """The classic RS256->HS256 confusion attack must fail: even when the JWKS
    lookup returns the public key, HS256 is outside the asymmetric allow-list.

    pyjwt itself refuses to *encode* such a token (hxm protection), so the
    attack token is hand-crafted the way a real attacker would craft it.
    """
    import base64
    import hashlib
    import hmac
    import json as json_mod

    client, _, public_pem = jwks_client

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64url(json_mod.dumps({"alg": "HS256", "typ": "JWT", "kid": "unit-key-1"}).encode())
    payload = b64url(
        json_mod.dumps(
            {
                "sub": TEST_USER_ID,
                "aud": "authenticated",
                "iss": TEST_ISSUER,
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
                "app_metadata": {"role": "SUPER_ADMIN"},
            }
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode()
    signature = b64url(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    forged = f"{header}.{payload}.{signature}"

    r = client.get(ME, headers=auth(forged))
    assert r.status_code == 401


def test_jwks_alg_none_rejected(jwks_client):
    client, _, _ = jwks_client
    none_token = pyjwt.encode(
        {
            "sub": TEST_USER_ID,
            "aud": "authenticated",
            "iss": TEST_ISSUER,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        None,
        algorithm="none",
    )
    assert client.get(ME, headers=auth(none_token)).status_code == 401


# ── JWKS key rotation (provider unit test) ──────────────────────────────────


def test_jwks_provider_refreshes_on_unknown_kid(monkeypatch):
    from app.auth.jwt import _JwksProvider

    class StubJWKClient:
        """Simulates a JWKS endpoint that rotates in a new key."""

        def __init__(self):
            self.fetch_calls = 0
            self.known = {"kid-1": "PUBLIC-1"}

        def fetch_data(self):
            self.fetch_calls += 1
            self.known["kid-2"] = "PUBLIC-2"  # rotation completes here

        def get_signing_key_from_jwt(self, token):
            kid = pyjwt.get_unverified_header(token).get("kid")
            if kid in self.known:
                return SimpleNamespace(key=self.known[kid])
            raise pyjwt.PyJWKClientError("unknown kid")

    provider = _JwksProvider("https://jwks.unit-test")
    provider._ttl = 0  # forced refresh always allowed in this test
    stub = StubJWKClient()
    provider._client = stub

    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    rotated_token = pyjwt.encode({"sub": TEST_USER_ID}, key, algorithm="RS256", headers={"kid": "kid-2"})
    signing = provider.signing_key(rotated_token)
    assert signing.key == "PUBLIC-2"
    assert stub.fetch_calls == 1


def test_jwks_provider_throttles_forced_refetch():
    import time as _time

    from app.auth.jwt import _JwksProvider

    class AlwaysUnknown:
        def __init__(self):
            self.fetch_calls = 0

        def fetch_data(self):
            self.fetch_calls += 1

        def get_signing_key_from_jwt(self, _token):
            raise pyjwt.PyJWKClientError("unknown kid")

    provider = _JwksProvider("https://jwks.unit-test", ttl=9999)
    stub = AlwaysUnknown()
    provider._client = stub
    # Simulate "we just refreshed": failures within the TTL must not refetch.
    provider._last_forced_refresh = _time.monotonic()

    for _ in range(3):
        with pytest.raises(pyjwt.PyJWKClientError):
            provider.signing_key("fake.jwt.token")
    # Flooding bogus kids must not flood the JWKS endpoint.
    assert stub.fetch_calls == 0
