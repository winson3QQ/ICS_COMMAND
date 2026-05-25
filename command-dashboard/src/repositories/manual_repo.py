import json
import uuid

from core.database import get_conn

from ._helpers import audit, now_utc, row_to_dict


def create_manual_record(data: dict, exercise_id: int | None = None) -> dict:
    rid     = str(uuid.uuid4())
    now     = now_utc()
    payload = data.get("payload", {})

    sql = """
        INSERT INTO manual_records
            (id, form_id, form_type, target_table, operator,
             summary, payload, sync_status, submitted_at, exercise_id)
        VALUES (?,?,?,?,?, ?,?,?,?,?)
    """
    with get_conn() as conn:
        conn.execute(sql, (
            rid,
            data["form_id"],
            data["form_type"],
            data["target_table"],
            data["operator"],
            data.get("summary"),
            json.dumps(payload, ensure_ascii=False),
            "pending",
            now,
            exercise_id,
        ))

    audit(data["operator"], data.get("device_id"),
          "manual_input", "manual_records", rid,
          {"form_id": data["form_id"], "summary": data.get("summary")},
          exercise_id)
    return {"id": rid, "submitted_at": now}


def get_manual_records(sync_status: str | None = None, limit: int = 100,
                       exercise_id: int | None = None) -> list[dict]:
    with get_conn() as conn:
        if exercise_id is not None and sync_status:
            rows = conn.execute(
                "SELECT * FROM manual_records WHERE sync_status=? AND exercise_id=? "
                "ORDER BY submitted_at DESC LIMIT ?",
                (sync_status, exercise_id, limit)).fetchall()
        elif exercise_id is not None:
            rows = conn.execute(
                "SELECT * FROM manual_records WHERE exercise_id=? "
                "ORDER BY submitted_at DESC LIMIT ?",
                (exercise_id, limit)).fetchall()
        elif sync_status:
            rows = conn.execute(
                "SELECT * FROM manual_records WHERE sync_status=? "
                "ORDER BY submitted_at DESC LIMIT ?",
                (sync_status, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM manual_records ORDER BY submitted_at DESC LIMIT ?",
                (limit,)).fetchall()

    result = []
    for r in rows:
        d = row_to_dict(r)
        if d.get("payload") and isinstance(d["payload"], str):
            try:
                d["payload"] = json.loads(d["payload"])
            except Exception:
                pass
        result.append(d)
    return result


def mark_manual_record_synced(record_id: str, operator: str,
                               exercise_id: int | None = None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE manual_records SET sync_status='synced', synced_at=? WHERE id=?",
            (now_utc(), record_id))
    audit(operator, None, "manual_record_synced", "manual_records", record_id, {}, exercise_id)
