"""
cop_service.py — COP（共同作戰圖像）正規化層（C0 stub）

設計原則（Wave 6 實作）：
- 多來源正規化：Pi push / TAK CoT / 手動輸入 → 統一進 COP
- 聯邦式最終一致：各節點獨立維護本地 COP 副本
- 時間軸支援：存快照供 Wave 6 時間軸回放

C0 階段：stub，COP 資料模型預留，實際正規化 Wave 6 實作。
"""

from repositories.snapshot_repo import get_latest_snapshot


def get_cop_summary(exercise_id: int | None = None) -> dict:
    """取得各組最新 COP 狀態摘要（C0：直接讀 snapshots）"""
    units = ["medical", "shelter", "forward", "security"]
    return {
        unit: get_latest_snapshot(unit, exercise_id)
        for unit in units
    }
