"""
cop_service.py — COP（Common Operational Picture）正規化層 v1（P1-03）

設計原則：
- 多來源正規化：manual / pi-node / tak / waveink → 統一進 cop_entities
- 對齊 TAK CoT 規格（不自創欄位）
- 4 個 normalize_* 函式為各 source 入口；P2-04 起 normalize_cot 落地

歷史 read API：
- `get_cop_summary()` — 沿用既有摘要（讀 snapshots），與新 cop_entities 並存

P2-04（#105）新增：
- `normalize_cot()` — CoTEventIn → CoPEntity（純函式）
- `ingest_cot_event()` — TAK CoT 進 COP 的**共用消費者（接縫）**：normalize → upsert
  （version_clock CAS）→ WebSocket 廣播。由 subscribe(:8089, P2-02 W2) 與
  routers/tak.py(REST push, P2-03) **共同呼叫**；兩者只呼叫、不定義（協調契約見 #105）。
"""

import logging
import sqlite3

from core.config import TRACK_MIN_INTERVAL_S
from repositories import client_faction_repo, cop_entity_repo
from repositories._helpers import iso_to_dt
from repositories.snapshot_repo import get_latest_snapshot
from schemas.cop import CoPEntity, CoPEntityTrack
from schemas.manual import ManualRecordIn
from schemas.tak import CoTEventIn
from services import chat_service, geometry_service
from services.exercise_service import current_exercise_id
from services.realtime_hub import cop_hub

log = logging.getLogger(__name__)


def get_cop_summary(exercise_id: int | None = None) -> dict:
    """取得各組最新 COP 狀態摘要（C0：直接讀 snapshots）。

    P1-03 保留：snapshots 是 unit-level aggregated 統計（床位數、傷患數），
    與 cop_entities (per-record geographic entity) 是不同層級，並存無衝突。
    """
    units = ["medical", "shelter", "forward", "security"]
    return {unit: get_latest_snapshot(unit, exercise_id) for unit in units}


# ── normalize_* 入口（normalize_cot 已落地 P2-04；其餘待各 source 接入時實作）──

# CoT 後續推送（同 uid）會更新的欄位。**刻意排除**：
# - uid / source / exercise_id：身分與場次歸屬，更新位置時不得改（exercise_id 在 create 綁定）
# - version_clock / updated_* / received_at：DB 管理（update_cop_entity_cas 受保護欄位）
# - severity：由 normalize_cot 在 create 時決定（MEDEVAC=critical，其餘 info，P2-09），
#   位置更新不覆蓋（否則後續無 <_medevac_> 的位置幀會把 MEDEVAC critical 打回 info）
# - visible_to / origin_node_id：授權與 federation metadata，位置更新不動
_TAK_UPDATE_FIELDS = (
    "type", "time", "start", "stale", "how", "version",
    "lat", "lon", "hae", "ce", "le",
    "heading_deg", "speed_mps", "access", "callsign", "remarks", "attributes",
    "team_color", "role", "battery",  # P2-06c：小隊欄位隨 update 刷新（battery 會變）
    "archived",  # #161：archive 狀態隨 update 刷新（重畫可能加/去 <archive/>）
)  # fmt: skip
_CAS_MAX_RETRY = 3


def _opt_bounded(value, lo, hi, *, to_int: bool = False):
    """CoT 數值字串 → float（to_int=True → int），超界 / 非數 / inf → None（防 sensor
    garbage 讓整筆 ingest 炸掉）。int(float('inf')) 的 OverflowError 一併攔（review #126-1）。"""
    if value is None or value == "":
        return None
    try:
        f = float(value)
        n = int(f) if to_int else f
    except (TypeError, ValueError, OverflowError):
        return None
    return n if lo <= n <= hi else None


def _opt_bounded_float(value, lo: float, hi: float) -> float | None:
    """course/speed 字串 → float（heading[0,360] / speed[0,1000] 約束）。"""
    return _opt_bounded(value, lo, hi)


