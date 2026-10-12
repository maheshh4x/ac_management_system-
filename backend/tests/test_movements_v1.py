"""Phase 5: movement workflow (list joins/filters, request rules, decisions,
completion atomicity incl. concurrent races, cancel, comments, rollback).

Every test seeds short-lived profile rows (requested_by / approved_by /
completed_by / author_id / notification recipients all reference profiles)
and removes them — plus every row it created — afterwards: profiles.role is
authoritative over the JWT claim, and leaving rows behind would flip the
role-matrix tests.
"""

from __future__ import annotations

import sys
import threading
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import (  # noqa: E402
    TEST_SECRET,
    TEST_SUPABASE_URL,
    TEST_USER_ID,
    AuthClient,
    make_token,
)

ADMIN = TEST_USER_ID
MGR = "55555555-5555-5555-5555-555555555551"  # FACILITY_MANAGER, unscoped
MGR_MAIN = "55555555-5555-5555-5555-555555555552"  # FACILITY_MANAGER, MAIN block
MGR_OTHER = "55555555-5555-5555-5555-555555555553"  # FACILITY_MANAGER, other block
TECH_A = "55555555-5555-5555-5555-555555555554"  # the requester
TECH_B = "55555555-5555-5555-5555-555555555555"  # a different technician
VIEWER_U = "55555555-5555-5555-5555-555555555556"

