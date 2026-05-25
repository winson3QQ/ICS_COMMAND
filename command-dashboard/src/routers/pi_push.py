import json

from fastapi import APIRouter, Depends, HTTPException, Request

from auth.service import validate_session
from middleware.trusted_ingest import verify_hmac
from repositories.pi_batch_repo import get_latest_pi_batch
from services.pi_push_service import process_push

router = APIRouter(tags=["Pi 推送"])


@router.post("/api/pi-push/{unit_id}", dependencies=[Depends(verify_hmac)])
async def receive_pi_push(unit_id: str, request: Request):
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
