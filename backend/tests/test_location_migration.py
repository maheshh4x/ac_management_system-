"""§23 U — migration/backfill verification for 014/015 (fresh scratch DBs).

Every test builds its own throwaway database (the ``scratch_db`` fixture from
``test_migrations``), so the shared test database is never re-migrated:

* **backfill** — apply migrations *up to* 014, insert legacy-style assets, then
  apply 014/015 for real: the backfill derives the confirmed chain from
  ``acs.room_id`` alone, invents no source values, touches no acs column, and
  a second ``up`` is a no-op (checksums recorded, counts stable);
* **constraints** — the 014 model is enforced by the schema: the extended
  ``PARTIAL`` vocabulary, per-field status CHECKs, ``manual_fields`` subset
  CHECK, one-record-per-asset PK, the composite (room, floor) / (floor, block)
  FKs, and the alias registry's uniqueness;
* **mirror** — 015's fixed trigger keeps records in sync on every legacy
  ``acs.room_id`` write: derive on insert, the exact AmbiguousColumn path 015
  repaired, source-backed preservation on clear, full drop without sources.
"""

from __future__ import annotations

# `scratch_db` is imported as a pytest FIXTURE: each test signature below
# injects it as a parameter, which ruff reads as a module-level redefinition.
# ruff: noqa: F811
import os
import sys
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import migrate as migrate_mod  # noqa: E402
from tests.test_migrations import (  # noqa: E402,F401 - scratch_db is a pytest fixture
    _execute,
    _point_pool_at,
    _raw_connection,
    _scalar,
    scratch_db,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="needs a live database — set TEST_DATABASE_URL"
)

# A minimal canonical chain owned by these tests (never the seeded campus —
# this database starts empty except for what a migration file itself seeds).
LEGACY_MASTERS = (
    "INSERT INTO colleges (college_id, college_name) VALUES ('U-COLLEGE', 'U College')",
    "INSERT INTO departments (department_id, department_name) VALUES ('U-DEPT', 'U Dept')",
    "INSERT INTO blocks (block_id, college_id, block_name) VALUES ('U-BLOCK', 'U-COLLEGE', 'U Block')",
    "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)"
    " VALUES ('U-FLR-1', 'U-BLOCK', 'First', 1)",
    "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
    " VALUES ('U-ROOM', 'U-FLR-1', 'U-DEPT', 'U Room', 'Office')",
)
SECOND_FLOOR = (
    "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)"
    " VALUES ('U-FLR-2', 'U-BLOCK', 'Second', 2)",
    "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type)"
    " VALUES ('U-ROOM-2', 'U-FLR-2', 'U-DEPT', 'U Room 2', 'Office')",
)


def _rows(dbname: str, sql: str, params=()) -> list[tuple]:
    conn = _raw_connection(dbname)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()


def _pre_model_up(monkeypatch) -> list:
    """Apply every migration EXCEPT 014/015 — the world before the model."""
    all_files = migrate_mod._migration_files()
    pre_model = [f for f in all_files if not f.name.startswith(("014_", "015_", "016_"))]
    monkeypatch.setattr(migrate_mod, "_migration_files", lambda: pre_model)
    assert migrate_mod.main(["up"]) == 0
    return all_files


# ─────────────────────────────────────────────────────────────────────────
# Backfill: legacy data crossing the model's introduction
# ─────────────────────────────────────────────────────────────────────────