_SEED_PROFILES = (
    (ADMIN, "tester@acms.test", "Test User", "SUPER_ADMIN", None),
    (MGR, "mgr@acms.test", "Manager M", "FACILITY_MANAGER", None),
    (MGR_MAIN, "mgr-main@acms.test", "Manager Main", "FACILITY_MANAGER", ["MAIN-ACADEMIC-BLOCK"]),
    (MGR_OTHER, "mgr-other@acms.test", "Manager Other", "FACILITY_MANAGER", ["OTHER-BLOCK"]),
    (TECH_A, "tech-a@acms.test", "Tech A", "TECHNICIAN", None),
    (TECH_B, "tech-b@acms.test", "Tech B", "TECHNICIAN", None),
    (VIEWER_U, "viewer-u@acms.test", "Viewer U", "VIEWER", None),
)
PROFILE_IDS = [row[0] for row in _SEED_PROFILES]
AC_PREFIX = "P5-AC-"  # every asset these tests create
# LIKE pattern must travel as a parameter VALUE (a literal '%' in SQL breaks
# psycopg2's placeholder parsing) — same pattern as test_maintenance_v1.
AC_PATTERN = AC_PREFIX + "%"


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _p5_profiles(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """Profiles for every actor these tests use + full cleanup afterwards."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for pid, email, name, role, blocks in _SEED_PROFILES:
                cur.execute(
                    "INSERT INTO profiles (id, email, name, role, status, is_active, assigned_blocks)"
                    " VALUES (%s, %s, %s, %s, 'APPROVED', TRUE, %s)"
                    " ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role,"
                    "   assigned_blocks = EXCLUDED.assigned_blocks,"
                    "   status = 'APPROVED', is_active = TRUE",
                    (pid, email, name, role, blocks),
                )
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Break FK references first: audits/notifications point at profiles
            # and movements, so they go before the movements/acs themselves.
            cur.execute(
                "DELETE FROM audit_logs"
                " WHERE user_id = ANY(%s::uuid[])"
                "    OR entity_id IN (SELECT id::text FROM asset_movements"
                "                      WHERE ac_id LIKE %s)"
                "    OR entity_id LIKE %s",
                (PROFILE_IDS, AC_PATTERN, AC_PATTERN),
            )
            cur.execute(
                "DELETE FROM notifications WHERE user_id = ANY(%s::uuid[])",
                (PROFILE_IDS,),
            )
            # movement_comments cascade with their movement; delete any that
            # outlived it (author FK would block the profile delete).
            cur.execute(
                "DELETE FROM movement_comments WHERE author_id = ANY(%s::uuid[])",
                (PROFILE_IDS,),
            )
            cur.execute(
                "DELETE FROM asset_location_history WHERE ac_id LIKE %s",
                (AC_PATTERN,),
            )
            cur.execute(
                "DELETE FROM asset_movements WHERE ac_id LIKE %s",
                (AC_PATTERN,),
            )
            cur.execute("DELETE FROM acs WHERE ac_id LIKE %s", (AC_PATTERN,))
            cur.execute(
                "DELETE FROM profiles WHERE id = ANY(%s::uuid[])",
                (PROFILE_IDS,),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def app(db, monkeypatch):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """App under test (JWT pinned to the suite secret) — exposed so threaded
    tests can mint one test-client per thread."""
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.delenv("SUPABASE_JWKS_URL", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    from app import create_app
    from app.auth import jwt as jwt_mod

    jwt_mod.reset_caches()
    application = create_app()
    application.config["TESTING"] = True
    return application


def _auth(application, role: str, sub: str) -> AuthClient:
    return AuthClient(application.test_client(), make_token(role, sub=sub))


@pytest.fixture()
def admin(app):
    return _auth(app, "SUPER_ADMIN", ADMIN)


@pytest.fixture()
def tech(app):
    return _auth(app, "TECHNICIAN", TECH_A)


# ── helpers ──────────────────────────────────────────────────────────────────


def _ac(tag: str) -> str:
    return f"{AC_PREFIX}{tag}-{uuid.uuid4().hex[:6]}"


def _seed_asset(ac_id: str, room_id: str = "ROOM-102") -> None:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO acs (ac_id, room_id, is_active, lifecycle_status)"
                " VALUES (%s, %s, TRUE, 'ACTIVE')"
                " ON CONFLICT (ac_id) DO UPDATE SET room_id = EXCLUDED.room_id,"
                "   is_active = TRUE, lifecycle_status = 'ACTIVE'",
                (ac_id, room_id),
            )
        conn.commit()
    finally:
        conn.close()


def _create(client, ac_id: str, to_room: str = "ROOM-105", **body):
    r = client.post(
        "/api/v1/movements",
        json={"ac_id": ac_id, "to_room_id": to_room, "reason": "relocating", **body},
    )
    assert r.status_code == 201, r.get_json()
    return r.get_json()["data"]["id"]


def _movement(mv_id: str) -> dict:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, requested_by, approved_by, completed_by, completed_at,"
                "       cancelled_at, rejection_reason, from_room_id, to_room_id"
                "  FROM asset_movements WHERE id::text = %s",
                (mv_id,),
            )
            row = cur.fetchone()
            return dict(zip([d[0] for d in cur.description], row)) if row else {}
    finally:
        conn.close()


def _asset_room(ac_id: str):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT room_id FROM acs WHERE ac_id = %s", (ac_id,))
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _history(mv_id: str) -> list[tuple]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT from_room_id, to_room_id FROM asset_location_history"
                " WHERE movement_id::text = %s ORDER BY at",
                (mv_id,),
            )
            return list(cur.fetchall())
    finally:
        conn.close()


def _notif_types(user_id: str, entity_id: str) -> list[str]:
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT type FROM notifications WHERE user_id = %s AND entity_id = %s" " ORDER BY created_at",
                (str(user_id), str(entity_id)),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _error_code(r) -> str:
    body = r.get_json()
    assert body and body.get("success") is False, body
    return body["error"]["code"]


# ── GET /movements: joins + filters ──────────────────────────────────────────


def test_list_joins_asset_location_and_actor_names(admin, tech):
    ac = _ac("JOIN")
    _seed_asset(ac, "ROOM-102")
    mv = _create(tech, ac, "ROOM-105")

    r = admin.get(f"/api/v1/movements?ac_id={ac}")
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    rows = body["data"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == mv
    assert row["ac_id"] == ac
    assert row["status"] == "PENDING"
    # Asset join (code/make/model — seeded bare, but keys must exist).
    assert "asset_code" in row and "make" in row and "model" in row
    # Room → floor → block names, composed for the legacy UI format.
    assert row["from_room_name"] == "CSE Computer Lab 1"
    assert row["to_room_name"] == "HOD Cabin"
    assert row["to_location"] == "Main Academic Block / Floor 1 / HOD Cabin"
    assert row["from_location"] == "Main Academic Block / Floor 1 / CSE Computer Lab 1"
    # Requester/approver/completer names.
    assert row["requested_by_name"] == "Tech A"
    assert row["requested_by_role"] == "TECHNICIAN"
    assert row["approved_by_name"] is None
    assert row["comments"] == []
    assert body["pagination"]["total"] == 1


def test_list_mine_status_and_date_filters(app, admin, tech):
    ac1, ac2 = _ac("F1"), _ac("F2")
    _seed_asset(ac1)
    _seed_asset(ac2)
    _create(tech, ac1)
    other = _auth(app, "TECHNICIAN", TECH_B)
    _create(other, ac2)

    # mine=1 → only the caller's own requests.
    r = tech.get("/api/v1/movements?mine=1")
    assert r.status_code == 200
    mine = r.get_json()["data"]
    assert [m["ac_id"] for m in mine] == [ac1]

    # status filter (single + comma list) and validation.
    r = admin.get(f"/api/v1/movements?status=PENDING&ac_id={ac1}")
    assert r.get_json()["pagination"]["total"] == 1
    r = admin.get("/api/v1/movements?status=REJECTED")
    assert r.get_json()["pagination"]["total"] == 0
    r = admin.get("/api/v1/movements?status=NOPE")
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_STATUS"

    # date filters are ISO dates; 2099 matches nothing, 2000 matches everything.
    r = admin.get("/api/v1/movements?from=2099-01-01&to=2099-01-02")
    assert r.get_json()["pagination"]["total"] == 0
    r = admin.get("/api/v1/movements?from=2000-01-01&to=2099-12-31")
    assert r.get_json()["pagination"]["total"] >= 2
    r = admin.get("/api/v1/movements?from=banana")
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_DATE"

    # pagination envelope
    r = admin.get("/api/v1/movements?page=1&page_size=1")
    page = r.get_json()
    assert len(page["data"]) == 1
    assert page["pagination"]["page_size"] == 1
    assert page["pagination"]["total"] >= 2


# ── request rules ────────────────────────────────────────────────────────────


def test_create_rules_same_room_unknown_and_duplicate(app, tech):
    ac = _ac("CR")
    _seed_asset(ac, "ROOM-102")

    # Destination must differ from the current room.
    r = tech.post("/api/v1/movements", json={"ac_id": ac, "to_room_id": "ROOM-102"})
    assert r.status_code == 400
    assert _error_code(r) == "SAME_ROOM"

    # Unknown room / unknown asset.
    r = tech.post("/api/v1/movements", json={"ac_id": ac, "to_room_id": "ROOM-NOPE"})
    assert r.status_code == 400
    assert _error_code(r) == "INVALID_ROOM"
    r = tech.post("/api/v1/movements", json={"ac_id": _ac("MISSING"), "to_room_id": "ROOM-105"})
    assert r.status_code == 404
    assert _error_code(r) == "ASSET_NOT_FOUND"

    # Viewers cannot request at all.
    viewer = _auth(app, "VIEWER", VIEWER_U)
    r = viewer.post("/api/v1/movements", json={"ac_id": ac, "to_room_id": "ROOM-105"})
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"

    # Happy path: PENDING, requested_by = the technician.
    mv = _create(tech, ac, "ROOM-105")
    row = _movement(mv)
    assert row["status"] == "PENDING"
    assert str(row["requested_by"]) == TECH_A
    assert row["from_room_id"] == "ROOM-102"

    # Approvers (all active managers/admins except the requester) are notified.
    for approver in (ADMIN, MGR, MGR_MAIN, MGR_OTHER):
        assert "MOVEMENT_REQUESTED" in _notif_types(approver, mv)
    assert _notif_types(TECH_A, mv) == []

    # At most one OPEN movement per asset (009 partial unique index + 409).
    r = tech.post("/api/v1/movements", json={"ac_id": ac, "to_room_id": "ROOM-105"})
    assert r.status_code == 409
    assert _error_code(r) == "MOVEMENT_OPEN"
    assert r.get_json()["error"]["details"]["movement_id"] == mv

    # A decommissioned (or inactive) asset can never be requested.
    ac2 = _ac("DEAD")
    _seed_asset(ac2, "ROOM-102")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE acs SET is_active = FALSE, lifecycle_status = 'DECOMMISSIONED'" " WHERE ac_id = %s",
                (ac2,),
            )
        conn.commit()
    finally:
        conn.close()
    r = tech.post("/api/v1/movements", json={"ac_id": ac2, "to_room_id": "ROOM-105"})
    assert r.status_code == 409
    assert _error_code(r) == "ASSET_DECOMMISSIONED"


# ── approve / reject ─────────────────────────────────────────────────────────


def test_self_approve_is_forbidden_and_managers_are_block_scoped(app, tech):
    ac = _ac("SCOPE")
    _seed_asset(ac, "ROOM-102")
    # A manager may request a movement (CREATE_MOVEMENT) — but can never
    # decide their own request even though they hold APPROVE_MOVEMENT.
    requester_mgr = _auth(app, "FACILITY_MANAGER", MGR)
    mv = _create(requester_mgr, ac, "ROOM-105")

    r = requester_mgr.post(f"/api/v1/movements/{mv}/approve")
    assert r.status_code == 403
    assert _error_code(r) == "SELF_APPROVE"

    # A technician cannot even reach the rule: no APPROVE_MOVEMENT at all.
    r = tech.post(f"/api/v1/movements/{mv}/approve")
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"

    # A manager scoped to other blocks cannot decide for this asset.
    other_mgr = _auth(app, "FACILITY_MANAGER", MGR_OTHER)
    r = other_mgr.post(f"/api/v1/movements/{mv}/approve")
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"
    assert _movement(mv)["status"] == "PENDING"

    # The manager owning the asset's block can.
    main_mgr = _auth(app, "FACILITY_MANAGER", MGR_MAIN)
    r = main_mgr.post(f"/api/v1/movements/{mv}/approve")
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "APPROVED"
    row = _movement(mv)
    assert str(row["approved_by"]) == MGR_MAIN
    # Requester notified + transition audited.
    assert "MOVEMENT_APPROVED" in _notif_types(MGR, mv)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT action FROM audit_logs WHERE entity_id = %s", (mv,))
            actions = [r_[0] for r_ in cur.fetchall()]
    finally:
        conn.close()
    assert "APPROVED_MOVEMENT" in actions

    # Deciding twice → 409; a viewer has no say at all.
    r = main_mgr.post(f"/api/v1/movements/{mv}/approve")
    assert r.status_code == 409
    assert _error_code(r) == "INVALID_STATE"
    viewer = _auth(app, "VIEWER", VIEWER_U)
    r = viewer.post(f"/api/v1/movements/{mv}/reject", json={"rejection_reason": "no"})
    assert r.status_code == 403


def test_manager_without_block_scope_decides_any_asset(app, tech):
    ac = _ac("UNSCOPED")
    _seed_asset(ac, "ROOM-102")
    mv = _create(tech, ac, "ROOM-105")
    mgr = _auth(app, "FACILITY_MANAGER", MGR)
    r = mgr.post(f"/api/v1/movements/{mv}/approve")
    assert r.status_code == 200


def test_asset_without_room_is_not_decidable_by_block_scoped_manager(app, tech):
    ac = _ac("NOROOM")
    _seed_asset(ac, "ROOM-102")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE acs SET room_id = NULL WHERE ac_id = %s", (ac,))
        conn.commit()
    finally:
        conn.close()
    mv = _create(tech, ac, "ROOM-105")
    main_mgr = _auth(app, "FACILITY_MANAGER", MGR_MAIN)
    r = main_mgr.post(f"/api/v1/movements/{mv}/approve")
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"
    # An unscoped manager still may.
    unscoped = _auth(app, "FACILITY_MANAGER", MGR)
    r = unscoped.post(f"/api/v1/movements/{mv}/approve")
    assert r.status_code == 200


def test_reject_needs_a_reason(app, tech):
    ac = _ac("REJ")
    _seed_asset(ac, "ROOM-102")
    mv = _create(tech, ac, "ROOM-105")
    mgr = _auth(app, "FACILITY_MANAGER", MGR)

    r = mgr.post(f"/api/v1/movements/{mv}/reject", json={})
    assert r.status_code == 400
    assert _error_code(r) == "REASON_REQUIRED"
    assert _movement(mv)["status"] == "PENDING"

    r = mgr.post(f"/api/v1/movements/{mv}/reject", json={"rejection_reason": "Not needed this year"})
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "REJECTED"
    row = _movement(mv)
    assert row["rejection_reason"] == "Not needed this year"
    assert str(row["approved_by"]) == MGR  # the deciding actor, for display
    assert "MOVEMENT_REJECTED" in _notif_types(TECH_A, mv)


# ── complete ─────────────────────────────────────────────────────────────────


def test_complete_moves_asset_and_writes_history_atomically(app, admin, tech):
    ac = _ac("DONE")
    _seed_asset(ac, "ROOM-102")
    mv = _create(tech, ac, "ROOM-105")
    mgr = _auth(app, "FACILITY_MANAGER", MGR)
    assert mgr.post(f"/api/v1/movements/{mv}/approve").status_code == 200

    r = admin.post(f"/api/v1/movements/{mv}/complete")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["status"] == "COMPLETED"
    assert data["room_id"] == "ROOM-105"

    # Asset, movement and ledger all agree.
    assert _asset_room(ac) == "ROOM-105"
    row = _movement(mv)
    assert row["status"] == "COMPLETED"
    assert str(row["completed_by"]) == ADMIN
    assert row["completed_at"] is not None
    assert _history(mv) == [("ROOM-102", "ROOM-105")]
    assert "MOVEMENT_COMPLETED" in _notif_types(TECH_A, mv)

    # The ledger is surfaced in the asset detail with joined names.
    r = admin.get(f"/api/v1/assets/{ac}")
    assert r.status_code == 200
    loc = r.get_json()["data"]["location_history"]
    assert len(loc) == 1
    assert loc[0]["from_room_name"] == "CSE Computer Lab 1"
    assert loc[0]["to_room_name"] == "HOD Cabin"
    assert loc[0]["movement_id"] == mv

    # The asset is closed now — a NEW movement for it is allowed again.
    mv2 = _create(tech, ac, "ROOM-102")
    assert _movement(mv2)["status"] == "PENDING"


def test_complete_rules_and_double_complete_409(app, admin, tech):
    ac = _ac("CMP")
    _seed_asset(ac, "ROOM-102")

    # Completing a PENDING request is an invalid state transition.
    mv = _create(tech, ac, "ROOM-105")
    r = admin.post(f"/api/v1/movements/{mv}/complete")
    assert r.status_code == 409
    assert _error_code(r) == "INVALID_STATE"

    mgr = _auth(app, "FACILITY_MANAGER", MGR)
    assert mgr.post(f"/api/v1/movements/{mv}/approve").status_code == 200

    # A viewer has no COMPLETE_MOVEMENT permission at all.
    viewer = _auth(app, "VIEWER", VIEWER_U)
    r = viewer.post(f"/api/v1/movements/{mv}/complete")
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"

    # Another technician may not complete someone else's request.
    other_tech = _auth(app, "TECHNICIAN", TECH_B)
    r = other_tech.post(f"/api/v1/movements/{mv}/complete")
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"
    assert _movement(mv)["status"] == "APPROVED"

    # The requesting technician may complete their own approved request.
    r = tech.post(f"/api/v1/movements/{mv}/complete")
    assert r.status_code == 200
    assert str(_movement(mv)["completed_by"]) == TECH_A

    # Double complete → 409, no second history row.
    r = tech.post(f"/api/v1/movements/{mv}/complete")
    assert r.status_code == 409
    assert _error_code(r) == "INVALID_STATE"
    assert len(_history(mv)) == 1


def test_concurrent_double_complete_leaves_exactly_one_history_row(app, tech):
    ac = _ac("RACE")
    _seed_asset(ac, "ROOM-102")
    mv = _create(tech, ac, "ROOM-105")
    mgr = _auth(app, "FACILITY_MANAGER", MGR)
    assert mgr.post(f"/api/v1/movements/{mv}/approve").status_code == 200

    results: list[int] = []
    barrier = threading.Barrier(2)

    def hit():
        # One test-client per thread (Werkzeug clients are not thread-safe).
        client = _auth(app, "SUPER_ADMIN", ADMIN)
        barrier.wait()
        r = client.post(f"/api/v1/movements/{mv}/complete")
        results.append(r.status_code)

    threads = [threading.Thread(target=hit) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(results) == [200, 409], results
    assert _asset_room(ac) == "ROOM-105"
    assert len(_history(mv)) == 1
    assert _history(mv)[0] == ("ROOM-102", "ROOM-105")


def test_failure_mid_completion_rolls_back_both_sides(app, admin, tech):
    ac = _ac("RB")
    _seed_asset(ac, "ROOM-102")
    mv = _create(tech, ac, "ROOM-105")
    mgr = _auth(app, "FACILITY_MANAGER", MGR)
    assert mgr.post(f"/api/v1/movements/{mv}/approve").status_code == 200

    # A trigger explodes exactly when the asset is moved to the destination,
    # i.e. AFTER the history insert inside the completion transaction.
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE OR REPLACE FUNCTION acms_p5_boom() RETURNS trigger AS $$"
                " BEGIN RAISE EXCEPTION 'acms p5 forced failure'; END;"
                " $$ LANGUAGE plpgsql"
            )
            cur.execute("DROP TRIGGER IF EXISTS acms_p5_boom_trg ON acs")
            cur.execute(
                "CREATE TRIGGER acms_p5_boom_trg BEFORE UPDATE OF room_id ON acs"
                " FOR EACH ROW WHEN (NEW.room_id = 'ROOM-105')"
                " EXECUTE FUNCTION acms_p5_boom()"
            )
        conn.commit()
    finally:
        conn.close()

    try:
        r = admin.post(f"/api/v1/movements/{mv}/complete")
        assert r.status_code == 500
        assert _error_code(r) == "MOVEMENT_TRANSITION_FAILED"
        # Nothing leaked: room, status and ledger are all untouched.
        assert _asset_room(ac) == "ROOM-102"
        row = _movement(mv)
        assert row["status"] == "APPROVED"
        assert row["completed_by"] is None
        assert row["completed_at"] is None
        assert _history(mv) == []
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DROP TRIGGER IF EXISTS acms_p5_boom_trg ON acs")
                cur.execute("DROP FUNCTION IF EXISTS acms_p5_boom()")
            conn.commit()
        finally:
            conn.close()

    # The very same request succeeds once the failure is gone.
    r = admin.post(f"/api/v1/movements/{mv}/complete")
    assert r.status_code == 200
    assert _asset_room(ac) == "ROOM-105"
    assert len(_history(mv)) == 1


# ── cancel ───────────────────────────────────────────────────────────────────


def test_cancel_only_requester_while_pending(app, tech):
    ac = _ac("CANCEL")
    _seed_asset(ac, "ROOM-102")
    mv = _create(tech, ac, "ROOM-105")

    # Non-requesters (even managers) may not cancel someone else's request.
    mgr = _auth(app, "FACILITY_MANAGER", MGR)
    r = mgr.post(f"/api/v1/movements/{mv}/cancel")
    assert r.status_code == 403
    assert _error_code(r) == "FORBIDDEN"

    r = tech.post(f"/api/v1/movements/{mv}/cancel")
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "CANCELLED"
    row = _movement(mv)
    assert row["status"] == "CANCELLED"
    assert row["cancelled_at"] is not None

    # Cancelling twice → 409; approving a cancelled request → 409.
    r = tech.post(f"/api/v1/movements/{mv}/cancel")
    assert r.status_code == 409
    assert _error_code(r) == "INVALID_STATE"
    r = mgr.post(f"/api/v1/movements/{mv}/approve")
    assert r.status_code == 409

    # The asset is free again: a cancelled request does not block a new one.
    mv2 = _create(tech, ac, "ROOM-105")
    assert _movement(mv2)["status"] == "PENDING"

    # An APPROVED request can no longer be cancelled by its requester either.
    assert mgr.post(f"/api/v1/movements/{mv2}/approve").status_code == 200
    r = tech.post(f"/api/v1/movements/{mv2}/cancel")
    assert r.status_code == 409
    assert _error_code(r) == "INVALID_STATE"


# ── comments ─────────────────────────────────────────────────────────────────


def test_comments_only_while_pending_and_embedded_in_list(app, admin, tech):
    ac = _ac("CMT")
    _seed_asset(ac, "ROOM-102")
    mv = _create(tech, ac, "ROOM-105")
    viewer = _auth(app, "VIEWER", VIEWER_U)

    # Validation: empty body is rejected.
    r = viewer.post(f"/api/v1/movements/{mv}/comments", json={"body": "   "})
    assert r.status_code == 400
    assert _error_code(r) == "VALIDATION_ERROR"

    r = viewer.post(f"/api/v1/movements/{mv}/comments", json={"body": "Please prioritise."})
    assert r.status_code == 201
    created = r.get_json()["data"]
    assert created["movement_id"] == mv
    assert created["author_name"] == "Viewer U"

    # The list embeds the comment thread with the author name.
    r = admin.get(f"/api/v1/movements?ac_id={ac}")
    row = r.get_json()["data"][0]
    assert len(row["comments"]) == 1
    assert row["comments"][0]["body"] == "Please prioritise."
    assert row["comments"][0]["author_name"] == "Viewer U"

    # Unknown movement → 404.
    r = viewer.post(
        "/api/v1/movements/00000000-0000-0000-0000-000000000000/comments",
        json={"body": "hi"},
    )
    assert r.status_code == 404

    # Comments close with the decision.
    mgr = _auth(app, "FACILITY_MANAGER", MGR)
    assert mgr.post(f"/api/v1/movements/{mv}/approve").status_code == 200
    r = tech.post(f"/api/v1/movements/{mv}/comments", json={"body": "thanks!"})
    assert r.status_code == 409
    assert _error_code(r) == "INVALID_STATE"
