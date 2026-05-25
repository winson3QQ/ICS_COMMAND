"""
resource_snapshot_repo.py — 正規化 Pi push 資源快照（時間序列）
C0：供跨場次比較、AI 訓練用
"""

from core.database import get_conn

from ._helpers import now_utc, row_to_dict


def insert_resource_snapshot(exercise_id: int | None, unit_type: str,
                              data: dict, source: str = "pi_push") -> dict:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO resource_snapshots
                (exercise_id, unit_type, snapshot_at,
                 total_beds, occupied_beds,
                 light_count, medium_count, severe_count, deceased_count, source)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            exercise_id, unit_type, now,
            data.get("total_beds"),
            data.get("occupied_beds"),
            data.get("light_count"),
            data.get("medium_count"),
            data.get("severe_count"),
            data.get("deceased_count"),
            source,
        ))
    return {"id": cur.lastrowid, "snapshot_at": now}


def get_resource_snapshots(exercise_id: int, unit_type: str | None = None) -> list[dict]:
    with get_conn() as conn:
        if unit_type:
            rows = conn.execute(
                "SELECT * FROM resource_snapshots "
                "WHERE exercise_id=? AND unit_type=? ORDER BY snapshot_at",
                (exercise_id, unit_type)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM resource_snapshots WHERE exercise_id=? ORDER BY snapshot_at",
                (exercise_id,)).fetchall()
    return [row_to_dict(r) for r in rows]
