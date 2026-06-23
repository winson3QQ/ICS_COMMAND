"""
routers/cop.py — COP entity per-entity CRUD + 樂觀鎖 HTTP 層（issue #29 PR-B）

承接 PR-A 的 repo 層（update_cop_entity_cas / delete_cop_entity）。本 router 把
per-entity 樂觀鎖暴露成 HTTP，對映：

  GET    /api/cop/entities            列出（預設過濾 stale）
  GET    /api/cop/entities/{uid}      取單顆 + ETag header（= version_clock）
  POST   /api/cop/entities            手動建立（source 強制 manual）→ 201 + ETag
  PUT    /api/cop/entities/{uid}      樂觀鎖更新（需 If-Match）→ 200 / 409 / 428 / 404
  DELETE /api/cop/entities/{uid}      TAK soft-delete（需 If-Match）→ 200 / 409 / 428 / 404

ETag / If-Match 語意（對齊 issue #29 設計）：
- 每顆 entity 的 version_clock 即 ETag。GET 把它放進 ETag header。
- PUT / DELETE 必須帶 `If-Match: <version_clock>`（或 `W/"<n>"`）。
  - 缺 → 428 Precondition Required（強制 client 走樂觀鎖，不准盲寫）。
  - 版本對不上 → 409 Conflict + body 帶 server 現值（server_entity）讓 client merge，
    **不採 412**：我們要回傳現值供合併，而非單純「前置失敗」。
  - 從根本根除 commit 58bb5d4 的整檔 last-write-wins clobber。

RBAC 由 auth_middleware + allowed_roles_for() 集中把關（GET → READ_ROLES、
POST/PUT/DELETE → WRITE_ROLES，皆走預設分支，不需在 role_enum 加 case）。
actor（updated_by）取自 middleware 驗過後放在 request.state.session 的 username。

XSS：所有寫入 body 先過 core.input_safety.validate_no_unsafe_strings（issue #24 sink 防護）。
"""

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from auth.role_enum import COMMAND_ROLES, READ_ROLES, is_role_allowed, visible_factions_for_session
from auth.service import check_session
from core.config import FACTION_ISOLATION_ENABLED
from core.input_safety import validate_no_unsafe_strings
from repositories import cop_entity_repo, event_marker_repo, exercise_repo
from repositories._helpers import NULL_SCOPE, audit
from schemas.cop import CoPEntity
from services import tak_downlink, tak_runtime
from services.exercise_service import current_exercise_id, resolve_scope
from services.realtime_hub import cop_hub

router = APIRouter(prefix="/api/cop", tags=["COP"])
log = logging.getLogger(__name__)

_MAX_BODY_BYTES = 256 * 1024  # 256 KB，與 map_config 一致
_STALE_DEFAULT_HOURS = 24  # 手動建立未帶 stale 時的預設存活時間

# PUT patch 不接受的欄位（router 層政策，疊在 repo 的 _CAS_PROTECTED_COLS 之上）：
#   source — 本 endpoint 只服務 manual 編輯，不得改成 tak / pi-node 偽造來源
# 其餘 uid / version_clock / updated_by / updated_at / received_at 由 repo raise ValueError
# （此處列出是為了在 router 層先回明確 422，不必等 repo 拋例外）。
_PUT_FORBIDDEN_FIELDS = frozenset({"source", "uid", "version_clock", "updated_by", "updated_at", "received_at"})

# #146（TAK-B-rev）：來源所有權 + 情境守門 —— 哪些既有 entity 可經 PUT/DELETE 手動編輯。
# - manual / command（指揮部自建）：永遠可編輯。
# - tak / pi-node / waveink（外部現場鏡像）：僅**演習(TTX)模式**可手動編輯（道具/合成）；
#   **實戰模式鎖死** —— 保護真實前線位置不被造假 / 誤刪。下令請建 source='command' 新物件
#   （P2-13），不覆寫真實 TAK 單位。情境（演習/實戰）由 server 的 active exercise type 決定
#   （server 權威，不信 client 宣告；對齊 TAK-C）。無 active exercise = 實戰池 → 鎖死。
# 反向對應 cop_service.ingest_cot_event 的 TAK→本地 守門（#145），補齊本地→TAK 反向。
_DASHBOARD_OWNED_SOURCES = frozenset({"manual", "command"})


