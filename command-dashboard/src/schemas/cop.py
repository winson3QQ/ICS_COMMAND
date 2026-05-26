"""
schemas/cop.py — COP（Common Operational Picture）正規化層 Pydantic 模型 v1

P1-03 schema 凍結（issue #15）。對齊 TAK CoT 規格（不自創欄位），支援 4 source：
manual / pi-node / tak / waveink。

設計引用：
- TAK Server CoT Event XSD + CoT_link.xsd（官方規格）
- mini-taiwan map architecture（entity layer / track 0-1 內插 / collision）
- 本 repo ROADMAP P1-03（source enum 鎖定 manual / pi-node / tak / waveink，預留 pi-node 不關門）

v1 凍結邊界：欄位名稱 / type / 必填性以本檔為準。後續 ALTER ADD 算 v1 內擴充。
任何欄位 rename / drop / 改 PK 觸發 v2。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── 共用型別 alias ────────────────────────────────────────────────────────────

CoPSource    = Literal["manual", "pi-node", "tak", "waveink"]
CoPSeverity  = Literal["info", "warning", "critical"]


# ── cop_entities ──────────────────────────────────────────────────────────────


class CoPEntity(BaseModel):
    """COP 主表 entity。對齊 TAK CoT Event + ICS_Command 內部 metadata。

    attributes JSON 約定（不強制 schema）：
    - source='tak'      → CoT detail extensions（contact / color / track / __group / ...）
    - source='pi-node'  → unit_type / record_type / triage_color / care_status / ...
    - source='waveink'  → channel / transcript / audio_ref / consent_id / duration_ms
    - source='manual'   → form_id / form_type / payload
    """

    model_config = ConfigDict(extra="forbid")

    # ── CoT 規格核心欄位 ─────────────────────────────────────────────────
    uid:     str = Field(..., min_length=1)            # 全域 UID（CoT 慣例 GUID）
    type:    str = Field(..., min_length=1)            # MIL-STD-2525 grammar (e.g. a-f-G-U-C)
    time:    str                                        # event 產生 ISO 8601 UTC
    start:   str                                        # event 生效起始
    stale:   str                                        # event 過期
    how:     str = Field(..., min_length=1)            # CoT how (h-e / m-g / ...)
    version: str = "2.0"                                # CoT schema version

    # ── point（CoT 規格）──────────────────────────────────────────────────
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    hae: float = 0.0
    ce:  float = 9999999.0
    le:  float = 9999999.0

    # ── 運動內插（mini-taiwan 借鏡）─────────────────────────────────────
    heading_deg: float | None = Field(default=None, ge=0.0, lt=360.0)
    speed_mps:   float | None = Field(default=None, ge=0.0)

    # ── COP 內部 metadata ────────────────────────────────────────────────
    source:      CoPSource                              # ROADMAP P1-03 必填
    received_at: str | None = None                     # DB default 自動填
    exercise_id: int | None = None

    # ── 共用 / 授權（scenario 2 multi-team）─────────────────────────────
    access:     str | None = None                       # CoT 保密分級
    visible_to: list[str] = Field(default_factory=lambda: ["all"])

    # ── federation（scenario 7 離線重連）─────────────────────────────────
    origin_node_id: str | None = None
    last_synced_at: str | None = None
    version_clock:  int = 1

    # ── detail 常用提取（避免每次解 JSON）─────────────────────────────────
    callsign: str | None = None
    remarks:  str | None = None
    severity: CoPSeverity = "info"

    # ── escape hatch ─────────────────────────────────────────────────────
    attributes: dict = Field(default_factory=dict)


# ── cop_entity_tracks ────────────────────────────────────────────────────────


class CoPEntityTrack(BaseModel):
    """Entity 軌跡時間序列（mini-taiwan 0-1 插值 + Wave 6 時間軸回放）。"""

    model_config = ConfigDict(extra="forbid")

    uid: str = Field(..., min_length=1)                # FK → cop_entities.uid
    t:   str                                            # 時間 ISO 8601 UTC
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    hae: float = 0.0
    heading_deg: float | None = Field(default=None, ge=0.0, lt=360.0)
    speed_mps:   float | None = Field(default=None, ge=0.0)


# ── cop_entity_links ─────────────────────────────────────────────────────────


class CoPEntityLink(BaseModel):
    """Entity 之間的關係（對齊 TAK CoT_link.xsd）。

    relation 例：'follows'（車隊）/ 'riding'（人員搭車）/ 'origin'（通聯發話者）
                 / 'evidence'（媒體附件）/ 'assigned_to'（任務指派）

    target_uid 不一定在本 DB（federation 場景指向友軍 TAK Server entity）。
    mime 非空表示 target 是外部資源（圖片 / 影片）而非 CoT entity。
    """

    model_config = ConfigDict(extra="forbid")

    src_uid:     str = Field(..., min_length=1)        # FK → cop_entities.uid
    relation:    str = Field(..., min_length=1)
    target_uid:  str = Field(..., min_length=1)        # 可能外部
    target_type: str = Field(..., min_length=1)        # CoT type 或外部 MIME 類
    url:         str | None = None
    remarks:     str | None = None
    mime:        str | None = None                      # 非空 = 外部資源
