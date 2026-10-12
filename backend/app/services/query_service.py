from app.config.database import get_db_connection
from app.services import location_sql
from app.services.status_map import normalize_status_filter


def execute_query(query, params=None):
    conn = get_db_connection()

    try:
        cursor = conn.cursor()
        cursor.execute(query, params or [])

        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()

        data = [dict(zip(columns, row, strict=False)) for row in rows]

        cursor.close()

        return data

    finally:
        conn.close()


def total_acs():
    result = execute_query("SELECT COUNT(*) AS total FROM acs")
    return result[0]["total"]


def ac_company_counts():
    return execute_query("""
        SELECT COALESCE(ac_make_company, 'Data not available') AS company, COUNT(*) AS count
        FROM acs
        GROUP BY ac_make_company
        ORDER BY count DESC, company
        """)


def ac_block_counts():
    return execute_query(f"""
        SELECT {location_sql.label_or('block', "'Data not available'")} AS block,
               COUNT(*) AS count
        {location_sql.ASSET_JOINS}
        GROUP BY 1
        ORDER BY count DESC, block
        """)


def total_tonnage():
    result = execute_query("SELECT COALESCE(SUM(ac_capacity_ton), 0) AS total_tonnage FROM acs")
    return result[0]["total_tonnage"]


def missing_model_count():
    result = execute_query(
        "SELECT COUNT(*) AS total FROM acs WHERE NULLIF(TRIM(ac_model_number), '') IS NULL"
    )
    return result[0]["total"]


def acs_in_location(location):
    # Effective match (§7): canonical id/name first, preserved source text
    # too — an unresolved reference still finds its assets in chat.
    pattern = f"%{location}%"
    return execute_query(
        f"""
        SELECT a.ac_id, a.ac_make_company, a.ac_model_number, a.operational_status,
               a.ac_capacity_ton,
               {location_sql.label('block')} AS block_name,
               {location_sql.label('floor')} AS floor_name,
               {location_sql.label('room')} AS room_name
        {location_sql.ASSET_JOINS}
        WHERE LOWER(COALESCE(b.block_name, '')) LIKE LOWER(%s)
           OR LOWER(COALESCE(b.block_id, '')) LIKE LOWER(%s)
           OR LOWER(COALESCE(al.source_block_value, '')) LIKE LOWER(%s)
           OR LOWER(COALESCE(r.room_name, '')) LIKE LOWER(%s)
           OR LOWER(COALESCE(r.room_id, '')) LIKE LOWER(%s)
           OR LOWER(COALESCE(al.source_room_number_value, '')) LIKE LOWER(%s)
        ORDER BY a.ac_id
        """,
        [pattern] * 6,
    )


def ac_status(status):
    """Rows matching the queried status phrase (chat STATUS intent).

    Inputs are normalised through ``status_map`` — the free-text column this
    used to compare against was dropped in 013. The multi-word phrases keep
    their historic synonym coverage as the equivalent vocabulary sets:
    "not working" spans FAULT + OFFLINE (its old list included 'inactive'
    -> OFFLINE), "under maintenance" is exactly MAINTENANCE; anything else
    resolves through ``normalize_status_filter`` and an unrecognised token
    returns no rows.
    """
    if status.lower() == "not working":
        return execute_query("""
            SELECT ac_id, ac_make_company, ac_model_number, ac_serial_number,
                   operational_status, ac_capacity_ton, ac_type
            FROM acs
            WHERE operational_status IN ('FAULT', 'OFFLINE')
            ORDER BY ac_id
            """)
    if status.lower() == "under maintenance":
        return execute_query("""
            SELECT ac_id, ac_make_company, ac_model_number, ac_serial_number,
                   operational_status, ac_capacity_ton, ac_type
            FROM acs
            WHERE operational_status = 'MAINTENANCE'
            ORDER BY ac_id
            """)
    canonical = normalize_status_filter(status)
    if canonical is None:
        return []
    return execute_query(
        """
        SELECT
            ac_id,
            ac_make_company,
            ac_model_number,
            ac_serial_number,
            operational_status,
            ac_capacity_ton,
            ac_type
        FROM acs
        WHERE operational_status = %s
        ORDER BY ac_id
        """,
        [canonical],
    )


