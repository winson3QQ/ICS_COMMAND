# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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

# callsign **淨化**（#236）：允許字母（含 unicode 中文，\w）/數字/底線 + 空格與常見呼號標點
# `- . / ( ) ' + # :`（**加 `:`** 容 iTAK 預設名「iTAK: A1」，#236 主因）。**刻意允許 `'`**（容
# O'Brien，紅隊 RT-M3 陷阱 1）。其餘（`< > & " 反引號 ; =` 與控制字元等注入/破壞字元）→ **strip**。
# **改淨化非丟棄**：#157 原本 raise → 整筆 CoT 被丟 → 單位在 COP 隱形（#236）。感測層底線是
# 「marker 不消失」(#213)——名字髒就清名字，別丟整顆單位。真正 XSS 防線在 sink（渲染 textContent、
# 出向 builder XML escape），此層為縱深，不該犧牲可用性。
_CALLSIGN_DISALLOWED = re.compile(r"[^\w \-./()'+#:]")


def _sanitize_callsign(v: str | None) -> str | None:
    """淨化 callsign：strip 非白名單字元 + 去頭尾空白 + 截 128；全清空/全空白 → None（無名單位仍進 COP）。"""
    if not v:
        return None  # None 或 ""（空字串）一律歸 None，對齊 str|None 契約（不回空字串）
    # strip → 截 128 → 再 strip：第二次 strip 清掉「截斷邊界剛好落在空白」殘留的頭尾空白。
    cleaned = _CALLSIGN_DISALLOWED.sub("", v).strip()[:128].strip()
    return cleaned or None


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
        # #236：淨化非丟棄——髒字元只清掉，不讓整筆 CoT 因名字失敗而被丟（單位隱形）。
        return _sanitize_callsign(v)


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
        # #236：出向指令亦淨化（一致；XML builder 另 escape）。
        return _sanitize_callsign(v)

    @field_validator("uid")
    @classmethod
    def _validate_uid(cls, v: str | None) -> str | None:
        # uid 進 CoT attr，限保守字元集（字母數字 . _ -），擋注入/空白。
        if v and not re.match(r"^[A-Za-z0-9._-]{1,128}$", v):
            raise ValueError("uid 只允許 [A-Za-z0-9._-]")
        return v


# ── 出向 GeoChat（#216：ICS→TAK 文字通聯，對稱入向 P2-07 chat_service）──────────────
# 指揮部主動對現場發 GeoChat（CoT b-t-f）。發話者身分由 server 端（session operator）決定，
# 不信 client 宣告；client 只給訊息內容 + 收件路由（聊天室 / DM 收件 uid）。
class ChatSendIn(BaseModel):
    """指揮部發 GeoChat 的輸入 → `services/tak_downlink.build_geochat_cot`。

    收件路由：預設全體廣播（chatroom='All Chat Rooms'）；給 `recipient_uid` → 點對點 DM；
    給其他 `chatroom` 名 → 命名聊天室/隊伍頻道。message 走內容白名單（router）+ XML escape（builder）。
    """

    model_config = ConfigDict(extra="forbid")

    # 通聯文字（非空，上限 480 字——留裕度於 input_safety 的 512 上限，避免 schema 過、白名單卻 422）。
    message: str = Field(..., min_length=1, max_length=480)
    chatroom: str = Field(default="All Chat Rooms", max_length=128)  # 聊天室名（全體/隊伍頻道）
    recipient_uid: str | None = None  # 點對點 DM 收件裝置 uid（None=聊天室廣播）
    recipient_callsign: str | None = None  # DM 收件顯示呼號（chatroom 顯示用）
    lat: float = Field(default=0.0, ge=-90.0, le=90.0)  # 發訊位置（GeoChat 的「Geo」；可省）
    lon: float = Field(default=0.0, ge=-180.0, le=180.0)

    @field_validator("message")
    @classmethod
    def _strip_message(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("通聯訊息不得為空白")
        return v

    @field_validator("recipient_uid")
    @classmethod
    def _validate_recipient_uid(cls, v: str | None) -> str | None:
        # 收件 uid 進 CoT attr（chatgrp/link/uid 第三段），限保守字元集擋注入/空白。
        if v and not re.match(r"^[A-Za-z0-9._-]{1,128}$", v):
            raise ValueError("recipient_uid 只允許 [A-Za-z0-9._-]")
        return v

    @field_validator("chatroom")
    @classmethod
    def _sanitize_chatroom(cls, v: str | None) -> str:
        # 聊天室名淨化（沿用 callsign 白名單；builder 另 XML escape）。淨化後全空 → 退回全體
        # 廣播預設（欄位型別為 str，不得回 None——否則 audit 記 None 與實際送出的房名不符）。
        return _sanitize_callsign(v) or "All Chat Rooms"

    @field_validator("recipient_callsign")
    @classmethod
    def _sanitize_recipient_callsign(cls, v: str | None) -> str | None:
        # DM 顯示呼號淨化（可選；全空 → None，router 退回收件 uid 當顯示名）。
        return _sanitize_callsign(v)


class TakConnectionToggleIn(BaseModel):
    """P2-24（#164）：runtime 啟用/停用 TAK :8089 訂閱。"""

    enabled: bool
