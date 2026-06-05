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

from repositories import cop_entity_repo
from repositories.snapshot_repo import get_latest_snapshot
from schemas.cop import CoPEntity
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
)  # fmt: skip
_CAS_MAX_RETRY = 3


def _opt_bounded_float(value, lo: float, hi: float) -> float | None:
    """CoT track 的 course/speed 字串 → float，超界或非數 → None（防 sensor garbage
    讓整筆 ingest 炸掉；對齊 CoPEntity 的 heading[0,360] / speed[0,1000] 約束）。"""
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if lo <= f <= hi else None


def normalize_cot(cot_event: CoTEventIn) -> CoPEntity:
    """TAK CoT event → CoPEntity（純函式，無副作用）。

    輸入：schemas/tak.py 的 CoTEventIn（P2-02 tak_service.parse_cot_xml 產出）。
    輸出：CoPEntity 一筆，source='tak'。欄位刻意對齊 CoT（P1-03 doctrine），多為直通；
          CoT detail extensions 整包收進 attributes（不自創欄位，映射不到的不硬塞）。
    heading/speed 取自 detail 的 <track course=.. speed=..>（若有）。
    """
    detail = dict(cot_event.detail or {})
    track = detail.get("track")
    track = track if isinstance(track, dict) else {}
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
        exercise_id=current_exercise_id(),  # create 時綁當前 active 場（無 → NULL 實戰池）
        access=cot_event.access,
        callsign=cot_event.callsign,
        remarks=cot_event.remarks,
        severity="info",
        attributes=detail,
    )


def _is_newer(entity: CoPEntity, existing: dict) -> bool:
    """incoming event 是否比 DB 現值新。time 皆 ISO 8601 UTC 同格式（parse 層已正規化）
    → 字串比較即正確。防 out-of-order / 重送的 CoT 把位置倒退回舊值。"""
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
                raise
        else:
            await _broadcast_cop("create", created)
            return created

    # 既有 uid → 順序守門 + version_clock CAS update（並發落敗則用新版重試）
    patch = {f: getattr(entity, f) for f in _TAK_UPDATE_FIELDS}
    for _ in range(_CAS_MAX_RETRY):
        if not _is_newer(entity, existing):
            return None  # 落後 / 重送 → 丟棄，不倒退位置、不無謂 bump version
        res = cop_entity_repo.update_cop_entity_cas(entity.uid, existing["version_clock"], patch, actor="tak")
        if res["status"] == "ok":
            await _broadcast_cop("update", res["entity"])
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
                return None
            continue
        await _broadcast_cop("create", created)
        return created
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
