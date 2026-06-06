"""
services/chat_service.py — GeoChat（CoT b-t-f）通聯落地（P2-07，#129）

`cop_service.ingest_cot_event` 偵測 type `b-t-f` → 委派本服務，**不進 cop_entities**
（作戰圖主表）。message 在此 `html.escape`（XSS 後端防線，規格紅線）。讀 API + 通聯
面板 UI 留 P2-12。
"""

import html

from repositories import chat_repo
from schemas.chat import ChatIn
from schemas.tak import CoTEventIn
from services.exercise_service import current_exercise_id


def ingest_chat(event: CoTEventIn) -> dict:
    """b-t-f GeoChat → chats 表（綁當前 active 場）。

    message ← <remarks>（parse 層已抽 `event.remarks`），**存前 html.escape**；
    callsign ← `event.callsign` 或 <__chat senderCallsign>；group ← <__chat chatroom>；
    exercise_id ← `current_exercise_id()`（active→int / 無→NULL，與 normalize_cot 一致）。
    """
    detail = event.detail or {}
    chat = detail.get("__chat")
    chat = chat if isinstance(chat, dict) else {}
    record = ChatIn(
        sender_uid=event.uid,
        callsign=event.callsign or chat.get("senderCallsign"),
        message=html.escape(event.remarks or ""),  # XSS 後端防線
        group=chat.get("chatroom"),  # 只取聊天室名；groupOwner 是布林旗標（"false"/"true"）非群組名（review #131）
        lat=event.lat,
        lon=event.lon,
        time=event.time,
        exercise_id=current_exercise_id(),
    )
    return chat_repo.insert_chat(record)