def _require_editable_source(existing: dict) -> None:
    src = (existing or {}).get("source")
    if src in _DASHBOARD_OWNED_SOURCES:
        return
    active = exercise_repo.get_active_exercise()
    if active is not None and active.get("type") == "ttx":
        return
    raise HTTPException(
        403,
        f"實戰模式下 {src} 來源物件唯讀（外部現場鏡像，禁手動覆寫/刪除；下令請建指揮部物件）",
    )


# P1-16（security review HIGH-1）：節點(zone)/設施(infra)的「建立 / 刪除」限指揮層。
# 前端 canUseRealModeControls() 只是 UI 遮罩，非安全邊界；此處為後端真實授權。
# 其餘 kind（event / route / polygon / 拖事件位置）維持 WRITE_ROLES（operator 日常可寫），
# 故不整路徑升級、只針對這兩種 kind 加 gate，避免誤傷 operator 的合法 cop 寫入。
_COMMAND_ONLY_KINDS = frozenset({"zone", "infra"})


def _require_command_for_kind(request: Request, kind) -> None:
    """kind ∈ {zone, infra} 的建立/刪除限 COMMAND_ROLES（sysadmin/commander），否則 403。"""
    if kind in _COMMAND_ONLY_KINDS and not is_role_allowed(request.state.session, COMMAND_ROLES):
        raise HTTPException(403, "節點 / 設施的建立與刪除限指揮層（sysadmin / commander）")


def _etag(version_clock: int) -> str:
    """weak ETag = entity 的 version_clock。"""
    return f'W/"{version_clock}"'


def _parse_if_match(request: Request) -> int:
    """從 If-Match header 取 expected version_clock。缺 → 428；格式錯 → 400。"""
    raw = request.headers.get("If-Match")
    if not raw:
        raise HTTPException(428, "需要 If-Match header（帶你手上 entity 的 version_clock）")
    cleaned = raw.strip().removeprefix("W/").strip().strip('"')
    try:
        return int(cleaned)
    except ValueError as e:
        raise HTTPException(400, f"If-Match 格式錯誤，需為整數 version_clock：{raw!r}") from e


async def _read_json_body(request: Request) -> dict:
    """讀 + size guard + JSON 解析 + 必為 object。"""
    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(413, f"body 過大（{len(raw)} > {_MAX_BODY_BYTES}）")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"無效 JSON：{e}") from e
    if not isinstance(body, dict):
        raise HTTPException(400, "body 必須是 JSON object")
    return body


def _actor(request: Request) -> str:
    # auth_middleware 已驗 session 並放進 request.state.session
    return request.state.session["username"]


def _conflict_response(server_entity: dict) -> JSONResponse:
    """409 + server 現值，讓 client 直接 merge（不必再 GET 一次）。"""
    return JSONResponse(
        status_code=409,
        content={"detail": "version conflict", "server_entity": server_entity},
        headers={"ETag": _etag(server_entity["version_clock"])},
    )


async def _broadcast(op: str, entity: dict) -> None:
    """寫操作成功後 push 給 WS 訂閱者（PR-D）。op ∈ create / update / delete。
    依 entity 的 exercise_id filter；broadcast 內部已對死連線容錯，不會拋。"""
    await cop_hub.broadcast(
        {
            "op": op,
            "uid": entity["uid"],
            "version_clock": entity["version_clock"],
            "entity": entity,
        },
        exercise_id=entity.get("exercise_id"),
        source=entity.get("source"),  # #343：faction 過濾（manual/command 非 tak → 恆送藍方）
        faction=entity.get("faction"),
    )


