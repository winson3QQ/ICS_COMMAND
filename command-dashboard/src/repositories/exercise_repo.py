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


# 演習-scoped 的表（皆有 exercise_id 欄）。刪除一場時連同其資料級聯清除，
# 範圍對齊 admin /reset-exercise，但限定 WHERE exercise_id=?（單場）。table 名為常數白名單。
_EXERCISE_SCOPED_TABLES = (
    "events",
    "cop_entities",
    "decisions",
    "audit_log",
    "manual_records",
    "snapshots",
    "resource_snapshots",
    "aar_entries",
    "exercise_kpis",
    "ai_recommendations",
    "ttx_injects",
    # #348-F10：chats 原漏在此清單外 → 刪演習不清通聯 PII（message/callsign/lat-lon）。補回
    # 一致性；chats.exercise_id=NULL（實戰/未分場廣播）不受 WHERE exercise_id=? 影響、不誤刪。
    "chats",
)


def delete_exercise(exercise_id: int) -> dict:
    """硬刪一場演習 + 其所有 scoped 資料（級聯）。**呼叫端須先確認非 active**（active 不可刪）。
    回傳各表清除筆數。某表無 exercise_id 欄 / 不存在則略過（容錯）。"""
    cleared: dict[str, int] = {}
    with get_conn() as conn:
        # 防 TOCTOU（router 已 409 擋 active；此處同連線內再確認，避免 race 中清掉剛被啟動的場資料）
        row = conn.execute("SELECT status FROM exercises WHERE id=?", (exercise_id,)).fetchone()
        if row is None or row["status"] == "active":
            return {"skipped": "active_or_missing"}
        for table in _EXERCISE_SCOPED_TABLES:
            try:
                cur = conn.execute(f"DELETE FROM {table} WHERE exercise_id=?", (exercise_id,))  # nosec B608 — table 名為常數白名單
                if cur.rowcount:
                    cleared[table] = cur.rowcount
            except Exception:  # noqa: BLE001 — 表/欄不存在（依部署）容錯略過
                pass
        cur = conn.execute("DELETE FROM exercises WHERE id=?", (exercise_id,))
        cleared["exercises"] = cur.rowcount
    return cleared
