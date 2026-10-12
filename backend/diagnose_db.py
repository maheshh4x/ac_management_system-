"""Diagnostic script - safe read-only DB probe."""
import traceback
import sys
import os
from pathlib import Path

# ensure .env is loaded
from app.config.database import get_db_connection


def main():
    print("=" * 60)
    print("DB Diagnostics")
    print("=" * 60)
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT current_database(), current_user, version()")
        row = cur.fetchone()
        print(f"DB:      {row[0]}")
        print(f"User:    {row[1]}")
        print(f"Version: {row[2]}")
        print()

        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' ORDER BY table_name"
        )
        tables = [r[0] for r in cur.fetchall()]
        print("Tables:", tables)
        print()

        for tbl in ["acs", "rooms", "departments", "blocks", "floors", "colleges", "maintenance"]:
            if tbl in tables:
                cur.execute(
                    "SELECT column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_name=%s ORDER BY ordinal_position",
                    (tbl,)
                )
                cols = [(r[0], r[1]) for r in cur.fetchall()]
                cur.execute(f"SELECT COUNT(*) FROM {tbl}")
                cnt = cur.fetchone()[0]
                print(f"Table '{tbl}': {cnt} rows, columns={[c[0] for c in cols]}")
            else:
                print(f"Table '{tbl}': MISSING")
        print()

        # Check for dashboard query compatibility
        print("--- Dashboard query test ---")
        cur.execute("SELECT COUNT(*) FROM colleges")
        print(f"  colleges: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM blocks")
        print(f"  blocks: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM floors")
        print(f"  floors: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM departments")
        print(f"  departments: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM rooms")
        print(f"  rooms: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM acs")
        print(f"  acs: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM maintenance")
        print(f"  maintenance: {cur.fetchone()[0]}")

        # Check if maintenance has maintenance_type column
        print()
        print("--- maintenance table check ---")
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='maintenance' ORDER BY ordinal_position"
        )
        maint_cols = [r[0] for r in cur.fetchall()]
        print(f"  maintenance columns: {maint_cols}")

        # Sample ACS join test (what the GET /api/acs does)
        print()
        print("--- ACS join query test ---")
        try:
            cur.execute("""
                SELECT a.ac_id, a.room_id, r.room_name, r.room_type,
                       f.floor_id, f.floor_name, f.floor_number,
                       b.block_id, b.block_name, d.department_id, d.department_name,
                       a.ac_make_company, a.ac_model_number, a.ac_serial_number,
                       a.ac_capacity_ton, a.ac_type, a.manufacturing_year,
                       a.installation_year, a.current_status, a.ac_star_rating,
                       a.power_rating_kw, a.power_factor, a.installation_date,
                       a.last_maintenance_date, a.next_service_date, a.technician,
                       a.remarks, a.created_at, a.updated_at
                FROM acs a
                LEFT JOIN rooms r ON a.room_id = r.room_id
                LEFT JOIN floors f ON r.floor_id = f.floor_id
                LEFT JOIN blocks b ON f.block_id = b.block_id
                LEFT JOIN departments d ON r.department_id = d.department_id
                ORDER BY a.ac_id LIMIT 3
            """)
            rows = cur.fetchall()
            print(f"  ACS join query: OK ({len(rows)} sample rows)")
        except Exception as e:
            print(f"  ACS join query FAILED: {e}")
            traceback.print_exc()

        # Sample blocks query
        print()
        print("--- Blocks join query test ---")
        try:
            cur.execute("""
                SELECT b.block_id, b.college_id, c.college_name, b.block_name
                FROM blocks b LEFT JOIN colleges c ON b.college_id = c.college_id
                ORDER BY b.block_name LIMIT 3
            """)
            rows = cur.fetchall()
            print(f"  Blocks join query: OK ({len(rows)} sample rows)")
            if rows:
                print(f"  Sample: {rows[0]}")
        except Exception as e:
            print(f"  Blocks join query FAILED: {e}")
            traceback.print_exc()

        cur.close()
        conn.close()
        print()
        print("DB connection: SUCCESS")

    except Exception as e:
        print(f"\nDB FATAL ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
