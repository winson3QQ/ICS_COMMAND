"""
services/chat_service.py — GeoChat（CoT b-t-f）通聯落地（P2-07，#129）

`cop_service.ingest_cot_event` 偵測 type `b-t-f` → 委派本服務，**不進 cop_entities**
（作戰圖主表）。message 在此 `html.escape`（XSS 後端防線，規格紅線）。讀 API
（build_chat_feed）+ 右欄通聯面板 #213 b1；出向 compose 留 #216。
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


def build_chat_feed(exercise_id, since: str | None = None, until: str | None = None, limit: int = 200) -> dict:
    """通聯單一流投影（#213 b1）—— GET /api/chat 的回應主體。

    取最新 `limit` 筆，**chronological 升序**（oldest→newest）供右欄單流由上往下顯示。
    以 limit+1 探測截斷（取到 >limit 筆才算截、剛好 limit 筆不誤報，對齊 build_timeline）：
    `truncated=true` → 呼叫端縮 from/to 時間窗重查（不靜默截斷）。
    `group`（聊天室）原樣回傳，前端泛型生成 room 標籤/filter chips（不寫死房間清單）。
    回 {meta: {count, truncated}, chats: [{id, sender_uid, callsign, message, group, lat, lon, t}]}。
    """
    probe = limit + 1
    rows = chat_repo.list_chats(exercise_id, since=since, until=until, cap=probe)  # newest-first
    truncated = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()  # newest-first → 顯示用升序
    return {"meta": {"count": len(rows), "truncated": truncated}, "chats": rows}
