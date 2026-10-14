"""Outdoor-unit linking + universal issue reporting with approval (Features A/B/C).

All DB-touching tests run against TEST_DATABASE_URL only. Stability rules
under test: outdoor identity is a real acs row (never invented), reports
start PENDING_APPROVAL, approval creates exactly one work order even under
concurrency, and rejected reports never produce jobs.
"""

from __future__ import annotations

import os
import sys
import threading
import uuid
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
    created: list[str] = []

    def add(role="VIEWER", status="APPROVED", **extra):
        pid = str(uuid.uuid4())
        cols = {"id": pid, "role": role, "status": status,
                "email": f"{pid[:8]}@acms.test", "name": "Issue Test"}
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
            cur.execute("DELETE FROM notifications WHERE user_id = ANY(%s::uuid[])", (created,))
            cur.execute(
                "DELETE FROM maintenance_reports WHERE reporter_id = ANY(%s::uuid[])"
                " OR decided_by = ANY(%s::uuid[])",
                (created, created),
            )
            # Work orders adopted from approved reports (events cascade).
            cur.execute(
                "DELETE FROM maintenance_work_orders WHERE created_by = ANY(%s::uuid[])"
                " OR technician_id = ANY(%s::uuid[]) OR client_mutation_id LIKE 'report:%%'",
                (created, created),
            )
            cur.execute("DELETE FROM profiles WHERE id = ANY(%s::uuid[])", (created,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def indoor_asset(db):
    ac_id = f"IN-{uuid.uuid4().hex[:8]}"
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO acs (ac_id, room_id, ac_make_company, ac_model_number)"
                " VALUES (%s, 'ROOM-102', 'Daikin', 'FTKM50')",
                (ac_id,),
            )
        conn.commit()
    finally:
        conn.close()
    yield ac_id
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM acs WHERE ac_id = %s OR parent_ac_id = %s", (ac_id, ac_id))
        conn.commit()
    finally:
        conn.close()


def _tok(role, sub):
    return auth(make_token(role, sub=sub))


def _report_body(ac_id, **over):
    body = {
        "ac_id": ac_id,
        "category": "AC_NOT_COOLING",
        "description": "Indoor unit blows warm air since morning.",
        "priority": "HIGH",
    }
    body.update(over)
    return body


def _file_report(secret_client, role, sub, ac_id, **over):
    return secret_client.post(
        "/api/v1/issue-reports", json=_report_body(ac_id, **over), headers=_tok(role, sub)
    )


# ── Feature A: outdoor-unit linking ──────────────────────────────────────────


def test_indoor_detail_reports_no_linked_outdoor(secret_client, profiles, indoor_asset, db):
    viewer = profiles("VIEWER")
    r = secret_client.get(f"/api/v1/assets/{indoor_asset}", headers=_tok("VIEWER", viewer))
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["unit_role"] == "INDOOR"
    assert data["outdoor_unit"] is None  # frontend renders "Not Linked" from this
    assert data["parent_unit"] is None


def test_link_outdoor_unit_end_to_end(secret_client, profiles, indoor_asset, db):
    admin = profiles("SUPER_ADMIN")
    ou_id = f"OU-{uuid.uuid4().hex[:8]}"
    r = secret_client.post(
        "/api/v1/assets",
        json={"ac_id": ou_id, "room_id": "ROOM-102", "ac_make_company": "Daikin",
              "unit_role": "OUTDOOR", "parent_ac_id": indoor_asset},
        headers=_tok("SUPER_ADMIN", admin),
    )
    assert r.status_code == 201, r.get_json()

    viewer = profiles("VIEWER")
    r = secret_client.get(f"/api/v1/assets/{indoor_asset}", headers=_tok("VIEWER", viewer))
    data = r.get_json()["data"]
    assert data["outdoor_unit"]["ac_id"] == ou_id
    assert data["outdoor_unit"]["asset_uuid"]

    # The outdoor identity is stable across requests and is a real row.
    r2 = secret_client.get(f"/api/v1/assets/{indoor_asset}", headers=_tok("VIEWER", viewer))
    assert r2.get_json()["data"]["outdoor_unit"]["asset_uuid"] == data["outdoor_unit"]["asset_uuid"]

    # Outdoor row exposes its indoor parent (never the reverse confusion).
    r3 = secret_client.get(f"/api/v1/assets/{ou_id}", headers=_tok("VIEWER", viewer))
    assert r3.get_json()["data"]["parent_unit"]["ac_id"] == indoor_asset


def test_second_outdoor_for_same_parent_rejected(secret_client, profiles, indoor_asset, db):
    admin = profiles("SUPER_ADMIN")
    hdr = _tok("SUPER_ADMIN", admin)
    for suffix in ("A", "B"):
        r = secret_client.post(
            "/api/v1/assets",
            json={"ac_id": f"OU-{uuid.uuid4().hex[:8]}-{suffix}", "unit_role": "OUTDOOR",
                  "parent_ac_id": indoor_asset},
            headers=hdr,
        )
        if suffix == "A":
            assert r.status_code == 201, r.get_json()
        else:
            assert r.status_code == 409, r.get_json()
            assert _code(r) == "OUTDOOR_ALREADY_LINKED"


@pytest.mark.parametrize(
    "role_field,parent,status",
    [("INDOOR", "NOPE", 400), ("OUTDOOR", "MISSING-ASSET", 400)],
)
def test_invalid_parent_rejected(secret_client, profiles, indoor_asset, db, role_field, parent, status):
    admin = profiles("SUPER_ADMIN")
    r = secret_client.post(
        "/api/v1/assets",
        json={"ac_id": f"OU-{uuid.uuid4().hex[:8]}", "unit_role": role_field, "parent_ac_id": parent},
        headers=_tok("SUPER_ADMIN", admin),
    )
    assert r.status_code == status, r.get_json()


def test_viewer_cannot_link_units(secret_client, profiles, indoor_asset, db):
    viewer = profiles("VIEWER")
    r = secret_client.patch(
        f"/api/v1/assets/{indoor_asset}",
        json={"unit_role": "OUTDOOR", "parent_ac_id": indoor_asset},
        headers=_tok("VIEWER", viewer),
    )
    assert r.status_code == 403, r.get_json()  # no EDIT_ASSET; self-parent also invalid


# ── Feature B: report submission ─────────────────────────────────────────────


@pytest.mark.parametrize("role", ["SUPER_ADMIN", "FACILITY_MANAGER", "TECHNICIAN", "VIEWER"])
def test_all_roles_can_submit_report(secret_client, profiles, indoor_asset, db, role):
    sub = profiles(role)
    r = _file_report(secret_client, role, sub, indoor_asset)
    assert r.status_code == 201, r.get_json()
    data = r.get_json()["data"]
    assert data["status"] == "PENDING_APPROVAL"
    assert data["report_code"].startswith("IR-")
    assert data["work_order_id"] is None  # never a job at submission


def test_unauthenticated_submit_rejected(secret_client, indoor_asset, db):
    r = secret_client.post("/api/v1/issue-reports", json=_report_body(indoor_asset))
    assert r.status_code == 401


def test_invalid_asset_and_bad_description_rejected(secret_client, profiles, indoor_asset, db):
    viewer = profiles("VIEWER")
    hdr = _tok("VIEWER", viewer)
    r = secret_client.post(
        "/api/v1/issue-reports",
        json=_report_body("NO-SUCH-ASSET"), headers=hdr,
    )
    assert r.status_code == 404 and _code(r) == "ASSET_NOT_FOUND"
    r = secret_client.post(
        "/api/v1/issue-reports",
        json=_report_body(indoor_asset, description="   "), headers=hdr,
    )
    assert r.status_code == 400
    r = secret_client.post(
        "/api/v1/issue-reports",
        json=_report_body(indoor_asset, category="NOPE"), headers=hdr,
    )
    assert r.status_code == 400


def test_reporter_identity_comes_from_auth(secret_client, profiles, indoor_asset, db):
    viewer = profiles("VIEWER")
    other = profiles("VIEWER")
    r = secret_client.post(
        "/api/v1/issue-reports",
        json={**_report_body(indoor_asset), "reporter_id": other, "reporter_name": "Mallory"},
        headers=_tok("VIEWER", viewer),
    )
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["data"]["reporter_id"] == viewer  # body identity ignored


def test_idempotent_retry_returns_original(secret_client, profiles, indoor_asset, db):
    viewer = profiles("VIEWER")
    key = f"form-{uuid.uuid4()}"
    first = _file_report(secret_client, "VIEWER", viewer, indoor_asset, idempotency_key=key)
    second = _file_report(secret_client, "VIEWER", viewer, indoor_asset, idempotency_key=key)
    assert first.status_code == 201 and second.status_code == 200, second.get_json()
    assert second.get_json()["data"]["id"] == first.get_json()["data"]["id"]
    assert second.get_json()["data"].get("deduplicated") is True


def test_report_visibility_scoping(secret_client, profiles, indoor_asset, db):
    alice = profiles("VIEWER")
    bob = profiles("VIEWER")
    admin = profiles("SUPER_ADMIN")
    r = _file_report(secret_client, "VIEWER", alice, indoor_asset)
    rid = r.get_json()["data"]["id"]

    assert secret_client.get(f"/api/v1/issue-reports/{rid}", headers=_tok("VIEWER", alice)).status_code == 200
    stranger = secret_client.get(f"/api/v1/issue-reports/{rid}", headers=_tok("VIEWER", bob))
    assert stranger.status_code == 404  # indistinguishable from missing
    assert secret_client.get(f"/api/v1/issue-reports/{rid}", headers=_tok("SUPER_ADMIN", admin)).status_code == 200

    mine = secret_client.get("/api/v1/issue-reports/mine", headers=_tok("VIEWER", alice))
    assert mine.get_json()["data"]["pagination"]["total"] == 1
    others = secret_client.get("/api/v1/issue-reports/mine", headers=_tok("VIEWER", bob))
    assert others.get_json()["data"]["pagination"]["total"] == 0


# ── Feature C: approval ──────────────────────────────────────────────────────


def _approve(secret_client, admin_sub, rid):
    return secret_client.post(f"/api/v1/issue-reports/{rid}/approve", headers=_tok("SUPER_ADMIN", admin_sub))


@pytest.mark.parametrize("role", ["FACILITY_MANAGER", "TECHNICIAN", "VIEWER"])
def test_only_super_admin_can_decide(secret_client, profiles, indoor_asset, db, role):
    alice = profiles("VIEWER")
    admin = profiles("SUPER_ADMIN")
    decider = profiles(role)
    rid = _file_report(secret_client, "VIEWER", alice, indoor_asset).get_json()["data"]["id"]
    assert secret_client.post(
        f"/api/v1/issue-reports/{rid}/approve", headers=_tok(role, decider)
    ).status_code == 403
    assert secret_client.post(
        f"/api/v1/issue-reports/{rid}/reject",
        json={"reason": "no"}, headers=_tok(role, decider),
    ).status_code == 403
    # Still pending afterwards.
    got = secret_client.get(f"/api/v1/issue-reports/{rid}", headers=_tok("SUPER_ADMIN", admin))
    assert got.get_json()["data"]["status"] == "PENDING_APPROVAL"


def test_approve_creates_exactly_one_linked_work_order(secret_client, profiles, indoor_asset, db):
    alice = profiles("VIEWER")
    admin = profiles("SUPER_ADMIN")
    rid = _file_report(secret_client, "VIEWER", alice, indoor_asset).get_json()["data"]["id"]

    r = _approve(secret_client, admin, rid)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()["data"]
    assert body["status"] == "SCHEDULED" and body["already_processed"] is False
    wo_id = body["id"]

    # The job references the report and asset, and is visible in the queue.
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ac_id, status, client_mutation_id, problem_description"
                " FROM maintenance_work_orders WHERE id = %s",
                (wo_id,),
            )
            wo = cur.fetchone()
            assert wo[0] == indoor_asset and wo[1] == "SCHEDULED"
            assert wo[2] == f"report:{rid}"
            assert "warm air" in wo[3]
    finally:
        conn.close()

    detail = secret_client.get(f"/api/v1/issue-reports/{rid}", headers=_tok("SUPER_ADMIN", admin))
    assert detail.get_json()["data"]["work_order"]["id"] == wo_id

    # Sequential replay: same job, no duplicate.
    again = _approve(secret_client, admin, rid)
    assert again.status_code == 200
    assert again.get_json()["data"]["id"] == wo_id
    assert again.get_json()["data"]["already_processed"] is True
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM maintenance_work_orders WHERE client_mutation_id = %s",
                (f"report:{rid}",),
            )
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_concurrent_approvals_create_one_job(secret_client, profiles, indoor_asset, db):
    alice = profiles("VIEWER")
    admins = [profiles("SUPER_ADMIN") for _ in range(4)]
    rid = _file_report(secret_client, "VIEWER", alice, indoor_asset).get_json()["data"]["id"]

    results, errors = [], []

    def worker(sub):
        try:
            results.append(_approve(secret_client, sub, rid).get_json()["data"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(s,)) for s in admins]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors
    assert len(results) == 4
    wo_ids = {res["id"] for res in results}
    assert len(wo_ids) == 1  # exactly one job across all concurrent approvals


