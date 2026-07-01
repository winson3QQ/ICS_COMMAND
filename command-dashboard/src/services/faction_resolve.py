# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""services/faction_resolve.py — #459：裝置 uid → faction 的單一 source（cop / chat ingest 共用）。

#344 起 client_faction 以穩定的 cert CN（= TAK username）為鍵，但 CoT / GeoChat 流裡只有裝置
uid。故解 faction 前必先經 client_identity（TAK subscriptions 權威對照，非 CoT 自報）把 uid
翻成 CN 再查分類。此「uid→CN→faction + fail-closed」為 security-critical 不變式，原本在
cop_service._resolve_faction 與 chat_service.ingest_chat 兩處逐字重複 → 收斂於此、兩處 delegate，
避免任一處被單獨修改而 desync 成 faction 洩漏（#459 review）。
"""

from __future__ import annotations

from repositories import client_faction_repo, client_identity_repo


def resolve_faction_for_uid(exercise_id: int | None, uid: str | None) -> str | None:
    """裝置 uid → 該場 faction；先經 client_identity 翻 CN 再查分類。翻不到 CN → None = fail-closed。

    **security（#344 security-review）**：uid 無 client_identity 對照 → **None**，**不得**退回用 CoT
    自報的 uid 當鍵。否則持證紅方把 producer uid 設成已知藍方 CN（低熵可枚舉）即可命中 CN-keyed
    分類、把紅軍標記偽裝成藍洩漏給指揮官。唯有經 client_identity 驗證翻出的 CN 才算數。
    """
    cn = client_identity_repo.get_username(uid)
    return client_faction_repo.get_faction(exercise_id, cn) if cn else None