def _opt_bounded_int(value, lo: int, hi: int) -> int | None:
    """battery 等字串 → int（容 '78' 與 '78.0'）。"""
    return _opt_bounded(value, lo, hi, to_int=True)


def _dict_child(detail: dict, key: str) -> dict:
    """detail 的 child 取 dict。同 tag 多筆時 _extract_detail 收成 list → 取首個 dict
    （非靜默全丟，review #126-4）；非 dict/list → {}。track/__group/status 共用。"""
    v = detail.get(key)
    if isinstance(v, list):
        v = next((x for x in v if isinstance(x, dict)), None)
    return v if isinstance(v, dict) else {}


def _link_uid_by_relation(detail: dict, relation: str) -> str | None:
    """從 detail['link'] 挑出指定 relation 的那筆 <link> 的 uid（#343 producer 歸屬）。

    `<link>` 可能單筆（dict）或多筆（list，如 route 的多個 <link point>）；`_dict_child`
    只取首個、不過濾 relation，故另寫此 helper。真機實證：ATAK/iTAK 標記帶
    `<link relation="p-p" uid="<裝置 self-SA uid>">` 指回產生它的裝置。
    """
    v = detail.get("link")
    items = v if isinstance(v, list) else [v]
    for item in items:
        if isinstance(item, dict) and item.get("relation") == relation:
            uid = item.get("uid")
            if uid:
                return uid
    return None


def _resolve_client_key(entity: CoPEntity) -> str:
    """解析產生此 entity 的 client（裝置 self-SA uid）= faction 歸屬鍵（#343 §2.2）。

    歸屬鏈（全 by-uid，不靠猜）：
      1. attributes.creator.uid（ATAK 標記/繪圖明確帶）
      2. attributes.link[relation=p-p].uid（ATAK + iTAK 標記）
      3. fallback：entity.uid 本身（self-SA 單位 = uid 即裝置；無作者欄的 iTAK 繪圖則
         fallback 到繪圖自己的 GUID，不會命中任何 client_faction → NULL → fail-closed）
    """
    creator = _dict_child(entity.attributes, "creator")
    if creator.get("uid"):
        return creator["uid"]
    link_uid = _link_uid_by_relation(entity.attributes, "p-p")
    if link_uid:
        return link_uid
    return entity.uid


def _resolve_faction(entity: CoPEntity) -> str | None:
    """ingest 時解析 entity 的 faction（producer 經 admin 分類者繼承；未分類/解不到 → None
    = fail-closed）。查 client_faction 綁 entity 的 exercise_id（per-exercise 分類）。"""
    client_key = _resolve_client_key(entity)
    return client_faction_repo.get_faction(entity.exercise_id, client_key)


def _extract_squad(detail: dict) -> tuple[str | None, str | None, int | None]:
    """從 CoT detail 的 <__group>/<status> 提取小隊欄位（P2-06c，#126）。

    team_color ← <__group name>（strip + title 標準化大小寫，保留多字色名如 'Dark Blue'，
    **不強限 enum** 以免丟 Orange/Teal 等真實 ATAK 色，供 P2-06d GROUP BY 一致；非標準格式如
    'darkBlue' 經 title 會成 'Darkblue'，但 ATAK 標準色為空格分隔故不觸發）；
    role ← <__group role>（原樣 strip）；battery ← <status battery>（→int 0-100，越界/非數 None）。
    資料源 attributes 巢狀（_extract_detail 已收）；原 __group/status 仍保留在 attributes（CoT 忠實）。
    """
    group = _dict_child(detail, "__group")
    status = _dict_child(detail, "status")
    name = group.get("name")
    team_color = name.strip().title() if isinstance(name, str) and name.strip() else None
    role_val = group.get("role")
    role = role_val.strip() if isinstance(role_val, str) and role_val.strip() else None
    battery = _opt_bounded_int(status.get("battery"), 0, 100)
    return team_color, role, battery


