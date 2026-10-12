"""Phase 8.1 — HTTP coverage for ``app/api/v1/users.py``.

``tests/test_users_api.py`` only exercises ``evaluate_approval`` as pure logic;
the actual approve/reject handlers, the approver queue, user administration
and the notification endpoints were never driven end-to-end (the module sat at
50% when Phase 8 started). Those are the security-relevant flows — who may
grant a role, self-approval, the last-super-admin guard and notification
ownership — so each one is asserted here over real HTTP.

Profiles created by these tests are tracked by the ``ledger`` fixture, which
removes their audit rows first (``audit_logs.user_id`` is a foreign key to
``profiles``) and then the profiles themselves; ``notifications`` cascade on
profile delete.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import auth, make_token  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="needs a live database — set TEST_DATABASE_URL"
)

_PROFILE_COLS = (
    "id",
    "name",
    "role",
    "status",
    "requested_role",
    "rejection_reason",
    "approved_by",
    "is_active",
)


@pytest.fixture()
def ledger():
    """Create throwaway profile rows; remove them (and their audit rows) after.

    ``audit_logs.user_id`` is a foreign key to ``profiles``, so the audit rows
    written by the actor must go first or the profile delete violates it —
    exactly the trap that made the RBAC matrix leak a row per run.
    """
    created: list[str] = []

    def add(role="VIEWER", status="PENDING", **extra):
        pid = str(uuid.uuid4())
        cols = {
            "id": pid,
            "role": role,
            "status": status,
            "email": f"{pid[:8]}@acms.test",
            "name": extra.pop("name", "P8 Lifecycle"),
        }
        cols.update(extra)
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                names = ", ".join(cols)
                marks = ", ".join(["%s"] * len(cols))
                cur.execute(f"INSERT INTO profiles ({names}) VALUES ({marks})", tuple(cols.values()))
            conn.commit()
        finally:
            conn.close()
        created.append(pid)
        return pid

    yield add

    if not created:
        return
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE user_id = ANY(%s::uuid[])", (created,))
            cur.execute("DELETE FROM profiles WHERE id = ANY(%s::uuid[])", (created,))
        conn.commit()
    finally:
        conn.close()


def _code(r) -> str:
    return ((r.get_json() or {}).get("error") or {}).get("code", "<no error envelope>")


def _notify(profile_id: str, is_read: bool = False) -> str:
    """Seed a notification row (owned by ``profile_id``) and return its id."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notifications (user_id, type, title, body, is_read)"
                " VALUES (%s, 'TEST_SEEDED', 'P8 seeded note', 'body', %s) RETURNING id",
                (profile_id, is_read),
            )
            nid = str(cur.fetchone()[0])
        conn.commit()
    finally:
        conn.close()
    return nid


def _profile(pid: str) -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT " + ", ".join(_PROFILE_COLS) + " FROM profiles WHERE id = %s",
                (pid,),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return dict(zip(_PROFILE_COLS, row)) if row else {}


# ── approvals: approve ──────────────────────────────────────────────────────


def test_admin_approves_a_pending_signup_end_to_end(secret_client, ledger):
    actor = ledger(role="SUPER_ADMIN", status="APPROVED")
    target = ledger(role="VIEWER", status="PENDING", requested_role="TECHNICIAN")
    headers = auth(make_token("SUPER_ADMIN", sub=actor))

    r = secret_client.get("/api/v1/approvals?status=PENDING", headers=headers)
    assert r.status_code == 200, r.get_json()
    ids = [item["id"] for item in r.get_json()["data"]]
    assert target in ids, "pending signup missing from the approval queue"

    r = secret_client.post(f"/api/v1/approvals/{target}/approve", json={}, headers=headers)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["data"]
    assert body["status"] == "APPROVED"
    assert body["role"] == "TECHNICIAN", "requested_role must be granted when no override is sent"

    after = _profile(target)
    assert after["status"] == "APPROVED"
    assert after["role"] == "TECHNICIAN"
    assert str(after["approved_by"]) == actor

    # Approving twice must not silently re-run the transition.
    r = secret_client.post(f"/api/v1/approvals/{target}/approve", json={}, headers=headers)
    assert r.status_code == 409 and _code(r) == "ALREADY_APPROVED"

    r = secret_client.get("/api/v1/approvals?status=PENDING", headers=headers)
    assert target not in [i["id"] for i in r.get_json()["data"]]


