"""
tak.py — TAK（Team Awareness Kit）整合 stub

協議：CoT（Cursor on Target），對齊 MIL-STD-2525
欄位：見 `schemas/tak.py` 的 CoTEventIn（忠實對齊 CoT 2.0 規格）

C0：endpoint 仍是 stub。CoT 解析 = P2-02（`services/tak_service.py`）、
endpoint 升級為真 = P2-03、COP 正規化 = P2-04。
CoTEventIn schema 於 P2-02（#102）由本檔 inline 移至 `schemas/tak.py` 並補齊
start/how/version 等 CoT 必填欄位。
"""

from fastapi import APIRouter

from schemas.tak import CoTEventIn

router = APIRouter(prefix="/api/tak", tags=["TAK"])


@router.post("/events")
def receive_cot_event(body: CoTEventIn):
    """接收 CoT 事件（Wave 7 接 COP 正規化層）"""
    # C0 stub：驗證格式正確，回傳 ack
    return {
        "ok": True,
        "uid": body.uid,
        "status": "stub_received",
        "message": "TAK 整合 Wave 7 啟用，目前僅驗證格式",
    }


@router.get("/status")
def tak_status():
    return {
        "enabled": False,
        "phase": "Wave 7 stub",
        "protocol": "CoT XML (Cursor on Target)",
        "standard": "MIL-STD-2525",
    }
