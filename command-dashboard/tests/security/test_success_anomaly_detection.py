# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/security/test_success_anomaly_detection.py — #285 成功動作異常偵測

#280 H 既有告警只在失敗側。本擴充補成功面：可疑「成功動作」也發 SECURITY_ALERT。
鎖：
  - 敏感單筆動作（cert_bind/revoke、db_reset、批次停權…）→ 立即告警
  - 批次/異常量動作（account 建立/角色變更、大量刪除）→ 達閾值才告警（不洗版）
  - 正常動作（login 等）不告警
  - 偵測 best-effort：security_alert 爆炸不外溢、不擋 audit 主流程
  - audit() 中央 hook 確實接上 screen
"""

import pytest

from services import security_monitor as sm


@pytest.fixture(autouse=True)
def _reset_monitor_state():
    """模組級滑窗狀態跨測試殘留 → 每測前後清，避免污染/順序相依。"""
    sm._events.clear()
    sm._burst_events.clear()
    yield
    sm._events.clear()
    sm._burst_events.clear()


def _spy_alerts(monkeypatch) -> list[tuple]:
    """攔截 security_alert，收集 (kind, summary, detail)；不實際 log/webhook。"""
    calls: list[tuple] = []
    monkeypatch.setattr(sm, "security_alert", lambda kind, summary, **d: calls.append((kind, summary, d)))
    return calls


def test_sensitive_action_alerts_immediately(monkeypatch):
    calls = _spy_alerts(monkeypatch)
    sm.screen_audit_event("cert_bind", "admin", "account_certs", "1")
    assert any(c[0] == "cert_bind" for c in calls), calls
    assert calls[0][2]["user"] == "admin"
    assert calls[0][2]["target"] == "account_certs:1"


def test_normal_action_does_not_alert(monkeypatch):
    calls = _spy_alerts(monkeypatch)
    sm.screen_audit_event("login", "admin", "accounts", "admin")  # 不在任何集合
    sm.screen_audit_event("event_created", "op", "events", "5")
    assert calls == []


def test_burst_alerts_only_at_threshold(monkeypatch):
    calls = _spy_alerts(monkeypatch)
    for i in range(sm._BURST_THRESHOLD - 1):  # 未達閾值
        sm.screen_audit_event("account_created", "admin", "accounts", f"u{i}")
    assert not any(c[0] == "action_burst" for c in calls), "未達閾值不應告警"
    sm.screen_audit_event("account_created", "admin", "accounts", "uN")  # 剛好第 THRESHOLD 次
    bursts = [c for c in calls if c[0] == "action_burst"]
    assert len(bursts) == 1, calls
    assert bursts[0][2]["action"] == "account_created"
    assert bursts[0][2]["count"] == sm._BURST_THRESHOLD
    # 再多一次不重複洗版（只在剛跨閾值那次發）
    sm.screen_audit_event("account_created", "admin", "accounts", "uN2")
    assert len([c for c in calls if c[0] == "action_burst"]) == 1


def test_burst_counts_per_subject_isolated(monkeypatch):
    calls = _spy_alerts(monkeypatch)
    for i in range(sm._BURST_THRESHOLD):  # opA 達閾值
        sm.screen_audit_event("cop_entity_deleted", "opA", "cop_entities", f"x{i}")
    sm.screen_audit_event("cop_entity_deleted", "opB", "cop_entities", "y0")  # opB 才第 1 次
    bursts = [c for c in calls if c[0] == "action_burst"]
    assert len(bursts) == 1 and bursts[0][2]["user"] == "opA", calls


def test_screen_never_raises(monkeypatch):
    """security_alert 爆炸也不可外溢（偵測絕不擋主流程）。"""

    def boom(*a, **k):
        raise RuntimeError("alert boom")

    monkeypatch.setattr(sm, "security_alert", boom)
    sm.screen_audit_event("cert_bind", "admin", "account_certs", "1")  # 不應 raise


def test_audit_hook_wires_screen(monkeypatch, tmp_db):
    """整合：呼叫 _helpers.audit('cert_bind',…) → 中央 hook 觸發 screen → security_alert。"""
    calls = _spy_alerts(monkeypatch)
    from repositories._helpers import audit

    audit("admin", None, "cert_bind", "account_certs", "1", {})
    assert any(c[0] == "cert_bind" for c in calls), calls