def test_approve_payload_and_target_validation(secret_client, ledger):
    actor = ledger(role="SUPER_ADMIN", status="APPROVED")
    headers = auth(make_token("SUPER_ADMIN", sub=actor))

    r = secret_client.post(f"/api/v1/approvals/{uuid.uuid4()}/approve", json={}, headers=headers)
    assert r.status_code == 404 and _code(r) == "NOT_FOUND"

    target = ledger(role="VIEWER", status="PENDING")
    r = secret_client.post(
        f"/api/v1/approvals/{target}/approve", json={"role": "NOT_A_ROLE"}, headers=headers
    )
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"
    assert _profile(target)["status"] == "PENDING", "a rejected payload must not change anything"

    r = secret_client.get("/api/v1/approvals?status=BOGUS", headers=headers)
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = secret_client.get("/api/v1/approvals?page=abc", headers=headers)
    assert r.status_code == 400 and _code(r) == "INVALID_PAGINATION"


def test_self_approval_is_refused_over_http(secret_client, ledger):
    """Actor == target, so only self-approval guards can fire — both must refuse.

    The two endpoints are stopped at different layers: ``approve`` never gets
    past the status gate for a PENDING account, while ``reject`` reaches the
    handler and hits its own in-code "cannot reject yourself" guard.
    """
    me = ledger(role="SUPER_ADMIN", status="PENDING")
    headers = auth(make_token("SUPER_ADMIN", sub=me))

    # approve: a PENDING account never reaches the approver checks at all —
    # the status gate stops it first.
    r = secret_client.post(f"/api/v1/approvals/{me}/approve", json={}, headers=headers)
    assert r.status_code == 403, r.get_json()
    assert _code(r) == "ACCOUNT_PENDING"
    assert _profile(me)["status"] == "PENDING", "self-approval must not change the row"

    # reject: an approved account does reach the handler, where its own
    # "you cannot reject your own account" guard fires.
    approved = ledger(role="SUPER_ADMIN", status="APPROVED")
    approved_headers = auth(make_token("SUPER_ADMIN", sub=approved))
    r = secret_client.post(
        f"/api/v1/approvals/{approved}/reject", json={"reason": "nope"}, headers=approved_headers
    )
    assert r.status_code == 403 and _code(r) == "FORBIDDEN"
    assert _profile(approved)["status"] == "APPROVED"


def test_manager_can_only_grant_subordinate_roles(secret_client, ledger):
    manager = ledger(role="FACILITY_MANAGER", status="APPROVED")
    headers = auth(make_token("FACILITY_MANAGER", sub=manager))

    ok = ledger(role="VIEWER", status="PENDING", requested_role="TECHNICIAN")
    r = secret_client.post(f"/api/v1/approvals/{ok}/approve", json={}, headers=headers)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["role"] == "TECHNICIAN"

    # An out-of-scope request is refused outright...
    too_high = ledger(role="VIEWER", status="PENDING", requested_role="FACILITY_MANAGER")
    r = secret_client.post(f"/api/v1/approvals/{too_high}/approve", json={}, headers=headers)
    assert r.status_code == 403 and _code(r) == "FORBIDDEN"
    assert _profile(too_high)["status"] == "PENDING"

    # ...and an admin-only role override must never survive a manager approval.
    try_override = ledger(role="VIEWER", status="PENDING", requested_role="TECHNICIAN")
    r = secret_client.post(
        f"/api/v1/approvals/{try_override}/approve",
        json={"role": "SUPER_ADMIN"},
        headers=headers,
    )
    assert r.status_code == 200, r.get_json()
    granted = _profile(try_override)
    assert granted["role"] == "TECHNICIAN", "a manager must not be able to grant SUPER_ADMIN"
    assert granted["status"] == "APPROVED"


def test_manager_approval_queue_is_scoped_by_role_and_blocks(secret_client, ledger):
    manager = ledger(role="FACILITY_MANAGER", status="APPROVED", assigned_blocks=["BLOCK-A"])
    headers = auth(make_token("FACILITY_MANAGER", sub=manager))

    visible = ledger(role="VIEWER", status="PENDING", requested_role="TECHNICIAN")
    other_block = ledger(
        role="VIEWER",
        status="PENDING",
        requested_role="TECHNICIAN",
        assigned_blocks=["BLOCK-ELSEWHERE"],
    )
    out_of_scope_role = ledger(role="VIEWER", status="PENDING", requested_role="SUPER_ADMIN")

    r = secret_client.get("/api/v1/approvals?status=PENDING", headers=headers)
    assert r.status_code == 200, r.get_json()
    ids = [i["id"] for i in r.get_json()["data"]]
    assert visible in ids, "unscoped technician request must be visible to the manager"
    assert other_block not in ids, "another manager's block leaked into the queue"
    assert out_of_scope_role not in ids, "non TECHNICIAN/VIEWER request must never be listed"


