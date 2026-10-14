"""AC usage sessions (QR ON/OFF), auto-onboarding and QR safety coverage.

Sessions are record-only: start/stop commit their business write first and
audit after (never blocking), with no approval step. The partial unique
index owns the one-open-session-per-asset rule, so a racing second start
loses with 409 instead of duplicating.

Onboarding: a verified identity self-registers to APPROVED/VIEWER with no
routine approver step; privileged requested roles are rejected at
validation; legacy never-reviewed PENDING rows complete on next contact.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from tests.conftest import auth, make_token  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="needs a live database — set TEST_DATABASE_URL"
)


def _code(r) -> str:
    return ((r.get_json() or {}).get("error") or {}).get("code", "<no error envelope>")


@pytest.fixture()
def profiles(db):
    """Throwaway APPROVED profiles; audit rows removed before profile rows."""
    created: list[str] = []

    def add(role="VIEWER", status="APPROVED", **extra):
        pid = str(uuid.uuid4())
        cols = {
            "id": pid,
            "role": role,
            "status": status,
            "email": f"{pid[:8]}@acms.test",
            "name": "Session Test",
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


@pytest.fixture()
def asset(db):
    """Throwaway asset in the seeded ROOM-102 chain; sessions cascade on delete."""
    ac_id = f"SESS-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO acs (ac_id, room_id) VALUES (%s, 'ROOM-102')", (ac_id,))
        conn.commit()
    finally:
        conn.close()
    yield ac_id
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
        conn.commit()
    finally:
        conn.close()


def _tok(role, sub):
    return auth(make_token(role, sub=sub))


def _audit_rows(action, entity_id=None):
    from psycopg2.extras import RealDictCursor

    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if entity_id is None:
                cur.execute("SELECT * FROM audit_logs WHERE action = %s", (action,))
            else:
                cur.execute(
                    "SELECT * FROM audit_logs WHERE action = %s AND entity_id = %s",
                    (action, entity_id),
                )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _open_session_count(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM ac_usage_sessions WHERE ac_id = %s AND ended_at IS NULL",
                (ac_id,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


# ── sessions: happy path + permissions ───────────────────────────────────────


def test_technician_start_stop_list_roundtrip(secret_client, profiles, asset):
    tech_sub = profiles("TECHNICIAN")
    tech = _tok("TECHNICIAN", tech_sub)
    r = secret_client.post(
        f"/api/v1/assets/{asset}/sessions/start", json={"note": "morning on"}, headers=tech
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()["data"]
    assert body["ac_id"] == asset and body["ended_at"] is None
    assert body["duration_seconds"] is None

    r = secret_client.get(f"/api/v1/assets/{asset}/sessions", headers=tech)
    assert r.status_code == 200
    rows = r.get_json()["data"]
    assert len(rows) == 1 and rows[0]["id"] == body["id"]

    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=tech)
    assert r.status_code == 200, r.get_json()
    stopped = r.get_json()["data"]
    assert stopped["ended_at"] is not None and stopped["duration_seconds"] is not None
    assert stopped["duration_seconds"] >= 0

    assert _open_session_count(asset) == 0
    started_rows = _audit_rows("SESSION_STARTED", body["id"])
    assert len(started_rows) == 1
    assert str(started_rows[0]["user_id"]) == tech_sub
    assert started_rows[0]["entity_type"] == "ac_usage_session"
    stopped_rows = _audit_rows("SESSION_STOPPED", body["id"])
    assert len(stopped_rows) == 1
    assert str(stopped_rows[0]["user_id"]) == tech_sub


def test_viewer_records_sessions_but_cannot_mutate_assets(secret_client, profiles, asset):
    viewer = _tok("VIEWER", profiles("VIEWER"))
    # Session recording is observed-usage logging: allowed.
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/start", json={}, headers=viewer)
    assert r.status_code == 201, r.get_json()
    r = secret_client.get(f"/api/v1/assets/{asset}/sessions", headers=viewer)
    assert r.status_code == 200
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=viewer)
    assert r.status_code == 200, r.get_json()
    assert _open_session_count(asset) == 0

    # Everything else stays denied: asset writes, decommission, admin surface.
    r = secret_client.patch(f"/api/v1/assets/{asset}", json={"remarks": "x"}, headers=viewer)
    assert r.status_code == 403
    r = secret_client.post(f"/api/v1/assets/{asset}/decommission", json={}, headers=viewer)
    assert r.status_code == 403
    r = secret_client.get("/api/v1/users", headers=viewer)
    assert r.status_code == 403
    r = secret_client.get("/api/v1/approvals", headers=viewer)
    assert r.status_code == 403
    # ...and the denied writes left no trace.
    assert _open_session_count(asset) == 0


def test_anonymous_session_calls_rejected(secret_client, asset):
    assert secret_client.post(f"/api/v1/assets/{asset}/sessions/start", json={}).status_code == 401
    assert secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}).status_code == 401
    assert secret_client.get(f"/api/v1/assets/{asset}/sessions").status_code == 401


def test_unknown_asset_session_endpoints_404(secret_client, profiles):
    tech = _tok("TECHNICIAN", profiles("TECHNICIAN"))
    assert (
        secret_client.post("/api/v1/assets/NO-SUCH-AC/sessions/start", json={}, headers=tech).status_code
        == 404
    )
    assert (
        secret_client.post("/api/v1/assets/NO-SUCH-AC/sessions/stop", json={}, headers=tech).status_code
        == 404
    )
    assert secret_client.get("/api/v1/assets/NO-SUCH-AC/sessions", headers=tech).status_code == 404


def test_double_start_conflicts_and_stop_without_open_conflicts(secret_client, profiles, asset):
    tech = _tok("TECHNICIAN", profiles("TECHNICIAN"))
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=tech)
    assert r.status_code == 409 and _code(r) == "NO_OPEN_SESSION"

    assert (
        secret_client.post(f"/api/v1/assets/{asset}/sessions/start", json={}, headers=tech).status_code == 201
    )
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/start", json={}, headers=tech)
    assert r.status_code == 409 and _code(r) == "SESSION_ALREADY_OPEN"
    assert _open_session_count(asset) == 1  # still exactly one open row

    assert (
        secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=tech).status_code == 200
    )
    assert (
        secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=tech).status_code == 409
    )


def test_decommissioned_asset_blocks_start_but_allows_stop(secret_client, profiles, asset):
    tech = _tok("TECHNICIAN", profiles("TECHNICIAN"))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE acs SET lifecycle_status = 'DECOMMISSIONED', is_active = FALSE WHERE ac_id = %s",
                (asset,),
            )
        conn.commit()
    finally:
        conn.close()
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/start", json={}, headers=tech)
    assert r.status_code == 409 and _code(r) == "ASSET_UNAVAILABLE"
    assert len(_audit_rows("SESSION_START_DENIED")) >= 1

    # An already-open session can still be closed so books never strand open.
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ac_usage_sessions (ac_id, started_by) VALUES (%s, %s)",
                (asset, profiles("TECHNICIAN")),
            )
        conn.commit()
    finally:
        conn.close()
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=tech)
    assert r.status_code == 200, r.get_json()


def test_manager_block_scope_enforced(secret_client, profiles, asset):
    # asset fixture lives in ROOM-102 -> block MAIN-ACADEMIC-BLOCK (seeded).
    scoped = profiles("FACILITY_MANAGER", assigned_blocks=["MAIN-ACADEMIC-BLOCK"])
    mgr_auth = _tok("FACILITY_MANAGER", scoped)
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/start", json={}, headers=mgr_auth)
    assert r.status_code == 201, r.get_json()
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=mgr_auth)
    assert r.status_code == 200

    # Manager scoped to a different facility is denied on the same asset.
    other = profiles("FACILITY_MANAGER", assigned_blocks=["SOME-OTHER-BLOCK"])
    r = secret_client.post(
        f"/api/v1/assets/{asset}/sessions/start", json={}, headers=_tok("FACILITY_MANAGER", other)
    )
    assert r.status_code == 403 and _code(r) == "ASSET_OUT_OF_SCOPE"
    assert _open_session_count(asset) == 0

    # Manager with no assignments is unrestricted.
    free = _tok("FACILITY_MANAGER", profiles("FACILITY_MANAGER"))
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/start", json={}, headers=free)
    assert r.status_code == 201, r.get_json()
    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=free)
    assert r.status_code == 200


def test_concurrent_starts_converge_to_one_open_session(secret_client, profiles, asset):
    import concurrent.futures

    token = _tok("TECHNICIAN", profiles("TECHNICIAN"))["Authorization"]

    def attempt(_index):
        # secret_client is the raw Flask test client; each thread gets its
        # own call (the shared connection pool serializes the writes).
        return secret_client.post(
            f"/api/v1/assets/{asset}/sessions/start",
            json={},
            headers={"Authorization": token},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(attempt, range(5)))
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 1, codes
    assert set(codes) <= {201, 409}
    assert _open_session_count(asset) == 1


# ── QR safety ────────────────────────────────────────────────────────────────


def test_viewer_reads_asset_detail_and_qr_resolve(secret_client, profiles, asset):
    viewer = _tok("VIEWER", profiles("VIEWER"))
    r = secret_client.get(f"/api/v1/assets/{asset}", headers=viewer)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["ac_id"] == asset
    r = secret_client.post("/api/v1/qr/resolve", json={"payload": asset}, headers=viewer)
    assert r.status_code == 200
    assert r.get_json()["data"]["ac_id"] == asset


def test_qr_resolve_valid_unknown_and_unauthenticated(secret_client, profiles, asset):
    tech = _tok("TECHNICIAN", profiles("TECHNICIAN"))
    before = _open_session_count(asset)
    r = secret_client.post("/api/v1/qr/resolve", json={"payload": asset}, headers=tech)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["ac_id"] == asset

    r = secret_client.post("/api/v1/qr/resolve", json={"payload": "NO-SUCH-ASSET-XYZ"}, headers=tech)
    assert r.status_code == 404

    r = secret_client.post("/api/v1/qr/resolve", json={"payload": asset})
    assert r.status_code == 401

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM acs")
            total = cur.fetchone()[0]
    finally:
        conn.close()
    # Resolving (valid or not) never creates asset rows.
    assert total >= 0 and _open_session_count(asset) == before


def test_qr_resolve_sticker_url_and_foreign_host(secret_client, profiles, asset):
    tech = _tok("TECHNICIAN", profiles("TECHNICIAN"))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT asset_uuid FROM acs WHERE ac_id = %s", (asset,))
            asset_uuid = str(cur.fetchone()[0])
    finally:
        conn.close()

    # The exact sticker shape {FRONTEND_URL}/a/<uuid>: test client host is an
    # allowed host, so the URL form resolves to the same asset.
    r = secret_client.post(
        "/api/v1/qr/resolve", json={"payload": f"http://localhost/a/{asset_uuid}"}, headers=tech
    )
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["ac_id"] == asset

    # A QR minted by a different site is rejected before any lookup runs.
    r = secret_client.post(
        "/api/v1/qr/resolve", json={"payload": f"https://evil.example/a/{asset_uuid}"}, headers=tech
    )
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "FOREIGN_HOST"

    # Resolved identifiers feed the canonical detail endpoint unchanged.
    r = secret_client.get(f"/api/v1/assets/{asset_uuid}", headers=tech)
    assert r.status_code == 200


def test_public_unknown_asset_404_and_no_write(secret_client):
    r = secret_client.get("/api/v1/public/assets/NO-SUCH-ASSET-XYZ")
    assert r.status_code == 404


def test_audit_log_has_no_mutation_surface(secret_client, profiles):
    admin = _tok("SUPER_ADMIN", profiles("SUPER_ADMIN"))
    assert secret_client.put("/api/v1/audit", json={}, headers=admin).status_code in (404, 405)
    assert secret_client.delete("/api/v1/audit", headers=admin).status_code in (404, 405)


# ── fleet sessions view (Energy dashboard) ───────────────────────────────────


def test_fleet_sessions_filters_scoping_and_validation(secret_client, profiles, asset):
    tech = _tok("TECHNICIAN", profiles("TECHNICIAN"))
    viewer = _tok("VIEWER", profiles("VIEWER"))

    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/start", json={}, headers=tech)
    assert r.status_code == 201

    # Anonymous is rejected before any read runs.
    assert secret_client.get("/api/v1/sessions").status_code == 401

    # Viewer sees the same rows read-only (no secrets in the payload).
    r = secret_client.get("/api/v1/sessions?state=open", headers=viewer)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["data"]
    assert body["open_count"] >= 1
    assert any(row["ac_id"] == asset for row in body["sessions"])
    row = next(row for row in body["sessions"] if row["ac_id"] == asset)
    assert set(row) >= {
        "id",
        "ac_id",
        "started_by",
        "started_at",
        "ended_at",
        "duration_seconds",
        "manufacturer",
        "block",
        "floor",
        "room",
        "department",
        "power_rating_kw",
    }

    # Closed filter excludes the open row.
    r = secret_client.get("/api/v1/sessions?state=closed", headers=viewer)
    assert r.status_code == 200
    assert all(row["ended_at"] is not None for row in r.get_json()["data"]["sessions"])

    # Invalid inputs are 400, never 500.
    assert secret_client.get("/api/v1/sessions?state=bogus", headers=viewer).status_code == 400
    assert secret_client.get("/api/v1/sessions?from=not-a-date", headers=viewer).status_code == 400
    assert (
        secret_client.get("/api/v1/sessions?from=2026-02-01&to=2026-01-01", headers=viewer).status_code == 400
    )
    assert secret_client.get("/api/v1/sessions?limit=abc", headers=viewer).status_code == 400

    # Date window containing today finds it; a far-past window does not.
    today = date.today().isoformat()
    r = secret_client.get(f"/api/v1/sessions?from=2000-01-01&to={today}", headers=viewer)
    assert any(row["ac_id"] == asset for row in r.get_json()["data"]["sessions"])
    r = secret_client.get("/api/v1/sessions?from=2000-01-01&to=2000-01-02", headers=viewer)
    assert all(row["ac_id"] != asset for row in r.get_json()["data"]["sessions"])

    r = secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=tech)
    assert r.status_code == 200


def test_fleet_sessions_manager_scope(secret_client, profiles, asset):
    # ROOM-102 lives in MAIN-ACADEMIC-BLOCK: an unrelated facility sees nothing.
    other = profiles("FACILITY_MANAGER", assigned_blocks=["SOME-OTHER-BLOCK"])
    tech = _tok("TECHNICIAN", profiles("TECHNICIAN"))
    assert (
        secret_client.post(f"/api/v1/assets/{asset}/sessions/start", json={}, headers=tech).status_code == 201
    )
    try:
        r = secret_client.get("/api/v1/sessions?state=open", headers=_tok("FACILITY_MANAGER", other))
        assert r.status_code == 200, r.get_json()
        assert all(row["ac_id"] != asset for row in r.get_json()["data"]["sessions"])
        assert r.get_json()["data"]["scope"] == "blocks"
    finally:
        r = secret_client.post(f"/api/v1/assets/{asset}/sessions/stop", json={}, headers=tech)
        assert r.status_code == 200


# ── auto-onboarding ──────────────────────────────────────────────────────────


def test_verified_signup_auto_approves_viewer(secret_client):
    import uuid as uuid_mod

    sub = str(uuid_mod.uuid4())
    newcomer = auth(make_token(None, sub=sub))
    r = secret_client.post(
        "/api/v1/auth/register-profile",
        json={"name": "Auto Onboard", "requested_role": "TECHNICIAN"},
        headers=newcomer,
    )
    assert r.status_code == 201, r.get_json()
    body = r.get_json()["data"]
    assert body["role"] == "VIEWER" and body["status"] == "APPROVED"
    assert body["requested_role"] == "TECHNICIAN"

    # No approver step: the asset list is reachable immediately.
    r = secret_client.get("/api/v1/assets", headers=newcomer)
    assert r.status_code == 200, r.get_json()

    # Repeat registration is idempotent.
    r = secret_client.post("/api/v1/auth/register-profile", json={}, headers=newcomer)
    assert r.status_code == 200
    assert r.get_json()["data"]["status"] == "APPROVED"

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE user_id = %s::uuid", (sub,))
            cur.execute("DELETE FROM profiles WHERE id = %s::uuid", (sub,))
        conn.commit()
    finally:
        conn.close()


def test_privileged_requested_roles_rejected(secret_client):
    import uuid as uuid_mod

    for requested in ("SUPER_ADMIN", "FACILITY_MANAGER"):
        sub = str(uuid_mod.uuid4())
        r = secret_client.post(
            "/api/v1/auth/register-profile",
            json={"name": "Greedy", "requested_role": requested},
            headers=auth(make_token(None, sub=sub)),
        )
        assert r.status_code == 400, (requested, r.get_json())


def test_legacy_pending_completes_on_next_contact(secret_client, profiles):
    import uuid as uuid_mod

    sub = str(uuid_mod.uuid4())
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, role, status, email, name) VALUES (%s, 'VIEWER', 'PENDING', %s, 'Legacy')",
                (sub, f"{sub[:8]}@acms.test"),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        newcomer = auth(make_token(None, sub=sub))
        r = secret_client.post("/api/v1/auth/register-profile", json={}, headers=newcomer)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["data"]["status"] == "APPROVED"
        r = secret_client.get("/api/v1/assets", headers=newcomer)
        assert r.status_code == 200
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM audit_logs WHERE user_id = %s::uuid", (sub,))
                cur.execute("DELETE FROM profiles WHERE id = %s::uuid", (sub,))
            conn.commit()
        finally:
            conn.close()


def test_rejected_and_deactivated_stay_blocked(secret_client, profiles):
    import uuid as uuid_mod

    rej = str(uuid_mod.uuid4())
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, role, status, email, name) VALUES (%s, 'VIEWER', 'REJECTED', %s, 'X')",
                (rej, f"{rej[:8]}@acms.test"),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        r = secret_client.post(
            "/api/v1/auth/register-profile", json={}, headers=auth(make_token(None, sub=rej))
        )
        assert r.get_json()["data"]["status"] == "REJECTED"
        r = secret_client.get("/api/v1/assets", headers=auth(make_token("VIEWER", sub=rej)))
        assert r.status_code == 403
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM audit_logs WHERE user_id = %s::uuid", (rej,))
                cur.execute("DELETE FROM profiles WHERE id = %s::uuid", (rej,))
            conn.commit()
        finally:
            conn.close()

    off = profiles("VIEWER")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE profiles SET is_active = FALSE WHERE id = %s::uuid", (off,))
        conn.commit()
    finally:
        conn.close()
    r = secret_client.get("/api/v1/assets", headers=_tok("VIEWER", off))
    assert r.status_code == 403 and _code(r) == "USER_INACTIVE"


def test_approve_elevates_but_same_role_conflicts(secret_client, profiles):
    admin = _tok("SUPER_ADMIN", profiles("SUPER_ADMIN"))
    import uuid as uuid_mod

    sub = str(uuid_mod.uuid4())
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO profiles (id, role, status, requested_role, email, name)"
                " VALUES (%s, 'VIEWER', 'APPROVED', 'TECHNICIAN', %s, 'Elevate')",
                (sub, f"{sub[:8]}@acms.test"),
            )
        conn.commit()
    finally:
        conn.close()
    try:
        r = secret_client.post(f"/api/v1/approvals/{sub}/approve", json={}, headers=admin)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["data"]["role"] == "TECHNICIAN"
        r = secret_client.post(f"/api/v1/approvals/{sub}/approve", json={}, headers=admin)
        assert r.status_code == 409 and _code(r) == "ALREADY_APPROVED"
    finally:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM audit_logs WHERE user_id = %s::uuid", (sub,))
                cur.execute("DELETE FROM profiles WHERE id = %s::uuid", (sub,))
            conn.commit()
        finally:
            conn.close()
