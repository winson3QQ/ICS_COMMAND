import json
import uuid

from core.database import get_conn

from ._helpers import NULL_SCOPE, audit, now_utc, row_to_dict


def create_manual_record(data: dict, exercise_id: int | None = None) -> dict:
    rid = str(uuid.uuid4())
    now = now_utc()
    payload = data.get("payload", {})

    sql = """
        INSERT INTO manual_records
            (id, form_id, form_type, target_table, operator,
             summary, payload, sync_status, submitted_at, exercise_id)
        VALUES (?,?,?,?,?, ?,?,?,?,?)
    """
    with get_conn() as conn:
        conn.execute(
            sql,
            (
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
            ),
        )

    audit(
        data["operator"],
        data.get("device_id"),
        "manual_input",
        "manual_records",
        rid,
        {"form_id": data["form_id"], "summary": data.get("summary")},
        exercise_id,
    )
    return {"id": rid, "submitted_at": now}


def get_manual_records(sync_status: str | None = None, limit: int = 100, exercise_id=None) -> list[dict]:
    # P1-14：exercise_id 三態（見 _helpers.NULL_SCOPE）——
    #   int → exact / NULL_SCOPE → IS NULL（實戰池）/ None → 不過濾（內部 caller）
    clauses, params = [], []
    if sync_status:
        clauses.append("sync_status=?")
        params.append(sync_status)
    if exercise_id is NULL_SCOPE:
        clauses.append("exercise_id IS NULL")
    elif exercise_id is not None:
        clauses.append("exercise_id=?")
        params.append(exercise_id)
    sql = "SELECT * FROM manual_records"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY submitted_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()  # nosec B608 — clause 全常數，值 parameterized

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


def mark_manual_record_synced(record_id: str, operator: str, exercise_id: int | None = None):
    with get_conn() as conn:
        conn.execute("UPDATE manual_records SET sync_status='synced', synced_at=? WHERE id=?", (now_utc(), record_id))
    audit(operator, None, "manual_record_synced", "manual_records", record_id, {}, exercise_id)