async def _resync_tak_if_shared(entity: dict) -> None:
    """P2-30 part 3（#180）：廣播後即時同步 —— 已 `attributes.shared_tak` 的 entity 被 move/note
    編輯（cop PUT）→ 重推 entity CoT（現場端位置/說明即時更新，不需再手動廣播）。
    best-effort：TAK 未啟用 / 送出失敗只記 warning，**不擋 cop 操作**（ICS 編輯不該因 TAK 斷線而失敗）。
    ICS 端 audit 已記 move（cop_entity_updated）；TAK 推送為其傳輸 side-effect，不另稽核。
    **刪除不在此**：刪除已廣播標記到 TAK 經實證 streaming 做不到（server 持久層不認 t-x-d-d/stale）→
    可靠刪除 = Mission/DataSync = P2-14（見 tak_downlink 註 + strategy §4b）。"""
    # P2-24（#164）：改讀 runtime 有效狀態（持久選擇優先、回退 env），與 :8089 訂閱同源——
    # 否則 runtime 關掉 TAK 後 move 已分享標記仍會推、或純 toggle 開啟時反而不推（與開關不一致）。
    if not tak_runtime.effective_enabled():
        return
    if not (entity.get("attributes") or {}).get("shared_tak"):
        return
    try:
        await tak_downlink.send_cot(tak_downlink.entity_to_cot(entity))
    except Exception as e:  # noqa: BLE001 — 同步 best-effort，不擋 cop 操作
        log.warning("[cop] 廣播後即時同步 TAK 更新失敗（best-effort）uid=%s：%s", entity.get("uid"), e)


def _audit_cop(action: str, actor: str, entity: dict, extra: dict | None = None) -> None:
    """COP 操作寫 audit（issue #93）。best-effort —— 記帳失敗**不得**影響 cop 寫入 / 即時同步
    （否則 logging 故障會反過來擋住地圖更新）。exercise_id 走 Model B（audit() 自動戳 active）。"""
    try:
        attrs = entity.get("attributes") or {}
        detail = {
            "kind": attrs.get("kind"),
            "label": entity.get("callsign"),
            "lat": entity.get("lat"),
            "lon": entity.get("lon"),
        }
        # #338：polygon/route 區域 → 在 audit detail 記**整包 attributes 快照**，供 AAR 時間精確
        # 重現（畫/改/刪 @ T 折疊）。**不 cherry-pick 個別欄**：移動 label 只改 attributes.label_anchor、
        # 未動 vertices/lat/lon（dogfood 實證）→ 挑欄會漏標籤位置。整包存才完整、且耐未來新欄。
        # audit **不可回填** → 不在演習前記，中途被刪/改的區域狀態永久遺失。
        # 單位/點 kind 不記（位置由 cop_entity_tracks 承載；且其高頻 update 會把 audit 撐爆）。
        if attrs.get("kind") in ("polygon", "route"):
            detail["attributes"] = attrs
        if extra:
            detail.update(extra)
        audit(actor, None, action, "cop_entities", entity["uid"], detail)
    except Exception:
        log.warning("[cop] audit 失敗（best-effort，不影響寫入）", exc_info=True)


# ── read ─────────────────────────────────────────────────────────────────────


def _visible_factions(request: Request) -> frozenset[str] | None:
    """#343：開關開時回此 session 可見 faction（sysadmin→None 全見）；開關關→None（完全不過濾）。"""
    if not FACTION_ISOLATION_ENABLED:
        return None
    return visible_factions_for_session(request.state.session)


@router.get("/entities")
def list_entities(
    request: Request,
    source: str | None = None,
    exercise_id: int | None = None,
    include_stale: bool = False,
    include_standing: bool = False,
    limit: int = 500,
):
    """列出 COP entity（預設過濾 stale）。前線 client 啟動 / WS 重連時全量 resync 用。
    P1-14：預設只回當前 active 場；commander 顯式帶 exercise_id 才看歷史（resolve_scope 守門）。
    #267：include_standing（限 COMMAND）→ active 場再疊加 NULL 常駐 entity，與 WS `?standing=1`
    對等（否則 resync 會把 WS 推來的常駐單位刪掉＝鬼影）。"""
    scope = resolve_scope(request.state.session, exercise_id)
    vf = _visible_factions(request)  # #343：紅藍過濾（None=全見/開關關）
    entities = cop_entity_repo.list_cop_entities(
        source=source, exercise_id=scope, include_stale=include_stale, limit=limit, visible_factions=vf
    )
    # int scope＝有 active 場；疊加常駐（NULL_SCOPE）。已是 NULL_SCOPE（無 active）者本就看得到常駐、不疊。
    # SECURITY：限 COMMAND_ROLES（對齊 WS gate）；非指揮層帶 include_standing 也忽略。
    if include_standing and isinstance(scope, int) and is_role_allowed(request.state.session, COMMAND_ROLES):
        standing = cop_entity_repo.list_cop_entities(
            source=source, exercise_id=NULL_SCOPE, include_stale=include_stale, limit=limit, visible_factions=vf
        )
        seen = {e["uid"] for e in entities}
        entities = entities + [e for e in standing if e["uid"] not in seen]
    return {"entities": entities}


