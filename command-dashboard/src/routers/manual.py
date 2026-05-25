
from fastapi import APIRouter

from repositories.manual_repo import create_manual_record, get_manual_records, mark_manual_record_synced
from schemas.manual import ManualRecordIn

router = APIRouter(prefix="/api/manual_records", tags=["手動輸入"])

FORM_TARGET_MAP = {
    "shelter-intake":  ("S1_入站登記",  "PERSON+SHELTER_PROFILE"),
    "shelter-srt":     ("S2_SRT評估",   "SHELTER_PROFILE"),
    "shelter-cmist":   ("S3_CMIST評估", "CMIST"),
    "shelter-exit":    ("S4_離站記錄",  "SHELTER_PROFILE"),
    "med-patient":     ("M2_傷患接收",  "MEDICAL_PROFILE"),
    "intel-vehicle":   ("V1_車輛狀態",  "VEHICLE"),
    "intel-situation": ("C1_局勢摘要",  "COMMAND_SUMMARY"),
}


@router.post("")
def post_manual(body: ManualRecordIn):
    meta   = FORM_TARGET_MAP.get(body.form_id, ("手動輸入", "未知"))
    data   = body.model_dump()
    data["form_type"]    = meta[0]
    data["target_table"] = meta[1]
    return create_manual_record(data, body.exercise_id)


@router.get("")
def get_manual(sync_status: str | None = None, limit: int = 100):
    return get_manual_records(sync_status, limit)


@router.patch("/{record_id}/synced")
def mark_synced(record_id: str, operator: str = "system"):
    mark_manual_record_synced(record_id, operator)
    return {"ok": True}
