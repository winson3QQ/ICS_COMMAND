"""
tak_service.py — TAK（Team Awareness Kit）CoT 解析 + 訂閱（ROADMAP P2-02 / issue #102）

本檔範圍：
- **Wave 1（本檔目前內容）**：`parse_cot_xml()` — CoT XML → `CoTEventIn`，XXE-safe。
- **Wave 2（待活 TAK server 解鎖）**：`subscribe()` — 以 pytak client + mTLS 連 :8089
  訂閱 CoT 串流。需 client cert（擴充 `deploy/tak-server/pki/issue-tak-certs.sh`）
  且 :8089 是 TAK Protocol（v0 XML / v1 protobuf），非純 XML，由 pytak 處理協商。

安全紅線（ASVS V5 / V13、ROADMAP P2-08 接點）：
- CoT 來自外部，是不可信輸入。XML 解析**一律走 defusedxml**，DTD/外部實體/實體展開
  全關，擋 XXE / billion-laughs / quadratic-blowup。**禁止** stdlib `xml.etree`。

解析產出 = `CoTEventIn`（`schemas/tak.py`），交給 P2-04 `cop_service.normalize_cot`。
本檔**不碰 DB、不碰前端**（scope 邊界見 #102）。
"""

from __future__ import annotations

from datetime import UTC, datetime

# defusedxml：對外部 XML 的唯一允許解析路徑（forbid_dtd 連 DTD 都拒，斷 billion-laughs 根）
from defusedxml.ElementTree import fromstring as _safe_fromstring

from schemas.tak import CoTEventIn

# 單筆 CoT event 大小上限（防 well-formed 巨型 XML 的記憶體 DoS — defusedxml 只擋
# DTD/entity，不管整體 size/節點數）。TAK CoT event 典型 <2KB；256KB 已極寬鬆。
_MAX_COT_BYTES = 256 * 1024


class CoTParseError(ValueError):
    """CoT XML 解析失敗（格式錯 / 缺必填 / 過大 / 不可信內容被擋）。"""


