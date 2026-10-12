"""Lifecycle consistency + asset identity (migration 004) tests."""

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.database import get_db_connection  # noqa: E402


@pytest.fixture()
def client(db_admin_client):
    return db_admin_client


def _fetch_asset(ac_id):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT lifecycle_status, is_active, decommissioned_at FROM acs WHERE ac_id = %s",
                (ac_id,),
            )
            return cur.fetchone()
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


def test_patch_cannot_decommission_an_active_asset(client):
    ac_id = f"LIFE-{uuid.uuid4().hex[:8]}"
    try:
        r = client.post("/api/v1/assets", json={"ac_id": ac_id})
        assert r.status_code == 201
        r = client.patch(f"/api/v1/assets/{ac_id}", json={"lifecycle_status": "DECOMMISSIONED"})
        assert r.status_code == 400
        assert r.get_json()["error"]["code"] == "VALIDATION_ERROR"
        # row untouched — no contradictory DECOMMISSIONED + is_active state
        status, is_active, _ = _fetch_asset(ac_id)
        assert status == "ACTIVE" and is_active is True
    finally:
        _cleanup(ac_id)


def test_decommission_endpoint_sets_consistent_state(client):
    ac_id = f"LIFE-{uuid.uuid4().hex[:8]}"
    try:
        assert client.post("/api/v1/assets", json={"ac_id": ac_id}).status_code == 201
        r = client.post(f"/api/v1/assets/{ac_id}/decommission")
        assert r.status_code == 200
        status, is_active, decommissioned_at = _fetch_asset(ac_id)
        assert status == "DECOMMISSIONED"
        assert is_active is False  # consistent pair, not contradictory
        assert decommissioned_at is not None
    finally:
        _cleanup(ac_id)


def test_migration_004_guards_installed():
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.acs_asset_uuid_unique')")
            assert cur.fetchone()[0] == "acs_asset_uuid_unique"
            cur.execute("SELECT 1 FROM pg_constraint WHERE conname = 'acs_lifecycle_consistency'")
            assert cur.fetchone() is not None
    finally:
        conn.close()