@router.get("/entities/{uid}")
def get_entity(uid: str, request: Request, response: Response):
    ent = cop_entity_repo.get_cop_entity(uid)
    if ent is None:
        raise HTTPException(404, f"entity 不存在：{uid}")
    # P1-14：非指揮層只能取當前 scope 內的 entity（防 by-uid 跨場讀取）。
    # 指揮層（sysadmin/commander）可取任意 uid，對齊 list endpoint 的 ?exercise_id override。
    # 不在 scope → 404（不洩漏存在性，與 None 同一回應）。
    if not is_role_allowed(request.state.session, COMMAND_ROLES):
        scope = resolve_scope(request.state.session, None)  # active id 或 NULL_SCOPE（實戰池）
        ent_ex = ent.get("exercise_id")
        in_scope = (ent_ex is None) if scope is NULL_SCOPE else (ent_ex == scope)
        if not in_scope:
            raise HTTPException(404, f"entity 不存在：{uid}")
    # #343：faction 隔離——tak 來源且不在可見 faction（含 NULL fail-closed）→ 404（不洩漏存在性）。
    # 非 tak（manual/command 自建）不受限（#146 所有權）。sysadmin / 開關關 → vf=None 不過濾。
    vf = _visible_factions(request)
    if vf is not None and ent.get("source") == "tak" and ent.get("faction") not in vf:
        raise HTTPException(404, f"entity 不存在：{uid}")
    response.headers["ETag"] = _etag(ent["version_clock"])
    return ent


@router.get("/squads")
def list_squads(request: Request, exercise_id: int | None = None):
    """按 team_color 聚合該場 COP entity → 小隊態勢（total/online/offline/avg_battery/centroid）。

    P2-06d（issue #128）。RBAC：走 allowed_roles_for GET 預設分支 → READ_ROLES（observer 可讀）。
    P1-14：exercise_id 由 resolve_scope 守門（不直接信 query param；歷史場限 COMMAND_ROLES）。
    team_color IS NULL 的 entity 聚成「未分隊」組（team_color=null，排列首）。
    #343：套 faction 過濾（否則藍方經聚合 centroid/兵力推得紅軍位置）。
    """
    return {
        "squads": cop_entity_repo.aggregate_squads(
            exercise_id=resolve_scope(request.state.session, exercise_id),
            visible_factions=_visible_factions(request),
        )
    }


# ── create ─────────────────────────────────────────────────────────────────