# ── approvals: reject ───────────────────────────────────────────────────────


def test_reject_requires_a_reason_and_stores_it(secret_client, ledger):
    actor = ledger(role="SUPER_ADMIN", status="APPROVED")
    headers = auth(make_token("SUPER_ADMIN", sub=actor))

    # Payload validation runs before the lookup, so an unknown id only shows
    # up once the body itself is acceptable.
    r = secret_client.post(f"/api/v1/approvals/{uuid.uuid4()}/reject", json={}, headers=headers)
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = secret_client.post(f"/api/v1/approvals/{uuid.uuid4()}/reject", json={"reason": "x"}, headers=headers)
    assert r.status_code == 404 and _code(r) == "NOT_FOUND"

    target = ledger(role="VIEWER", status="PENDING")
    r = secret_client.post(f"/api/v1/approvals/{target}/reject", json={}, headers=headers)
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = secret_client.post(f"/api/v1/approvals/{target}/reject", json={"reason": ""}, headers=headers)
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = secret_client.post(
        f"/api/v1/approvals/{target}/reject", json={"reason": "Missing staff record"}, headers=headers
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["status"] == "REJECTED"
    after = _profile(target)
    assert after["status"] == "REJECTED"
    assert after["rejection_reason"] == "Missing staff record"


def test_rejecting_your_own_profile_is_refused(secret_client, ledger):
    actor = ledger(role="SUPER_ADMIN", status="APPROVED")
    headers = auth(make_token("SUPER_ADMIN", sub=actor))

    r = secret_client.post(f"/api/v1/approvals/{actor}/reject", json={"reason": "nope"}, headers=headers)
    assert r.status_code == 403 and _code(r) == "FORBIDDEN"
    assert _profile(actor)["status"] == "APPROVED"


# ── user administration ─────────────────────────────────────────────────────


def test_users_list_filters_and_pagination(secret_client, ledger):
    actor = ledger(role="SUPER_ADMIN", status="APPROVED")
    headers = auth(make_token("SUPER_ADMIN", sub=actor))
    token = f"P8-{uuid.uuid4().hex[:8]}"
    tech = ledger(role="TECHNICIAN", status="APPROVED", name=f"Tech {token}")

    r = secret_client.get("/api/v1/users?page_size=5", headers=headers)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body["success"] is True
    assert isinstance(body["data"], list)
    assert set(body["pagination"]) >= {"page", "page_size", "total", "total_pages"}

    r = secret_client.get(f"/api/v1/users?search={token}", headers=headers)
    assert [i["id"] for i in r.get_json()["data"]] == [tech]

    r = secret_client.get(f"/api/v1/users?search={token}&role=VIEWER", headers=headers)
    assert r.get_json()["data"] == [], "role filter was not applied"

    for query in ("role=NOT_A_ROLE", "status=NOT_A_STATUS", "page=abc", "page_size=xyz"):
        r = secret_client.get(f"/api/v1/users?{query}", headers=headers)
        assert r.status_code == 400, query
        assert _code(r) in {"VALIDATION_ERROR", "INVALID_PAGINATION"}


def test_admin_can_change_a_role_and_the_user_is_notified(secret_client, ledger):
    actor = ledger(role="SUPER_ADMIN", status="APPROVED")
    target = ledger(role="VIEWER", status="APPROVED")
    headers = auth(make_token("SUPER_ADMIN", sub=actor))

    r = secret_client.patch(f"/api/v1/users/{target}", json={"role": "TECHNICIAN"}, headers=headers)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["role"] == "TECHNICIAN"
    assert _profile(target)["role"] == "TECHNICIAN"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT type FROM notifications WHERE user_id = %s AND type = 'ROLE_CHANGED'",
                (target,),
            )
            assert cur.fetchone() is not None, "role change produced no notification"
    finally:
        conn.close()

    r = secret_client.patch(f"/api/v1/users/{target}", json={}, headers=headers)
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = secret_client.patch(f"/api/v1/users/{target}", json={"role": "NOT_A_ROLE"}, headers=headers)
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = secret_client.patch(
        f"/api/v1/users/{target}", json={"reporting_manager_id": "not-a-uuid"}, headers=headers
    )
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = secret_client.patch(f"/api/v1/users/{uuid.uuid4()}", json={"role": "VIEWER"}, headers=headers)
    assert r.status_code == 404 and _code(r) == "NOT_FOUND"


