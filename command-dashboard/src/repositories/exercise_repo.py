"""
exercise_repo.py — 演練場次管理
C0：exercises 表（合併原 ttx_sessions）
"""

from core.database import get_conn

from ._helpers import audit, now_utc


def create_exercise(data: dict) -> dict:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO exercises
                (name, date, location, type, scenario_summary, weather,
                 participant_count, organizing_body, status,
                 facilitator, scenario_id, created_at)
            VALUES (?,?,?,?,?,?, ?,?,?, ?,?,?)
        """,
            (
                data["name"],
                data.get("date"),
                data.get("location"),
                data.get("type", "ttx"),
                data.get("scenario_summary"),
                data.get("weather"),
                data.get("participant_count"),
                data.get("organizing_body"),
                "setup",
                data.get("facilitator"),
                data.get("scenario_id"),
                now,
            ),
        )
        eid = cur.lastrowid
    audit(
        "system",
        None,
        "exercise_created",
        "exercises",
        str(eid),
        {"name": data["name"], "type": data.get("type", "ttx")},
    )
    return get_exercise(eid)


def get_exercise(exercise_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM exercises WHERE id=?", (exercise_id,)).fetchone()
    return dict(row) if row else None


def list_exercises(type_filter: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if type_filter:
            rows = conn.execute(
                "SELECT * FROM exercises WHERE type=? ORDER BY created_at DESC", (type_filter,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM exercises ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def update_exercise_status(exercise_id: int, status: str, operator: str) -> bool:
    """
    更新場次狀態。
    'active' → 啟動 mutex（同一時間只能有一個 active）。
    'archived' → 釋放 mutex。
    """
    valid = {"setup", "active", "archived"}
    if status not in valid:
        raise ValueError(f"status 必須是 {valid} 之一")

    now = now_utc()
    with get_conn() as conn:
        if status == "active":
            # 原子寫入防 TOCTOU：UPDATE 與 NOT EXISTS 在同一 statement，SQLite
            # 寫鎖序列化；兩條 thread 並發時只有一條會成功 set active。
            cur = conn.execute(
                """
                UPDATE exercises
                SET status='active', started_at=?, mutex_locked=1
                WHERE id=?
                  AND NOT EXISTS (
                    SELECT 1 FROM exercises WHERE status='active' AND id != ?
                  )
                """,
                (now, exercise_id, exercise_id),
            )
            if cur.rowcount == 0:
                # 0 rows 有兩種可能：(1) 別處已有 active（mutex 衝突）;
                # (2) exercise_id 不存在。判斷後給對應錯誤訊息。
                conflict = conn.execute(
                    "SELECT 1 FROM exercises WHERE status='active' AND id != ?", (exercise_id,)
                ).fetchone()
                if conflict:
                    raise ValueError("已有進行中的演練，請先封存後再啟動")
        elif status == "archived":
            conn.execute(
                "UPDATE exercises SET status=?, ended_at=?, mutex_locked=0 WHERE id=?", (status, now, exercise_id)
            )
        else:
            conn.execute("UPDATE exercises SET status=? WHERE id=?", (status, exercise_id))

    audit(operator, None, "exercise_status_updated", "exercises", str(exercise_id), {"status": status})
    return True


def get_active_exercise() -> dict | None:
    """取得目前 active 的演練（至多一個）"""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM exercises WHERE status='active' LIMIT 1").fetchone()
    return dict(row) if row else None
