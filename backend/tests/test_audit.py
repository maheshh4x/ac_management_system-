"""Audit logging behavior: mutations are audited, failures are loud but safe."""

import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402
from app.services.audit_service import REDACTED  # noqa: E402
from tests.conftest import TEST_USER_ID  # noqa: E402


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


@pytest.fixture(autouse=True)
def _audit_user_profile(db):  # noqa: ARG001 - db gates skip without TEST_DATABASE_URL
    """audit_logs.user_id has a FK to profiles: the audit writer records the
    token subject (TEST_USER_ID), so that profile must exist during these
    tests. It is removed again afterwards because profiles.role is
    authoritative over the JWT claim — leaving it would flip the role-matrix
    tests (they run tokens without a profile row on purpose).
    """
    created = False
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM profiles WHERE id = %s", (TEST_USER_ID,))
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO profiles (id, email, name, role, status, is_active) "
                    "VALUES (%s, 'tester@acms.test', 'Test User', 'SUPER_ADMIN', 'APPROVED', TRUE)",
                    (TEST_USER_ID,),
                )
                created = True
        conn.commit()
    finally:
        conn.close()
    yield
    if created:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM audit_logs WHERE user_id = %s AND entity_id LIKE 'AUDIT-%%'",
                    (TEST_USER_ID,),
                )
                cur.execute("DELETE FROM profiles WHERE id = %s", (TEST_USER_ID,))
            conn.commit()
        finally:
            conn.close()


