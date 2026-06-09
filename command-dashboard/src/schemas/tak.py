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

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── P2-10 內容層白名單 ──────────────────────────────────────────────────────
# 任何 ATAK 裝置都能推 CoT 進 TAK Server → COP，TAK Server 的裝置准入是其管理員責任；
# 本層為 ICS 端「**最後一道防線**」，在 ingest 邊界（CoTEventIn 建構）擋畸形/惡意內容。
# 座標越界已由 lat/lon 的 ge/le 約束擋下（下方）。本處補 type / callsign。

# CoT type 須符 MIL-STD-2525 grammar：首為單一域字母（a=atoms/b=bits/t=tasking/…），
# 其後 dash 分隔的 alphanumeric token（如 a-f-G-U-C / b-a-o-tbl-medevac / t-x-takp-v）。
# 擋掉 `<script>`、空白、`;`、注入字元等非法 type。
_COT_TYPE_RE = re.compile(r"^[a-z](?:-[A-Za-z0-9]+)+$")

# callsign 允許：字母（含 unicode，\w）/數字/底線 + 空格與常見標點 `- . / ( ) ' + #`。
# **刻意允許 `'`**（容 O'Brien 等真實呼號，對齊紅隊 RT-M3 陷阱 1：別誤殺合法輸入）；
# 擋 `< > & " 反引號 ; =` 與控制字元等注入/破壞字元（縱深防護；渲染端另走 textContent）。
_CALLSIGN_RE = re.compile(r"^[\w \-./()'+#]{1,128}$")


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
    geometry: dict | None = None  # CoT <shape>/<link> → GeoJSON（P2-08，geometry_service 填）
    archived: bool = False  # CoT <archive/>：持久標記，過 stale 也保留（#161，parse 時偵測）

    # ── P2-10 內容層白名單（ingest 邊界最後防線）──────────────────────────
    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if not _COT_TYPE_RE.match(v):
            raise ValueError(f"CoT type 不符 2525 grammar 白名單：{v!r}")
        return v

    @field_validator("callsign")
    @classmethod
    def _validate_callsign(cls, v: str | None) -> str | None:
        # None / 空字串放行（無呼號）；非空則須過字元白名單。
        if v and not _CALLSIGN_RE.match(v):
            raise ValueError("callsign 含內容層白名單不允許的字元")
        return v


# ── 下行指令（P2-13 A：streaming-write downlink，issue #176 reality check 定案）─────────
# 指揮部「下達指令」= 建一個 CoT 寫進 :8089 → server 廣播給同 group 所有現場 ATAK。
# 入向 CoTEventIn 是「收外部 CoT」；本模型是「指揮部主動發 CoT」的輸入（欄位精簡、server 補齊
# time/start/stale）。沿用同一套 type / callsign 內容白名單（縱深防護；XML escape 在 builder）。
class DownlinkCommandIn(BaseModel):
    """指揮部下達指令的輸入 → `services/tak_downlink.build_command_cot` 建 CoT。

    與 CoTEventIn 的差異：指揮部是發送端，故 uid 可省（server 生）、time/start/stale 由
    server 依 `stale_minutes` 算（不信 client 時鐘，對齊 P2-23 doctrine）。
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(..., min_length=1)  # 指令的 CoT type（2525 grammar，如 a-f-G / b-m-r）
    lat: float = Field(..., ge=-90.0, le=90.0)
    lon: float = Field(..., ge=-180.0, le=180.0)
    hae: float = 0.0
    callsign: str | None = None  # 指令標籤（現場端顯示名）
    remarks: str | None = None  # 指令內容文字
    uid: str | None = None  # 省略則 server 生 ICS-CMD-<uuid>
    stale_minutes: int = Field(default=60, ge=1, le=10080)  # 指令存活（1 分鐘 ~ 7 天）
    planned: bool = True  # 計畫中指令（ICS COP 端 2525 空心框；現場端視為一般標記）

    @field_validator("type")
    @classmethod
    def _validate_type(cls, v: str) -> str:
        if not _COT_TYPE_RE.match(v):
            raise ValueError(f"CoT type 不符 2525 grammar 白名單：{v!r}")
        return v

    @field_validator("callsign")
    @classmethod
    def _validate_callsign(cls, v: str | None) -> str | None:
        if v and not _CALLSIGN_RE.match(v):
            raise ValueError("callsign 含內容層白名單不允許的字元")
        return v

    @field_validator("uid")
    @classmethod
    def _validate_uid(cls, v: str | None) -> str | None:
        # uid 進 CoT attr，限保守字元集（字母數字 . _ -），擋注入/空白。
        if v and not re.match(r"^[A-Za-z0-9._-]{1,128}$", v):
            raise ValueError("uid 只允許 [A-Za-z0-9._-]")
        return v
