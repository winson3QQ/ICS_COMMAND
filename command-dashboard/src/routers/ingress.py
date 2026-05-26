"""
ingress.py — 通用 ingress 路由（COP 外部來源接收）

ROADMAP P1-02 完成：自 `pi_push.py` 改名而來，URL 命名空間統一為 `/api/ingress/*`。

目前承載：
- pi-node ingress（既有 Pi 上游節點推送）
- pi-data 讀取（沿用既有 endpoint，未來檔案肥大可拆出 `pi_data.py`）

未來預期（依 ROADMAP）：
- P3-02：增 `POST /api/ingress/waveink` 與 `WS /api/ingress/waveink/stream`
- TAK 不住這檔，留在 `routers/tak.py`（`/api/tak/*`，P2-03）

向後相容：
- 舊路徑 `/api/pi-push/{unit_id}` 以**同 handler 雙裝飾器**保留，避免打爆 ICS_DMAS
  上游 Pi node client。POST + HMAC 場景無法用 308 redirect（會壞 signature），
  雙裝飾器是唯一乾淨解。
- middleware (`auth/middleware.py`) 對兩條路徑都放行（authn 由 HMAC + Bearer 雙重把關）。

⚠️ 移除別名前必須先遷移所有 client：
- `server/sync.js`（本 repo internal relay；本 PR 未遷）
- ICS_DMAS 上游 Pi node client（外部 repo）
追蹤 issue 與遷移時序待 P2/P3 階段 client 升級時一併處理；別名為長期保留意圖。

⚠️ 雙裝飾器陷阱：勿為 `receive_pi_node_ingress` 手動指定 `operation_id=`，
否則 FastAPI 啟動會 ValueError（兩 route 共用 function name，operation_id 必須
依賴 FastAPI 自動加 path 後綴消歧）。
"""
import json

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.service import validate_session
from middleware.trusted_ingest import verify_hmac
from repositories.pi_batch_repo import get_latest_pi_batch
from services.pi_push_service import process_push

router = APIRouter(tags=["Ingress"])


@router.post("/api/ingress/pi-node/{unit_id}", dependencies=[Depends(verify_hmac)])
@router.post("/api/pi-push/{unit_id}",         dependencies=[Depends(verify_hmac)])
async def receive_pi_node_ingress(unit_id: str, request: Request):
    """Pi 上游節點推送接收。

    主路徑：`/api/ingress/pi-node/{unit_id}`（P1-02 新命名空間）
    別名：  `/api/pi-push/{unit_id}`（向後相容，ICS_DMAS Pi client 仍在用）
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(401, "缺少 Bearer token")
    token = auth_header[7:]
    body  = await request.json()
    try:
        return process_push(unit_id, token, body)
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e


@router.get("/api/pi-data/{unit_id}/list", tags=["Pi 資料"])
def get_pi_data(unit_id: str, request: Request):
    """Pi 節點最新一批資料讀取（session-authed）。

    讀取側 endpoint，沿用既有路徑（無 ingress 命名空間轉換需求）；
    未來檔案膨脹再拆出 `routers/pi_data.py`。
    """
    validate_session(request)
    batch = get_latest_pi_batch(unit_id)
    if not batch:
        return {"records": [], "grouped": {}, "pushed_at": None,
                "received_at": None, "offline": True}
    records = (json.loads(batch["records_json"])
               if isinstance(batch["records_json"], str) else batch["records_json"])
    grouped: dict = {}
    for r in records:
        tbl = r.get("table_name", "unknown")
        grouped.setdefault(tbl, []).append(r)
    return {"records": records, "grouped": grouped,
            "pushed_at": batch["pushed_at"], "received_at": batch["received_at"],
            "offline": False}
