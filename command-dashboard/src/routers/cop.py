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
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from auth.role_enum import READ_ROLES, is_role_allowed
from auth.service import check_session
from core.input_safety import validate_no_unsafe_strings
from repositories import cop_entity_repo
from schemas.cop import CoPEntity
from services.realtime_hub import cop_hub

router = APIRouter(prefix="/api/cop", tags=["COP"])

_MAX_BODY_BYTES = 256 * 1024  # 256 KB，與 map_config 一致
_STALE_DEFAULT_HOURS = 24  # 手動建立未帶 stale 時的預設存活時間

# PUT patch 不接受的欄位（router 層政策，疊在 repo 的 _CAS_PROTECTED_COLS 之上）：
#   source — 本 endpoint 只服務 manual 編輯，不得改成 tak / pi-node 偽造來源
# 其餘 uid / version_clock / updated_by / updated_at / received_at 由 repo raise ValueError
# （此處列出是為了在 router 層先回明確 422，不必等 repo 拋例外）。
_PUT_FORBIDDEN_FIELDS = frozenset({"source", "uid", "version_clock", "updated_by", "updated_at", "received_at"})


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
    )


# ── read ─────────────────────────────────────────────────────────────────────


@router.get("/entities")
def list_entities(
    source: str | None = None,
    exercise_id: int | None = None,
    include_stale: bool = False,
    limit: int = 500,
):
    """列出 COP entity（預設過濾 stale）。前線 client 啟動 / WS 重連時全量 resync 用。"""
    return {
        "entities": cop_entity_repo.list_cop_entities(
            source=source,
            exercise_id=exercise_id,
            include_stale=include_stale,
            limit=limit,
        )
    }


@router.get("/entities/{uid}")
def get_entity(uid: str, response: Response):
    ent = cop_entity_repo.get_cop_entity(uid)
    if ent is None:
        raise HTTPException(404, f"entity 不存在：{uid}")
    response.headers["ETag"] = _etag(ent["version_clock"])
    return ent


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

    try:
        entity = CoPEntity(**body)
    except ValidationError as e:
        raise HTTPException(422, f"entity 欄位驗證失敗：{e.errors()}") from e

    try:
        created = cop_entity_repo.insert_cop_entity(entity)
    except sqlite3.IntegrityError as e:
        raise HTTPException(409, f"uid 已存在：{entity.uid}") from e

    await _broadcast("create", created)
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

    try:
        result = cop_entity_repo.update_cop_entity_cas(uid, expected, body, actor=_actor(request))
    except ValueError as e:  # repo 欄位白名單 / 受保護欄位
        raise HTTPException(422, str(e)) from e

    if result["status"] == "notfound":
        raise HTTPException(404, f"entity 不存在：{uid}")
    if result["status"] == "conflict":
        return _conflict_response(result["entity"])
    await _broadcast("update", result["entity"])
    response.headers["ETag"] = _etag(result["entity"]["version_clock"])
    return result["entity"]


@router.delete("/entities/{uid}")
async def delete_entity(uid: str, request: Request, response: Response):
    """TAK soft-delete：標 stale=now + bump version_clock（不 hard delete）。"""
    expected = _parse_if_match(request)
    result = cop_entity_repo.delete_cop_entity(uid, expected, actor=_actor(request))

    if result["status"] == "notfound":
        raise HTTPException(404, f"entity 不存在：{uid}")
    if result["status"] == "conflict":
        return _conflict_response(result["entity"])
    # delete 也廣播（op=delete）：訂閱端據此把 entity 從畫面移除（TAK 語意）
    await _broadcast("delete", result["entity"])
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
        exercise_id = int(raw_ex) if raw_ex not in (None, "") else None
    except ValueError:
        await websocket.close(code=_WS_BAD_REQUEST)
        return

    # echo 常數協定（不含 token）；client 必須 offer 它，否則 handshake 不成立
    await websocket.accept(subprotocol=_WS_SUBPROTOCOL)
    conn = await cop_hub.connect(websocket, exercise_id)
    try:
        await websocket.send_json({"op": "hello", "exercise_id": exercise_id})
        # server→client push only；仍 loop receive 以偵測斷線（client 不需送任何東西）
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await cop_hub.disconnect(conn)
