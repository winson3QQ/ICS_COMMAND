"""repositories/chat_repo.py — chats 表存取（P2-07，#129）。"""

from core.database import get_conn

from ._helpers import NULL_SCOPE, row_to_dict
from schemas.chat import ChatIn


def insert_chat(chat: ChatIn) -> dict:
    """寫一筆通聯記錄，回完整 row。message 應已 escape（chat_service 負責）。

    全參數化（named placeholder）；`"group"` 為 SQL 保留字故 quote。WAL 下第二 connection
    看不到 uncommitted row，故 with 退出（auto-commit）後再讀（對齊 cop_entity_repo）。
    """
    payload = chat.model_dump()
    with get_conn() as conn:
        cur = conn.execute(
            'INSERT INTO chats (sender_uid, callsign, message, "group", lat, lon, time, exercise_id) '
            "VALUES (:sender_uid, :callsign, :message, :group, :lat, :lon, :time, :exercise_id)",
            payload,
        )
        rid = cur.lastrowid
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM chats WHERE id = ?", (rid,)).fetchone()
        return row_to_dict(row)


def list_chats(exercise_id, since: str | None = None, until: str | None = None, cap: int = 200) -> list[dict]:
    """列出通聯（chats）——GET /api/chat（#213 b1）唯讀投影。

    時間以 COALESCE(time, received_at) 為準（client 未帶 event 時間時用入庫時間）。
    **newest-first** 取最新 `cap` 筆（chat feed 要的是最近通聯，非最舊）；呼叫端
    （chat_service.build_chat_feed）負責 limit+1 探測截斷 + reverse 成顯示用升序。

    exercise_id 三態（見 _helpers.NULL_SCOPE，對齊 cop_entity_repo.list_cop_entities）——
      int → exact / NULL_SCOPE → IS NULL（實戰池）/ None → 不過濾（內部 caller）。
    message 入庫前已 html.escape（chat_service 防線）；本層唯讀不再處理。
    """
    tcol = "COALESCE(time, received_at)"
    clauses, params = [], []
    if exercise_id is NULL_SCOPE:
        clauses.append("exercise_id IS NULL")
    elif exercise_id is not None:
        clauses.append("exercise_id = ?")
        params.append(exercise_id)
    if since is not None:
        clauses.append(f"{tcol} >= ?")
        params.append(since)
    if until is not None:
        clauses.append(f"{tcol} <= ?")
        params.append(until)
    sql = f'SELECT id, sender_uid, callsign, message, "group", lat, lon, {tcol} AS t FROM chats'  # nosec B608 — tcol 常數
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY t DESC, id DESC LIMIT ?"  # newest-first；id 當同秒 stable tiebreak
    params.append(cap)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": r["id"],
            "sender_uid": r["sender_uid"],
            "callsign": r["callsign"],
            "message": r["message"],
            "group": r["group"],
            "lat": r["lat"],
            "lon": r["lon"],
            "t": r["t"],
        }
        for r in rows
    ]