def _to_opt_bool(value) -> bool | None:
    """CoT 布林字串 → bool；None/空 → None（語意「未知」，非 False）。"""
    if value is None or value == "":
        return None
    return str(value).strip().lower() in ("true", "1", "yes")


def _opt_str(d: dict, key: str) -> str | None:
    """dict[key] 取字串 strip，非字串/純空白 → None（對齊 _dict_child 的 (dict, key) 形狀）。"""
    v = d.get(key)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _argb_int_to_hex(value) -> str | None:
    """ATAK 顏色 = 有號 32-bit ARGB 整數字串（如 '-1'=0xFFFFFFFF 白、'2130706432'=0x7F000000）
    → '#rrggbb'（取 RGB 去 alpha，前端用單色描邊+填色）。非整數 → None。"""
    if value is None:
        return None
    try:
        n = int(str(value).strip()) & 0xFFFFFFFF
    except (TypeError, ValueError):
        return None
    return f"#{(n >> 16) & 0xFF:02x}{(n >> 8) & 0xFF:02x}{n & 0xFF:02x}"


def _extract_color(detail: dict) -> str | None:
    """CoT shape 顏色 → '#rrggbb'（P2-10 #5：前端 polygon/route 描邊+填色用）。ATAK 真機：
    shape 走 <strokeColor value>（外框＝使用者選色）/ <fillColor value>（填色，含 alpha）；
    marker 走 <color argb>。優先外框色 → marker color → 填色（取 RGB）。皆無法解析 → None
    （前端退預設色）。_extract_detail 把這些 element 收成 {attr: val} dict child。"""
    for tag, attr in (("strokeColor", "value"), ("color", "argb"), ("color", "value"), ("fillColor", "value")):
        child = detail.get(tag)
        if isinstance(child, dict):
            hexv = _argb_int_to_hex(child.get(attr))
            if hexv is not None:
                return hexv
    return None


def _extract_medevac(detail: dict) -> dict | None:
    """從 CoT <_medevac_> element 萃取 9-line 後送請求摘要（P2-09，#135）。

    ATAK CASEVAC plugin 把 9-line 各欄位放在 <_medevac_> element 的「屬性」，
    _extract_detail 已通用收成 detail["_medevac_"]（同 __group 模式）。本函式把原始
    字串屬性正規化成乾淨型別（precedence 傷亡數→int、casevac→bool）成穩定摘要，
    消費方（前端後送面板 / P3-06 WaveInk 同 card schema）免挖原始字串 + 統一型別。
    原始 _medevac_ 仍完整保留在 attributes（CoT 忠實；真機屬性名小差時不漏資料）。

    9-line 是聚合後送態勢（傷亡數 by precedence / pickup 位置 / 通訊頻率），非個別
    病患 PII（無姓名病史）→ 進 attributes 隨 COP 廣播，指揮部透明可見（#135 取捨：
    覆蓋 TAK-F 紅隊的 medical_records 分流建議，採單一 COP 不開新表）。

    無 <_medevac_> → None（非 MEDEVAC 事件，severity 不升 critical）。
    ATAK 屬性大小寫不一（Title/Priority/Security 大寫，freq/urgent/routine 小寫）
    → 全轉小寫鍵取值。
    """
    mv = _dict_child(detail, "_medevac_")
    if not mv:
        return None
    low = {k.lower(): v for k, v in mv.items() if isinstance(k, str)}
    return {
        "title": _opt_str(low, "title"),  # 後送請求標題 / 識別
        "freq": _opt_str(low, "freq"),  # Line 2：後送通訊頻率
        "precedence": {  # Line 3：傷亡數 by 後送優先級（→int，0-9999 防 garbage）
            k: _opt_bounded_int(low.get(k), 0, 9999) for k in ("urgent", "priority", "routine")
        },
        "casevac": _to_opt_bool(low.get("casevac")),  # CASEVAC（非醫療專機）vs MEDEVAC
        "security": _opt_str(low, "security"),  # Line 6：pickup 點安全狀況
        "marking": _opt_str(low, "hlz_marking"),  # Line 7：HLZ 標記方式
    }


