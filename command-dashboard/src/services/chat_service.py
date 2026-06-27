# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
services/chat_service.py — GeoChat（CoT b-t-f）通聯落地（P2-07，#129）

`cop_service.ingest_cot_event` 偵測 type `b-t-f` → 委派本服務，**不進 cop_entities**
（作戰圖主表）。message 在此 `html.escape`（XSS 後端防線，規格紅線）。讀 API
（build_chat_feed）+ 右欄通聯面板 #213 b1；即時 WS 推播 #213 b2；出向 compose 留 #216。
"""

import html

from repositories import chat_repo, client_faction_repo
from schemas.chat import ChatIn
from schemas.tak import CoTEventIn
from services.exercise_service import current_exercise_id
from services.realtime_hub import cop_hub


def _geochat_client_key(uid: str | None) -> str | None:
    """#343：從 GeoChat uid 取產生它的裝置 uid（= faction client_key）。

    GeoChat uid 格式 `GeoChat.<裝置uid>.<聊天室>.<訊息GUID>`（真機實證 ATAK/iTAK），第二段即裝置。
    非此格式（無法歸屬）→ None → faction 解 NULL → fail-closed。
    """
    parts = (uid or "").split(".")
    if len(parts) >= 2 and parts[0] == "GeoChat" and parts[1]:
        return parts[1]
    return None


def _feed_item(row: dict) -> dict:
    """chats row → 與 GET /api/chat 一致的 feed 形狀（t = time or received_at）。
    WS 即時推播與 poll 走同一前端渲染路徑，故形狀必須與 `chat_repo.list_chats` 對齊。
    `time` 由 CoTEventIn 保證 min_length≥1（非空），故 `or` 與 SQL `COALESCE(time,received_at)`
    在此等價。形狀 SoT 待後續抽 shared helper（與 list_chats 共用，避免加欄位時雙改漏一處）。"""
    return {
        "id": row["id"],
        "sender_uid": row["sender_uid"],
        "callsign": row["callsign"],
        "message": row["message"],
        "group": row["group"],
        "lat": row["lat"],
        "lon": row["lon"],
        "t": row.get("time") or row.get("received_at"),
    }


async def ingest_chat(event: CoTEventIn) -> dict | None:
    """b-t-f GeoChat → chats 表（綁當前 active 場）+ 即時 WS 廣播（#213 b2）。

    空訊息（收條/ack b-t-f）→ 回 None 跳過、不寫不廣播（#248）。

    message ← <remarks>（parse 層已抽 `event.remarks`），**存前 html.escape**；
    callsign ← `event.callsign` 或 <__chat senderCallsign>；group ← <__chat chatroom>；
    exercise_id ← `current_exercise_id()`（active→int / 無→NULL，與 normalize_cot 一致）。

    b2：寫入後 `cop_hub.broadcast({op:chat,...})` 推給 `/ws/updates` 訂閱者——省去前端 7s
    poll 的延遲。exercise scope 沿用 `cop_hub` 既有 `wants` 過濾（與 entity 同模型，
    observer/operator 鎖當前場）；broadcast 對死連線容錯不拋（同 entity 廣播路徑）。
    """
    detail = event.detail or {}
    chat = detail.get("__chat")
    chat = chat if isinstance(chat, dict) else {}
    # #248：GeoChat 收條/ack —— type b-t-f 但 <remarks> 空（uid=訊息 GUID、無 __chat group）。
    # 空訊息 chat 無意義，且會在前端「直接」頻道冒空白列（Windows dogfood 實證）→ 不寫、不廣播。
    if not (event.remarks or "").strip():
        return None
    # 冪等：GeoChat 的 uid 含訊息 GUID（唯一）。TAK 重訂閱 / `/cot/sa` resync 會重播持久化 GeoChat →
    # 同 uid 重來不再入庫、不再廣播（否則每次重連多一筆同訊息，dogfood 實證 42 筆「Enemy founded」）。
    if chat_repo.chat_exists(event.uid):
        return None
    ex_id = current_exercise_id()
    # #343：解發話端裝置 → 陣營（與 cop_entity 同 server-authoritative 分類）。NULL=未分類→fail-closed。
    client_key = _geochat_client_key(event.uid)
    # #216：ICS 自身出向 GeoChat 經 server 回送（ICS 也訂閱 :8089）→ sender uid = 站台身分
    # ICS_SELF_UID，不在 client_faction 名冊 → 原解 NULL → fail-closed 對藍方隱藏，連發話的
    # commander 自己都看不到自己發的訊息。ICS 出向＝指揮部(藍方)發話 → 歸 blue，對齊
    # 「manual/command 自建恆對藍方可見」doctrine。
    from services.tak_downlink import ICS_SELF_UID  # lazy：避免 import 期載 tak_downlink→pytak

    if client_key == ICS_SELF_UID:
        faction = "blue"
    elif client_key:
        faction = client_faction_repo.get_faction(ex_id, client_key)
    else:
        faction = None
    record = ChatIn(
        sender_uid=event.uid,
        callsign=event.callsign or chat.get("senderCallsign"),
        message=html.escape(event.remarks or ""),  # XSS 後端防線
        group=chat.get("chatroom"),  # 只取聊天室名；groupOwner 是布林旗標（"false"/"true"）非群組名（review #131）
        lat=event.lat,
        lon=event.lon,
        time=event.time,
        exercise_id=ex_id,
        faction=faction,
    )
    row = chat_repo.insert_chat(record)
    # #343：帶 source='tak'+faction → _Conn._faction_ok 過濾紅軍通聯不漏給藍方 WS。
    await cop_hub.broadcast(
        {"op": "chat", "chat": _feed_item(row)},
        exercise_id=row.get("exercise_id"),
        source="tak",
        faction=row.get("faction"),
    )
    return row


def build_chat_feed(
    exercise_id,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
    visible_factions: frozenset[str] | None = None,
) -> dict:
    """通聯單一流投影（#213 b1）—— GET /api/chat 的回應主體。

    取最新 `limit` 筆，**chronological 升序**（oldest→newest）供右欄單流由上往下顯示。
    以 limit+1 探測截斷（取到 >limit 筆才算截、剛好 limit 筆不誤報，對齊 build_timeline）：
    `truncated=true` → 呼叫端縮 from/to 時間窗重查（不靜默截斷）。
    `group`（聊天室）原樣回傳，前端泛型生成 room 標籤/filter chips（不寫死房間清單）。
    回 {meta: {count, truncated}, chats: [{id, sender_uid, callsign, message, group, lat, lon, t}]}。
    """
    probe = limit + 1
    rows = chat_repo.list_chats(exercise_id, since=since, until=until, cap=probe, visible_factions=visible_factions)
    truncated = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()  # newest-first → 顯示用升序
    return {"meta": {"count": len(rows), "truncated": truncated}, "chats": rows}
