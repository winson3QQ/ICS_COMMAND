"""
schemas/chat.py — GeoChat（CoT b-t-f）通聯記錄（P2-07，#129）

對齊 ICS-214 Unit Log。b-t-f 不進 cop_entities（作戰圖主表），由 chat_service 分流落此。
message 應已由 chat_service `html.escape`（XSS 後端防線，規格紅線）。
"""

from pydantic import BaseModel, ConfigDict, Field


class ChatIn(BaseModel):
    """寫入 chats 表的通聯記錄。"""

    model_config = ConfigDict(extra="forbid")

    sender_uid: str = Field(..., min_length=1)  # CoT event uid（發話端）
    callsign: str | None = None  # 發話呼號
    message: str = ""  # 通聯內文（**已 escape**）
    group: str | None = None  # 聊天室 / 群組（__chat chatroom）
    lat: float | None = None
    lon: float | None = None
    time: str | None = None  # event 時間 ISO 8601
    exercise_id: int | None = None  # 綁 active 場（無 → NULL）
    faction: str | None = None  # #343：發話端陣營 blue/red/neutral；NULL=未分類→fail-closed