def test_014_backfills_pre_model_assets_from_db_truth(scratch_db, monkeypatch):
    _point_pool_at(monkeypatch, scratch_db)
    all_files = _pre_model_up(monkeypatch)

    # The model's tables do not exist yet in the pre-014 world.
    assert _scalar(scratch_db, "SELECT to_regclass('public.asset_locations')") is None
    assert _scalar(scratch_db, "SELECT to_regclass('public.location_aliases')") is None

    # Legacy data: one AC with a confirmed room, one with nothing known.
    _execute(
        scratch_db,
        *LEGACY_MASTERS,
        "INSERT INTO acs (ac_id, room_id, location_status) VALUES ('U-AC-A', 'U-ROOM', 'ASSIGNED')",
        "INSERT INTO acs (ac_id, room_id, location_status) VALUES ('U-AC-B', NULL, 'PENDING')",
    )

    # Now introduce the model for real: 014 + 015.
    monkeypatch.setattr(migrate_mod, "_migration_files", lambda: all_files)
    assert migrate_mod.main(["up"]) == 0

    # Room-assigned AC: the chain is derived from acs.room_id alone, every
    # status RESOLVED, and NO source value is invented (DB truth only).
    (a,) = _rows(
        scratch_db,
        "SELECT room_id, floor_id, block_id, college_id, department_id,"
        " room_status, floor_status, block_status, college_status, department_status,"
        " source_college_value, source_block_value, source_floor_value,"
        " source_department_value, source_room_number_value,"
        " source_ref, resolved_by"
        " FROM asset_locations WHERE ac_id = 'U-AC-A'",
    )
    assert a == (
        "U-ROOM",
        "U-FLR-1",
        "U-BLOCK",
        "U-COLLEGE",
        "U-DEPT",
        "RESOLVED",
        "RESOLVED",
        "RESOLVED",
        "RESOLVED",
        "RESOLVED",
        None,  # source_college_value
        None,  # source_block_value
        None,  # source_floor_value
        None,  # source_department_value
        None,  # source_room_number_value
        "migration-014",
        "backfill",
    )

    # Room-less AC: everything MISSING, never guessed.
    (b,) = _rows(
        scratch_db,
        "SELECT room_id, floor_id, block_id, college_id, department_id,"
        " room_status, floor_status, block_status, college_status, department_status"
        " FROM asset_locations WHERE ac_id = 'U-AC-B'",
    )
    assert b == (None, None, None, None, None, "MISSING", "MISSING", "MISSING", "MISSING", "MISSING")

    # The backfill documents where it came from and never claims a source.
    notes = _rows(scratch_db, "SELECT resolution_notes FROM asset_locations WHERE ac_id = 'U-AC-A'")[0][0]
    assert notes["_origin"].startswith("legacy import")

    # acs itself is untouched: the backfill only reads it.
    assert _rows(scratch_db, "SELECT ac_id, room_id, location_status FROM acs ORDER BY ac_id") == [
        ("U-AC-A", "U-ROOM", "ASSIGNED"),
        ("U-AC-B", None, "PENDING"),
    ]

    # Idempotent: a second `up` applies nothing and changes nothing.
    assert migrate_mod.main(["up"]) == 0
    assert _scalar(scratch_db, "SELECT count(*) FROM asset_locations") == 2
    assert _scalar(scratch_db, "SELECT count(*) FROM acs") == 2
    assert _scalar(scratch_db, "SELECT count(*) FROM schema_migrations") == len(all_files)

    # 014/015 recorded with checksums matching the files on disk (immutable).
    recorded = dict(_rows(scratch_db, "SELECT version, checksum FROM schema_migrations"))
    for path in all_files:
        if path.name.startswith(("014_", "015_")):
            assert recorded[path.name] == migrate_mod._checksum(path.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────
# Constraints: the model is enforced by the schema, not by application care
# ─────────────────────────────────────────────────────────────────────────


def test_014_schema_constraints_enforce_the_model(scratch_db, monkeypatch):
    _point_pool_at(monkeypatch, scratch_db)
    assert migrate_mod.main(["up"]) == 0

    _execute(
        scratch_db,
        *LEGACY_MASTERS,
        # A second floor/block chain for mismatch tests.
        "INSERT INTO blocks (block_id, college_id, block_name)"
        " VALUES ('U-BLOCK-2', 'U-COLLEGE', 'U Block 2')",
        "INSERT INTO floors (floor_id, block_id, floor_name, floor_number)"
        " VALUES ('U-FLR-3', 'U-BLOCK-2', 'Third', 3)",
        # One acs row per constraint probe (the PK test occupies its own).
        "INSERT INTO acs (ac_id, location_status) VALUES ('U-C1', 'PENDING')",
        "INSERT INTO acs (ac_id, location_status) VALUES ('U-C2', 'PENDING')",
        "INSERT INTO acs (ac_id, location_status) VALUES ('U-C3', 'PENDING')",
        "INSERT INTO acs (ac_id, location_status) VALUES ('U-C4', 'PENDING')",
        "INSERT INTO acs (ac_id, location_status) VALUES ('U-C5', 'PENDING')",
    )
    # The mirror trigger already gave every probe row a record; remove them so
    # each probe below succeeds/fails for the constraint under test alone.
    _execute(scratch_db, "DELETE FROM asset_locations WHERE ac_id LIKE 'U-C%'")

    # Extended vocabulary: PARTIAL accepted, anything else refused.
    _execute(scratch_db, "INSERT INTO acs (ac_id, location_status) VALUES ('U-OK', 'PARTIAL')")
    with pytest.raises(psycopg2.Error):
        _execute(scratch_db, "INSERT INTO acs (ac_id, location_status) VALUES ('U-NOPE', 'BOGUS')")

    # One resolution record per asset (PK).
    _execute(scratch_db, "INSERT INTO asset_locations (ac_id) VALUES ('U-C1')")
    with pytest.raises(psycopg2.Error):
        _execute(scratch_db, "INSERT INTO asset_locations (ac_id) VALUES ('U-C1')")

    # Per-field status vocabulary.
    with pytest.raises(psycopg2.Error):
        _execute(scratch_db, "INSERT INTO asset_locations (ac_id, room_status) VALUES ('U-C2', 'BOGUS')")

    # manual_fields may only pin known levels.
    _execute(
        scratch_db,
        "INSERT INTO asset_locations (ac_id, manual_fields) VALUES ('U-C3', ARRAY['room', 'college'])",
    )
    with pytest.raises(psycopg2.Error):
        _execute(
            scratch_db,
            "INSERT INTO asset_locations (ac_id, manual_fields) VALUES ('U-C4', ARRAY['roomx'])",
        )

    # Composite FK (room, floor): a room can only sit on its own floor...
    with pytest.raises(psycopg2.Error):
        _execute(
            scratch_db,
            "INSERT INTO asset_locations (ac_id, room_id, floor_id) VALUES ('U-C5', 'U-ROOM', 'U-FLR-2')",
        )
    # ...and (floor, block): a floor can only sit on its own block.
    with pytest.raises(psycopg2.Error):
        _execute(
            scratch_db,
            "INSERT INTO asset_locations (ac_id, floor_id, block_id)"
            " VALUES ('U-C5', 'U-FLR-1', 'U-BLOCK-2')",
        )
    # A consistent chain is accepted (the probes above really were FK-shaped).
    _execute(
        scratch_db,
        "INSERT INTO asset_locations (ac_id, room_id, floor_id, block_id)"
        " VALUES ('U-C5', 'U-ROOM', 'U-FLR-1', 'U-BLOCK')",
    )

    # Alias registry: exactly one mapping per (entity_type, normalized alias).
    _execute(
        scratch_db,
        "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)"
        " VALUES ('block', 'U-BLOCK', 'NIT-T', 'nittt', 'manual')",
    )
    with pytest.raises(psycopg2.Error):
        _execute(
            scratch_db,
            # Different display text, same normalized alias, other target.
            "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias, source)"
            " VALUES ('block', 'U-BLOCK-2', 'NIT T', 'nittt', 'manual')",
        )


# ─────────────────────────────────────────────────────────────────────────
# Mirror trigger: 015's fix on every legacy writer path
# ─────────────────────────────────────────────────────────────────────────


def test_015_mirror_keeps_records_in_sync(scratch_db, monkeypatch):
    _point_pool_at(monkeypatch, scratch_db)
    assert migrate_mod.main(["up"]) == 0
    _execute(scratch_db, *LEGACY_MASTERS, *SECOND_FLOOR)

    # 1. Legacy INSERT with a room derives the whole chain and syncs the
    #    overall verdict even when the column said otherwise.
    _execute(
        scratch_db,
        "INSERT INTO acs (ac_id, room_id, location_status) VALUES ('U-M1', 'U-ROOM', 'PENDING')",
    )
    (record,) = _rows(
        scratch_db,
        "SELECT room_id, floor_id, block_id, college_id, department_id,"
        " room_status, floor_status, block_status, college_status, department_status"
        " FROM asset_locations WHERE ac_id = 'U-M1'",
    )
    assert record == (
        "U-ROOM",
        "U-FLR-1",
        "U-BLOCK",
        "U-COLLEGE",
        "U-DEPT",
        "RESOLVED",
        "RESOLVED",
        "RESOLVED",
        "RESOLVED",
        "RESOLVED",
    )
    assert _scalar(scratch_db, "SELECT location_status FROM acs WHERE ac_id = 'U-M1'") == "ASSIGNED"
    assert (
        _rows(scratch_db, "SELECT resolution_notes FROM asset_locations WHERE ac_id = 'U-M1'")[0][0][
            "_origin"
        ]
        == "derived from confirmed room by acs mirror"
    )

    # Represent preserved import source on the record before the legacy
    # writer touches it again (source text must survive every mirror write).
    _execute(
        scratch_db,
        "UPDATE asset_locations SET source_room_number_value = 'Ghost 9',"
        " source_floor_value = 'First' WHERE ac_id = 'U-M1'",
    )

    # 2. THE 015 REGRESSION: re-pointing an asset that ALREADY has a record
    #    used to raise AmbiguousColumn in the ON CONFLICT clause.
    _execute(scratch_db, "UPDATE acs SET room_id = 'U-ROOM-2' WHERE ac_id = 'U-M1'")
    (moved,) = _rows(
        scratch_db,
        "SELECT room_id, floor_id, block_id, source_room_number_value, source_floor_value"
        " FROM asset_locations WHERE ac_id = 'U-M1'",
    )
    assert moved == ("U-ROOM-2", "U-FLR-2", "U-BLOCK", "Ghost 9", "First")
    assert _scalar(scratch_db, "SELECT location_status FROM acs WHERE ac_id = 'U-M1'") == "ASSIGNED"

    # 3. Clearing the room preserves source-backed levels (PARTIAL verdict),
    #    drops the unbacked ones, and records why the room went away.
    _execute(scratch_db, "UPDATE acs SET room_id = NULL WHERE ac_id = 'U-M1'")
    (cleared,) = _rows(
        scratch_db,
        "SELECT room_id, room_status, floor_id, floor_status, block_id, block_status,"
        " college_id, college_status, department_id, department_status, manual_fields"
        " FROM asset_locations WHERE ac_id = 'U-M1'",
    )
    assert cleared == (
        None,  # room_id
        "UNRESOLVED",  # source_room_number_value present -> review, not erased
        "U-FLR-2",  # floor kept: its source value backs it
        "RESOLVED",
        None,  # block dropped: no source, no pin
        "MISSING",
        None,
        "MISSING",
        None,
        "MISSING",
        [],
    )
    assert (
        _rows(scratch_db, "SELECT resolution_notes FROM asset_locations WHERE ac_id = 'U-M1'")[0][0]["room"]
        == "cleared by direct acs.room_id write"
    )
    assert _scalar(scratch_db, "SELECT location_status FROM acs WHERE ac_id = 'U-M1'") == "PARTIAL"

    # 4. Without any source, clearing drops every level: back to PENDING.
    _execute(
        scratch_db,
        "INSERT INTO acs (ac_id, room_id, location_status) VALUES ('U-M2', 'U-ROOM-2', 'PENDING')",
    )
    assert _scalar(scratch_db, "SELECT location_status FROM acs WHERE ac_id = 'U-M2'") == "ASSIGNED"
    _execute(scratch_db, "UPDATE acs SET room_id = NULL WHERE ac_id = 'U-M2'")
    (dropped,) = _rows(
        scratch_db,
        "SELECT room_id, room_status, floor_id, floor_status, block_id, block_status,"
        " college_id, college_status, department_id, department_status"
        " FROM asset_locations WHERE ac_id = 'U-M2'",
    )
    assert dropped == (
        None,
        "MISSING",
        None,
        "MISSING",
        None,
        "MISSING",
        None,
        "MISSING",
        None,
        "MISSING",
    )
    assert _scalar(scratch_db, "SELECT location_status FROM acs WHERE ac_id = 'U-M2'") == "PENDING"