def normalize_cot(cot_event: CoTEventIn) -> CoPEntity:
    """TAK CoT event → CoPEntity（純函式，無副作用）。

    輸入：schemas/tak.py 的 CoTEventIn（P2-02 tak_service.parse_cot_xml 產出）。
    輸出：CoPEntity 一筆，source='tak'。欄位刻意對齊 CoT（P1-03 doctrine），多為直通；
          CoT detail extensions 整包收進 attributes（不自創欄位，映射不到的不硬塞）。
    heading/speed 取自 detail 的 <track course=.. speed=..>（若有）。
    """
    detail = dict(cot_event.detail or {})
    track = _dict_child(detail, "track")
    team_color, role, battery = _extract_squad(detail)
    # P2-09：MEDEVAC <_medevac_> 9-line → attributes["medevac"] 結構化摘要（型別轉乾淨）；
    # 原始 _medevac_ 仍在 detail（CoT 忠實）。severity 升 critical 讓地圖醒目（#135）。
    medevac = _extract_medevac(detail)
    if medevac is not None:
        detail["medevac"] = medevac
    # P2-08：CoT <shape>/<link> 幾何 → attributes.kind（route/polygon）+ vertices。
    # lat/lon **沿用 CoT <point>**（ATAK 給的錨點，忠實對位；不重算 centroid，避免與 ATAK 端
    # 顯示位置不一致 + 避免未驗證 centroid 覆寫已驗證 point — review #132）。
    if cot_event.geometry:
        verts = geometry_service.geojson_to_vertices(cot_event.geometry)
        if verts:
            detail["kind"] = "polygon" if cot_event.geometry.get("type") == "Polygon" else "route"
            detail["vertices"] = verts
            # P2-10 #5：把 CoT strokeColor/fillColor → attributes.color，前端才不會一律退灰/藍。
            color = _extract_color(detail)
            if color:
                detail["color"] = color
            # P2-10：CoT <strokeStyle> 三種筆觸 → 前端渲染旗標（solid→實線，不設旗標）。
            #   dashed → dash（長虛線）；dotted → dotted（小圓點）。兩者互斥。
            style = detail.get("strokeStyle")
            if isinstance(style, dict):
                sv = str(style.get("value", "")).strip().lower()
                if sv == "dashed":
                    detail["dash"] = True
                elif sv == "dotted":
                    detail["dotted"] = True
    return CoPEntity(
        uid=cot_event.uid,
        type=cot_event.type,
        time=cot_event.time,
        start=cot_event.start,
        stale=cot_event.stale,
        how=cot_event.how,
        version=cot_event.version,
        lat=cot_event.lat,
        lon=cot_event.lon,
        hae=cot_event.hae,
        ce=cot_event.ce,
        le=cot_event.le,
        heading_deg=_opt_bounded_float(track.get("course"), 0.0, 360.0),
        speed_mps=_opt_bounded_float(track.get("speed"), 0.0, 1000.0),
        source="tak",
        exercise_id=current_exercise_id(),  # 綁當前 active 場（ttx 演習 / real 實戰）；無 active → NULL（非演習非實戰）
        access=cot_event.access,
        callsign=cot_event.callsign,
        remarks=cot_event.remarks,
        archived=cot_event.archived,  # CoT <archive/> 持久標記（#161）；list 豁免 stale
        # P2-09：MEDEVAC 事件升 critical（地圖醒目 + P2-12 pulse）；其餘維持 info。
        # severity 不在 _TAK_UPDATE_FIELDS → 後續位置更新不覆寫，critical 維持（#135）。
        severity="critical" if medevac is not None else "info",
        team_color=team_color,
        role=role,
        battery=battery,
        attributes=detail,
    )