def test_reject_preserves_reason_and_creates_no_job(secret_client, profiles, indoor_asset, db):
    alice = profiles("VIEWER")
    admin = profiles("SUPER_ADMIN")
    rid = _file_report(secret_client, "VIEWER", alice, indoor_asset).get_json()["data"]["id"]

    r = secret_client.post(
        f"/api/v1/issue-reports/{rid}/reject",
        json={"reason": "Duplicate of an existing job."},
        headers=_tok("SUPER_ADMIN", admin),
    )
    assert r.status_code == 200, r.get_json()
    data = r.get_json()["data"]
    assert data["status"] == "REJECTED"
    assert data["rejection_reason"] == "Duplicate of an existing job."
    assert data["work_order_id"] is None

    # A rejected report cannot be approved afterwards.
    r2 = _approve(secret_client, admin, rid)
    assert r2.status_code == 200
    assert r2.get_json()["data"]["already_processed"] is True
    assert r2.get_json()["data"]["id"] is None


def test_reject_requires_reason(secret_client, profiles, indoor_asset, db):
    alice = profiles("VIEWER")
    admin = profiles("SUPER_ADMIN")
    rid = _file_report(secret_client, "VIEWER", alice, indoor_asset).get_json()["data"]["id"]
    r = secret_client.post(
        f"/api/v1/issue-reports/{rid}/reject", json={"reason": "  "},
        headers=_tok("SUPER_ADMIN", admin),
    )
    assert r.status_code == 400


