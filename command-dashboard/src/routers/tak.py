# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tak.py — TAK（Team Awareness Kit）CoT REST ingest endpoint（P2-03 / #107）

協議：CoT（Cursor on Target），對齊 MIL-STD-2525。欄位見 `schemas/tak.py` 的 CoTEventIn。

ingestion 兩條路徑（共用 #105 的 `cop_service.ingest_cot_event` 接縫）：
- **:8089 串流訂閱** → `tak_service.subscribe()`（P2-02 W2，由 main.py lifespan 跑背景 task）
- **REST/federation push** → 本檔 `POST /api/tak/events`（本 issue 升真）

下行（P2-13 A，issue #176）：
- **下達指令** → `POST /api/tak/downlink`（COMMAND_ROLES + 強制 audit）→ `tak_downlink.send_cot`
  寫 CoT 進 :8089 → server 廣播現場 ATAK。live broadcast；持久/可靠刪除是 P2-14 未解問題。

協調契約（#105/#107）：本檔**只呼叫** `ingest_cot_event`，不定義（接縫是 cop_service 的）；
RBAC 由 `auth/role_enum.py` 中央 gate（POST=COMMAND_ROLES，見 #146）。
"""

import uuid

from fastapi import APIRouter, HTTPException, Request

from auth.role_enum import visible_factions_for_session
from core import config
from core.input_safety import validate_no_unsafe_strings
from repositories import cop_entity_repo
from repositories._helpers import audit
from schemas.tak import ChatSendIn, CoTEventIn, DownlinkCommandIn, TakConnectionToggleIn
from services import cop_service, tak_downlink, tak_resync, tak_runtime, tak_service
from services.exercise_service import current_exercise_id
from services.tak_rest_client import TakRestError
from services.tak_service import CoTParseError

router = APIRouter(prefix="/api/tak", tags=["TAK"])


@router.post("/events")
async def receive_cot_event(body: CoTEventIn):
    """接收 REST/federation push 的 CoT 事件 → 走 #105 接縫落 COP（persist + 廣播）。

    回傳真實結果：
    - 落地（create/update）→ `status="ingested"` + uid + version_clock
    - 被守門丟棄（out-of-order / 重送 / stale / CAS 重試耗盡）→ `status="skipped"`
    """
    result = await cop_service.ingest_cot_event(body)
    if result is None:
        return {"ok": True, "uid": body.uid, "status": "skipped"}
    return {
        "ok": True,
        "uid": result["uid"],
        "version_clock": result["version_clock"],
        "status": "ingested",
    }


@router.post("/downlink")
async def push_downlink(body: DownlinkCommandIn, request: Request):
    """P2-13(A) 下達指令：建 CoT → 寫 :8089 → server 廣播現場 ATAK。

    RBAC = COMMAND_ROLES（role_enum 中央 gate：POST /api/tak/* → COMMAND_ROLES）。
    機制見 `services/tak_downlink`（issue #176 reality check：streaming-write 廣播，
    不需 mission/admin）。**live broadcast only** —— 無持久/可靠刪除（P2-14 未解）。

    Audit 紀律（DoD：不得 best-effort）：**audit-first** —— 先寫稽核再送 CoT。
    audit 失敗 → 例外上拋（指令不送，無未稽核之下達）；送出失敗 → 503（稽核已留下達意圖）。
    """
    # #222：出向受開關閘控——TAK 連線停用時不下達指令。caller-side gate，對齊 cop.py
    # _resync_tak_if_shared（P2-24 #164 已 gate 重推路徑，本處補上當時漏掉的直接端點）。
    if not tak_runtime.effective_enabled():
        raise HTTPException(409, "TAK 連線已停用，無法下達指令（請先於系統設定啟用 TAK 連線）")
    operator = request.state.session["username"]
    # 縱深防護：內容白名單已在 schema validator，這裡再過一次 sink 防護（對齊 cop.py #24）。
    validate_no_unsafe_strings(body.model_dump())

    uid = body.uid or f"ICS-CMD-{uuid.uuid4().hex[:12]}"
    cot = tak_downlink.build_command_cot(
        uid=uid,
        type_=body.type,
        lat=body.lat,
        lon=body.lon,
        hae=body.hae,
        callsign=body.callsign,
        remarks=body.remarks,
        stale_minutes=body.stale_minutes,
    )

    # audit-first：先落稽核（失敗即拋 → 指令不送），再送 CoT。
    audit(
        operator,
        None,
        "TAK_DOWNLINK",
        "tak",
        uid,
        {
            "type": body.type,
            "lat": body.lat,
            "lon": body.lon,
            "callsign": body.callsign,
            "planned": body.planned,
            "stale_minutes": body.stale_minutes,
        },
        exercise_id=current_exercise_id(),
    )

    try:
        await tak_downlink.send_cot(cot)
    except Exception as e:  # noqa: BLE001 — 連線/配置失敗統一回 503（稽核已記下達意圖）
        raise HTTPException(503, f"TAK 下行送出失敗：{e}") from e

    return {"ok": True, "uid": uid, "status": "sent", "planned": body.planned}


@router.post("/chat")
async def send_geochat(body: ChatSendIn, request: Request):
    """#216 出向 GeoChat：指揮部對現場 TAK 發文字通聯（對稱入向 P2-07 chat_service）。

    RBAC = COMMAND_ROLES（role_enum 中央 gate：POST /api/tak/* → COMMAND_ROLES）。
    **發話者身分 server 端決定**（session display_name/username），不信 client 宣告——
    對齊「不信 client 時鐘/身分」doctrine。audit-first（指揮對外發話須留痕；只記路由 +
    長度 metadata，**不**記訊息內文——內文走 chats 表的 PII 保留政策 #348-F10，不雙重保留）。
    內容白名單 `validate_no_unsafe_strings`（縱深防護）+ builder XML escape。

    回送 ICS 自身：server 廣播給所有訂閱者（含 ICS 自己的 :8089 訂閱）→ 經入向 ingest_chat
    落 chats 表顯示。uid 含 msg_id GUID（唯一）→ `chat_repo.chat_exists` 冪等去重，不重複入庫。
    """
    # #222：出向受開關閘控——TAK 連線停用時不發通聯。
    if not tak_runtime.effective_enabled():
        raise HTTPException(409, "TAK 連線已停用，無法發送通聯（請先於系統設定啟用 TAK 連線）")
    operator = request.state.session["username"]
    sender_callsign = request.state.session.get("display_name") or operator
    # 縱深防護：內容白名單（schema 已驗，這裡再過一次 sink 防護，對齊 downlink/cop）。
    validate_no_unsafe_strings(body.model_dump())

    msg_id = uuid.uuid4().hex
    # DM 顯示名：優先收件呼號、退收件 uid；非 DM → 聊天室名（防空白退全體）。
    if body.recipient_uid:
        chatroom = body.recipient_callsign or body.recipient_uid
    else:
        chatroom = body.chatroom or tak_downlink.ALL_CHAT_ROOMS
    cot = tak_downlink.build_geochat_cot(
        sender_callsign=sender_callsign,
        message=body.message,
        msg_id=msg_id,
        chatroom=chatroom,
        recipient_uid=body.recipient_uid,
        lat=body.lat,
        lon=body.lon,
    )

    room_seg = body.recipient_uid or chatroom
    uid = f"GeoChat.{tak_downlink.ICS_SELF_UID}.{room_seg}.{msg_id}"
    # audit-first（DoD 不得 best-effort）：先稽核再送；送出失敗 → 503，稽核已留發話意圖。
    audit(
        operator,
        None,
        "TAK_CHAT_SEND",
        "tak",
        uid,
        {
            "chatroom": chatroom,
            "recipient_uid": body.recipient_uid,
            "msg_len": len(body.message),  # 不記內文（PII 走 chats 保留政策），只記長度
        },
        exercise_id=current_exercise_id(),
    )
    try:
        await tak_downlink.send_cot(cot)
    except Exception as e:  # noqa: BLE001 — 連線/配置失敗統一回 503（稽核已記發話意圖）
        raise HTTPException(503, f"TAK 通聯送出失敗：{e}") from e

    return {"ok": True, "uid": uid, "status": "sent", "chatroom": chatroom}


@router.post("/share/{uid}")
async def share_entity_to_tak(uid: str, request: Request):
    """P2-30 part 2（#180）：把**既有 COP 感知標記**推到 TAK（共享閘）。

    「共享閘」= 指揮層顯式、受 audit 的分享動作（非自動轟全網）。RBAC = COMMAND_ROLES
    （role_enum 中央 gate：POST /api/tak/* → COMMAND_ROLES）。放 tak.py 不放 cop.py：與
    P2-27（另一 session 改 cop.py/events）零檔案重疊。對 cop_entities **唯讀**（get），不改 schema。

    流程：查 entity → `entity_to_cot`（點/幾何分流）→ **audit-first** → send_cot。
    """
    # #222：出向受開關閘控——TAK 連線停用時不廣播（gate 在查 entity 前，停用時連查都省）。
    if not tak_runtime.effective_enabled():
        raise HTTPException(409, "TAK 連線已停用，無法廣播到 TAK（請先於系統設定啟用 TAK 連線）")
    operator = request.state.session["username"]
    entity = cop_entity_repo.get_cop_entity(uid)
    if entity is None:
        raise HTTPException(404, f"COP entity 不存在：{uid}")
    # #343 紅藍隔離：藍方不可分享/操作其看不到的 tak entity（與 cop.get_entity 同檢查）——
    # 否則 share 成了存在性 oracle + 讓藍方對紅軍 entity 越權動作（即使回應不含 entity 資料）。
    # 只 source='tak' 受限（manual/command 自建恆可分享）；sysadmin / 開關關 → vf=None 不過濾。
    vf = visible_factions_for_session(request.state.session) if config.FACTION_ISOLATION_ENABLED else None
    if vf is not None and entity.get("source") == "tak" and entity.get("faction") not in vf:
        raise HTTPException(404, f"COP entity 不存在：{uid}")

    # entity → CoT 失敗（畸形幾何：kind=polygon/route 但 vertices 不足/越界）→ 乾淨 422，
    # 不讓 ValueError 變未審計的 500（cop POST 不驗 kind↔vertices 一致性，此 entity 可能存在）。
    try:
        cot = tak_downlink.entity_to_cot(entity)
    except ValueError as e:
        raise HTTPException(422, f"entity 無法序列化為 CoT（幾何無效）：{e}") from e
    # audit-first（DoD 不得 best-effort）：先稽核再送；送出失敗 → 503，稽核已留分享意圖。
    audit(
        operator,
        None,
        "COP_SHARE_TAK",
        "cop_entities",
        uid,
        {
            "source": entity.get("source"),
            "type": entity.get("type"),
            "kind": (entity.get("attributes") or {}).get("kind"),
        },
        exercise_id=current_exercise_id(),
    )
    try:
        await tak_downlink.send_cot(cot)
    except Exception as e:  # noqa: BLE001 — 連線/配置失敗統一 503（稽核已記分享意圖）
        raise HTTPException(503, f"分享到 TAK 失敗：{e}") from e
    # P2-30 part 3：標記已廣播 → 之後 move/note 編輯（cop PUT）即時重推（不需再手動廣播）。
    # 刪除不推 TAK（streaming 做不到 → P2-14，見 cop.delete_entity / tak_downlink 註）。
    # 非-CAS 單語句更新不 bump version（不影響前端樂觀鎖）。
    cop_entity_repo.mark_shared_tak(uid, True)
    return {"ok": True, "uid": uid, "status": "shared"}


@router.post("/resync")
async def resync_from_tak(request: Request):
    """P2-14 (C)（#194/#173）：Marti 權威 resync —— 拉 `/cot/sa` 快照補 streaming 漏掉的靜態標記。

    :8089 串流不對重連者重播既有 marker → ICS 重啟/斷線後 COP 漏標記。本端點主動拉 server
    權威快照逐筆補進 cop_entities（**只 upsert 不刪**，見 services/tak_resync 設計註）。

    RBAC = COMMAND_ROLES（role_enum 中央 gate：POST /api/tak/* → COMMAND_ROLES）。讀身分用
    TAK_MARTI_READ_CERT（truststore 信任即通）。audit-first：先稽核 resync 意圖再執行。
    未配置 Marti URL/讀 cert → 422（功能停用）；HTTP 拉取失敗 → 503。
    """
    operator = request.state.session["username"]
    if not tak_resync.resync_enabled():
        raise HTTPException(422, "Marti resync 未配置（缺 TAK_MARTI_URL 或讀 cert）")
    audit(
        operator,
        None,
        "TAK_RESYNC",
        "tak",
        "cot/sa",
        {"lookback_s": config.TAK_RESYNC_LOOKBACK_S},
        exercise_id=current_exercise_id(),
    )
    try:
        summary = await tak_resync.run_resync()
    except (TakRestError, CoTParseError) as e:
        # HTTP 層失敗（TakRestError）或 server 回畸形/超大 `<events>`（CoTParseError）→ 乾淨 503
        # （非未處理 500）。稽核已在前面 audit-first 留下 resync 意圖。
        raise HTTPException(503, f"Marti resync 失敗：{e}") from e
    return {"ok": True, **summary}


@router.get("/status")
def tak_status():
    """TAK 整合啟用狀態 + **連線健康**（P2-23 #163）。:8089 訂閱由 lifespan 依 TAK_ENABLED 啟動。
    RBAC = READ_ROLES（role_enum 中央 gate）。前端 header 連線指示燈用：
      enabled=false → 灰；enabled 但 connected=false → 紅；connected 但 last_cot 太舊 → 黃；
      connected + 近期有 CoT → 綠。age 由 server 算（避免信任 client 時鐘）。"""
    health = tak_service.get_tak_status()
    last = health.get("last_cot_at")
    age_s = None
    if last:
        from datetime import UTC, datetime

        try:
            dt = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            age_s = int((datetime.now(UTC) - dt).total_seconds())
        except ValueError:
            age_s = None
    # P2-24（#164）：enabled 改讀 runtime 有效狀態（持久選擇優先、回退 TAK_ENABLED env），
    # 燈號才會跟著開關走，而非只反映啟動時的 env。
    enabled = tak_runtime.effective_enabled()
    return {
        "enabled": enabled,
        "cot_url": config.TAK_COT_URL if enabled else None,
        "protocol": "CoT (Cursor on Target)",
        "standard": "MIL-STD-2525",
        # P2-23 連線健康
        "connected": health["connected"],
        "last_cot_at": last,
        "last_cot_age_s": age_s,
        # P2-24 前端尾（#164）：唯讀診斷欄位，讓 admin 開關燈號能誠實區分
        #   running   = 訂閱 task 是否真的在跑（!running 而 enabled → 啟動失敗，非「重連中」）
        #   configured= 部署層連線參數是否齊備（!configured 而 enabled → 部署未備妥，非 admin 在 UI 修）
        "running": tak_runtime.is_running(),
        "configured": tak_runtime.is_configured(),
    }


@router.post("/connection")
async def set_tak_connection(body: TakConnectionToggleIn, request: Request):
    """P2-24（#164）：runtime 啟用/停用 :8089 訂閱，**不重啟 process**；選擇持久化、重啟後維持。

    RBAC = **SYSADMIN_ONLY**（role_enum 中央 gate：path `/api/tak/connection`）——關 TAK＝整 COP
    態勢全斷、blast radius 最大，比照演習刪除鎖 admin；commander 不可（斷線屬基礎設施控制、
    非 per-incident 指揮決策）。

    Audit 紀律（DoD：不得 best-effort）：**audit-first** —— 先記 `TAK_CONNECTION_TOGGLE`
    再實際啟停。啟動失敗不 raise（TAK 選配，tak_runtime.start 內部 log），故回傳 `running`
    讓呼叫端看實際結果（enabled=true 但 running=false → config/連線有問題）。
    """
    operator = request.state.session["username"]
    audit(
        operator,
        None,
        "TAK_CONNECTION_TOGGLE",
        "config",
        "tak.connection_enabled",
        {"enabled": body.enabled},
    )
    tak_runtime.set_persisted_enabled(body.enabled)  # 持久化（重啟後維持）
    if body.enabled:
        await tak_runtime.start()
    else:
        await tak_runtime.stop()
    return {"ok": True, "enabled": body.enabled, "running": tak_runtime.is_running()}
