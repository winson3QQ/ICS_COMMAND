"""
cop_service.py — COP（Common Operational Picture）正規化層 v1（P1-03）

設計原則：
- 多來源正規化：manual / pi-node / tak / waveink → 統一進 cop_entities
- 對齊 TAK CoT 規格（不自創欄位）
- 4 個 normalize_* 函式為各 source 入口，本檔 v1 提供 stub，真實實作於 P2-04 / P3-05

歷史 read API：
- `get_cop_summary()` — 沿用既有摘要（讀 snapshots），與新 cop_entities 並存
"""

from repositories.snapshot_repo import get_latest_snapshot
from schemas.cop import CoPEntity
from schemas.manual import ManualRecordIn


def get_cop_summary(exercise_id: int | None = None) -> dict:
    """取得各組最新 COP 狀態摘要（C0：直接讀 snapshots）。

    P1-03 保留：snapshots 是 unit-level aggregated 統計（床位數、傷患數），
    與 cop_entities (per-record geographic entity) 是不同層級，並存無衝突。
    """
    units = ["medical", "shelter", "forward", "security"]
    return {unit: get_latest_snapshot(unit, exercise_id) for unit in units}


# ── normalize_* 入口（v1 stub，實作分別於 P2-04 / P3-05 / 後續 PR） ─────────


def normalize_cot(cot_event) -> CoPEntity:
    """TAK CoT XML → CoPEntity。實作於 P2-04（ROADMAP 行 245）。

    輸入：schemas/tak.py 的 CoTEventIn（P2-02 tak_service.parse_cot_xml 產出）。
    輸出：cop_entities row 一筆，type 取自 CoT type（MIL-STD-2525 grammar），
          source='tak'，attributes 收 CoT detail extensions（CoTEventIn.detail）。
    """
    raise NotImplementedError("P2-04 implementation")


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
