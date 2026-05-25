"""
repositories/nonce_repo.py — nonce replay cache（Lazy Expiry）

nonce_cache 表在 ics.db，disk-persisted。
TTL = NONCE_TTL_MS（預設 10 min）。
每次 check 時同步刪過期記錄（Lazy Expiry，無 background task）。
"""

import sqlite3

from core.config import NONCE_TTL_MS


def check_and_store_nonce(conn: sqlite3.Connection, nonce: str, now_ms: int) -> bool:
    """nonce 尚未使用則儲存並回傳 True；已使用（replay）回傳 False。

    副作用：同步刪除所有超過 TTL 的過期 nonce（Lazy Expiry）。
    """
    ttl_cutoff = now_ms - NONCE_TTL_MS

    # Lazy Expiry：刪過期 nonce（bounded table size = request_rate × TTL）
    conn.execute(
        "DELETE FROM nonce_cache WHERE created_at < ?",
        (ttl_cutoff,),
    )

    # 原子性 replay 檢查 + 寫入（INSERT OR IGNORE）
    # 以單一原子操作取代原本的 SELECT → INSERT（TOCTOU 競爭條件修正）：
    #   - 首次使用：INSERT 成功，changes() = 1 → 回傳 True
    #   - 重送（replay）：PRIMARY KEY 衝突，IGNORE 靜默跳過，changes() = 0 → 回傳 False
    # SQLite INSERT OR IGNORE 為原子操作，多 worker / multi-process 均安全。
    conn.execute(
        "INSERT OR IGNORE INTO nonce_cache (nonce, created_at) VALUES (?, ?)",
        (nonce, now_ms),
    )
    conn.commit()
    return conn.execute("SELECT changes()").fetchone()[0] > 0