def test_admin_can_track_all_reports_after_decision(secret_client, profiles, indoor_asset, db):
    """Decided reports leave /pending but stay visible via GET /issue-reports
    with their linked job — this is the administrative tracking surface."""
    alice = profiles("VIEWER")
    admin = profiles("SUPER_ADMIN")
    mgr = profiles("FACILITY_MANAGER")
    rid = _file_report(secret_client, "VIEWER", alice, indoor_asset).get_json()["data"]["id"]
    wo_id = _approve(secret_client, admin, rid).get_json()["data"]["id"]

    all_r = secret_client.get("/api/v1/issue-reports", headers=_tok("SUPER_ADMIN", admin))
    assert all_r.status_code == 200, all_r.get_json()
    rows = all_r.get_json()["data"]["reports"]
    match = next(r for r in rows if r["id"] == rid)
    assert match["status"] == "APPROVED"
    assert match["work_order_id"] == wo_id
    assert match["work_order_status"] == "SCHEDULED"

    filt = secret_client.get("/api/v1/issue-reports?status=APPROVED", headers=_tok("SUPER_ADMIN", admin))
    assert any(r["id"] == rid for r in filt.get_json()["data"]["reports"])
    filt = secret_client.get("/api/v1/issue-reports?status=REJECTED", headers=_tok("SUPER_ADMIN", admin))
    assert all(r["id"] != rid for r in filt.get_json()["data"]["reports"])
    bad = secret_client.get("/api/v1/issue-reports?status=NOPE", headers=_tok("SUPER_ADMIN", admin))
    assert bad.status_code == 400

    # Non-admins get no tracking surface at all.
    assert secret_client.get("/api/v1/issue-reports", headers=_tok("FACILITY_MANAGER", mgr)).status_code == 403
    assert secret_client.get("/api/v1/issue-reports").status_code == 401


