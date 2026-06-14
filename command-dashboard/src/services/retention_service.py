"""retention_service — 軌跡 PII retention（P2-20 收尾 / #207，threat_model §8.4 政策乙案）。

政策：`cop_entity_tracks`（人員行蹤個資）= **exercise 刪除 cascade（既有）＋ N 天 TTL
自動清理（本檔）**。TTL 防「沒人刪演習就永遠留著」的個資累積（外洩 blast radius +
磁碟無界成長）；trade-off = 超過 N 天的演習不可再 AAR 回放（政策已知、記 threat_model）。

Admin runtime 開關（比照 P2-24 TAK toggle 模式）：config 表 `retention.tracks_ttl_enabled`
持久化；**預設啟用**（安全政策出廠生效，sysadmin 可關以支援長保存需求）。
每次清理筆數寫 `RETENTION_CLEANUP` audit（個資刪除須留痕，exercise_id=NULL 系統層）。
"""

import structlog

from core import config
from core.database import get_conn
from repositories import config_repo
from repositories._helpers import audit

log = structlog.get_logger()

_CONFIG_KEY = "retention.tracks_ttl_enabled"


def ttl_enabled() -> bool:
    """持久開關；未設 → True（政策預設生效）。"""
    raw = config_repo.get_config(_CONFIG_KEY)
    if raw is None:
        return True
    return raw.strip().lower() == "true"


def set_ttl_enabled(enabled: bool) -> None:
    """持久化開關（audit 由 endpoint 以 RETENTION_TOGGLE audit-first 記，避免雙記）。"""
    config_repo.set_config(_CONFIG_KEY, "true" if enabled else "false")


def cleanup_expired_tracks() -> int:
    """刪除超過 TRACKS_TTL_DAYS 的軌跡點。開關關閉 → no-op 回 0。

    cutoff 用 SQLite UTC now 計（與 tracks.t 的 ISO Z 同域字串比較）；刪除筆數 >0 才寫
    `RETENTION_CLEANUP` audit（無事不洗版）。失敗讓例外上拋——caller（週期 task）log
    後下輪再試，不吞錯。
    """
    if not ttl_enabled():
        return 0
    days = max(int(config.TRACKS_TTL_DAYS), 1)  # 防呆：≥1 天，拒絕「TTL=0 全清」誤設
    # 字串比較沿用 codebase 既有時間過濾慣例（如 list_cop_entities 的 `stale > strftime(...,'now')`）：
    # 假設 t 為 canonical UTC `YYYY-MM-DDTHH:MM:SSZ`（XML 串流 ingest 已正規化）。REST/federation
    # push 的非正規 t（毫秒/offset）僅造成 90 天窗**邊界次秒~次時**誤差（對 PII TTL immaterial）；
    # 不在此單點改 strftime(t)——會與全域慣例分歧，且舊版 SQLite(<3.42) 無法解析 'Z' 反而全不刪。
    # 走 idx_cop_tracks_t（#207 補）索引範圍刪。
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM cop_entity_tracks "
            "WHERE t < strftime('%Y-%m-%dT%H:%M:%SZ','now', ?)",
            (f"-{days} days",),
        )
        deleted = cur.rowcount
    if deleted > 0:
        audit("system", None, "RETENTION_CLEANUP", "cop_entity_tracks", "ttl",
              {"deleted": deleted, "ttl_days": days})
        log.info("[retention] 軌跡 TTL 清理：刪 %d 筆（>%d 天）", deleted, days)
    return deleted
