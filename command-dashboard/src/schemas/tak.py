"""
schemas/tak.py — TAK CoT（Cursor on Target）事件 Pydantic 模型

P2-02：把原本 inline 在 `routers/tak.py` 的 `CoTEventIn` 移來這裡並補齊欄位。

設計約束：
- **忠實對齊 CoT 2.0 規格，不自創欄位**（ROADMAP P2-02 紅線）。
- event 7 必填 attr：version / type / uid / time / start / stale / how。
  （原 stub 只有 type/uid/time/stale，缺 start/how/version → 下游 CoPEntity
   必填 how/start 會生不出來，#102 reality check 確認的 contract gap，本檔補齊。）
- point 5 必填 attr：lat / lon / hae / ce / le（hae/ce/le 缺省採 CoT「未知」慣例）。
- detail = lax any-XML container：常用的 callsign / remarks 提取為一等欄位，
  其餘結構化收進 `detail` dict 交給 P2-04 `normalize_cot` 落 `attributes`。

CoT 2.0 規格來源：MITRE CoT Event XSD（event/point/detail），與 `schemas/cop.py`
CoPEntity 的 CoT 核心欄位對齊（uid/type/time/start/stale/how/version + lat/lon/hae/ce/le）。
"""

from pydantic import BaseModel, ConfigDict, Field


class CoTEventIn(BaseModel):
    """CoT（Cursor on Target）事件 — 真實 CoT 欄位，非自創格式。

    `services/tak_service.parse_cot_xml()` 的輸出型別，也是 P2-04
    `cop_service.normalize_cot()` 的輸入型別。
    """

    # 未知的頂層 key 視為錯誤（CoT detail 擴充走 `detail` dict，不放頂層）
    model_config = ConfigDict(extra="forbid")

    # ── event 屬性（CoT 規格 7 必填 + 3 選）─────────────────────────────
    version: str = "2.0"  # CoT schema version（XSD 必填，給缺省）
    uid: str = Field(..., min_length=1)  # 全域唯一識別碼
    type: str = Field(..., min_length=1)  # MIL-STD-2525 grammar（e.g. a-f-G-U-C）
    time: str = Field(..., min_length=1)  # event 產生 ISO 8601 UTC
    start: str = Field(..., min_length=1)  # event 生效起始 ISO 8601 UTC
    stale: str = Field(..., min_length=1)  # event 過期 ISO 8601 UTC
    how: str = Field(..., min_length=1)  # CoT how（h-e / m-g / ...）
    access: str | None = None  # 保密分級（選）
    qos: str | None = None  # Quality of Service（選）
    opex: str | None = None  # o/e/s = operations/exercise/simulation（選）

    # ── point（CoT 規格 5 必填；hae/ce/le 缺省採 CoT 未知慣例）──────────
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    hae: float = 0.0
    ce: float = 9999999.0
    le: float = 9999999.0

    # ── detail 常用提取（其餘進 detail dict 交 P2-04）──────────────────
    callsign: str | None = None
    remarks: str | None = None
    detail: dict = Field(default_factory=dict)  # 結構化 detail children（P2-04 → attributes）