@router.post("/entities", status_code=201)
async def create_entity(request: Request, response: Response):
    """手動建立 entity。source 一律強制 manual（不信任 client 來源宣告）；
    uid / 時間戳 / how 未帶則 server 兜底，方便前端只送 {type, lat, lon, callsign}。
    """
    body = await _read_json_body(request)
    validate_no_unsafe_strings(body, label="cop_entity")

    now = datetime.now(UTC)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    body["source"] = "manual"  # 強制：manual endpoint 不得偽造 tak / pi-node
    # P1-14：exercise_id 由 server 端 active 場決定（不信任 client；同 source 強制 doctrine）。
    # 無 active → NULL＝實戰/未分場。**這一行同時是 P1-16 placement 圖釘綁定 active 場的點**。
    body["exercise_id"] = current_exercise_id()
    # P1-16：節點/設施建立限指揮層（後端授權邊界；event/route/polygon 不受限）
    _require_command_for_kind(request, (body.get("attributes") or {}).get("kind"))
    body.setdefault("uid", f"manual:{uuid.uuid4()}")
    body.setdefault("time", now_iso)
    body.setdefault("start", now_iso)
    body.setdefault(
        "stale",
        (now + timedelta(hours=_STALE_DEFAULT_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    body.setdefault("how", "h-e")  # human estimated
    body.pop("version_clock", None)  # 新建一律從 DB default 1
    body.pop("received_at", None)  # DB default 自動填
    # P2-33b（#196）：event↔marker 連結走 first-class top-level `event_id`（junction 權威），
    # 從 body pop 掉——不入 CoPEntity（extra=forbid）也不入 DB attributes（glue 退役）。
    # back-compat：舊 / 快取 client 仍可能把 event_id 放 attributes → fallback 取之。
    _event_id = body.pop("event_id", None) or (body.get("attributes") or {}).get("event_id")

    try:
        entity = CoPEntity(**body)
    except ValidationError as e:
        raise HTTPException(422, f"entity 欄位驗證失敗：{e.errors()}") from e

    try:
        created = cop_entity_repo.insert_cop_entity(entity)
    except sqlite3.IntegrityError as e:
        raise HTTPException(409, f"uid 已存在：{entity.uid}") from e

    # P2-27/P2-33b：event 圖釘 → **先**建 event↔marker junction 關聯（權威 FK）**再廣播**，
    # 讓 created/廣播帶頂層 junction `event_id`（前端 copEntityToEventZone 改吃頂層、不讀 attributes glue）。
    # best-effort：關聯失敗只 warn 不擋圖釘（圖照樣上 COP）；攔 sqlite3.Error（IntegrityError=event 不存在 +
    # OperationalError=DB locked）——只攔 IntegrityError 則高併發 link 撞 locked 會噴 500、client 重試又撞 409。
    _kind = (created.get("attributes") or {}).get("kind")
    # 甲-1b（#240 刀0）：事件圖釘 kind 'event'→'sighting' 降級 → 認兩者（新建走 sighting）。
    if _kind in ("event", "sighting") and _event_id:
        # glue 退役後 event 圖釘的 event-ness **唯一**靠此 junction link 成功（前端只認頂層 event_id）。
        # 故對 OperationalError（DB locked，瞬時；get_conn 已有 5s busy timeout 兜底）**重試一次**，
        # 保「event 圖釘必有 event_id」不變式；IntegrityError（event 不存在）不重試＝正解（orphan）。
        # 仍 best-effort：終究失敗只 warn、不擋圖釘上 COP（event_id 留 None）。
        for _attempt in (1, 2):
            try:
                event_marker_repo.link_marker(_event_id, created["uid"], "primary")
                created["event_id"] = _event_id  # 廣播/回應帶上（insert 回的 created 在 link 前 event_id=None）
                break
            except sqlite3.IntegrityError as e:
                log.warning(
                    "[cop] event↔marker 關聯失敗（event 不存在→orphan）event_id=%s uid=%s：%s",
                    _event_id,
                    created["uid"],
                    e,
                )
                break
            except sqlite3.OperationalError as e:
                if _attempt == 2:
                    log.warning(
                        "[cop] event↔marker 關聯失敗（DB locked，重試後仍失敗）event_id=%s uid=%s：%s",
                        _event_id,
                        created["uid"],
                        e,
                    )

    await _broadcast("create", created)
    # #93：COP 建立 audit。**跳過事件圖釘**（event_created 已涵蓋，避免同動作雙記）；
    # zone/route/polygon/infra 等才是真正未被 audit 的地圖物件。
    # 甲-1b（#240）：事件圖釘 kind 'event'→'sighting' → 兩者都跳過 audit。
    if _kind not in ("event", "sighting"):
        _audit_cop("cop_entity_created", _actor(request), created)
    response.headers["ETag"] = _etag(created["version_clock"])
    return created


# ── update / delete（樂觀鎖）─────────────────────────────────────────────────


@router.put("/entities/{uid}")
async def update_entity(uid: str, request: Request, response: Response):
    expected = _parse_if_match(request)
    body = await _read_json_body(request)
    validate_no_unsafe_strings(body, label="cop_entity")

    forbidden = set(body) & _PUT_FORBIDDEN_FIELDS
    if forbidden:
        raise HTTPException(422, f"這些欄位不可經 PUT 修改：{sorted(forbidden)}")
    if not body:
        raise HTTPException(422, "patch 不可為空")

    # #146：來源所有權 + 情境守門（外部來源 entity 實戰模式唯讀）。existing 為 None 時
    # 不擋，交由下方 CAS 回 notfound→404（語意一致，不洩漏「不存在 vs 唯讀」差異）。
    existing = cop_entity_repo.get_cop_entity(uid)
    if existing is not None:
        _require_editable_source(existing)
        # #257：`shared_tak` 是 **server 管的旗標**（只有 `mark_shared_tak`／share 端點以 json_set 設、
        # **不 broadcast** → 前端 entity 無此值）。client PUT **整包覆寫** attributes（move 改 vertices /
        # label drag 改 label_anchor）會把它洗掉 → 斷「編輯後即時重推 TAK」(_resync_tak_if_shared)。
        # 故 PUT 一律以 existing 的值蓋掉 client 帶的 shared_tak：① existing 有 → 保留（修我的移動 +
        # pre-existing label drag 兩處 clobber）；② existing 無 → 移除（client 不得經 PUT 自設「已分享」、
        # 繞過 share 端點的 COP_SHARE_TAK audit + send_cot；分享只能走 /api/tak/share，security hardening）。
        if isinstance(body.get("attributes"), dict):
            _shared = (existing.get("attributes") or {}).get("shared_tak")
            if _shared:
                body["attributes"]["shared_tak"] = _shared
            else:
                body["attributes"].pop("shared_tak", None)

    try:
        result = cop_entity_repo.update_cop_entity_cas(uid, expected, body, actor=_actor(request))
    except ValueError as e:  # repo 欄位白名單 / 受保護欄位
        raise HTTPException(422, str(e)) from e

    if result["status"] == "notfound":
        raise HTTPException(404, f"entity 不存在：{uid}")
    if result["status"] == "conflict":
        return _conflict_response(result["entity"])
    await _broadcast("update", result["entity"])
    # P2-30 part 3：已廣播的 entity 被 move/note 編輯 → 即時重推 TAK（不需再手動廣播）。
    await _resync_tak_if_shared(result["entity"])
    # #93：COP 更新 audit（**全 kind 含 event**）—— 捕捉 QRF/事件等「移動」軌跡（位置變更只走
    # 此路徑，events 表不 audit location）。fields 記本次改了哪些欄；移動路徑＝updated 列序列。
    _audit_cop("cop_entity_updated", _actor(request), result["entity"], {"fields": sorted(body)})
    response.headers["ETag"] = _etag(result["entity"]["version_clock"])
    return result["entity"]


@router.delete("/entities/{uid}")
async def delete_entity(uid: str, request: Request, response: Response):
    """TAK soft-delete：標 stale=now + bump version_clock（不 hard delete）。"""
    expected = _parse_if_match(request)
    # P1-16：刪除節點/設施限指揮層（先取 entity 看 kind；不存在則交由下方 notfound 處理）
    _ent = cop_entity_repo.get_cop_entity(uid)
    if _ent is not None:
        _require_command_for_kind(request, (_ent.get("attributes") or {}).get("kind"))
        _require_editable_source(_ent)  # #146：實戰模式 TAK/外部來源禁手動刪除
    result = cop_entity_repo.delete_cop_entity(uid, expected, actor=_actor(request))

    if result["status"] == "notfound":
        raise HTTPException(404, f"entity 不存在：{uid}")
    if result["status"] == "conflict":
        return _conflict_response(result["entity"])
    # delete 也廣播（op=delete）：訂閱端據此把 entity 從畫面移除（TAK 語意）
    await _broadcast("delete", result["entity"])
    # P2-30 part 3：**刪除不推 TAK** —— 已廣播標記的可靠刪除 streaming 做不到（server 持久層不認
    # t-x-d-d/stale，真機+活 server 實證）→ Mission/DataSync = P2-14。刪除目前僅 ICS 端生效。
    # #93：COP 刪除 audit（全 kind 含 event；事件圖釘移除/結案也留痕）。
    _audit_cop("cop_entity_deleted", _actor(request), result["entity"])
    # 與 PUT-ok / 409 一致：成功也回 ETag（soft-delete 後的新 version_clock）
    response.headers["ETag"] = _etag(result["entity"]["version_clock"])
    return {
        "status": "deleted",
        "uid": uid,
        "version_clock": result["entity"]["version_clock"],
    }


# ── WebSocket：per-entity 即時推播（PR-D）────────────────────────────────────

# WS close code（4xxx = application-defined）
_WS_UNAUTHORIZED = 4401
_WS_BAD_REQUEST = 4400

# token 走 Sec-WebSocket-Protocol 而非 query param —— 避免 session token 進
# uvicorn / nginx access-log 的 request URL（security-review PR-D Vuln 4）。
# client offer ["ics-cop-v1", "ics.session.<token>"]；server 只 echo 常數協定（不含 token）。
_WS_SUBPROTOCOL = "ics-cop-v1"
_WS_TOKEN_PREFIX = "ics.session."


def _ws_token(websocket: WebSocket) -> str | None:
    """從 offered subprotocols 取 session token（不讀 query param）。"""
    for proto in websocket.scope.get("subprotocols", []):
        if proto.startswith(_WS_TOKEN_PREFIX):
            return proto[len(_WS_TOKEN_PREFIX) :]
    return None


@router.websocket("/ws/updates")
async def cop_ws_updates(websocket: WebSocket):
    """訂閱 COP entity 變更。server→client 單向 push `{op, uid, version_clock, entity}`。

    auth：http middleware 不跑 WS scope，故此處顯式驗 session —— 且傳入 websocket
    讓 check_session 套用與 HTTP 相同的 IP / UA binding（不可降級成 unbound）。token
    走 Sec-WebSocket-Protocol（見 _ws_token）。role 須在 READ_ROLES（與 GET 同政策）。
    exercise filter 走 query param `?exercise_id=<N?>`（非機密）。
    斷線 / 重連策略：client 重連後應 GET /api/cop/entities 全量 resync（version_clock
    merge idempotent），不在 WS 內補發歷史。
    """
    token = _ws_token(websocket)
    if not token:
        await websocket.close(code=_WS_UNAUTHORIZED)
        return
    # request=websocket → 與 HTTP 相同的 session IP/UA binding（Vuln 1 修正）
    sess, failure = check_session(token, request=websocket, touch=False)
    if failure or sess is None:
        await websocket.close(code=_WS_UNAUTHORIZED)
        return
    # role 須可讀（與 allowed_roles_for GET → READ_ROLES 一致；擋 unknown/降級 role）
    if not is_role_allowed(sess, READ_ROLES):
        await websocket.close(code=_WS_UNAUTHORIZED)
        return

    raw_ex = websocket.query_params.get("exercise_id")
    try:
        requested_ex = int(raw_ex) if raw_ex not in (None, "") else None
    except ValueError:
        await websocket.close(code=_WS_BAD_REQUEST)
        return
    # P1-14 安全收緊：原本直接信任 client query param → 任何 READ_ROLE 可訂閱任意場（越權）。
    # 改走 resolve_scope：預設訂當前 active；顯式指定歷史場限 COMMAND_ROLES，否則強制回 active。
    exercise_id = resolve_scope(sess, requested_ex)
    # #265：未顯式帶 ?exercise_id（dashboard 常態）＝跟隨 active；切換演習時由 cop_hub 就地
    # rescope，不必重連。顯式 pin 歷史場（指揮層）的連線不跟隨。
    follows_active = requested_ex is None
    # #267 常駐層疊看：client 帶 ?standing=1 → active 場連線也收 NULL 常駐 entity。
    # SECURITY：**限 COMMAND_ROLES**（對齊 resolve_scope 看歷史的權限模型）。非指揮層即使帶
    # standing=1 也強制 False —— 不讓低權限在演習中窺看常駐/real-world 單位。仍不跨演習（見 wants）。
    include_standing = websocket.query_params.get("standing") == "1" and is_role_allowed(sess, COMMAND_ROLES)
    # #343：handshake 時依角色定可見 faction（sysadmin→None 全見；藍軍→{blue,neutral}）。
    # 開關關 → None（完全不過濾）。整段 WS 生命週期固定（角色不會中途變），與 REST 同映射。
    visible_factions = visible_factions_for_session(sess) if FACTION_ISOLATION_ENABLED else None

    # echo 常數協定（不含 token）；client 必須 offer 它，否則 handshake 不成立
    await websocket.accept(subprotocol=_WS_SUBPROTOCOL)
    conn = await cop_hub.connect(
        websocket,
        exercise_id,
        follows_active=follows_active,
        include_standing=include_standing,
        visible_factions=visible_factions,
    )
    try:
        # P1-14：exercise_id 可能是 NULL_SCOPE（object，無 active＝實戰池），不可序列化 → 送 None
        hello_ex = exercise_id if isinstance(exercise_id, int) else None
        await websocket.send_json({"op": "hello", "exercise_id": hello_ex})
        # server→client push only；仍 loop receive 以偵測斷線（client 不需送任何東西）
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await cop_hub.disconnect(conn)
