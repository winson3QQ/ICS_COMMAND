"""#280 H — 安全監控告警。

把高訊號的鑑權異常（帳號鎖定 = 持續爆破、第二因子失敗 = 盜 PIN/裝置不符、撤銷證再用）
集中成結構化 `SECURITY_ALERT` log；可選 webhook 推播（`ICS_SECURITY_WEBHOOK_URL`）。

刻意只用**帳號/事件**為主體（非來源 IP）——Docker port-publish 下真實 IP 不可靠（見
`deploy/perimeter/README`），但「某帳號被鎖/被試」與 IP 無關，照樣是可靠訊號。
同一主體短時間反覆觸發 → 升級 critical（持續性攻擊）。webhook 走背景緒不擋登入。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections import defaultdict, deque

import structlog

import core.config as config

log = structlog.get_logger()

# 升級門檻：同 (kind, subject) 在 window 秒內達 count 次 → critical
_ESCALATE_WINDOW = 3600
_ESCALATE_COUNT = 3
_events: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = threading.Lock()


def _bump(kind: str, subject: str) -> int:
    now = time.time()
    with _lock:
        dq = _events[(kind, subject)]
        while dq and dq[0] < now - _ESCALATE_WINDOW:
            dq.popleft()
        dq.append(now)
        return len(dq)


def security_alert(kind: str, summary: str, **detail) -> None:
    """發一筆安全告警：結構化 log（一律）+ webhook（若配置）。subject 取 detail.user。"""
    subject = str(detail.get("user") or detail.get("subject") or "-")
    count = _bump(kind, subject)
    severity = "critical" if count >= _ESCALATE_COUNT else "warning"
    log.warning("SECURITY_ALERT", kind=kind, msg=summary,
                severity=severity, count=count, detail=detail)
    if config.ICS_SECURITY_WEBHOOK_URL:
        # 背景緒 fire-and-forget，不擋登入路徑、不因 webhook 失敗影響鑑權
        threading.Thread(
            target=_post_webhook,
            args=(kind, summary, severity, count, detail),
            daemon=True,
        ).start()


def _post_webhook(kind: str, summary: str, severity: str, count: int, detail: dict) -> None:
    url = config.ICS_SECURITY_WEBHOOK_URL  # 部署層信任值（env），非使用者輸入
    try:
        body = json.dumps(
            {"kind": kind, "summary": summary, "severity": severity,
             "count": count, "detail": detail},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(  # nosec B310 - URL 由部署 env 提供（信任）
            url, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=3)  # nosec B310
    except Exception as e:  # noqa: BLE001 - webhook 失敗絕不可影響鑑權
        log.warning("security_webhook_failed", error=str(e))