def test_admin_cannot_edit_their_own_role_or_activation(secret_client, ledger):
    actor = ledger(role="SUPER_ADMIN", status="APPROVED")
    headers = auth(make_token("SUPER_ADMIN", sub=actor))

    r = secret_client.patch(f"/api/v1/users/{actor}", json={"role": "VIEWER"}, headers=headers)
    assert r.status_code == 403 and _code(r) == "FORBIDDEN"
    assert _profile(actor)["role"] == "SUPER_ADMIN"

    r = secret_client.patch(f"/api/v1/users/{actor}", json={"is_active": False}, headers=headers)
    assert r.status_code == 403 and _code(r) == "FORBIDDEN"
    assert _profile(actor)["is_active"] is True

    # Name/contact fields are not managed here at all (PATCH /me owns them):
    # they fall through to "no updatable fields" rather than being written.
    r = secret_client.patch(f"/api/v1/users/{actor}", json={"name": "Renamed"}, headers=headers)
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"
    assert _profile(actor)["name"] == "P8 Lifecycle"


def test_last_active_super_admin_cannot_be_demoted(secret_client, ledger):
    """The account that could lock everyone out of MANAGE_USERS must refuse."""
    target = ledger(role="SUPER_ADMIN", status="APPROVED")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM profiles"
                " WHERE role = 'SUPER_ADMIN' AND is_active AND status = 'APPROVED'"
            )
            others = cur.fetchone()[0] - 1  # our own target
    finally:
        conn.close()
    if others:
        pytest.skip(f"test database already holds {others} other super admin(s)")

    # The actor authenticates as SUPER_ADMIN via the token alone (no profile
    # row), so it is deliberately not one of the accounts being counted.
    headers = auth(make_token("SUPER_ADMIN", sub=str(uuid.uuid4())))
    r = secret_client.patch(f"/api/v1/users/{target}", json={"role": "VIEWER"}, headers=headers)
    assert r.status_code == 409, r.get_json()
    assert _code(r) == "LAST_SUPER_ADMIN"
    assert _profile(target)["role"] == "SUPER_ADMIN"


# ── self-service profile ────────────────────────────────────────────────────