def _is_newer(entity: CoPEntity, existing: dict) -> bool:
    """incoming event 是否比 DB 現值新。time 皆 ISO 8601 UTC 同格式（parse 層已正規化）
    → 字串比較即正確。防 out-of-order / 重送的 CoT 把位置倒退回舊值。

    限制（code-review #108）：parse_cot_xml 的 _normalize_iso8601 把時間 floor 到**秒精度**，
    故同一秒內的多筆 CoT（快速移動單位 ATAK 可能 >1Hz）會被視為「非更新」而丟棄，
    顯示位置最多落後 ~1s。COP 顯示尺度可接受；若 P2-06 時間軸要求 sub-second 保真，
    需在上游（tak_service parse 層，非本檔產權）保留毫秒精度再放寬此比較。"""
    prev = existing.get("time")
    if not prev:
        return True
    return entity.time > prev


async def _broadcast_cop(op: str, entity: dict) -> None:
    """寫操作成功後 push 給 WS 訂閱者。訊息格式對齊 routers/cop.py._broadcast，
    前端 cop_stream 既有處理路徑可直接吃。broadcast 內部對死連線容錯，不拋。"""
    await cop_hub.broadcast(
        {
            "op": op,
            "uid": entity["uid"],
            "version_clock": entity["version_clock"],
            "entity": entity,
        },
        exercise_id=entity.get("exercise_id"),
        source=entity.get("source"),  # #343：faction 過濾（只 tak 受限）
        faction=entity.get("faction"),
    )


# ── P2-06a：CoT 軌跡寫入（cop_entity_tracks，issue #120）────────────────────────
# 每次位置持久化（create/update）後寫一筆軌跡點，為 P2-20 AAR 逐格回放鋪資料。
# exercise 歸屬**不在本表存**（決策 B）：track 透過 uid 綁 cop_entities，查詢時
# JOIN 取 exercise_id（SoT 單一，不反正規化）；exercise 刪除靠 uid ON DELETE CASCADE。
# 抽樣間隔由 config.TRACK_MIN_INTERVAL_S 覆寫（預設 5s）。


def _within_min_interval(last_t: str, new_t: str) -> bool:
    """new_t 距 last_t 是否 < TRACK_MIN_INTERVAL_S（抽樣基準 = CoT event time，非
    wall-clock；回放要的是事件時間軸）。iso_to_dt 統一補 UTC，故跨來源混格式
    （REST push 未正規化 / XML 已正規化）也能安全相減，不因 aware−naive 觸發
    TypeError；parse 或相減失敗 → False（不擋，寧可多寫一筆也不漏軌跡）。"""
    try:
        return (iso_to_dt(new_t) - iso_to_dt(last_t)).total_seconds() < TRACK_MIN_INTERVAL_S
    except (ValueError, AttributeError, TypeError):
        return False


def _record_track(entity: dict) -> None:
    """位置持久化後寫一筆軌跡點。**best-effort**：任何失敗只 log.warning，
    絕不讓軌跡寫入擋住 ingest / 即時廣播（對齊 _broadcast_cop 容錯風格）。

    範圍：只記有 active 場（演習 ttx / 實戰 real）的軌跡；非演習也非實戰
    （exercise_id=NULL，無 active 場）無複盤對象 → 跳過（issue #123）。

    抽樣：查該 uid 上一筆軌跡時間，距今 < 間隔跳過。新 entity（create）/ 重插
    （reinsert，舊軌跡已隨 entity cascade 刪）皆無前一筆 → 第一筆必寫。
    """
    try:
        uid = entity.get("uid")
        t = entity.get("time")
        if not uid or not t:
            return
        # #3：濾 (0,0) null island（ATAK 無 GPS fix / 手點位置前的壞點）+ 超範圍 → 不記軌跡，
        # 否則 AAR 尾跡會拉一條線到 (0,0)。即時 entity 仍照常 upsert（不影響 live 顯示判斷）。
        lat, lon = entity.get("lat"), entity.get("lon")
        if lat is None or lon is None or (lat == 0 and lon == 0) or abs(lat) > 90 or abs(lon) > 180:
            return
        # 非演習也非實戰（無 active 場 → exercise_id=NULL）：不記軌跡（issue #123）。
        # entity 已照常 upsert（即時 COP 不受影響），僅跳過軌跡時間序列寫入。
        if entity.get("exercise_id") is None:
            return
        last = cop_entity_repo.get_last_track_time(uid)
        if last is not None and _within_min_interval(last, t):
            return
        cop_entity_repo.insert_cop_track(
            CoPEntityTrack(
                uid=uid,
                t=t,
                lat=entity["lat"],
                lon=entity["lon"],
                hae=entity.get("hae") or 0.0,
                heading_deg=entity.get("heading_deg"),
                speed_mps=entity.get("speed_mps"),
            )
        )
    except Exception as e:  # noqa: BLE001 — best-effort，吞所有例外只留痕
        log.warning("[tak] 軌跡寫入失敗（不擋同步）uid=%s：%s", entity.get("uid"), e)


