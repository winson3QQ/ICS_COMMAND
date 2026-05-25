
from pydantic import BaseModel


class SnapshotIn(BaseModel):
    """通用快照格式。相容 QR-SHELTER / QR-MEDICAL / QR-FORWARD / QR-SECURITY。"""
    v:           int
    type:        str
    snapshot_id: str
    t:           str              # ISO 8601 UTC
    src:         str

    source: str | None = "auto"  # auto / qr_scan / auto_sync / manual

    # SHELTER
    bed_used:       int | None  = None
    bed_total:      int | None  = None
    srt:            dict | None = None
    pending_intake: int | None  = None
    cmist_pending:  int | None  = None
    staff_on_duty:  int | None  = None

    # MEDICAL
    casualties:      dict | None = None
    waiting_count:   int | None  = None
    pending_evac:    int | None  = None
    evacuated_total: int | None  = None
    supplies:        dict | None = None

    # FORWARD
    units: list | None = None

    # SECURITY
    post_total:      int | None = None
    post_anomaly:    str | None = None
    qrf_available:   int | None = None
    isolation_count: int | None = None

    vehicle_available: int | None  = None
    extra:             dict | None = None

    class Config:
        extra = "allow"
