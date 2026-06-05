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

import structlog

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


# ════════════════════════════════════════════════════════════════════════════
# Wave 2（#106）：TAK :8089 mTLS CoT 串流訂閱
#
# 設計（依 #106 契約 + 活 server 實測 intel，見 #106 comment）：
# - 用 pytak[with-takproto] 當傳輸層：protocol_factory 建 mTLS 連線、RXWorker.readcot
#   以 readuntil(b"</event>") 處理 TCP 分幀、use_protobuf 自動 v0/v1（CLAUDE.md「先找先例」）。
# - **只呼叫** `cop_service.ingest_cot_event(event)`（#105 擁有），本檔不定義、不碰 cop_service.py。
# - client cert 須送**完整鏈**（leaf+intermediate）；TAK truststore 只有 root，缺鏈 → peer not verified。
# - `t-x-takp-v`（TakControl 協商）是傳輸層產物，**在此濾掉**不丟 ingest（非真實 entity）。
# - 斷線指數退避重連；單筆解析失敗只記 log 不中斷串流。
# pytak / asyncio 走 **lazy import**：讓 parse_cot_xml（Wave 1 純函式）路徑零 pytak 依賴。
# ════════════════════════════════════════════════════════════════════════════

log = structlog.get_logger()

# TakControl 協定協商事件型別前綴（t-x-takp-v…）— 傳輸層控制訊息，非 COP entity
_TAKCONTROL_TYPE_PREFIX = "t-x-takp"


def build_subscribe_config(
    *,
    cot_url: str,
    client_cert: str,
    client_key: str,
    cafile: str | None = None,
    check_hostname: bool = False,
    allow_insecure_tls: bool = False,
):
    """組 pytak 連線設定（回 ConfigParser SectionProxy，相容 pytak 的 .get/.getboolean）。

    **安全（fail-closed）**：server 憑證驗證**預設開啟**。沒給 `cafile` 又沒顯式
    `allow_insecure_tls=True` → raise，不靜默放行。關掉 server 驗證 = MITM 可冒充 TAK
    server、把偽造 CoT 注入 COP（對作戰圖的完整性攻擊），故須刻意 opt-in（security-review #106）。

    Args:
        cot_url:     `tls://<host>:8089`（TAK CoT streaming）。
        client_cert: client 憑證 PEM，**須含完整鏈（leaf + intermediate）**。
        client_key:  client 私鑰 PEM。
        cafile:      驗 server 憑證的 CA（step-ca root）。**正式部署必填。**
        check_hostname: 是否驗 server SAN。dev 預設 False（SAN=tak.ics.local 非連線 IP）；
                        與驗證脫鉤 —— 給 cafile + check_hostname=False = 驗憑證鏈但不卡 hostname。
        allow_insecure_tls: 顯式允許「無 cafile → 完全不驗 server」（僅 PoC/dev，會大聲 warn）。
    Raises:
        ValueError: 無 cafile 且未顯式 allow_insecure_tls。
    """
    from configparser import ConfigParser

    cp = ConfigParser()
    section = {
        "COT_URL": cot_url,
        "PYTAK_TLS_CLIENT_CERT": client_cert,
        "PYTAK_TLS_CLIENT_KEY": client_key,
        # v0 CoT XML（活 server 實測預設可用；不強制 protobuf，v1 是選配升級）
        "TAK_PROTO": "0",
    }
    if cafile:
        section["PYTAK_TLS_CLIENT_CAFILE"] = cafile  # 驗 server 憑證（對 step-ca 信任鏈）
    elif allow_insecure_tls:
        section["PYTAK_TLS_DONT_VERIFY"] = "1"
        log.warning(
            "tak.tls_verification_disabled",
            reason="無 cafile + allow_insecure_tls",
            risk="MITM 可冒充 TAK server 注入偽造 CoT 進 COP；僅限 dev/PoC",
        )
    else:
        raise ValueError(
            "build_subscribe_config：須提供 cafile 驗 server 憑證；"
            "dev 無 CA 時須顯式傳 allow_insecure_tls=True（會關閉 server 驗證，有 MITM 風險）"
        )
    if not check_hostname:
        section["PYTAK_TLS_DONT_CHECK_HOSTNAME"] = "1"
    cp["tak_subscribe"] = section
    return cp["tak_subscribe"]


def _resolve_ingest():
    """延遲取得 `cop_service.ingest_cot_event`（#105 擁有；契約：本檔只呼叫不定義）。

    lazy import：避免 import 期硬綁 #105 尚未 merge 的符號（rebase 後即指向 main 的實作）。
    """
    from services.cop_service import ingest_cot_event

    return ingest_cot_event