async def _handle_tak_delete(event: CoTEventIn) -> dict | None:
    """TAK `t-x-d-d` 刪除命令 → 軟刪目標 entity（墓碑 deleted=1）+ 廣播 op=delete。

    目標 uid 取自 CoT `<link uid=...>`（_extract_detail 收成 detail['link']）。
    來源所有權守門：只准刪 source='tak' 的 entity（防偽造 t-x-d-d 刪本地 manual 標繪）。
    目標不存在 / 無 link / 已刪 / 非 tak 來源 → None（不動作）。t-x-d-d 本身不進主表。
    """
    link = _dict_child(dict(event.detail or {}), "link")
    target = link.get("uid") if isinstance(link, dict) else None
    if not target:
        log.info("[tak] 收到 t-x-d-d 但無 link uid，忽略")
        return None
    existing = cop_entity_repo.get_cop_entity(target)
    if existing is None or existing.get("deleted"):
        return None  # 目標未收過 / 早已刪除（重送 t-x-d-d 不重複廣播）
    if existing.get("source") != "tak":
        log.warning("[tak] t-x-d-d 指向非 tak 來源 entity，拒刪 uid=%s source=%s", target, existing.get("source"))
        return None
    res = cop_entity_repo.delete_cop_entity(target, existing["version_clock"], actor="tak")
    if res["status"] == "ok":
        await _broadcast_cop("delete", res["entity"])
        log.info("[tak] t-x-d-d → 軟刪 uid=%s", target)
        return res["entity"]
    # conflict（別人剛改過）/ notfound（剛被刪）→ 不重試，下一筆會校正
    return None