def _audit_count(entity_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_logs WHERE entity_id = %s", (entity_id,))
            return cur.fetchone()[0]
    finally:
        conn.close()


def _cleanup(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE entity_id = %s", (ac_id,))
            cur.execute("DELETE FROM acs WHERE ac_id = %s", (ac_id,))
        conn.commit()
    finally:
        conn.close()


def test_mutation_generates_audit_record(client):
    ac_id = f"AUDIT-{uuid.uuid4().hex[:8]}"
    try:
        before = _audit_count(ac_id)
        r = client.post("/api/v1/assets", json={"ac_id": ac_id})
        assert r.status_code == 201
        assert _audit_count(ac_id) == before + 1
    finally:
        _cleanup(ac_id)


def test_audit_failure_is_loud_but_never_breaks_request(client, monkeypatch, caplog):
    import app.config.database as dbmod

    ac_id = f"AUDIT-{uuid.uuid4().hex[:8]}"

    def _broken_db():
        raise RuntimeError("simulated audit db outage")

    # audit_service resolves get_db_connection lazily from app.config.database,
    # so only the audit insert breaks — the business request keeps working.
    monkeypatch.setattr(dbmod, "get_db_connection", _broken_db)
    try:
        with caplog.at_level(logging.WARNING, logger="app.services.audit_service"):
            r = client.post("/api/v1/assets", json={"ac_id": ac_id})
        assert r.status_code == 201  # audit failure must not break the request
        assert "audit write failed" in caplog.text
        assert "CREATE_ASSET" in caplog.text
        rid = r.headers.get("X-Request-ID")
        assert rid and rid in caplog.text  # failure is traceable
    finally:
        monkeypatch.setattr(dbmod, "get_db_connection", get_db_connection)
        _cleanup(ac_id)


# ── Phase 7: read API (filters, cursor, redaction, CSV) ──────────────────────

AUDIT = "/api/v1/audit"


def _seed_audit_rows(action="P7_TEST_ACTION", count=3, actor=TEST_USER_ID, **values):
    """Insert N audit rows with a unique entity id, return (entity_id, ids)."""
    entity_id = f"P7-AUDIT-{uuid.uuid4().hex[:8]}"
    ids = []
    old = json.dumps(values["old_values"]) if values.get("old_values") is not None else None
    new = json.dumps(values["new_values"]) if values.get("new_values") is not None else None
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for i in range(count):
                cur.execute(
                    "INSERT INTO audit_logs (user_id, action, entity_type, entity_id,"
                    " old_values, new_values)"
                    " VALUES (%s, %s, 'ac', %s, %s::jsonb, %s::jsonb) RETURNING id",
                    (
                        actor,
                        action if i == 0 else f"{action}_{i}",
                        entity_id,
                        old,
                        new,
                    ),
                )
                ids.append(cur.fetchone()[0])
        conn.commit()
    finally:
        conn.close()
    return entity_id, ids


def _delete_audit_rows(ids):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM audit_logs WHERE id = ANY(%s::uuid[])", ([[str(i) for i in ids]],))
        conn.commit()
    finally:
        conn.close()


def _items(resp):
    """The paginated envelope puts the rows in ``data`` and paging metadata in
    a sibling ``pagination`` object (see app.core.paginated)."""
    return resp.get_json()["data"]


def _next_cursor(resp):
    return (resp.get_json().get("pagination") or {}).get("next_cursor")


def test_audit_list_requires_permission(secret_client):
    r = secret_client.get(AUDIT)
    assert r.status_code == 401


def test_audit_filters_by_entity_type_and_action(client):
    entity_id, ids = _seed_audit_rows()
    try:
        r = client.get(f"{AUDIT}?entity_type=AC")
        assert r.status_code == 200, r.get_json()
        assert any(row["entity_id"] == entity_id for row in _items(r))

        r = client.get(f"{AUDIT}?action=P7_TEST_ACTION")
        assert r.status_code == 200
        assert any(row["entity_id"] == entity_id for row in _items(r))

        r = client.get(f"{AUDIT}?action=no-such-action-xyz")
        assert r.status_code == 200
        assert all(row["entity_id"] != entity_id for row in _items(r))
    finally:
        _delete_audit_rows(ids)


def test_audit_filters_by_actor_and_joins_profile_name(client):
    entity_id, ids = _seed_audit_rows()
    try:
        r = client.get(f"{AUDIT}?user={TEST_USER_ID}")
        assert r.status_code == 200
        rows = [row for row in _items(r) if row["entity_id"] == entity_id]
        assert rows, "actor filter should keep our rows"
        for row in rows:
            assert row["user_id"] == TEST_USER_ID
            assert row["actor_name"] == "Test User"
            assert row["actor_role"] == "SUPER_ADMIN"
    finally:
        _delete_audit_rows(ids)


def test_audit_user_filter_rejects_non_uuid(client):
    r = client.get(f"{AUDIT}?user=not-a-uuid")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "INVALID_FILTER"


def test_audit_date_filters_reject_bad_dates(client):
    for query in ("?from=07/10/2026", "?to=yesterday"):
        r = client.get(AUDIT + query)
        assert r.status_code == 400, query
        assert r.get_json()["error"]["code"] == "INVALID_DATE"


def test_audit_date_filter_narrows_results(client):
    entity_id, ids = _seed_audit_rows()
    try:
        # audit_logs.created_at is a timestamptz rendered in the database's
        # session zone (UTC); date.today() is the host's local date and differs
        # from it for the first hours of every day.
        today = datetime.now(timezone.utc).date().isoformat()
        r = client.get(f"{AUDIT}?from={today}&to={today}&action=P7_TEST_ACTION")
        assert r.status_code == 200
        assert any(row["entity_id"] == entity_id for row in _items(r))

        r = client.get(f"{AUDIT}?from=2001-01-01&to=2001-01-02&action=P7_TEST_ACTION")
        assert r.status_code == 200
        assert all(row["entity_id"] != entity_id for row in _items(r))
    finally:
        _delete_audit_rows(ids)


def test_audit_cursor_pagination_is_stable(client):
    entity_id, ids = _seed_audit_rows(count=3)
    try:
        r = client.get(f"{AUDIT}?action=P7_TEST_ACTION&page_size=1")
        assert r.status_code == 200
        assert len(_items(r)) == 1
        cursor = _next_cursor(r)
        assert cursor, r.get_json()

        seen = [_items(r)[0]["id"]]
        while cursor:
            page = client.get(f"{AUDIT}?action=P7_TEST_ACTION&page_size=1&cursor={cursor}")
            assert page.status_code == 200, page.get_json()
            rows = _items(page)
            if not rows:
                break
            seen.append(rows[0]["id"])
            cursor = _next_cursor(page)
        # Keyset paging walks distinct rows — no repeats, all three covered.
        assert len(seen) == len(set(seen)) == 3, seen
        assert set(seen) == {str(i) for i in ids}
    finally:
        _delete_audit_rows(ids)


def test_audit_invalid_cursor_rejected(client):
    r = client.get(f"{AUDIT}?cursor=@@not-base64@@")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "INVALID_CURSOR"


def test_audit_values_are_redacted_on_read(client):
    entity_id, ids = _seed_audit_rows(
        count=1,
        old_values={"password": "hunter2", "name": "ok"},
        new_values={"jwt_secret": "s3cr3t", "nested": {"api_key": "sk-live-123"}},
    )
    try:
        r = client.get(f"{AUDIT}?action=P7_TEST_ACTION")
        assert r.status_code == 200
        row = next(row for row in _items(r) if row["entity_id"] == entity_id)
        assert row["old_values"]["password"] == REDACTED
        assert row["old_values"]["name"] == "ok"
        assert row["new_values"]["jwt_secret"] == REDACTED
        assert row["new_values"]["nested"]["api_key"] == REDACTED
        assert "hunter2" not in r.get_data(as_text=True)
        assert "sk-live-123" not in r.get_data(as_text=True)
    finally:
        _delete_audit_rows(ids)


def test_audit_csv_export(client):
    entity_id, ids = _seed_audit_rows(count=2)
    try:
        r = client.get(f"{AUDIT}?action=P7_TEST_ACTION&format=csv")
        assert r.status_code == 200, r.get_json()
        assert r.mimetype == "text/csv"
        assert "attachment" in r.headers.get("Content-Disposition", "")
        body = r.get_data(as_text=True)
        header = body.splitlines()[0].lstrip("﻿")
        assert header.startswith("created_at,action,entity_type,entity_id,actor_name")
        assert entity_id in body
    finally:
        _delete_audit_rows(ids)


def test_audit_rejects_unknown_format(client):
    r = client.get(f"{AUDIT}?format=xlsx")
    assert r.status_code == 400
    assert r.get_json()["error"]["code"] == "UNSUPPORTED_FORMAT"


def test_audit_offset_paging_returns_a_different_page(client):
    """Phase 8 regression: offset paging was echoed but never applied.

    ``list_audit`` documented "classic ``page``/``page_size`` offset paging
    still works when ``cursor`` is absent", but the page query carried a
    ``LIMIT`` and no ``OFFSET`` — so ``pagination.page`` faithfully reported
    page 2 while the rows were always page 1's. The audit UI paginates by
    offset, which meant "page 2 of N" silently showed the first page.
    ``test_audit_cursor_pagination_is_stable`` covers the cursor path only,
    which is why this went unnoticed until Phase 8.
    """
    _entity_id, ids = _seed_audit_rows(count=3)
    try:
        first = client.get(f"{AUDIT}?page=1&page_size=1")
        second = client.get(f"{AUDIT}?page=2&page_size=1")
        assert first.status_code == 200, first.get_json()
        assert second.status_code == 200, second.get_json()
        assert (first.get_json().get("pagination") or {}).get("page") == 1
        assert (second.get_json().get("pagination") or {}).get("page") == 2
        assert _items(first), "page 1 was empty"
        assert _items(second), "page 2 was empty"
        assert _items(first) != _items(
            second
        ), "page 2 repeated page 1 — offset paging is not applied to the audit query"

        past = client.get(f"{AUDIT}?page=99999999&page_size=5")
        assert past.status_code == 200, past.get_json()
        assert _items(past) == [], "a page past the end must come back empty, not as page 1"
    finally:
        _delete_audit_rows(ids)


# ── Phase 8: append-only storage (migration 011) ─────────────────────────────


def test_audit_rows_cannot_be_rewritten_or_revoked(db):  # noqa: ARG001 - db gates skip
    """Migration 011: audit history is append-only, in two independent layers.

    The security-review checklist item is "audit table append-only", so both
    layers are asserted rather than trusting the migration text:

    1. a ``BEFORE UPDATE`` trigger raises on every rewrite. Triggers are not
       subject to ACLs, so this holds even for the superuser the test suite
       connects as — which is exactly why it is the layer that matters here.
    2. the recorded ACL grants ``UPDATE``/``DELETE`` to nobody but the table
       owner, so a non-superuser app role is refused by the grant system
       before it ever reaches the trigger.
    """
    entity_id, ids = _seed_audit_rows(action="P8_APPEND_ONLY", count=1)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error) as exc:
                cur.execute("UPDATE audit_logs SET action = 'TAMPERED' WHERE id = %s", (ids[0],))
            conn.rollback()
            assert "append-only" in str(exc.value), str(exc.value)

            cur.execute("SELECT action FROM audit_logs WHERE id = %s", (ids[0],))
            assert cur.fetchone()[0] == "P8_APPEND_ONLY", "the rewrite landed — audit is mutable"

            cur.execute(
                "SELECT tgenabled FROM pg_trigger"
                " WHERE tgname = 'trg_audit_logs_block_update' AND NOT tgisinternal"
            )
            row = cur.fetchone()
            assert row is not None, "append-only trigger is missing"
            assert row[0] == "O", f"append-only trigger is not enabled (tgenabled={row[0]})"

            cur.execute("""
                SELECT COALESCE(r.rolname, 'PUBLIC'), e.privilege_type
                  FROM pg_catalog.pg_class c
                  CROSS JOIN LATERAL pg_catalog.aclexplode(
                       COALESCE(c.relacl, pg_catalog.acldefault('r', c.relowner))) e
                  LEFT JOIN pg_catalog.pg_roles r ON r.oid = e.grantee
                 WHERE c.oid = 'public.audit_logs'::pg_catalog.regclass
                   AND e.privilege_type IN ('UPDATE', 'DELETE')
                   AND e.grantee <> c.relowner
                """)
            assert cur.fetchall() == [], "a non-owner holds UPDATE/DELETE on audit_logs"
    finally:
        conn.close()
    _delete_audit_rows(ids)
