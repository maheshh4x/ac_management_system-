"""Throwaway verification for migration 014 (deleted after run)."""
import psycopg2

conn = psycopg2.connect(
    host="localhost", port="5433", dbname="postgres",
    user="postgres", password="postgres",
)
conn.autocommit = False
cur = conn.cursor()

# Idempotent baseline (same as tests/conftest.py) in case this DB was recreated.
for stmt in (
    "INSERT INTO blocks (block_id, college_id, block_name) VALUES "
    "('MAIN-ACADEMIC-BLOCK', 'NITTTR-CHENNAI', 'Main Academic Block') "
    "ON CONFLICT (block_id) DO NOTHING",
    "INSERT INTO departments (department_id, department_name) VALUES "
    "('DEPT-COMPUTER-SCIENCE', 'Computer Science') ON CONFLICT (department_id) DO NOTHING",
    "INSERT INTO floors (floor_id, block_id, floor_name, floor_number) VALUES "
    "('MAIN-ACADEMIC-BLOCK-FLR-1', 'MAIN-ACADEMIC-BLOCK', 'Floor 1', 1) "
    "ON CONFLICT (floor_id) DO NOTHING",
    "INSERT INTO rooms (room_id, floor_id, department_id, room_name, room_type) VALUES "
    "('ROOM-102', 'MAIN-ACADEMIC-BLOCK-FLR-1', 'DEPT-COMPUTER-SCIENCE', "
    "'CSE Computer Lab 1', 'Lab') ON CONFLICT (room_id) DO NOTHING",
    "INSERT INTO rooms (room_id, floor_id, department_id, room_name) VALUES "
    "('ROOM-105', 'MAIN-ACADEMIC-BLOCK-FLR-1', 'DEPT-ADMINISTRATION', 'HOD Cabin') "
    "ON CONFLICT (room_id) DO NOTHING",
):
    cur.execute(stmt)


def one(sql, params=()):
    cur.execute(sql, params)
    return cur.fetchone()


def expect_error(sql, params=(), exc=Exception):
    """Run SQL expecting a specific failure; keep the transaction usable."""
    cur.execute("SAVEPOINT expect_err")
    try:
        cur.execute(sql, params)
    except exc:
        cur.execute("ROLLBACK TO SAVEPOINT expect_err")
        return True
    cur.execute("RELEASE SAVEPOINT expect_err")
    return False


try:
    # 0. overall status constraint accepts PARTIAL
    cur.execute(
        "INSERT INTO acs (ac_id, location_status) VALUES ('TMP-014', 'PARTIAL')"
    )

    # 1. INSERT with no room -> mirror creates an all-MISSING record + PENDING
    row = one(
        "SELECT location_status FROM acs WHERE ac_id = 'TMP-014'"
    )
    assert row[0] == "PENDING", f"roomless insert overall={row[0]}"
    row = one(
        "SELECT room_status, block_status, location_status FROM asset_locations "
        "JOIN acs USING (ac_id) WHERE ac_id = 'TMP-014'"
    )
    assert row[0] == "MISSING" and row[1] == "MISSING" and row[2] == "PENDING", row
    print("OK 1: roomless insert -> record created, overall PENDING")

    # 2. legacy UPDATE acs SET room_id -> chain derived, ASSIGNED
    cur.execute("UPDATE acs SET room_id = 'ROOM-102' WHERE ac_id = 'TMP-014'")
    row = one(
        "SELECT al.room_id, al.floor_id, al.block_id, al.college_id, al.department_id, "
        "       al.room_status, al.block_status, a.location_status "
        "FROM asset_locations al JOIN acs a USING (ac_id) WHERE al.ac_id = 'TMP-014'"
    )
    assert row == (
        "ROOM-102", "MAIN-ACADEMIC-BLOCK-FLR-1", "MAIN-ACADEMIC-BLOCK",
        "NITTTR-CHENNAI", "DEPT-COMPUTER-SCIENCE", "RESOLVED", "RESOLVED", "ASSIGNED",
    ), row
    print("OK 2: legacy room write -> chain derived, overall ASSIGNED")

    # 3. legacy UPDATE room_id = NULL -> room cleared, statuses recompute
    cur.execute("UPDATE acs SET room_id = NULL WHERE ac_id = 'TMP-014'")
    row = one(
        "SELECT al.room_id, al.room_status, al.block_status, a.location_status "
        "FROM asset_locations al JOIN acs a USING (ac_id) WHERE al.ac_id = 'TMP-014'"
    )
    assert row == (None, "MISSING", "MISSING", "PENDING"), row
    print("OK 3: legacy room clear -> record follows, overall PENDING")

    # 4. composite FK rejects a contradictory room/floor pair
    cur.execute("UPDATE acs SET room_id = 'ROOM-105' WHERE ac_id = 'TMP-014'")
    assert expect_error(
        "UPDATE asset_locations SET floor_id = 'MAIN-ACADEMIC-BLOCK-FLR-999' "
        "WHERE ac_id = 'TMP-014'",
        exc=psycopg2.errors.ForeignKeyViolation,
    ), "contradictory chain was accepted"
    print("OK 4: composite FK rejects room/floor contradiction")

    # 5. alias table: insert + duplicate rejection
    cur.execute(
        "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias) "
        "VALUES ('college', 'NITTTR-CHENNAI', 'NITTTRC', 'nitttrc')"
    )
    assert expect_error(
        "INSERT INTO location_aliases (entity_type, entity_id, alias, normalized_alias) "
        "VALUES ('college', 'OTHER', 'NITTTRC ', 'nitttrc')",
        exc=psycopg2.errors.UniqueViolation,
    ), "duplicate alias was accepted"
    print("OK 5: alias duplicate rejected")

    # 6. invalid manual_fields element rejected
    assert expect_error(
        "UPDATE asset_locations SET manual_fields = ARRAY['rooom'] WHERE ac_id = 'TMP-014'",
        exc=psycopg2.errors.CheckViolation,
    ), "bad manual field accepted"
    print("OK 6: manual_fields CHECK works")

    conn.rollback()
    print("ALL 014 SANITY CHECKS PASSED")
except Exception:
    conn.rollback()
    raise
finally:
    cur.close()
    conn.close()