async def _consume_cot(raw: bytes, ingest) -> object | None:
    """處理單筆 raw CoT bytes：parse → 濾 TakControl → ingest。

    - 解析失敗：只記 log、回 None，**不 raise**（單筆壞不該斷整條串流）。
    - `t-x-takp-v` 等控制事件：跳過，不進 ingest。
    - 其餘：呼叫 ingest（同步或 async 皆可），回傳其結果。
    """
    import inspect

    try:
        event = parse_cot_xml(raw)
    except CoTParseError as exc:
        log.warning("tak.cot_parse_failed", error=str(exc))
        return None
    if event.type.startswith(_TAKCONTROL_TYPE_PREFIX):
        log.debug("tak.control_event_skipped", type=event.type, uid=event.uid)
        return None
    try:
        result = ingest(event)
        if inspect.isawaitable(result):
            result = await result
        return result
    except Exception as exc:  # noqa: BLE001 — 單筆 ingest 失敗只記 log，不中斷整條串流
        # （CancelledError 是 BaseException，不被這裡攔，shutdown 仍能中斷）
        log.warning("tak.ingest_failed", uid=event.uid, error=str(exc))
        return None


def _build_receiver_class(pytak):
    """工廠：在 lazy 拿到 pytak 後定義 RXWorker 子類（子類化需 def-time 有基底）。"""

    class _CoTReceiver(pytak.RXWorker):
        """pytak RXWorker 子類：每筆 readcot() → _consume_cot → ingest。

        覆寫 run()：readcot() 回 None（EOF/斷線）即 return，交回 subscribe() 重連
        （基底 run() 會空轉，不利重連）。
        """

        def __init__(self, queue, config, reader, *, ingest, stop_event=None):
            super().__init__(queue, config, reader)
            self._ingest = ingest
            self._stop_event = stop_event
            self.handled_count = 0  # 本次連線收到的 frame 數（subscribe 用來判連線健康）

        async def handle_data(self, data: bytes) -> None:  # 滿足 abstractmethod
            await _consume_cot(data, self._ingest)

        async def run(self, _=-1) -> None:
            import asyncio

            while not (self._stop_event and self._stop_event.is_set()):
                data = await self.readcot()
                if not data:  # None（IncompleteReadError）或空 → EOF/斷線，交回 subscribe 重連
                    return
                self.handled_count += 1
                await self.handle_data(data)
                await asyncio.sleep(0)  # 讓出 event loop

    return _CoTReceiver


async def subscribe(
    config,
    *,
    ingest=None,
    stop_event=None,
    backoff_initial: float = 1.0,
    backoff_max: float = 30.0,
) -> None:
    """長駐背景 task：mTLS 連 TAK :8089 訂閱 CoT 串流，逐筆 → ingest_cot_event。

    斷線指數退避重連，直到 `stop_event` 被設（graceful shutdown）。

    關閉語意：
    - `stop_event.set()` 是**軟停**：停止重連，且 receiver 在「兩次 readcot 之間」會察覺並結束；
      但若正卡在 `readcot()`（等資料），要等下一筆/EOF 才返回。
    - 要**立即停**（如 FastAPI lifespan shutdown），請 `task.cancel()`：CancelledError 會
      中斷 await，並被原樣往上拋（不被內部吞）。

    Args:
        config:   `build_subscribe_config(...)` 的產物（pytak SectionProxy）。
        ingest:   消費函式，預設延遲解析 `cop_service.ingest_cot_event`（測試可注入 mock）。
        stop_event: `asyncio.Event`，set 後停止重連並結束。
        backoff_initial / backoff_max: 重連退避秒數下/上限。
    """
    import asyncio

    import pytak  # lazy：parser 路徑不載 pytak（避免 aiohttp warning + 無謂依賴）

    ingest = ingest or _resolve_ingest()
    Receiver = _build_receiver_class(pytak)
    backoff = backoff_initial

    while not (stop_event and stop_event.is_set()):
        writer = None
        try:
            reader, writer = await pytak.protocol_factory(config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 連線各種失敗統一退避重試
            log.warning("tak.connect_failed", error=str(exc), retry_in=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, backoff_max)
            continue

        log.info("tak.connected", cot_url=config.get("COT_URL"))
        receiver = Receiver(asyncio.Queue(), config, reader, ingest=ingest, stop_event=stop_event)
        try:
            await receiver.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 串流中斷統一進重連
            log.warning("tak.stream_error", error=str(exc))
        finally:
            if writer is not None:
                try:
                    writer.close()  # asyncio：關 writer 即拆共用 transport（reader 一併結束）
                except Exception:  # noqa: BLE001
                    pass

        if stop_event and stop_event.is_set():
            break
        # 只在「這次連線真的收到過資料」才重置退避 —— 否則 flapping server（接受 TLS 後
        # 立即 EOF、零資料）會被當健康，每次都重置成 backoff_initial 快速重連刷 log。
        if receiver.handled_count:
            backoff = backoff_initial
        log.info("tak.disconnected_reconnecting", retry_in=backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, backoff_max)

    log.info("tak.subscribe_stopped")