def test_patch_me_validation(secret_client, ledger):
    tech = ledger(role="TECHNICIAN", status="APPROVED")
    viewer = ledger(role="VIEWER", status="APPROVED")

    r = secret_client.patch(
        "/api/v1/me", json={"name": "Renamed Tech"}, headers=auth(make_token("TECHNICIAN", sub=tech))
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["name"] == "Renamed Tech"
    assert _profile(tech)["name"] == "Renamed Tech"

    r = secret_client.patch(
        "/api/v1/me", json={"name": "Nope"}, headers=auth(make_token("VIEWER", sub=viewer))
    )
    assert r.status_code == 403 and _code(r) == "FORBIDDEN"
    assert _profile(viewer)["name"] == "P8 Lifecycle", "a viewer's profile must be untouched"

    r = secret_client.patch("/api/v1/me", json={}, headers=auth(make_token("TECHNICIAN", sub=tech)))
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"

    r = secret_client.patch(
        "/api/v1/me", json={"phone": "x" * 33}, headers=auth(make_token("TECHNICIAN", sub=tech))
    )
    assert r.status_code == 400 and _code(r) == "VALIDATION_ERROR"


# ── notifications ───────────────────────────────────────────────────────────


def test_notifications_list_and_unread_filter(secret_client, ledger):
    me = ledger(role="TECHNICIAN", status="APPROVED")
    _notify(me)
    _notify(me, is_read=True)
    headers = auth(make_token("TECHNICIAN", sub=me))

    r = secret_client.get("/api/v1/notifications", headers=headers)
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert len(data["items"]) == 2
    assert data["unread_count"] == 1
    assert set(data["pagination"]) >= {"page", "page_size", "total", "total_pages"}

    r = secret_client.get("/api/v1/notifications?unread=1", headers=headers)
    assert len(r.get_json()["data"]["items"]) == 1
    assert r.get_json()["data"]["pagination"]["total"] == 1

    for query in ("page=abc", "since=not-a-date"):
        r = secret_client.get(f"/api/v1/notifications?{query}", headers=headers)
        assert r.status_code == 400, query
        assert _code(r) in {"INVALID_PAGINATION", "INVALID_DATE"}

    r = secret_client.get("/api/v1/notifications?since=2099-01-01", headers=headers)
    assert r.status_code == 200
    assert r.get_json()["data"]["items"] == []


def test_notifications_are_never_visible_across_users(secret_client, ledger):
    me = ledger(role="TECHNICIAN", status="APPROVED")
    stranger = ledger(role="TECHNICIAN", status="APPROVED")
    headers = auth(make_token("TECHNICIAN", sub=me))

    mine = _notify(me)
    theirs = _notify(stranger)

    r = secret_client.post(f"/api/v1/notifications/{mine}/read", headers=headers)
    assert r.status_code == 200 and r.get_json()["data"]["is_read"] is True

    # Another user's notification must be indistinguishable from a missing one.
    r = secret_client.post(f"/api/v1/notifications/{theirs}/read", headers=headers)
    assert r.status_code == 404 and _code(r) == "NOT_FOUND"

    r = secret_client.post(f"/api/v1/notifications/{uuid.uuid4()}/read", headers=headers)
    assert r.status_code == 404 and _code(r) == "NOT_FOUND"

    assert _notifications_read(stranger) == 0, "cross-user read attempt modified a row"


def test_notifications_read_all_only_touches_the_caller(secret_client, ledger):
    me = ledger(role="TECHNICIAN", status="APPROVED")
    stranger = ledger(role="TECHNICIAN", status="APPROVED")
    headers = auth(make_token("TECHNICIAN", sub=me))
    _notify(me)
    _notify(me)
    _notify(stranger)

    r = secret_client.post("/api/v1/notifications/read-all", headers=headers)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["updated"] == 2

    assert _notifications_read(me) == 2
    assert _notifications_read(stranger) == 0

    r = secret_client.get("/api/v1/notifications?unread=1", headers=headers)
    assert r.get_json()["data"]["unread_count"] == 0


def _notifications_read(profile_id: str) -> int:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM notifications WHERE user_id = %s AND is_read", (profile_id,))
            return cur.fetchone()[0]
    finally:
        conn.close()


# ── fresh signup lifecycle (no trigger): /me signal -> register -> approve ──


def test_subject_without_profile_row_registers_pending_then_approved(secret_client, ledger):
    """A confirmed signup with no profiles row (email-confirmation flow when
    the auth trigger is absent): GET /me answers role null — the frontend's
    self-registration signal — register-profile creates VIEWER/PENDING, the
    subject stays gated until an admin approves, then /me shows APPROVED."""
    import uuid as uuid_mod

    sub = str(uuid_mod.uuid4())
    newcomer = auth(make_token(None, sub=sub))

    # 1. No profile row: /me answers 200 with role null (never 403/404), so
    #    the client can tell "register me" apart from every other state.
    assert _profile(sub) == {}
    r = secret_client.get("/api/v1/me", headers=newcomer)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["role"] is None

    # 2. Self-registration creates the PENDING row (requested role recorded,
    #    never granted); repeating it is a no-op.
    r = secret_client.post(
        "/api/v1/auth/register-profile",
        json={"name": "Fresh Signup", "requested_role": "TECHNICIAN"},
        headers=newcomer,
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()["data"]
    assert body["role"] == "VIEWER" and body["status"] == "PENDING"
    assert body["requested_role"] == "TECHNICIAN"
    r = secret_client.post("/api/v1/auth/register-profile", json={}, headers=newcomer)
    assert r.status_code == 200, r.get_json()

    # 3. Pending: visible on /me, blocked everywhere else.
    r = secret_client.get("/api/v1/me", headers=newcomer)
    assert r.get_json()["data"]["status"] == "PENDING"
    r = secret_client.get("/api/v1/assets", headers=newcomer)
    assert r.status_code == 403 and _code(r) == "ACCOUNT_PENDING"

    # 4. Admin approval moves them through the normal workflow.
    actor = ledger(role="SUPER_ADMIN", status="APPROVED")
    admin = auth(make_token("SUPER_ADMIN", sub=actor))
    r = secret_client.get("/api/v1/approvals?status=PENDING", headers=admin)
    assert sub in [item["id"] for item in r.get_json()["data"]]
    r = secret_client.post(f"/api/v1/approvals/{sub}/approve", json={}, headers=admin)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["status"] == "APPROVED"
    r = secret_client.get("/api/v1/me", headers=newcomer)
    assert r.get_json()["data"]["status"] == "APPROVED"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE user_id = %s::uuid", (sub,))
            cur.execute("DELETE FROM profiles WHERE id = %s::uuid", (sub,))
        conn.commit()
    finally:
        conn.close()
