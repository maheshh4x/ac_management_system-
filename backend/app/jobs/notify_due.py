"""Due/overdue job reminders — ``python -m app.jobs.notify_due``.

Selects SCHEDULED/ASSIGNED jobs whose scheduled_date is within
``DUE_SOON_DAYS`` (overdue included), then notifies the assigned technician,
or the active managers when the job is still unassigned:

* ``JOB_OVERDUE`` — scheduled_date before today
* ``JOB_DUE``     — scheduled_date today or within DUE_SOON_DAYS

Deduplicated per job + type per calendar day (one check before the recipient
loop), so a daily — or more frequent — scheduler can call this safely.

Standalone by design: own connection, explicit commit per job (a failure for
one job rolls back only that job's reminders), no Flask app context and no
``audit()`` calls.
"""

from __future__ import annotations

import logging
import sys

from app.config.database import get_db_connection
from app.services.notify import notify

logger = logging.getLogger("app.jobs.notify_due")

DUE_SOON_DAYS = 2
MANAGER_ROLES = ("SUPER_ADMIN", "FACILITY_MANAGER")


def _managers(conn) -> list[str]:
    """Active managers — fallback recipients for unassigned jobs."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id FROM profiles" " WHERE role IN %s AND is_active",
            (MANAGER_ROLES,),
        )
        return [str(row[0]) for row in cur.fetchall()]
    finally:
        cur.close()


def _already_notified(conn, ntype: str, job_id: str) -> bool:
    """True when this job already got this reminder type today."""
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM notifications"
            " WHERE type = %s AND entity_type = 'maintenance_work_order'"
            " AND entity_id = %s AND created_at::date = CURRENT_DATE"
            " LIMIT 1",
            (ntype, str(job_id)),
        )
        return cur.fetchone() is not None
    finally:
        cur.close()


def run() -> int:
    """Send due/overdue reminders; returns notifications written (attempted)."""
    conn = get_db_connection()
    sent = 0
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, ac_id, maintenance_type, technician_id, scheduled_date,"
            " (scheduled_date < CURRENT_DATE) AS is_overdue"
            " FROM maintenance_work_orders"
            " WHERE status IN %s AND scheduled_date IS NOT NULL"
            " AND scheduled_date <= CURRENT_DATE + %s"
            " ORDER BY scheduled_date ASC",
            (("SCHEDULED", "ASSIGNED"), DUE_SOON_DAYS),
        )
        jobs = cur.fetchall()
        cur.close()

        for job in jobs:
            job_id, ac_id, mtype, technician_id, scheduled_date, is_overdue = job
            ntype = "JOB_OVERDUE" if is_overdue else "JOB_DUE"
            try:
                if _already_notified(conn, ntype, str(job_id)):
                    continue
                recipients = [str(technician_id)] if technician_id else _managers(conn)
                kind = mtype or "Maintenance"
                verb = "was due on" if is_overdue else "is due on"
                title = "Maintenance job overdue" if is_overdue else "Maintenance job due soon"
                body = f"{kind} job on {ac_id} {verb} {scheduled_date}."
                for uid in recipients:
                    notify(
                        uid,
                        ntype,
                        title,
                        body,
                        "maintenance_work_order",
                        str(job_id),
                        conn=conn,
                    )
                    sent += 1
                conn.commit()
            except Exception:
                # Roll back so one bad job cannot poison the remaining ones.
                conn.rollback()
                logger.warning("notify_due skipped job=%s", job_id, exc_info=True)
    finally:
        conn.close()
    return sent


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        sent = run()
    except Exception:
        logger.exception("notify_due failed")
        return 1
    logger.info("notify_due finished: %s notification(s) written", sent)
    return 0


if __name__ == "__main__":
    sys.exit(main())
