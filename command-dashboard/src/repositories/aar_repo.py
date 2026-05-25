from core.database import get_conn

from ._helpers import now_utc, row_to_dict


def create_aar_entry(exercise_id: int, category: str, content: str,
                     created_by: str | None = None) -> dict:
    valid_categories = {"well", "improve", "recommend"}
    if category not in valid_categories:
        raise ValueError(f"category 必須是 {valid_categories} 之一")
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO aar_entries (exercise_id, category, content, created_by, created_at) "
            "VALUES (?,?,?,?,?)",
            (exercise_id, category, content, created_by, now))
    return {"id": cur.lastrowid, "exercise_id": exercise_id, "category": category,
            "content": content, "created_by": created_by, "created_at": now}


def get_aar_entries(exercise_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM aar_entries WHERE exercise_id=? ORDER BY created_at",
            (exercise_id,)).fetchall()
    return [row_to_dict(r) for r in rows]
