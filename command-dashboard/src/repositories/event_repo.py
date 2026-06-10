import json
import sqlite3
import time
import uuid

import structlog
from core.database import get_conn

from ._helpers import NULL_SCOPE, add_minutes, audit, iso_utc, now_utc, row_to_dict

_log = structlog.get_logger()


def create_event(data: dict, exercise_id: int | None = None) -> dict:
    eid      = str(uuid.uuid4())
    now      = now_utc()
    severity = data.get("severity", "info")

    deadline_min      = {"critical": 10, "warning": 30, "info": 60}.get(severity, 60)
    # P2-20(A) #199 review：occurred_at 正規化為 UTC Z 再入庫——timeline / 查詢以字串序比較時間，
    # offset ISO（+08:00）混 Z 會排錯序、時間窗漏抓（前端送 Z，但 API client / TTX inject 不保證）。
    occurred          = iso_utc(data.get("occurred_at")) or now
    response_deadline = add_minutes(occurred, deadline_min)

    mmdd   = now[5:7] + now[8:10]
    prefix = f"EV-{mmdd}-"

    sql = """
        INSERT INTO events
            (id, event_code, reported_by_unit, location_desc,
             event_type, severity, status, response_type, response_deadline,
             needs_commander_decision, description,
             related_person_name, assigned_unit, occurred_at, operator_name,
             created_at, exercise_id)
        VALUES (?,
            (SELECT ? || printf('%03d',
                COALESCE(MAX(CAST(SUBSTR(event_code,-3) AS INTEGER)), 0) + 1)
             FROM events WHERE event_code LIKE ?),
            ?,?, ?,?,?,?, ?,?, ?,?,?, ?,?,?,?)
    """

    for attempt in range(10):
        try:
            with get_conn() as conn:
                conn.execute(sql, (
                    eid,
                    prefix, prefix + "%",
                    data["reported_by_unit"],
                    data.get("location_desc"),
                    data["event_type"],
                    severity,
                    "open",
                    data.get("response_type"),
                    response_deadline,
                    1 if data.get("needs_commander_decision") else 0,
                    data["description"],
                    data.get("related_person_name"),
                    data.get("assigned_unit"),
                    occurred,
                    data["operator_name"],
                    now,
                    exercise_id,
                ))
                row        = conn.execute("SELECT event_code FROM events WHERE id=?", (eid,)).fetchone()
                event_code = row["event_code"]
            break
        except sqlite3.IntegrityError as e:
            if "UNIQUE" in str(e) and attempt < 9:
                time.sleep(0.01 * (attempt + 1))
                eid = str(uuid.uuid4())
                continue
            _log.error("db_write_error", msg="DB 寫入失敗",
                       detail={"table": "events", "error": str(e)})
            raise

    audit(data["operator_name"], None, "event_created", "events", eid,
          {"event_code": event_code, "event_type": data["event_type"], "severity": severity},
          exercise_id)
    return {"id": eid, "event_code": event_code, "response_deadline": response_deadline}


def get_events(status: str | None = None, limit: int = 50,
               exercise_id=None) -> list[dict]:
    # P1-14：exercise_id 三態（見 _helpers.NULL_SCOPE）——
    #   int → exact / NULL_SCOPE → IS NULL（實戰池）/ None → 不過濾（內部 caller）
    clauses, params = [], []
    if status:
        clauses.append("status=?")
        params.append(status)
    if exercise_id is NULL_SCOPE:
        clauses.append("exercise_id IS NULL")
    elif exercise_id is not None:
        clauses.append("exercise_id=?")
        params.append(exercise_id)
    sql = "SELECT * FROM events"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY occurred_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()  # nosec B608 — clause 全常數，值 parameterized
    return [row_to_dict(r) for r in rows]


def patch_event(event_id: str, updates: dict):
    allowed = {"assigned_unit", "response_deadline", "location_desc"}
    safe    = {k: v for k, v in updates.items() if k in allowed}
    if not safe:
        return
    set_clause = ", ".join(f"{k}=?" for k in safe)
    values     = list(safe.values()) + [event_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE events SET {set_clause} WHERE id=?", values)  # nosec B608


def update_event_status(event_id: str, status: str, operator: str,
                        exercise_id: int | None = None):
    valid_transitions = {
        "open":        {"in_progress", "resolved", "closed"},
        "in_progress": {"in_progress", "resolved", "closed"},
        "resolved":    set(),
        "closed":      set(),
    }
    now          = now_utc()
    label        = {"open":"未結","in_progress":"處理中","resolved":"已結案","closed":"已關閉"}.get(status, status)
    note_json    = json.dumps({"time": now, "text": f"狀態變更為「{label}」", "by": operator}, ensure_ascii=False)

    with get_conn() as conn:
        row = conn.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise ValueError(f"事件 {event_id} 不存在")
        if status not in valid_transitions.get(row["status"], set()):
            raise ValueError(f"不允許從「{row['status']}」轉換到「{status}」")

        if status in ("resolved", "closed"):
            conn.execute(
                """UPDATE events SET status=?, resolved_at=?,
                   notes = json_insert(COALESCE(notes,'[]'), '$[#]', json(?))
                   WHERE id=?""",
                (status, now, note_json, event_id))
        else:
            conn.execute(
                """UPDATE events SET status=?,
                   notes = json_insert(COALESCE(notes,'[]'), '$[#]', json(?))
                   WHERE id=?""",
                (status, note_json, event_id))

    audit(operator, None, "event_status_updated", "events", event_id, {"status": status}, exercise_id)


def add_event_note(event_id: str, text: str, operator: str,
                   exercise_id: int | None = None) -> dict:
    now       = now_utc()
    note_json = json.dumps({"time": now, "text": text, "by": operator}, ensure_ascii=False)
    with get_conn() as conn:
        row = conn.execute("SELECT status FROM events WHERE id=?", (event_id,)).fetchone()
        if not row:
            raise ValueError(f"Event {event_id} not found")
        new_status = "in_progress" if row["status"] == "open" else row["status"]
        conn.execute(
            """UPDATE events SET
               notes  = json_insert(COALESCE(notes,'[]'), '$[#]', json(?)),
               status = ?
               WHERE id=?""",
            (note_json, new_status, event_id))
        cnt_row = conn.execute(
            "SELECT json_array_length(COALESCE(notes,'[]')) as cnt FROM events WHERE id=?",
            (event_id,)).fetchone()
    audit(operator, None, "event_note_added", "events", event_id, {"text": text[:50]}, exercise_id)
    return {"ok": True, "notes_count": cnt_row["cnt"] if cnt_row else 0}
