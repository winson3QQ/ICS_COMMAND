from core.database import get_conn

from ._helpers import row_to_dict


def get_audit_log(limit: int = 100, exercise_id: int | None = None) -> list[dict]:
    with get_conn() as conn:
        if exercise_id is not None:
            rows = conn.execute("""
                SELECT a.*, e.event_code AS _event_code, e.description AS _event_desc
                FROM   audit_log a
                LEFT JOIN events e ON a.target_table='events' AND a.target_id=e.id
                WHERE  a.exercise_id=?
                ORDER  BY a.created_at DESC LIMIT ?
            """, (exercise_id, limit)).fetchall()
        else:
            rows = conn.execute("""
                SELECT a.*, e.event_code AS _event_code, e.description AS _event_desc
                FROM   audit_log a
                LEFT JOIN events e ON a.target_table='events' AND a.target_id=e.id
                ORDER  BY a.created_at DESC LIMIT ?
            """, (limit,)).fetchall()
    return [row_to_dict(r) for r in rows]
