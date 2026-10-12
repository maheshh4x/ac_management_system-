"""Phase 8.1 — migration tests.

The plan asks for "migration up/down on an empty and a seeded DB". There is no
down path to test: ``app.migrate`` is deliberately forward-only (``up`` /
``status``), no ``*.down.sql`` files exist, and the runner refuses to re-run a
file whose content changed. What is asserted here is therefore the real
contract:

* ``up`` builds a complete schema in an **empty** database;
* ``up`` on an **already-migrated, seeded** database is a no-op and leaves the
  data untouched;
* a changed applied file is **refused** (exit code 2) instead of half-applied;
* the checksums recorded in the shared test database still match the files on
  disk, i.e. nobody edited an applied migration.

Each test runs against a throwaway database created on the fly and dropped
afterwards, so the shared test database is never re-migrated.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import migrate as migrate_mod  # noqa: E402
from app.config import database as dbmod  # noqa: E402

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="needs a live database — set TEST_DATABASE_URL"
)

# A representative slice of the schema: enough to prove the runner built a
# working application database rather than just its bookkeeping table.
REQUIRED_TABLES = {
    "schema_migrations",
    "colleges",
    "blocks",
    "floors",
    "rooms",
    "departments",
    "acs",
    "profiles",
    "audit_logs",
    "notifications",
    "maintenance",
    "maintenance_work_orders",
    "asset_movements",
    "asset_location_history",
}


def _raw_connection(dbname: str):
    """A direct connection, deliberately not taken from the app's pool."""
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=dbname,
        user=os.environ["DB_USER"],
        password=os.environ.get("DB_PASSWORD", ""),
        sslmode=os.environ.get("DB_SSLMODE", "prefer"),
    )


def _scalar(dbname: str, sql: str, params=()):
    conn = _raw_connection(dbname)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _execute(dbname: str, *statements: str) -> None:
    conn = _raw_connection(dbname)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
    finally:
        conn.close()


def _reset_pool() -> None:
    if dbmod._pool is not None:
        try:
            dbmod._pool.closeall()
        except Exception:  # noqa: BLE001 - already gone is fine
            pass
        dbmod._pool = None


@pytest.fixture()
def scratch_db(_never_reach_the_dotenv_database, monkeypatch):  # noqa: ARG001
    """Yield a fresh, empty database name; drop it and restore the pool after."""
    owner_db = os.environ["DB_NAME"]
    name = f"acms_migrate_{uuid.uuid4().hex[:8]}"
    _execute(owner_db, f'CREATE DATABASE "{name}"')

    try:
        yield name
    finally:
        _reset_pool()
        _execute(owner_db, f'DROP DATABASE IF EXISTS "{name}" (FORCE)')


def _point_pool_at(monkeypatch, dbname: str) -> None:
    monkeypatch.setenv("DB_NAME", dbname)
    _reset_pool()


def test_up_builds_everything_in_an_empty_database(scratch_db, monkeypatch):  # noqa: ARG001
    _point_pool_at(monkeypatch, scratch_db)

    assert _scalar(scratch_db, "SELECT to_regclass('public.schema_migrations')") is None
    assert migrate_mod.main(["up"]) == 0

    assert _scalar(scratch_db, "SELECT count(*) FROM schema_migrations") == len(
        migrate_mod._migration_files()
    )

    present = set(_objects(scratch_db, "BASE TABLE", "public"))
    missing = REQUIRED_TABLES - present
    assert not missing, f"schema incomplete after 'up': {sorted(missing)}"

    # The application's own connection now works against the fresh database.
    assert dbmod.get_db_connection() is not None


def _objects(dbname: str, kind: str, schema: str) -> set[str]:
    conn = _raw_connection(dbname)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_type = %s AND table_schema = %s",
                (kind.upper(), schema),
            )
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def test_up_is_a_noop_on_a_seeded_database(scratch_db, monkeypatch):  # noqa: ARG001
    _point_pool_at(monkeypatch, scratch_db)
    assert migrate_mod.main(["up"]) == 0
    before = _scalar(scratch_db, "SELECT count(*) FROM schema_migrations")

    _execute(
        scratch_db,
        "INSERT INTO colleges (college_id, college_name) VALUES ('SEED-COLLEGE', 'Seeded College')",
        "INSERT INTO blocks (block_id, college_id, block_name)"
        " VALUES ('SEED-BLOCK', 'SEED-COLLEGE', 'Seeded Block')",
    )
    seeded = _scalar(scratch_db, "SELECT count(*) FROM blocks")

    assert migrate_mod.main(["up"]) == 0, "re-running migrations must succeed"
    assert (
        _scalar(scratch_db, "SELECT count(*) FROM schema_migrations") == before
    ), "a second 'up' recorded migrations twice"
    assert (
        _scalar(scratch_db, "SELECT count(*) FROM blocks") == seeded
    ), "re-running migrations destroyed seeded data"
    assert _scalar(scratch_db, "SELECT block_name FROM blocks WHERE block_id = 'SEED-BLOCK'") == (
        "Seeded Block"
    )


def test_status_reports_every_file(scratch_db, monkeypatch, capsys):  # noqa: ARG001
    _point_pool_at(monkeypatch, scratch_db)
    assert migrate_mod.main(["status"]) == 0
    pending = capsys.readouterr().out
    assert "PENDING" in pending

    assert migrate_mod.main(["up"]) == 0
    capsys.readouterr()
    assert migrate_mod.main(["status"]) == 0
    applied = capsys.readouterr().out
    assert "PENDING" not in applied
    assert applied.count("applied") == len(migrate_mod._migration_files())


def test_up_refuses_when_an_applied_file_changed(scratch_db, monkeypatch, tmp_path, capsys):  # noqa: ARG001
    """Never edit an applied migration — the runner must stop, not re-run it."""
    _point_pool_at(monkeypatch, scratch_db)
    assert migrate_mod.main(["up"]) == 0
    count_before = _scalar(scratch_db, "SELECT count(*) FROM schema_migrations")

    # Re-present 001 with altered content under the same filename.
    source = migrate_mod._migration_files()[0]
    (tmp_path / source.name).write_text(
        source.read_text(encoding="utf-8") + "\n-- edited after the fact\n", encoding="utf-8"
    )
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", tmp_path)

    capsys.readouterr()
    assert migrate_mod.main(["up"]) == 2
    assert "REFUSING" in capsys.readouterr().err
    assert (
        _scalar(scratch_db, "SELECT count(*) FROM schema_migrations") == count_before
    ), "a refused migration must not have applied anything"


def test_recorded_checksums_still_match_the_files_on_disk(db):  # noqa: ARG001
    """Integrity check on the shared test database: applied == current.

    Migrations are immutable; if this fails someone edited one after it had
    been applied, and every environment that has run it is now divergent.
    """
    files = {p.name: p for p in migrate_mod._migration_files()}
    conn = dbmod.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version, checksum FROM schema_migrations")
            recorded = dict(cur.fetchall())
    finally:
        conn.close()

    assert set(recorded) == set(files), (
        f"applied={sorted(set(recorded) - set(files))} " f"files={sorted(set(files) - set(recorded))}"
    )
    for name, path in files.items():
        assert recorded[name] == migrate_mod._checksum(
            path.read_text(encoding="utf-8")
        ), f"{name} was edited after it was applied — migrations are immutable"