async def ingest_cot_event(event: CoTEventIn) -> dict | None:
    """CoT 進 COP 的**共用消費者（接縫）**：normalize → upsert(CAS) → 廣播。

    協調契約（#105）：本函式由 P2-04 擁有並實作；P2-02 W2 subscribe(:8089) 與
    P2-03 routers/tak.py(REST push) **只呼叫、不定義**。

    回傳：持久化後的 cop_entities DB row（create 或 update）。
          out-of-order / 重送（非更新）/ CAS 重試耗盡 → None。
    """
    # P2-07（#129）：GeoChat（type b-t-f）分流到 chats 表，不進 cop_entities（作戰圖主表）。
    # 放共用接縫 → :8089 串流（_consume_cot）與 REST push（routers/tak.py）兩條路徑都擋。
    if event.type.startswith("b-t-f"):
        await chat_service.ingest_chat(event)  # b2（#213）：async 化以即時 WS 廣播通聯
        return None
    # TAK 刪除命令（t-x-d-d）：不是 COP 物件，是「移除某 uid」的指令。解出 <link uid> →
    # 軟刪該 entity（墓碑）→ 廣播 op=delete。本身不存進主表。此前誤把 t-x-d-d 當 entity 存
    # → 刪除無作用，正是 #161 部分真因（iTAK 其實有送刪除信號，是我們沒處理）。
    if event.type.startswith("t-x-d-d"):
        return await _handle_tak_delete(event)
    entity = normalize_cot(event)
    # #343：解析 producer faction（藍/紅/中立），create 時隨 entity 落地（insert 走 model_dump
    # 自動帶 faction 欄）。未分類 / 解不到 producer → None = fail-closed（對 commander 不可見）。
    # faction 不在 _TAK_UPDATE_FIELDS → 後續位置更新幀不覆寫（保留），admin 重分類走另路徑。
    entity.faction = _resolve_faction(entity)
    entity.faction_source = "auto"
    existing = cop_entity_repo.get_cop_entity(entity.uid)

    # TAK-B（紅隊）：來源所有權守門。CoT uid 由來源系統命名、規格上應已全域唯一
    # （The Developer's Guide to CoT：來源前綴 + MITRE 發放，如 Link16.01520），且標準
    # 要求轉傳時**保留 uid 不改寫**（供 P2-13 下行對 ATAK 下令時 uid 對位）。故**不前綴
    # 改名**，改以「來源所有權」隔離：TAK 事件只准動 source='tak' 的 entity；若 uid 撞上
    # 本地他源 entity（manual:* / pi-node / waveink，可能是惡意偽造或意外碰撞），一律拒絕
    # 覆寫（不動 type/lat/lon/callsign），回 None 不廣播，記 log 供查。
    if existing is not None and existing.get("source") != "tak":
        log.warning(
            "[tak] uid 撞本地他源 entity，拒絕覆寫（防偽造/碰撞）uid=%s existing_source=%s",
            entity.uid,
            existing.get("source"),
        )
        return None

    # 新 uid → insert
    if existing is None:
        try:
            created = cop_entity_repo.insert_cop_entity(entity)
        except sqlite3.IntegrityError:
            # 並發：別人在 get 與 insert 之間插了同 uid → 轉 update 路徑
            existing = cop_entity_repo.get_cop_entity(entity.uid)
            if existing is None:
                # 非 uid race（如 CHECK 違規）→ 不吞，往上拋
                log.warning("[tak] ingest insert 失敗且 uid 仍不存在（非並發）：%s", entity.uid)
                raise
        else:
            await _broadcast_cop("create", created)
            _record_track(created)
            return created

    # 既有 uid → 順序守門 + version_clock CAS update（並發落敗則用新版重試）
    patch = {f: getattr(entity, f) for f in _TAK_UPDATE_FIELDS}
    # P2-06c（review #126-2）：小隊欄位間歇出現（ATAK 非每幀帶 <__group>/<status>）。
    # None 不覆寫已存值（coalesce），否則無 group 的位置幀會把 team_color 打成 NULL，
    # 破壞 P2-06d GROUP BY；保留語意 = 最後已知隊伍歸屬 / 電量。
    for _f in ("team_color", "role", "battery"):
        if patch.get(_f) is None:
            patch.pop(_f, None)
    # #161（post-merge review）：archived coalesce —— 缺 <archive/> 的更新幀**不可**把既有 archived
    # 打回 0（否則 archived marker 收到輕量更新幀就失去持久 = 重現本 bug，與 team_color 同理）。
    # 只在幀明確帶 <archive/> 時才更新（單調設真）；un-archive 走明確刪除（deleted 墓碑），不靠 stream 倒退。
    if not event.archived:
        patch.pop("archived", None)
    # P2-09（review #135-3）：severity 不在 _TAK_UPDATE_FIELDS（位置幀不打回 info），但需允許
    # **單調升級**——同 uid 先普通幀（info）後 MEDEVAC 幀，critical 應升上去（否則漏升=地圖不醒目）。
    # 只升不降：critical 才寫入 patch；普通幀（info）不寫 → 保留既有值，不把既有 critical 打回。
    if entity.severity == "critical":
        patch["severity"] = "critical"
    for _ in range(_CAS_MAX_RETRY):
        if not _is_newer(entity, existing):
            return None  # 落後 / 重送 → 丟棄，不倒退位置、不無謂 bump version
        # 復活（#161）：既有 entity 是刪除墓碑（deleted=1），又收到**更新的**同 uid CoT →
        # 代表該物件又出現了（場端刪了又重畫 / self-marker 持續回報）。清墓碑 + 以 create 廣播
        # （前端刪除時已 _remove，需重新加回）。順序守門（_is_newer）保證舊的 in-flight 幀不誤復活。
        resurrect = bool(existing.get("deleted"))
        upd = {**patch, "deleted": 0} if resurrect else patch
        res = cop_entity_repo.update_cop_entity_cas(entity.uid, existing["version_clock"], upd, actor="tak")
        if res["status"] == "ok":
            await _broadcast_cop("create" if resurrect else "update", res["entity"])
            _record_track(res["entity"])
            return res["entity"]
        if res["status"] == "conflict":
            existing = res["entity"]  # 別人剛改過 → 用新版本重試（順序守門會再判一次）
            continue
        # notfound：get 後 entity 被刪（soft-delete/reset）→ 當新 entity 重插
        try:
            created = cop_entity_repo.insert_cop_entity(entity)
        except sqlite3.IntegrityError:
            existing = cop_entity_repo.get_cop_entity(entity.uid)
            if existing is None:
                log.warning("[tak] ingest notfound 重插落空（entity 同時被刪又無法插）：%s", entity.uid)
                return None
            continue
        await _broadcast_cop("create", created)
        _record_track(created)
        return created
    # CAS 重試耗盡：高並發同 uid 下確認較新的事件被丟（data loss），留痕供查（code-review #108）
    log.warning("[tak] ingest CAS 重試 %d 次耗盡，丟棄較新事件：%s", _CAS_MAX_RETRY, entity.uid)
    return None


