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
RBAC 由 `auth/role_enum.py` 中央 gate（POST 預設 COMMAND_ROLES 見 #146；例外：share #180 與
chat #463 放寬 WRITE_ROLES，清單見 role_enum 的 /api/tak/ 各 case）。
"""

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile

from auth.role_enum import visible_factions_for_session
from core import config
from core.input_safety import validate_no_unsafe_strings
from repositories import cop_entity_repo
from repositories._helpers import audit
from schemas.tak import ChatSendIn, CoTEventIn, DownlinkCommandIn, TakConnectionToggleIn
from services import (
    cop_service,
    tak_attachments,
    tak_downlink,
    tak_files,
    tak_missions,
    tak_photo_push,
    tak_resync,
    tak_runtime,
    tak_service,
)
from services.exercise_service import current_exercise_id
from services.tak_files import TakFilestoreError
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

    RBAC = WRITE_ROLES（#463 公測回報放寬：role_enum 窄洞 POST /api/tak/chat → WRITE_ROLES，
    operator 一線操作訊息；observer 仍唯讀）。
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

    # 出向 DM **不在 ICS 層做 faction 檢查**——operator 比照 commander（改動前即如此，兩者同為
    # 藍方 faction，發 DM 一律無 ICS 檢查）。為何不加（防下個 reviewer 誤補漏洞檢查）：TAK server
    # 靠 <marti><dest callsign> 投遞（tak_downlink.build_geochat_cot），而 callsign「顯示不可信、
    # 同 uid 可多陣營」（memory tak-faction-group-identifier）＝**非可靠 faction 鍵**，ICS 層依它擋
    # 不牢（crafted callsign 可繞 by-uid 檢查）。出向跨陣營隔離的**權威邊界＝TAK #344 server group
    # 隔離**。（對比 share 端點有 faction 檢查：它 by-uid 操作既有 entity、檢查與操作同鍵才成立；
    # DM 是 by-callsign 投遞，鍵不同，同型檢查放這裡是解耦的假防線。）
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

    「共享閘」= 顯式、受 audit 的分享動作（非自動轟全網）。RBAC = WRITE_ROLES（#180 part 3
    放寬：operator 前線感知職責；role_enum 窄洞）。放 tak.py 不放 cop.py：與
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


@router.post("/mission-sync")
async def mission_sync_from_tak(request: Request):
    """#506 M1：消費配置的 Data Sync mission（`TAK_MISSION_FEEDS`）→ 拉 `/cot` 補進 COP。

    對稱 resync，只換來源（mission `/cot` 取代 `/cot/sa`）：對每個配置的 mission 逐筆走既有
    ingest 縫（只 upsert 不刪，faction/場域歸屬沿用）。mission `/cot` **只含真正投遞進 mission
    的 CoT**（reality-check #506 實證），故消費的是現場真加進 feed 的 marker。

    RBAC = COMMAND_ROLES（role_enum 中央 gate：POST /api/tak/* → COMMAND_ROLES）。讀身分用
    TAK_MARTI_READ_CERT。audit-first。未配置（缺 URL/讀 cert 或無 TAK_MISSION_FEEDS）→ 422；
    HTTP/解析失敗 → 503（單一 mission 失敗已在 service 層吞為該 mission 的 error，不整批 503）。
    """
    operator = request.state.session["username"]
    if not tak_missions.mission_sync_enabled():
        raise HTTPException(422, "Mission 消費未配置（缺 TAK_MARTI_URL/讀 cert 或 TAK_MISSION_FEEDS）")
    audit(
        operator,
        None,
        "TAK_MISSION_SYNC",
        "tak",
        "missions",
        {"feeds": tak_missions.configured_mission_names()},
        exercise_id=current_exercise_id(),
    )
    try:
        summary = await tak_missions.run_mission_sync()
    except (TakRestError, CoTParseError) as e:
        raise HTTPException(503, f"Mission 同步失敗：{e}") from e
    return {"ok": True, **summary}


# ── #503 上行（TAK→ICS）：file store 照片下載 ──────────────────────────────
#
# 地點型雙向照片的「上行」半：現場（ATAK/iTAK）把照片存進 TAK Enterprise Sync file store，
# ICS 主動拉下來在 COP 顯示。讀側協定已驗（tak_files；下載/搜尋/metadata），上傳（下行）另計。
#
# faction 安全設計（兩層，見 #507「兩面一軸」）：
# ① TAK group 層——READ cert（ics-marti-read）由 #507 **宣告並註冊進 blue/red/neutral 全群**
#    （services/tak_identity SoT + 面板一鍵對帳），故對 TAK 確為「跨群落全見」（指揮站權威豁免）。
#    ⚠ 此非天生成立：read cert 若漏群只讀得到 public（現場 blue 檔 404）——故 #507 有開機漂移警示。
# ② ICS 施加層——**不做全庫通搜**（會跨陣營洩圖）。列表端點綁在「本 session 看得到的 cop_entity」
#    下 → 繼承該 marker 的 faction 可見度（看不到的 entity 一律 404、不洩存在）。
# 即：TAK 讓 ICS 讀全部、faction 邊界由 **ICS 這側 per-session 施加**（非靠 TAK group 對 ICS 分艙）。

_IMAGE_MIME_PREFIX = "image/"
_CONTENT_DISPOSITION_SAFE = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._- "


def _safe_filename(name: str) -> str:
    """清出可安全放進 Content-Disposition 的檔名（擋 header 注入：CR/LF/引號/非白名單字元）。

    檔名源自 TAK metadata（可能被現場裝置左右）→ 只留白名單字元，空則回退 'file'。"""
    cleaned = "".join(c for c in (name or "") if c in _CONTENT_DISPOSITION_SAFE).strip()
    return cleaned[:120] or "file"


def _file_meta_public(meta: dict) -> dict:
    """把 TAK Resource metadata 收斂為前端要的安全子集（不外拋 groups/creatorUid 等內部欄位）。"""
    return {
        "hash": tak_files.extract_hash(meta),
        "name": tak_files.extract_name(meta),
        "mimeType": tak_files.extract_mimetype(meta),
        "size": meta.get("size"),
        "submissionTime": meta.get("submissionTime"),
        "submitter": meta.get("submitter"),
    }


@router.get("/files/for-entity/{uid}")
async def list_entity_files(uid: str, request: Request):
    """#503 上行：列出某 COP marker 在 TAK file store 的附件照片（地點型）。

    RBAC = READ_ROLES（中央 gate：GET /api/tak/* → READ_ROLES）。
    faction 安全：只回「本 session 看得到的 entity」之附件（繼承 marker 可見度）；
    看不到 / 不存在的 entity 一律 404（不洩存在）。未配置 file store → 422。

    附件↔marker 連結：以 marker uid 打 `/sync/search?uid=`（TAK Enterprise Sync 慣例）。
    只回 image/* 附件（照片）。回 hash 供前端向下方下載端點取圖。
    """
    if not tak_files.filestore_enabled():
        raise HTTPException(422, "TAK file store 未配置（缺 TAK_MARTI_URL 或讀 cert）")
    entity = cop_entity_repo.get_cop_entity(uid)
    vis = visible_factions_for_session(request.state.session)
    # 不存在，或存在但本 session 不可見（faction）→ 同一 404，不區分（不洩存在）
    if not entity or (vis is not None and entity.get("faction") not in vis):
        raise HTTPException(404, "找不到該 COP 物件")
    client = tak_files._build_read_client()
    try:
        results = await tak_files.search_files(client, uid=uid)
    except TakRestError as e:
        raise HTTPException(503, f"TAK file store 查詢失敗：{e}") from e
    finally:
        await client.close()
    images = [
        _file_meta_public(m)
        for m in results
        if (tak_files.extract_mimetype(m) or "").startswith(_IMAGE_MIME_PREFIX) and tak_files.extract_hash(m)
    ]
    # #509：併入 ICS 本地附件（現場分享/廣播照片：b-f-t-r → mission-package 解出的照片，掛此 marker）。
    # 形狀對齊 Enterprise Sync 結果（{hash,name,mimeType}）→ 前端 tak_photos 不用改即一併顯示。
    images.extend(tak_attachments.list_local_attachments(uid))
    return {"uid": uid, "files": images}


@router.get("/files/{file_hash}")
async def download_tak_file(file_hash: str, request: Request):
    """#503 上行：下載 TAK file store 單檔（照片）→ 原樣回傳 bytes（inline 顯示）。

    RBAC = READ_ROLES。hash 先驗 SHA-256（擋 path 注入/遍歷）。每次下載強制 audit（可追）。
    未配置 → 422；hash 非法 → 400；查無 → 404；TAK HTTP 失敗 → 503。

    faction 邊界（slice-1）：hash 為不可猜 SHA-256、僅經上方 faction-gated 列表取得，
    故實務上使用者只會拿到看得到之 marker 的附件 hash。**嚴格 per-hash faction gating**
    （反查附件所屬 marker 是否可見）待附件↔marker 連結真機確認後補（follow-up #503）。
    """
    if not tak_files.is_valid_hash(file_hash):
        raise HTTPException(400, "非法檔案 hash（須 SHA-256 hex）")
    operator = request.state.session["username"]
    # #509：ICS 本地附件（現場照片，存 DATA_DIR/tak_attachments/<sha256>）——優先本地、免打 TAK；
    # faction 守門於所掛 marker 可見度（反查附件所屬 marker 不可見 → 404 不洩存在）。
    local_owner = tak_attachments.local_attachment_owner(file_hash)
    if local_owner is not None:
        ent = cop_entity_repo.get_cop_entity(local_owner)
        vis = visible_factions_for_session(request.state.session)
        if ent and vis is not None and ent.get("faction") not in vis:
            raise HTTPException(404, "找不到該檔案")
        got = tak_attachments.read_local_attachment(file_hash)
        if got is None:
            raise HTTPException(404, "查無此附件")
        data, content_type = got
        audit(
            operator,
            None,
            "TAK_FILE_DOWNLOAD",
            "tak",
            file_hash,
            {"bytes": len(data), "mime": content_type, "source": "local"},
            exercise_id=current_exercise_id(),
        )
        return Response(
            content=data, media_type=content_type, headers={"Content-Disposition": 'inline; filename="attachment"'}
        )
    # ── 否則走 TAK Enterprise Sync（#503）──
    if not tak_files.filestore_enabled():
        raise HTTPException(422, "TAK file store 未配置（缺 TAK_MARTI_URL 或讀 cert）")
    client = tak_files._build_read_client()
    try:
        meta = await tak_files.get_file_metadata(client, file_hash)
        data = await tak_files.download_file(client, file_hash)
    except TakFilestoreError as e:
        raise HTTPException(400, str(e)) from e
    except TakRestError as e:
        raise HTTPException(503, f"TAK file store 下載失敗：{e}") from e
    finally:
        await client.close()
    if data is None:
        raise HTTPException(404, "TAK file store 查無此檔")
    # per-hash faction gating（#506 review 修，取代原 slice-1 缺口）：檔案 Resource 的 uid＝所掛
    # marker（上傳/ATAK 附件皆設此）→ 只准看得到該 marker 的 session 下載；反查 entity 不可見 →
    # 404 不洩存在。entity 不在 ICS（無從判 faction）→ 退回 login+hash 不可猜守門（serve）。
    file_marker = (meta or {}).get("uid") or (meta or {}).get("UID")
    if file_marker:
        ent = cop_entity_repo.get_cop_entity(file_marker)
        vis = visible_factions_for_session(request.state.session)
        if ent and vis is not None and ent.get("faction") not in vis:
            raise HTTPException(404, "找不到該檔案")
    content_type = (tak_files.extract_mimetype(meta) if meta else None) or "application/octet-stream"
    filename = _safe_filename(tak_files.extract_name(meta) if meta else "")
    audit(
        operator,
        None,
        "TAK_FILE_DOWNLOAD",
        "tak",
        file_hash,
        {"bytes": len(data), "mime": content_type},
        exercise_id=current_exercise_id(),
    )
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.delete("/files/{file_hash}")
async def delete_tak_attachment(
    file_hash: str,
    request: Request,
    purge_server: bool = Query(False),
):
    """#518：刪除一張本地附件（現場照片）。方向感知、L1（本地一律）+ L2（`purge_server` 連 server 檔庫）。

    RBAC = COMMAND_ROLES（破壞性指揮動作，中央 gate：DELETE /api/tak/files/* → COMMAND_ROLES）。
    faction 安全：反查附件所屬 marker，只准刪**本 session 看得到的 marker** 之附件（看不到/不存在 → 404
    不洩存在）。只有 **ICS 本地附件** 可經此刪（純 Enterprise Sync 檔非本端點對象）→ 查無本地附件 → 404。
    audit-first：破壞性動作（含不可逆 L2）前先落稽核意圖（marker + 是否請求 purge_server）；server 刪除
    結果（成敗）走 log（tak_attachments.delete_attachment 內 log.info/warning）。

    誠實紅線：任何刪除都不會讓照片從現場持有裝置本機消失（TAK client 硬限制）——前端須明講。
    回：delete_attachment 的狀態 dict（local_deleted / server_purged / server_error / direction）。
    """
    if not tak_files.is_valid_hash(file_hash):
        raise HTTPException(400, "非法檔案 hash（須 SHA-256 hex）")
    operator = request.state.session["username"]
    # faction 守門：反查附件所屬 marker，須存在且本 session 可見（繼承 download/for-entity 同守門）。
    owner = tak_attachments.local_attachment_owner(file_hash)
    if owner is None:
        raise HTTPException(404, "查無此本地附件")
    ent = cop_entity_repo.get_cop_entity(owner)
    vis = visible_factions_for_session(request.state.session)
    if not ent or (vis is not None and ent.get("faction") not in vis):
        raise HTTPException(404, "找不到該附件")
    # audit-first：破壞性動作（含不可逆 L2 server 清）前先落稽核意圖，杜絕「刪了但崩在 audit 前 → 無軌」。
    audit(
        operator,
        None,
        "TAK_ATTACHMENT_DELETE",
        "tak",
        file_hash,
        {"marker_uid": owner, "purge_server_requested": purge_server},
        exercise_id=current_exercise_id(),
    )
    # 只刪 caller 已守門的這個 marker 的連結（不波及同 sha 掛在其他不可見 marker 的連結）。
    result = await tak_attachments.delete_attachment(
        file_hash, marker_uid=owner, actor=operator, purge_server=purge_server
    )
    if not result.get("local_deleted"):
        raise HTTPException(404, "查無此本地附件")
    return result


_MAX_UPLOAD_BYTES = 32 * 1024 * 1024  # 上傳單檔上限（照片有界，防灌爆）
# 圖片 magic bytes（JPEG/PNG/GIF/WebP）——不信 client 的 Content-Type（可偽造），驗真實內容，
# 防指揮層把非圖片內容夾帶進 TAK file store 標成 image/*（security-review）。
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a")


def _looks_like_image(content: bytes) -> bool:
    """檢查檔案內容前綴是否為已知圖片格式（magic bytes）。WebP＝RIFF….WEBP 另判。"""
    if any(content.startswith(sig) for sig in _IMAGE_MAGIC):
        return True
    return content[:4] == b"RIFF" and content[8:12] == b"WEBP"  # WebP


@router.post("/files/upload")
async def upload_tak_file(request: Request, marker_uid: str = Form(...), file: UploadFile = File(...)):
    """#506 M3 下行：上傳一張照片到 TAK Enterprise Sync、掛在某 COP marker 上（地點型情境注入，
    如 TTX 白隊推現場照給藍隊）。上傳後檔案 Resource 的 uid=marker_uid → `GET /files/for-entity/
    {marker_uid}` 即看得到（與上行同一條顯示路徑）。

    RBAC = COMMAND_ROLES（推內容到 TAK＝指揮動作，中央 gate：POST /api/tak/* → COMMAND_ROLES）。
    faction 安全：只准掛在**本 session 看得到的 marker**（繼承 for-entity 同守門；看不到/不存在
    → 404 不洩存在）。audit-first。未配置寫 cert → 422；非圖片 → 415；空/過大 → 400/413；
    上傳格式錯/回應異常 → 502；HTTP 失敗 → 503。
    """
    if not tak_files.filestore_write_enabled():
        raise HTTPException(422, "TAK file store 上傳未配置（缺 TAK_MARTI_URL 或寫 cert）")
    # faction 安全：marker 必須存在且本 session 可見（繼承 for-entity 守門）
    entity = cop_entity_repo.get_cop_entity(marker_uid)
    vis = visible_factions_for_session(request.state.session)
    if not entity or (vis is not None and entity.get("faction") not in vis):
        raise HTTPException(404, "找不到該 COP 物件")
    mimetype = file.content_type or ""
    if not mimetype.startswith("image/"):
        raise HTTPException(415, "只接受 image/* 照片")
    content = await file.read()
    if not content:
        raise HTTPException(400, "空檔案")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案過大（上限 {_MAX_UPLOAD_BYTES} bytes）")
    # 不信 client Content-Type（可偽造）——驗真實 magic bytes（security-review）
    if not _looks_like_image(content):
        raise HTTPException(415, "檔案內容非圖片（magic bytes 不符）")
    operator = request.state.session["username"]
    audit(
        operator,
        None,
        "TAK_FILE_UPLOAD",
        "tak",
        marker_uid,
        {"name": file.filename, "mime": mimetype, "bytes": len(content)},
        exercise_id=current_exercise_id(),
    )
    client = tak_files._build_write_client()
    try:
        result = await tak_files.upload_file(
            client,
            content=content,
            filename=file.filename or "photo.jpg",
            mimetype=mimetype,
            creator_uid="ICS-CMD",
            marker_uid=marker_uid,
        )
    except TakFilestoreError as e:
        raise HTTPException(502, f"TAK 上傳回應異常：{e}") from e
    except TakRestError as e:
        raise HTTPException(503, f"TAK file store 上傳失敗：{e}") from e
    finally:
        await client.close()
    return {"ok": True, "hash": tak_files.extract_hash(result), "marker_uid": marker_uid}


@router.post("/downlink/photo")
async def downlink_photo(
    request: Request,
    marker_uid: str = Form(...),
    file: UploadFile = File(...),
    dest: str = Form(""),
):
    """#509-P3 下行「顯示到現場」：把照片（掛在 marker M 上）推到現場 TAK client 的地圖。

    與 `/files/upload`（只上 Enterprise Sync 供 ICS 側 #503 面板顯示）不同——本端點**打包
    mission-package zip + 廣播/點對點 `b-f-t-r`**，讓現場 client 建 marker + 掛照片（真機定讞：
    推裸 jpg 不吃、要推 zip）。甲（M=現有 marker）/乙（M=ICS 新建 marker）同一端點。

    `dest`：逗號分隔的 client callsign → 點對點只送這些；空 → 廣播（送 ICS 所在 group）。
    RBAC=COMMAND_ROLES（中央 gate：POST /api/tak/*）。faction：只准推**本 session 看得到的 marker**
    （繼承 for-entity 守門，看不到/不存在 → 404）。audit-first。非圖片 → 415；空/過大 → 400/413；
    未配置寫 cert / 缺座標 → 422；上傳或送出失敗 → 502。
    """
    # faction 安全：marker 必須存在且本 session 可見（同 /files/upload 守門）。
    entity = cop_entity_repo.get_cop_entity(marker_uid)
    vis = visible_factions_for_session(request.state.session)
    if not entity or (vis is not None and entity.get("faction") not in vis):
        raise HTTPException(404, "找不到該 COP 物件")
    mimetype = file.content_type or ""
    if not mimetype.startswith("image/"):
        raise HTTPException(415, "只接受 image/* 照片")
    content = await file.read()
    if not content:
        raise HTTPException(400, "空檔案")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案過大（上限 {_MAX_UPLOAD_BYTES} bytes）")
    if not _looks_like_image(content):  # 不信 client Content-Type，驗真實 magic bytes
        raise HTTPException(415, "檔案內容非圖片（magic bytes 不符）")
    # dest：逗號分隔 callsign（點對點）；空 → 廣播。內容白名單擋注入（callsign 進 CoT XML）。
    dest_callsigns = [c.strip() for c in (dest or "").split(",") if c.strip()] or None
    if dest_callsigns:
        validate_no_unsafe_strings(dest_callsigns)
    operator = request.state.session["username"]
    audit(
        operator,
        None,
        "TAK_PHOTO_PUSH",
        "tak",
        marker_uid,
        {"name": file.filename, "mime": mimetype, "bytes": len(content), "dest": dest_callsigns or "broadcast"},
        exercise_id=current_exercise_id(),
    )
    try:
        result = await tak_photo_push.push_photo_to_marker(
            marker_uid=marker_uid,
            photo_bytes=content,
            filename=file.filename or "photo.jpg",
            mimetype=mimetype,
            dest_callsigns=dest_callsigns,
        )
    except tak_photo_push.PhotoPushError as e:
        raise HTTPException(422, f"下行推送失敗：{e}") from e
    except (TakFilestoreError, TakRestError) as e:
        raise HTTPException(502, f"TAK 上傳/送出失敗：{e}") from e
    except (RuntimeError, OSError, TimeoutError) as e:
        # send_cot 傳輸層失敗（未配置 / :8089 連不上 / 逾時）→ 502，不外洩 500 stack（zip 可能已上傳）。
        raise HTTPException(502, f"TAK b-f-t-r 送出失敗：{e}") from e
    return {"ok": True, **result}


@router.get("/clients")
def list_tak_clients(request: Request):
    """線上 TAK client 名單（callsign+uid）——供 #509-P3 下行「點對點」挑收件人。

    **來源＝COP（`cop_entities`, source='tak'），與「隊伍」面板／地圖同源**：所見即可選。
    原走 Marti `/clientEndPoints` 會漏**憑證直連**的現場 client（truststore-trusted 非 managed
    user → 不在名單）且與 COP 視圖不一致（memory `tak-marti-authz-model`）；改自 COP 取，消除
    「隊伍看得到卻選不到」的落差，並免依賴時好時壞的 Marti REST 讀路徑。

    可見性沿用地圖（#472 跨場共享池、只看 faction；無 active 場時容 NULL faction）；只列在線
    （stale 未過 / archived）；排除 ICS 自身 presence beacon（推給自己無意義）。
    RBAC=READ_ROLES（中央 gate GET）。回 {clients:[{callsign, uid}]}，uid 去重、callsign 排序。
    """
    from services.tak_downlink import ICS_SELF_UID

    vf = visible_factions_for_session(request.state.session)
    entities = cop_entity_repo.list_cop_entities(
        source="tak",
        exercise_id=None,  # 同地圖：跨場共享池，不按場過濾
        include_stale=False,  # 只列在線（honor archive/stale）
        visible_factions=vf,
        allow_null_faction=current_exercise_id() is None,  # 同 cop._allow_unclassified_tak
    )
    seen: set[str] = set()
    clients: list[dict] = []
    for e in entities:
        uid, cs = e.get("uid"), e.get("callsign")
        if not uid or not cs or uid in seen or uid == ICS_SELF_UID:
            continue
        seen.add(uid)
        clients.append({"callsign": cs, "uid": uid})
    clients.sort(key=lambda c: c["callsign"].lower())
    return {"clients": clients}


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
