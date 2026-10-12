"""Tiny migration runner (Phase 1.0).

Usage:
    python -m app.migrate up       # apply pending migrations in filename order
    python -m app.migrate status   # show applied / pending state

Tracks applied files in ``schema_migrations(version TEXT PK, checksum TEXT,
applied_at TIMESTAMPTZ)``. Re-running is a no-op. If a previously applied
migration file's content changed (checksum mismatch), the runner refuses to
continue — never edit an applied migration; add a new numbered file.

Files ``*.skip.sql`` and files starting with an underscore are documentation
(for example ``supabase_auth_trigger.sql`` must be executed by hand in the
Supabase SQL editor, where the ``auth`` schema exists) and are never applied
by this runner.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from app.config.database import get_db_connection

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _migration_files() -> list[Path]:
    return sorted(
        p
        for p in MIGRATIONS_DIR.glob("*.sql")
        if not p.name.startswith("_") and not p.name.endswith(".skip.sql")
    )


def _applied(conn) -> dict[str, str]:
    from psycopg2.extras import RealDictCursor

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT to_regclass('public.schema_migrations') AS present")
        if cur.fetchone()["present"] is None:
            return {}
        cur.execute("SELECT version, checksum FROM schema_migrations")
        return {r["version"]: r["checksum"] for r in cur.fetchall()}


def cmd_up() -> int:
    from psycopg2.extras import RealDictCursor

    files = _migration_files()
    if not files:
        print("no migration files found")
        return 0

    conn = get_db_connection()
    try:
        conn.autocommit = False
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(_CREATE_TABLE)
        conn.commit()

        cur.execute("SELECT version, checksum FROM schema_migrations")
        applied = {r["version"]: r["checksum"] for r in cur.fetchall()}

        for path in files:
            text = path.read_text(encoding="utf-8")
            digest = _checksum(text)
            recorded = applied.get(path.name)
            if recorded is not None:
                if recorded != digest:
                    print(
                        f"REFUSING: {path.name} was already applied but its content "
                        "changed. Migrations are immutable — add a new file.",
                        file=sys.stderr,
                    )
                    conn.rollback()
                    return 2
                continue  # already applied: no-op

            print(f"applying {path.name} ...")
            try:
                cur.execute("BEGIN")
                cur.execute(text)
                cur.execute(
                    "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                    (path.name, digest),
                )
                conn.commit()
                print(f"applied  {path.name}")
            except Exception:
                conn.rollback()
                print(f"FAILED   {path.name} (rolled back)", file=sys.stderr)
                raise
        print("up to date")
        return 0
    finally:
        conn.close()


def cmd_status() -> int:
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(_CREATE_TABLE)
        conn.commit()

        applied = _applied(conn)
        for path in _migration_files():
            state = "applied" if path.name in applied else "PENDING"
            print(f"{state:8} {path.name}")
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.migrate")
    parser.add_argument("command", choices=["up", "status"])
    args = parser.parse_args(argv)
    if args.command == "up":
        return cmd_up()
    return cmd_status()


if __name__ == "__main__":
    sys.exit(main())
