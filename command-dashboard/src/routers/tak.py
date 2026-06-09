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

from core import config
from core.input_safety import validate_no_unsafe_strings
from repositories._helpers import audit
from schemas.tak import CoTEventIn, DownlinkCommandIn
from services import cop_service, tak_downlink, tak_service
from services.exercise_service import current_exercise_id

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
    return {
        "enabled": config.TAK_ENABLED,
        "cot_url": config.TAK_COT_URL if config.TAK_ENABLED else None,
        "protocol": "CoT (Cursor on Target)",
        "standard": "MIL-STD-2525",
        # P2-23 連線健康
        "connected": health["connected"],
        "last_cot_at": last,
        "last_cot_age_s": age_s,
    }
