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
from repositories import cop_entity_repo
from repositories._helpers import iso_to_dt
from repositories.snapshot_repo import get_latest_snapshot
from schemas.cop import CoPEntity, CoPEntityTrack
from schemas.manual import ManualRecordIn
from schemas.tak import CoTEventIn
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
# - severity：CoT 無此概念，create 設 info，不在位置更新時覆蓋（保留未來 P2-05 enrich 空間）
# - visible_to / origin_node_id：授權與 federation metadata，位置更新不動
_TAK_UPDATE_FIELDS = (
    "type", "time", "start", "stale", "how", "version",
    "lat", "lon", "hae", "ce", "le",
    "heading_deg", "speed_mps", "access", "callsign", "remarks", "attributes",
    "team_color", "role", "battery",  # P2-06c：小隊欄位隨 update 刷新（battery 會變）
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
        severity="info",
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


async def ingest_cot_event(event: CoTEventIn) -> dict | None:
    """CoT 進 COP 的**共用消費者（接縫）**：normalize → upsert(CAS) → 廣播。

    協調契約（#105）：本函式由 P2-04 擁有並實作；P2-02 W2 subscribe(:8089) 與
    P2-03 routers/tak.py(REST push) **只呼叫、不定義**。

    回傳：持久化後的 cop_entities DB row（create 或 update）。
          out-of-order / 重送（非更新）/ CAS 重試耗盡 → None。
    """
    entity = normalize_cot(event)
    existing = cop_entity_repo.get_cop_entity(entity.uid)

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
    for _ in range(_CAS_MAX_RETRY):
        if not _is_newer(entity, existing):
            return None  # 落後 / 重送 → 丟棄，不倒退位置、不無謂 bump version
        res = cop_entity_repo.update_cop_entity_cas(entity.uid, existing["version_clock"], patch, actor="tak")
        if res["status"] == "ok":
            await _broadcast_cop("update", res["entity"])
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
