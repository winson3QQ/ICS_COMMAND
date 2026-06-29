# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""peermap.py — pubkey→callsign 輕耦合豐富化（#447 / B 方案）。

doctrine：監測**核心偵測零依賴 ICS**；callsign 純為顯示加值、有 fallback。
- 契約 = 一個扁平檔（每行 `<pubkey>\\t<callsign>`），由 WG 平面的 wg-peer-registrar 產（發證時 ICS
  已把 callsign 當 label 送進 WG 佇列 → registrar 落檔）。**監測只唯讀讀檔，不碰 ICS 的 DB/API/code。**
- 檔不在 / 壞 / 缺某 pubkey → 回 None（退回只顯 pubkey）。單向、無 runtime 依賴。
"""

from __future__ import annotations


def load_map(path: str) -> dict[str, str]:
    """讀 pubkey→callsign 扁平檔。空 path / 讀不到 / 畸形 → 回 {}（不丟例外）。"""
    if not path:
        return {}
    out: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line or "\t" not in line:
                    continue
                pubkey, callsign = line.split("\t", 1)
                pubkey, callsign = pubkey.strip(), callsign.strip()
                if pubkey and callsign:
                    out[pubkey] = callsign
    except (OSError, UnicodeError):  # 讀不到 / 非 UTF-8 / 二進位 → 退回 {}（豐富化絕不可崩 viewer/collector）
        return {}
    return out