def test_decisions_are_audited(secret_client, profiles, indoor_asset, db):
    alice = profiles("VIEWER")
    admin = profiles("SUPER_ADMIN")
    rid1 = _file_report(secret_client, "VIEWER", alice, indoor_asset).get_json()["data"]["id"]
    rid2 = _file_report(secret_client, "VIEWER", alice, indoor_asset).get_json()["data"]["id"]
    _approve(secret_client, admin, rid1)
    secret_client.post(
        f"/api/v1/issue-reports/{rid2}/reject", json={"reason": "Not an AC issue."},
        headers=_tok("SUPER_ADMIN", admin),
    )
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT action, entity_id FROM audit_logs WHERE entity_type = 'issue_report'"
                " AND entity_id IN (%s, %s)",
                (rid1, rid2),
            )
            actions = {(row[0], str(row[1])) for row in cur.fetchall()}
    finally:
        conn.close()
    assert ("SUBMIT_ISSUE_REPORT", rid1) in actions
    assert ("APPROVE_ISSUE_REPORT", rid1) in actions
    assert ("SUBMIT_ISSUE_REPORT", rid2) in actions
    assert ("REJECT_ISSUE_REPORT", rid2) in actions


def test_approved_job_lifecycle_still_works(secret_client, profiles, indoor_asset, db):
    """The generated job flows through the existing assign/accept/start/
    complete endpoints and stays traceable to the report."""
    alice = profiles("VIEWER")
    admin = profiles("SUPER_ADMIN")
    tech = profiles("TECHNICIAN")
    rid = _file_report(secret_client, "VIEWER", alice, indoor_asset).get_json()["data"]["id"]
    wo_id = _approve(secret_client, admin, rid).get_json()["data"]["id"]

    assert secret_client.post(
        f"/api/v1/maintenance/{wo_id}/assign", json={"technician_id": tech},
        headers=_tok("SUPER_ADMIN", admin),
    ).status_code == 200
    assert secret_client.post(
        f"/api/v1/maintenance/{wo_id}/accept", headers=_tok("TECHNICIAN", tech)
    ).status_code == 200
    assert secret_client.post(
        f"/api/v1/maintenance/{wo_id}/start", headers=_tok("TECHNICIAN", tech)
    ).status_code == 200
    assert secret_client.post(
        f"/api/v1/maintenance/{wo_id}/complete",
        json={"work_performed": "Replaced capacitor.", "labor_hours": 1.5},
        headers=_tok("TECHNICIAN", tech),
    ).status_code == 200

    detail = secret_client.get(f"/api/v1/issue-reports/{rid}", headers=_tok("SUPER_ADMIN", admin))
    assert detail.get_json()["data"]["work_order"]["status"] == "COMPLETED"
