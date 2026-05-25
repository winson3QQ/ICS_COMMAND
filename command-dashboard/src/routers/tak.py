"""
tak.py — TAK（Team Awareness Kit）整合 stub（Wave 7）

協議：CoT（Cursor on Target）XML，對齊 MIL-STD-2525
欄位：type, uid, time, stale, lat, lon, hae, ce, le

C0：stub，正確定義 schema，Wave 7 接 CoT 解析 + COP 正規化。
"""


from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/tak", tags=["TAK"])


class CoTEventIn(BaseModel):
    """CoT（Cursor on Target）事件，真實 CoT 欄位，非自創格式"""
    type:  str           # MIL-STD-2525 代碼（e.g. a-f-G-U-C）
    uid:   str           # 全域唯一識別碼
    time:  str           # ISO 8601 UTC
    stale: str           # 過期時間 ISO 8601 UTC
    lat:   float         # 緯度（WGS-84）
    lon:   float         # 經度（WGS-84）
    hae:   float = 0.0   # Height Above Ellipsoid（公尺）
    ce:    float = 9999  # Circular Error（公尺）
    le:    float = 9999  # Linear Error（公尺）
    callsign: str | None = None
    remarks:  str | None = None


@router.post("/events")
def receive_cot_event(body: CoTEventIn):
    """接收 CoT 事件（Wave 7 接 COP 正規化層）"""
    # C0 stub：驗證格式正確，回傳 ack
    return {
        "ok":      True,
        "uid":     body.uid,
        "status":  "stub_received",
        "message": "TAK 整合 Wave 7 啟用，目前僅驗證格式",
    }


@router.get("/status")
def tak_status():
    return {
        "enabled": False,
        "phase":   "Wave 7 stub",
        "protocol": "CoT XML (Cursor on Target)",
        "standard": "MIL-STD-2525",
    }
