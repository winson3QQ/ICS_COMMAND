# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
from core.database import get_conn

from ._helpers import audit, now_utc


def get_config(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_config(key: str, value: str, operator: str | None = None):
    now = now_utc()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now),
        )
    if operator:
        audit(operator, None, "config_updated", "config", key, {"value": value})


# #384：升級安裝可能殘留已廢的 admin_pin* config 列（PIN hash+salt）。Admin PIN 移除後，
# GET /api/config/{key} 不再特例擋 admin_pin → 殘列會被低權角色(observer)讀到 hash（縱深退步）。
# 開機一次性刪除（冪等；fresh deploy 無此列即 no-op）。
_LEGACY_ADMIN_PIN_KEYS = ("admin_pin", "admin_pin_failed_count", "admin_pin_locked_until")


def cleanup_legacy_admin_pin_config() -> int:
    placeholders = ",".join("?" * len(_LEGACY_ADMIN_PIN_KEYS))
    with get_conn() as conn:
        cur = conn.execute(f"DELETE FROM config WHERE key IN ({placeholders})", _LEGACY_ADMIN_PIN_KEYS)
        conn.commit()
    return cur.rowcount
