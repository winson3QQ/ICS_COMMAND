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
    log.warning("SECURITY_ALERT", kind=kind, msg=summary, severity=severity, count=count, detail=detail)
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
            {"kind": kind, "summary": summary, "severity": severity, "count": count, "detail": detail},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(  # nosec B310 - URL 由部署 env 提供（信任）
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)  # nosec B310
    except Exception as e:  # noqa: BLE001 - webhook 失敗絕不可影響鑑權
        log.warning("security_webhook_failed", error=str(e))


# ── #285：成功動作異常偵測（接 _helpers.audit() 中央 hook）──────────────────────
# #280 H 既有告警只在「失敗側」（帳號鎖定、第二因子失敗）。紅隊缺口：**成功但可疑**的動作
# 不會自己喊怪（bypass 在補之前是條正常 login_success）。本擴充補成功面：主體一律取 operator
# （IP 不可靠，#280）。時段/異常來源規則待 #280 真實 IP 還原，未納本批。

# 單筆即告警：高權／罕見／破壞性成功動作（cert 生命週期含「新裝置授權」訊號＝#285 bullet 3 收斂）。
_SENSITIVE_ACTIONS = {
    "cert_bind": "裝置憑證綁定（新裝置授權／盜 PIN+新裝置訊號）",
    "cert_revoke": "裝置憑證撤銷",
    "db_reset": "資料庫重置（破壞性）",
    "exercise_reset": "演習重置（破壞性）",
    "all_accounts_suspended": "批次停權所有帳號",
    "pi_node_deleted": "Pi 節點刪除",
}

# 達閾值才告警：單筆正常、短時間多次才異常（批次帳號操作 / 大量刪除）。
_BURST_ACTIONS = {
    "account_created",
    "account_status_updated",
    "account_role_updated",
    "cop_entity_deleted",
    "exercise_deleted",
}
_BURST_WINDOW = 300  # 秒
_BURST_THRESHOLD = 5  # 同 operator 同類動作達此數/window → 異常量告警
_burst_events: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _burst_bump(action_type: str, subject: str) -> int:
    now = time.time()
    with _lock:
        dq = _burst_events[(action_type, subject)]
        while dq and dq[0] < now - _BURST_WINDOW:
            dq.popleft()
        dq.append(now)
        return len(dq)


def screen_audit_event(action_type: str, operator: str, target_table: str, target_id: str) -> None:
    """#285：每筆 audit 寫入後由 `_helpers.audit()` 呼叫，對可疑「成功動作」發 SECURITY_ALERT。

    best-effort：**絕不 raise**（偵測失敗不可擋 audit／主流程）。同主體反覆觸發由
    `security_alert` 內建 `_bump` 升 critical（含大量 cert 撤銷）。
    """
    try:
        subject = operator or "-"
        if action_type in _SENSITIVE_ACTIONS:
            security_alert(
                action_type,
                _SENSITIVE_ACTIONS[action_type],
                user=subject,
                target=f"{target_table}:{target_id}",
            )
        if action_type in _BURST_ACTIONS:
            n = _burst_bump(action_type, subject)
            if n == _BURST_THRESHOLD:  # 剛跨閾值發一次（避免每筆洗版；更多次由 _bump 升 critical）
                security_alert(
                    "action_burst",
                    f"短時間多次 {action_type}（{n} 次/{_BURST_WINDOW}s，疑批次/異常量操作）",
                    user=subject,
                    action=action_type,
                    count=n,
                )
    except Exception:  # noqa: BLE001 - 偵測絕不可影響 audit/主流程
        log.warning("security_screen_failed", action_type=action_type, exc_info=True)
