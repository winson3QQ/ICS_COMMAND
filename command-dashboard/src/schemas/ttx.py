
from pydantic import BaseModel


class TTXInjectBulkIn(BaseModel):
    """批次上傳 TTX injects"""
    injects: list  # list of {seq, target_unit, type, title, description, payload, scheduled_offset_min}


class TTXInjectStatusIn(BaseModel):
    status: str    # pending / active / delivered / skipped


class TTXInjectFireIn(BaseModel):
    """手動觸發單一 inject 送出"""
    inject_id: int
    operator:  str
