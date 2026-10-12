"""Phase 7: POST /api/v1/chat — auth, validation, intent gate, MY_JOBS scoping.

The legacy anonymous ``/api/chat`` is gone (see tests/test_auth.py for the
suite-wide auth matrix); this file covers its replacement. Chat calls are
rate limited (30/min per identity), so each test signs in under its own
subject to keep every bucket well under budget.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import TEST_SECRET, TEST_SUPABASE_URL, AuthClient, make_token  # noqa: E402

CHAT = "/api/v1/chat"

TECH_A = "55555555-5555-5555-5555-555555555551"
TECH_B = "55555555-5555-5555-5555-555555555552"
VIEWER_U = "55555555-5555-5555-5555-555555555553"


def _authed_client(monkeypatch, role, sub):
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
    return AuthClient(app.test_client(), make_token(role, sub=sub))


@pytest.fixture(autouse=True)
def _chat_scope(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """Profiles for the two technicians + one work order each, cleaned up."""
    suffix = uuid.uuid4().hex[:8]
    ac_id = f"P7-CHAT-{suffix}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for pid, name in ((TECH_A, "Chat Tech A"), (TECH_B, "Chat Tech B")):
                cur.execute(
                    "INSERT INTO profiles (id, email, name, role, status, is_active)"
                    " VALUES (%s, %s, %s, 'TECHNICIAN', 'APPROVED', TRUE)"
                    " ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role,"
                    " status = 'APPROVED', is_active = TRUE, email = EXCLUDED.email",
                    (pid, f"{name.lower().replace(' ', '-')}@acms.test", name),
                )
            cur.execute(
                "INSERT INTO profiles (id, email, name, role, status, is_active)"
                " VALUES (%s, 'chat-viewer-only@acms.test', 'Chat Viewer', 'VIEWER', 'APPROVED', TRUE)"
                " ON CONFLICT (id) DO UPDATE SET role = 'VIEWER', status = 'APPROVED', is_active = TRUE",
                (VIEWER_U,),
            )
            cur.execute("INSERT INTO acs (ac_id) VALUES (%s)", (ac_id,))
            cur.execute(
                "INSERT INTO maintenance_work_orders (ac_id, priority, status, technician_id)"
                " VALUES (%s, 'HIGH', 'ASSIGNED', %s)",
                (ac_id, TECH_A),
            )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM maintenance_work_orders WHERE ac_id = %s",
                (ac_id,),
            )
            cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
            cur.execute(
                "DELETE FROM profiles WHERE id = ANY(%s::uuid[])",
                ([[TECH_A, TECH_B, VIEWER_U]],),
            )
        conn.commit()
    finally:
        conn.close()


def _ask(client, question, **extra):
    return client.post(CHAT, json={"question": question, **extra})


def _error_code(resp):
    return (resp.get_json() or {}).get("error", {}).get("code")


# ── auth + validation ────────────────────────────────────────────────────────


def test_chat_requires_auth(client):
    r = client.post(CHAT, json={"question": "How many ACs are there?"})
    assert r.status_code == 401


def test_chat_requires_question(monkeypatch):
    client = _authed_client(monkeypatch, "VIEWER", VIEWER_U)
    for payload in ({}, {"question": ""}, {"question": "   "}, None):
        r = client.post(CHAT, json=payload) if payload is not None else client.post(CHAT)
        assert r.status_code == 400, payload
        assert _error_code(r) == "QUESTION_REQUIRED"


def test_chat_rejects_overlong_question(monkeypatch):
    client = _authed_client(monkeypatch, "VIEWER", VIEWER_U)
    r = _ask(client, "x" * 501)
    assert r.status_code == 400
    assert _error_code(r) == "QUESTION_TOO_LONG"


# ── answers ──────────────────────────────────────────────────────────────────


def test_chat_total_acs_answer(monkeypatch):
    client = _authed_client(monkeypatch, "VIEWER", VIEWER_U)
    r = _ask(client, "How many ACs are there?")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["intent"] == "TOTAL_ACS"
    assert "ACs" in data["answer"]
    assert isinstance(data["data"]["total"], int)
    assert data["latency_ms"] >= 0


def test_chat_unknown_intent_is_harmless(monkeypatch):
    client = _authed_client(monkeypatch, "VIEWER", VIEWER_U)
    r = _ask(client, "Tell me a joke about refrigerators")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["intent"] == "UNKNOWN"
    assert "could not understand" in data["answer"]


# ── per-intent permission gate ───────────────────────────────────────────────


def test_intent_permission_gate_returns_403(monkeypatch):
    """A tightened INTENT_PERMISSIONS entry turns into a 403, not a leak."""
    client = _authed_client(monkeypatch, "VIEWER", VIEWER_U)
    from app.api.v1 import chat as chat_mod

    monkeypatch.setitem(chat_mod.INTENT_PERMISSIONS, "TOTAL_ACS", "VIEW_REPORTS")
    try:
        r = _ask(client, "How many ACs are there?")
        assert r.status_code == 403
        assert _error_code(r) == "FORBIDDEN"
    finally:
        chat_mod.INTENT_PERMISSIONS.pop("TOTAL_ACS", None)


def test_admin_passes_tightened_gate(monkeypatch):
    client = _authed_client(monkeypatch, "SUPER_ADMIN", "55555555-5555-5555-5555-555555555559")
    from app.api.v1 import chat as chat_mod

    monkeypatch.setitem(chat_mod.INTENT_PERMISSIONS, "TOTAL_ACS", "VIEW_REPORTS")
    try:
        assert _ask(client, "How many ACs are there?").status_code == 200
    finally:
        chat_mod.INTENT_PERMISSIONS.pop("TOTAL_ACS", None)


# ── MY_JOBS identity scoping ─────────────────────────────────────────────────


def test_my_jobs_is_scoped_to_the_caller(monkeypatch):
    tech_a = _authed_client(monkeypatch, "TECHNICIAN", TECH_A)
    tech_b = _authed_client(monkeypatch, "TECHNICIAN", TECH_B)

    r = _ask(tech_a, "What are my work orders?")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["intent"] == "MY_JOBS"
    assert data["data"]["summary"]["total"] == 1
    assert len(data["data"]["items"]) == 1

    # B holds no jobs: B must see an empty list, never A's row.
    r = _ask(tech_b, "What are my work orders?")
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["data"]["summary"]["total"] == 0
    assert data["data"]["items"] == []
    assert "no work orders" in data["answer"].lower()


def test_my_jobs_never_returns_other_users_rows(monkeypatch):
    tech_b = _authed_client(monkeypatch, "TECHNICIAN", TECH_B)
    r = _ask(tech_b, "Show me work orders assigned to me")
    rows = r.get_json()["data"]["data"]["items"]
    assert all(row.get("status") for row in rows)  # empty or their own only
    assert rows == []
