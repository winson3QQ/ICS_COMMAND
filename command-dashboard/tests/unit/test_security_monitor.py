# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
unit/test_security_monitor.py — #280 H：安全監控告警

涵蓋：結構化 log 一律發、webhook 只在配置時發（背景緒，等待）、同主體反覆 → 升級 critical、
webhook 失敗不拋。
"""

import time

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_events():
    from services import security_monitor as sm

    with sm._lock:
        sm._events.clear()
    yield


class TestEscalation:
    def test_first_is_warning_then_critical(self, monkeypatch):
        from services import security_monitor as sm

        seen = []
        monkeypatch.setattr(sm.log, "warning", lambda evt, **kw: seen.append(kw) if evt == "SECURITY_ALERT" else None)
        for _ in range(3):
            sm.security_alert("account_locked", "x", user="alice")
        sev = [s["severity"] for s in seen]
        assert sev == ["warning", "warning", "critical"]  # 第 3 次升級
        assert seen[-1]["count"] == 3

    def test_different_subjects_independent(self, monkeypatch):
        from services import security_monitor as sm

        seen = []
        monkeypatch.setattr(sm.log, "warning", lambda evt, **kw: seen.append(kw) if evt == "SECURITY_ALERT" else None)
        sm.security_alert("account_locked", "x", user="alice")
        sm.security_alert("account_locked", "x", user="bob")
        assert all(s["severity"] == "warning" for s in seen)  # 不同主體各自計數


class TestWebhook:
    def test_no_webhook_when_unconfigured(self, monkeypatch):
        import core.config as config
        from services import security_monitor as sm

        monkeypatch.setattr(config, "ICS_SECURITY_WEBHOOK_URL", "")
        called = []
        monkeypatch.setattr(sm, "_post_webhook", lambda *a: called.append(a))
        sm.security_alert("account_locked", "x", user="alice")
        assert called == []

    def test_webhook_fired_when_configured(self, monkeypatch):
        import core.config as config
        from services import security_monitor as sm

        monkeypatch.setattr(config, "ICS_SECURITY_WEBHOOK_URL", "http://collector.local/hook")
        posted = []
        monkeypatch.setattr(sm, "_post_webhook", lambda *a: posted.append(a))
        sm.security_alert("cert_factor_failed", "x", user="alice")
        # 背景緒：等它跑完
        for _ in range(50):
            if posted:
                break
            time.sleep(0.02)
        assert posted and posted[0][0] == "cert_factor_failed"

    def test_webhook_failure_does_not_raise(self, monkeypatch):
        import core.config as config
        from services import security_monitor as sm

        monkeypatch.setattr(config, "ICS_SECURITY_WEBHOOK_URL", "http://bad.invalid/hook")

        def _boom(*a, **k):
            raise OSError("connection refused")

        monkeypatch.setattr(sm.urllib.request, "urlopen", _boom)
        # 不應拋（直接呼 _post_webhook 同步路徑驗）
        sm._post_webhook("account_locked", "x", "warning", 1, {"user": "alice"})
