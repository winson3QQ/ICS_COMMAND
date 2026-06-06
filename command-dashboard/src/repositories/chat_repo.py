"""repositories/chat_repo.py — chats 表存取（P2-07，#129）。"""

from core.database import get_conn

from ._helpers import row_to_dict
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
