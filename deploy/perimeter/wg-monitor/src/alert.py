# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""alert.py — 告警去抖/升級 + ntfy 投遞（#447 Phase 4）。

紀律：
  - **一律先落 alerts 表**（append-only，權威真相），再嘗試投遞；投遞失敗不丟告警。
  - 去抖：同 (pubkey, kind) 在 cooldown 內只報一次。
  - 升級：同 (pubkey, kind) 在 escalate 窗內達 count 次 → 升 critical（持續性攻擊）。
  - 投遞：POST 自架 ntfy（best-effort、有 timeout、失敗只記不 raise）。空 ntfy_url = 只落 log。
  - iOS 背景喚醒繞不過 APNs（見 #447）；本層只負責「把告警送到 ntfy」，APNs 是 ntfy/Apple 那段。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from detect import Detection

_PRIORITY = {"critical": "urgent", "warning": "high", "info": "default"}
_TAGS = {"critical": "rotating_light", "warning": "warning", "info": "information_source"}


def _deliver_ntfy(cfg, severity: str, det: Detection) -> bool:
    """POST 一則告警到 ntfy。回 True=2xx。空 url / 任何例外 → False（best-effort）。"""
    if not cfg.ntfy_url:
        return False
    # Title 走 HTTP header → 限 ASCII（kind/severity 皆 ASCII）；中文摘要放 body（UTF-8）。
    title = f"[WG-{severity.upper()}] {det.kind}"
    body = det.summary + "\n" + json.dumps(det.detail, ensure_ascii=False)
    req = urllib.request.Request(cfg.ntfy_url, data=body.encode("utf-8"), method="POST")
    req.add_header("Title", title)
    req.add_header("Priority", _PRIORITY.get(severity, "default"))
    req.add_header("Tags", _TAGS.get(severity, "information_source"))
    if cfg.ntfy_token:
        req.add_header("Authorization", f"Bearer {cfg.ntfy_token}")
    try:
        with urllib.request.urlopen(req, timeout=cfg.ntfy_timeout_s) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


class AlertManager:
    def __init__(self, store, cfg, deliver=_deliver_ntfy):
        self.store = store
        self.cfg = cfg
        self._deliver = deliver  # 可注入假投遞以單測去抖/升級

    def handle(self, det: Detection, now: int) -> bool:
        """處理一筆 alertable detection。回 True=本次有發出新告警；False=被 cooldown 壓掉。"""
        # 去抖：cooldown 內同 (pubkey, kind) 不重報（事件已另記 events，不丟）。
        # 守 `0 <= delta`：系統時鐘回跳（NTP step / VM 暫停恢復）使 last > now → 負 delta，
        # 不可讓它把告警「永久靜默」到 wall-clock 追過舊時間戳為止 → 負 delta 一律放行。
        last = self.store.last_alert_ts(det.pubkey, det.kind)
        if last is not None and 0 <= (now - last) < self.cfg.cooldown_s:
            return False
        # 升級：窗內累計（含本次）達門檻 → critical。
        prior_count = self.store.recent_alert_count(det.pubkey, det.kind, now - self.cfg.escalate_window_s)
        severity = det.severity
        if (prior_count + 1) >= self.cfg.escalate_count and severity != "critical":
            severity = "critical"
        alert_id = self.store.add_alert(now, det, severity)
        if self._deliver(self.cfg, severity, det):
            self.store.mark_delivered(alert_id)
        return True
