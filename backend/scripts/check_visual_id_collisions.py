"""Report Visual Asset ID collisions over the whole acs table (read-only).

Usage (operator, against the target database):
    cd ac_management_system/backend
    TEST_DATABASE_URL=postgresql://.../postgres python scripts/check_visual_id_collisions.py [--suffix-len 8]

Database selection (first wins): TEST_DATABASE_URL, else the DB_* env
(backend/.env is loaded by the app import below, so TEST_DATABASE_URL is
re-applied AFTER that import — shell exports alone would lose).

Prints: asset count, location completeness (complete/partial/unresolved),
distinct visual IDs, every colliding ID with its ac_ids.
Exit 0 when collision-free, exit 1 otherwise (then raise
VISUAL_ID_SUFFIX_LEN and re-run — no code or data change required).
"""

import argparse
import os
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from collections import defaultdict
from urllib.parse import urlparse

from app.config.database import get_db_connection  # noqa: E402
from app.services.visual_id import _clean, generate_visual_asset_id  # noqa: E402


def _resolve_db_env() -> None:
    """Pin the connection AFTER the app import ran load_dotenv(override)."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        return
    u = urlparse(url)
    os.environ.update(
        {
            "DB_HOST": u.hostname or "localhost",
            "DB_PORT": str(u.port or 5432),
            "DB_NAME": (u.path or "/postgres").lstrip("/"),
            "DB_USER": u.username or "postgres",
            "DB_PASSWORD": u.password or "",
            "DB_SSLMODE": "disable",
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suffix-len", type=int, default=None)
    args = parser.parse_args()

    _resolve_db_env()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Effective display strings, same rule as the API flats.
            cur.execute(
                "SELECT a.ac_id, a.asset_uuid,"
                " COALESCE(NULLIF(c.college_name, ''), c.college_id, al.source_college_value),"
                " COALESCE(NULLIF(b.block_name, ''), b.block_id, al.source_block_value),"
                " COALESCE(NULLIF(f.floor_name, ''), f.floor_id, al.source_floor_value),"
                " COALESCE(NULLIF(d.department_name, ''), d.department_id,"
                "          al.source_department_value),"
                " r.room_name, COALESCE(NULLIF(r.room_type, ''), al.source_room_type_value),"
                " COALESCE(al.room_id, a.room_id), al.source_room_number_value"
                " FROM acs a"
                " LEFT JOIN asset_locations al ON al.ac_id = a.ac_id"
                " LEFT JOIN rooms r ON r.room_id = COALESCE(al.room_id, a.room_id)"
                " LEFT JOIN floors f ON f.floor_id = COALESCE(al.floor_id, r.floor_id)"
                " LEFT JOIN blocks b ON b.block_id = COALESCE(al.block_id, f.block_id)"
                " LEFT JOIN departments d ON d.department_id = COALESCE(al.department_id,"
                "                                                      r.department_id)"
                " LEFT JOIN colleges c ON c.college_id = COALESCE(al.college_id, b.college_id)"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    by_visual: dict[str, list[str]] = defaultdict(list)
    complete = partial = unresolved = 0
    for r in rows:
        (
            ac_id,
            asset_uuid,
            college,
            block,
            floor,
            department,
            room_name,
            room_type,
            room_id,
            room_number,
        ) = r
        # How many of the five location levels carry real (non-placeholder)
        # information — drives the complete/partial/unresolved report.
        present = sum(1 for v in (college, block, floor, department) if _clean(v) is not None)
        if any(_clean(v) is not None for v in (room_name, room_type, room_id, room_number)):
            present += 1
        if present == 5:
            complete += 1
        elif present == 0:
            unresolved += 1
        else:
            partial += 1
        vid = generate_visual_asset_id(
            college=college,
            block=block,
            floor=floor,
            department=department,
            room_name=room_name,
            room_type=room_type,
            room_id=room_id,
            room_number=room_number,
            asset_uuid=str(asset_uuid) if asset_uuid is not None else None,
            suffix_len=args.suffix_len,
        )
        by_visual[vid].append(ac_id)

    collisions = {vid: ids for vid, ids in by_visual.items() if len(ids) > 1}
    print(f"assets evaluated: {len(rows)}")
    print(f"location complete (5/5): {complete}")
    print(f"location partial: {partial}")
    print(f"location unresolved (uuid-only fallback): {unresolved}")
    print(f"distinct visual IDs: {len(by_visual)}")
    print(f"collisions: {len(collisions)}")
    for vid, ids in sorted(collisions.items()):
        print(
            f"  {vid}: {', '.join(sorted(ids)[:10])}" + (f" (+{len(ids) - 10} more)" if len(ids) > 10 else "")
        )
    return 1 if collisions else 0


if __name__ == "__main__":
    raise SystemExit(main())
