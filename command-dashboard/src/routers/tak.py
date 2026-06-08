"""
tak.py — TAK（Team Awareness Kit）CoT REST ingest endpoint（P2-03 / #107）

協議：CoT（Cursor on Target），對齊 MIL-STD-2525。欄位見 `schemas/tak.py` 的 CoTEventIn。

ingestion 兩條路徑（共用 #105 的 `cop_service.ingest_cot_event` 接縫）：
- **:8089 串流訂閱** → `tak_service.subscribe()`（P2-02 W2，由 main.py lifespan 跑背景 task）
- **REST/federation push** → 本檔 `POST /api/tak/events`（本 issue 升真）

協調契約（#105/#107）：本檔**只呼叫** `ingest_cot_event`，不定義（接縫是 cop_service 的）；
RBAC 由 `auth/role_enum.py` 中央 gate（POST=WRITE_ROLES）。
"""

from fastapi import APIRouter

from core import config
from schemas.tak import CoTEventIn
from services import cop_service, tak_service

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