def _localname(tag: str) -> str:
    """去掉 XML namespace 前綴（`{ns}event` → `event`）。CoT 基底無 namespace，
    但 federation / 擴充可能帶，統一剝乾淨再比對。"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _normalize_iso8601(value: str, *, field: str) -> str:
    """把 CoT 時間戳 normalize 成 `%Y-%m-%dT%H:%M:%SZ`（UTC、秒精度）。

    為何必須 normalize：`cop_entity_repo.list_cop_entities` 用**字典序字串比較**
    過濾 stale（`stale > strftime('%Y-%m-%dT%H:%M:%SZ','now')`）。CoT producer 常送
    毫秒（`...00.000Z`）或帶時區偏移（`...+08:00`），不 normalize 會讓字典序比較失準
    （'.' < 'Z'、偏移時區跨日）。一律轉 UTC 秒精度 Z 結尾，與 repo 比較格式對齊。
    """
    raw = (value or "").strip()
    if not raw:
        raise CoTParseError(f"CoT 時間欄位 {field} 為空")
    iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise CoTParseError(f"CoT 時間欄位 {field} 非合法 ISO 8601：{raw!r}") from exc
    # 無時區資訊 → 視為 UTC（CoT 規格時間皆 UTC）
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_detail(detail_el) -> tuple[str | None, str | None, dict]:
    """從 <detail> 抽 callsign / remarks，其餘 children 結構化成 dict 交 P2-04。

    回傳 (callsign, remarks, detail_dict)。
    - callsign：CoT 慣例放 `<contact callsign="...">`，掃任一帶 callsign 屬性的後代。
    - remarks：`<remarks>text</remarks>` 的文字。
    - detail_dict：每個 direct child → {tag: {屬性..., "_text": 文字}}；同 tag 多筆收成 list。
    """
    callsign: str | None = None
    remarks: str | None = None
    detail_dict: dict = {}
    if detail_el is None:
        return callsign, remarks, detail_dict

    # callsign / remarks 都只看 <detail> 的**直接子元素**（CoT 慣例 <contact>/<remarks>
    # 為 detail 直屬）。不用 .iter() 掃全後代 — 否則會誤撈巢狀擴充元素裡的 callsign 屬性。
    for child in list(detail_el):
        tag = _localname(child.tag)
        if callsign is None and child.get("callsign"):
            callsign = child.get("callsign")
        if tag == "remarks" and child.text and child.text.strip():
            remarks = child.text.strip()
        entry = dict(child.attrib)
        if child.text and child.text.strip():
            entry["_text"] = child.text.strip()
        if tag in detail_dict:
            existing = detail_dict[tag]
            if isinstance(existing, list):
                existing.append(entry)
            else:
                detail_dict[tag] = [existing, entry]
        else:
            detail_dict[tag] = entry
    return callsign, remarks, detail_dict


def _to_float(value: str | None, *, field: str, default: float | None) -> float:
    if value is None or value == "":
        if default is None:
            raise CoTParseError(f"CoT point 缺必填欄位 {field}")
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CoTParseError(f"CoT point 欄位 {field} 非數值：{value!r}") from exc


def parse_cot_xml(raw: str | bytes) -> CoTEventIn:
    """解析單筆 CoT XML event → `CoTEventIn`（XXE-safe）。

    Args:
        raw: CoT XML 字串或 bytes（外部不可信輸入）。
    Returns:
        CoTEventIn（pydantic 驗證必填 + 座標範圍）。
    Raises:
        CoTParseError: XML 格式錯 / root 非 event / 缺 point / 缺必填 / 不可信內容被 defusedxml 擋。
    """
    if isinstance(raw, bytes | bytearray):
        if len(raw) > _MAX_COT_BYTES:
            raise CoTParseError(f"CoT XML 超過大小上限（{len(raw)} > {_MAX_COT_BYTES} bytes）")
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CoTParseError("CoT XML 非 UTF-8") from exc
    if not raw or not raw.strip():
        raise CoTParseError("CoT XML 為空")
    if len(raw) > _MAX_COT_BYTES:
        raise CoTParseError(f"CoT XML 超過大小上限（{len(raw)} > {_MAX_COT_BYTES} chars）")

    # defusedxml：DTD/外部實體/實體展開全擋。EntitiesForbidden / DTDForbidden 會 raise。
    try:
        root = _safe_fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except CoTParseError:
        raise
    except Exception as exc:  # noqa: BLE001 — defusedxml 各種防禦例外統一收斂為 parse error
        raise CoTParseError(f"CoT XML 解析失敗（含安全防禦攔截）：{exc}") from exc

    if _localname(root.tag) != "event":
        raise CoTParseError(f"CoT root 應為 <event>，實得 <{_localname(root.tag)}>")

    point_el = next((c for c in root if _localname(c.tag) == "point"), None)
    if point_el is None:
        raise CoTParseError("CoT event 缺必填 <point>")
    detail_el = next((c for c in root if _localname(c.tag) == "detail"), None)

    callsign, remarks, detail_dict = _extract_detail(detail_el)

    a = root.attrib
    try:
        return CoTEventIn(
            version=a.get("version", "2.0"),
            uid=a.get("uid", ""),
            type=a.get("type", ""),
            time=_normalize_iso8601(a.get("time", ""), field="time"),
            start=_normalize_iso8601(a.get("start", ""), field="start"),
            stale=_normalize_iso8601(a.get("stale", ""), field="stale"),
            how=a.get("how", ""),
            access=a.get("access"),
            qos=a.get("qos"),
            opex=a.get("opex"),
            lat=_to_float(point_el.get("lat"), field="lat", default=None),
            lon=_to_float(point_el.get("lon"), field="lon", default=None),
            hae=_to_float(point_el.get("hae"), field="hae", default=0.0),
            ce=_to_float(point_el.get("ce"), field="ce", default=9999999.0),
            le=_to_float(point_el.get("le"), field="le", default=9999999.0),
            callsign=callsign,
            remarks=remarks,
            detail=detail_dict,
        )
    except CoTParseError:
        raise
    except Exception as exc:  # pydantic ValidationError 等 → 收斂
        raise CoTParseError(f"CoT event 欄位驗證失敗：{exc}") from exc
