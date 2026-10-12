"""Phase 1.0 migration runner tests (checksum immutability + idempotency).

The pure checksum/listing logic runs without a database; the `up` flow needs
TEST_DATABASE_URL.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import migrate  # noqa: E402

needs_db = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="set TEST_DATABASE_URL")


def test_checksum_is_stable_and_sensitive():
    d1 = migrate._checksum("SELECT 1;")
    assert d1 == migrate._checksum("SELECT 1;")
    assert d1 != migrate._checksum("SELECT 2;")


def test_migration_files_are_numbered_and_trigger_file_skipped():
    names = [p.name for p in migrate._migration_files()]
    assert names == sorted(names)
    assert "_supabase_auth_trigger.sql" not in names
    assert "006_profiles_approval.sql" in names
    assert all(name[:3].isdigit() for name in names)


@needs_db
def test_up_is_idempotent(tmp_path=None):
    assert migrate.main(["up"]) == 0
    assert migrate.main(["up"]) == 0  # second run: no-op
