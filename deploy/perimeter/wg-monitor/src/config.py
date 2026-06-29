# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""config.py — wg-monitor 設定（全由環境變數注入；無使用者輸入）。"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    iface: str
    poll_interval_s: int
    db_path: str
    # 偵測門檻
    offline_after_s: int  # 握手老於此 = 離線
    oscillation_window_s: int  # 此窗內 distinct IP ≥2 = 並存（cloned-key proxy）
    dormant_after_s: int  # 沉睡門檻（復活偵測）
    volume_window_floor: int  # 流量基線地板（bytes / interval）
    volume_spike_factor: float  # delta > baseline * factor → 暴量
    volume_learning_samples: int  # 學習期樣本數（之前不報暴量）
    # 告警去抖 / 升級
    cooldown_s: int  # 同 (pubkey, kind) 冷卻
    escalate_window_s: int
    escalate_count: int
    # 投遞（ntfy）；空 ntfy_url = 只落 log 不推播
    ntfy_url: str | None
    ntfy_token: str | None
    ntfy_timeout_s: int
    # 保留
    retention_days: int
    # pubkey→callsign 豐富化（B 方案）：扁平檔路徑；空=停用，只顯 pubkey
    peermap_path: str


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def from_env() -> Config:
    return Config(
        iface=os.environ.get("WGMON_IFACE", "wg0"),
        poll_interval_s=_int("WGMON_POLL_S", 20),
        db_path=os.environ.get("WGMON_DB", "/data/wg-monitor.db"),
        offline_after_s=_int("WGMON_OFFLINE_AFTER_S", 180),
        oscillation_window_s=_int("WGMON_OSC_WINDOW_S", 180),
        dormant_after_s=_int("WGMON_DORMANT_AFTER_S", 86400),
        volume_window_floor=_int("WGMON_VOL_FLOOR", 10 * 1024 * 1024),  # 10 MiB
        volume_spike_factor=_float("WGMON_VOL_FACTOR", 8.0),
        volume_learning_samples=_int("WGMON_VOL_LEARN", 20),
        cooldown_s=_int("WGMON_COOLDOWN_S", 600),
        escalate_window_s=_int("WGMON_ESCALATE_WINDOW_S", 3600),
        escalate_count=_int("WGMON_ESCALATE_COUNT", 3),
        ntfy_url=os.environ.get("WGMON_NTFY_URL") or None,
        ntfy_token=os.environ.get("WGMON_NTFY_TOKEN") or None,
        ntfy_timeout_s=_int("WGMON_NTFY_TIMEOUT_S", 5),
        retention_days=_int("WGMON_RETENTION_DAYS", 30),
        peermap_path=os.environ.get("WGMON_PEERMAP", ""),
    )