def ac_by_company(company):
    return execute_query(
        """
        SELECT
            ac_id,
            ac_make_company,
            ac_model_number,
            ac_serial_number,
            operational_status,
            ac_capacity_ton,
            ac_type
        FROM acs
        WHERE LOWER(ac_make_company) = LOWER(%s)
        ORDER BY ac_id
        """,
        [company],
    )


def ac_details(ac_id):
    return execute_query(
        f"""
        SELECT
            a.ac_id,
            a.ac_make_company,
            a.ac_model_number,
            a.ac_serial_number,
            a.ac_capacity_ton,
            a.ac_type,
            a.manufacturing_year,
            a.installation_year,
            a.operational_status,
            a.ac_star_rating,
            a.power_rating_kw,
            a.power_factor,
            {location_sql.label('room')} AS room_name,
            COALESCE(r.room_type, al.source_room_type_value) AS room_type,
            {location_sql.label('department')} AS department_name,
            {location_sql.label('floor')} AS floor_name,
            {location_sql.label('block')} AS block_name
        {location_sql.ASSET_JOINS}
        WHERE a.ac_id = %s
        """,
        [ac_id],
    )


def maintenance_for_ac(ac_id):
    return execute_query(
        """
        SELECT
            m.maintenance_id,
            m.ac_id,
            m.maintenance_type,
            m.maintenance_date,
            m.remarks
        FROM maintenance m
        WHERE m.ac_id = %s
        ORDER BY m.maintenance_date DESC
        """,
        [ac_id],
    )


def maintenance_count():
    result = execute_query("SELECT COUNT(*) AS total FROM maintenance")
    return result[0]["total"]


def maintenance_list():
    return execute_query("""
        SELECT
            m.maintenance_id,
            m.ac_id,
            m.maintenance_type,
            m.maintenance_date,
            m.remarks
        FROM maintenance m
        ORDER BY m.maintenance_date DESC
        """)


def ac_list():
    return execute_query("""
        SELECT
            ac_id,
            ac_make_company,
            ac_model_number,
            operational_status,
            ac_capacity_ton,
            ac_type
        FROM acs
        ORDER BY ac_id
        """)


def my_jobs(profile_id, limit=50):
    """Work orders assigned to this profile (Phase 7 chat MY_JOBS intent).

    Identity-scoped: the caller only ever sees rows whose ``technician_id``
    equals their own profile id. Ordered by urgency (overdue/in-progress
    first, then nearest scheduled date).
    """
    return execute_query(
        f"""
        SELECT
            w.id,
            w.ac_id,
            w.maintenance_type,
            w.priority,
            w.status,
            w.scheduled_date,
            a.ac_make_company,
            {location_sql.label('room')} AS room_name,
            f.floor_id AS floor_id
        FROM maintenance_work_orders w
        LEFT JOIN acs a ON a.ac_id = w.ac_id
        {location_sql.LOCATION_JOIN}
        LEFT JOIN rooms r ON r.room_id = COALESCE(al.room_id, a.room_id)
        LEFT JOIN floors f ON f.floor_id = COALESCE(al.floor_id, r.floor_id)
        WHERE w.technician_id = %s
        ORDER BY
            CASE w.status
                WHEN 'IN_PROGRESS' THEN 0
                WHEN 'ASSIGNED' THEN 1
                WHEN 'SCHEDULED' THEN 2
                ELSE 3
            END,
            w.scheduled_date ASC NULLS LAST,
            w.priority DESC
        LIMIT %s
        """,
        [profile_id, limit],
    )


def my_jobs_summary(profile_id):
    """Aggregate counts for the caller's own work orders (chat MY_JOBS)."""
    return execute_query(
        """
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (
                WHERE status IN ('SCHEDULED', 'ASSIGNED', 'IN_PROGRESS')
            ) AS open,
            COUNT(*) FILTER (WHERE status = 'IN_PROGRESS') AS in_progress,
            COUNT(*) FILTER (
                WHERE status IN ('SCHEDULED', 'ASSIGNED', 'IN_PROGRESS')
                  AND scheduled_date IS NOT NULL
                  AND scheduled_date < CURRENT_DATE
            ) AS overdue,
            COUNT(*) FILTER (WHERE status = 'COMPLETED') AS completed
        FROM maintenance_work_orders
        WHERE technician_id = %s
        """,
        [profile_id],
    )
