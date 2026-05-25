
from pydantic import BaseModel


class SyncPushIn(BaseModel):
    source_unit:    str
    sync_start_ts:  str
    device_id:      str | None = None
    snapshots:      list = []
    events:         list = []
    manual_records: list = []

    class Config:
        extra = "allow"


class ConflictResolveIn(BaseModel):
    conflict_idx: int    # 衝突在列表中的索引
    action:       str    # keep_incoming / keep_existing / merge
    operator:     str
