"""
ttx_repo.py — TTX inject 管理
C0：inject 透過 exercise_id 關聯（ttx_sessions 已廢棄）
"""

import json
import uuid

from core.database import get_conn

from ._helpers import audit, now_utc


def create_ttx_inject(exercise_id: int, seq: int, target_unit: str,
                      inject_type: str, title: str, payload: dict,
                      description: str | None = None,
                      scheduled_offset_min: int | None = None) -> dict:
    iid = str(uuid.uuid4())
    now = now_utc()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ttx_injects
               (id, exercise_id, inject_seq, target_unit, inject_type, title,
                description, payload, scheduled_offset_min, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (iid, exercise_id, seq, target_unit, inject_type, title,
             description, json.dumps(payload, ensure_ascii=False),
             scheduled_offset_min, "pending", now))
    return {"id": iid, "exercise_id": exercise_id, "inject_seq": seq,
            "target_unit": target_unit, "inject_type": inject_type,
            "title": title, "status": "pending", "created_at": now}


def bulk_create_ttx_injects(exercise_id: int, injects: list[dict]) -> int:
    count = 0
    for inj in injects:
        create_ttx_inject(
            exercise_id=exercise_id,
            seq=inj["seq"],
            target_unit=inj["target_unit"],
            inject_type=inj["type"],
            title=inj["title"],
            payload=inj.get("payload", {}),
            description=inj.get("description"),
            scheduled_offset_min=inj.get("offset_min"),
        )
        count += 1
    return count


def get_ttx_injects(exercise_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM ttx_injects WHERE exercise_id=? ORDER BY inject_seq",
            (exercise_id,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        if isinstance(d.get("payload"), str):
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
        result.append(d)
    return result


def mark_ttx_inject_done(inject_id: str, facilitator: str) -> bool:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE ttx_injects SET status='injected', injected_at=? WHERE id=? AND status='pending'",
            (now, inject_id))
    if cur.rowcount:
        audit(facilitator, None, "ttx_inject_fired", "ttx_injects", inject_id, {})
    return cur.rowcount > 0