def normalize_pi_node(unit_id: str, record: dict) -> CoPEntity | None:
    """Pi 上游節點 push record → CoPEntity（若可定位）。

    輸入：pi_received_batches 解出的單筆 record（含 table_name / record）。
    輸出：
    - 若 record 含 lat/lon（如未來 Medical PWA 人員定位 / Shelter PWA 點位）
      → CoPEntity with source='pi-node'
    - 若是純統計類（床位數、傷患數）→ None（走既有 snapshot 路徑，不入 cop_entities）

    實作觸發點：Pi push 路徑升級或 Medical/Shelter PWA 重接（Wave 7+）。
    """
    raise NotImplementedError("Pi-node 真實 lat/lon 注入路徑出現時實作")


def normalize_waveink(parsed: dict) -> CoPEntity | None:
    """WaveInk 結構化結果 → CoPEntity（若可定位）。實作於 P3-05（ROADMAP 行 289）。

    輸入：WaveInk push 經 NLP parser 後的結構化 dict（含 transcript / callsign /
          channel / audio_ref / consent_id + 可選的 lat/lon）。
    輸出：
    - 通聯帶 location（如前進組 GPS 標記）→ CoPEntity with source='waveink'，
      attributes 含 transcript / audio_ref / channel / consent_id
    - 通聯無 location → None（走 events 路徑，不入 cop_entities）
    """
    raise NotImplementedError("P3-05 implementation")


def normalize_manual(record: ManualRecordIn) -> CoPEntity | None:
    """Manual form 輸入 → CoPEntity（若 form 帶地理資訊）。

    輸入：routers/manual.py 的 ManualRecordIn（form_id + payload）。
    輸出：
    - 地理性 form（如 intel-vehicle, intel-situation）含 lat/lon
      → CoPEntity with source='manual'
    - 純資料 form（shelter-intake / med-patient 等）→ None（既有 manual_records 路徑）

    實作觸發點：manual form 加入 lat/lon 欄位時。
    """
    raise NotImplementedError("manual form 加入地理欄位時實作")
