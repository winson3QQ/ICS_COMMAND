# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""test_alert.py — 去抖 / 升級 / 一律落帳（不依賴投遞成功）。"""

from alert import AlertManager
from detect import Detection
from store import Store
from test_detect import CFG


def _store():
    s = Store(":memory:")
    s.init()
    return s


def _det(kind="volume_spike", pubkey="PK", severity="warning"):
    return Detection(kind=kind, pubkey=pubkey, severity=severity, summary="x", detail={})


def test_alert_logged_even_when_delivery_fails():
    s = _store()
    am = AlertManager(s, CFG, deliver=lambda cfg, sev, det: False)  # 投遞恆失敗
    assert am.handle(_det(), now=1000) is True
    rows = s.conn.execute("SELECT severity, delivered FROM alerts").fetchall()
    assert len(rows) == 1
    assert rows[0]["delivered"] == 0  # 投遞失敗 → 未標 delivered，但告警仍落帳


def test_delivery_success_marks_delivered():
    s = _store()
    am = AlertManager(s, CFG, deliver=lambda cfg, sev, det: True)
    am.handle(_det(), now=1000)
    assert s.conn.execute("SELECT delivered FROM alerts").fetchone()["delivered"] == 1


def test_cooldown_suppresses_repeat():
    s = _store()
    am = AlertManager(s, CFG, deliver=lambda *a: True)
    assert am.handle(_det(), now=1000) is True
    # cooldown_s=600：300 秒後同 (pubkey, kind) 被壓掉。
    assert am.handle(_det(), now=1300) is False
    # 超過 cooldown 後可再報。
    assert am.handle(_det(), now=1000 + CFG.cooldown_s + 1) is True
    assert s.conn.execute("SELECT COUNT(*) c FROM alerts").fetchone()["c"] == 2


def test_escalation_to_critical():
    s = _store()
    am = AlertManager(s, CFG, deliver=lambda *a: True)
    # escalate_count=3：每次間隔 > cooldown 以免被去抖壓掉；第 3 次升 critical。
    step = CFG.cooldown_s + 1
    am.handle(_det(severity="warning"), now=10_000)
    am.handle(_det(severity="warning"), now=10_000 + step)
    am.handle(_det(severity="warning"), now=10_000 + 2 * step)
    sevs = [r["severity"] for r in s.conn.execute("SELECT severity FROM alerts ORDER BY id").fetchall()]
    assert sevs == ["warning", "warning", "critical"]


def test_clock_jump_backward_does_not_suppress_forever():
    # 時鐘回跳：第一筆在 t=5000，之後 now 退到 4000（負 delta）→ 不可被 cooldown 永久壓制。
    s = _store()
    am = AlertManager(s, CFG, deliver=lambda *a: True)
    assert am.handle(_det(), now=5000) is True
    assert am.handle(_det(), now=4000) is True  # 負 delta → 放行（非靜默）
    assert s.conn.execute("SELECT COUNT(*) c FROM alerts").fetchone()["c"] == 2


def test_distinct_kinds_independent_cooldown():
    s = _store()
    am = AlertManager(s, CFG, deliver=lambda *a: True)
    assert am.handle(_det(kind="volume_spike"), now=1000) is True
    # 不同 kind 各自獨立冷卻，不互相壓。
    assert am.handle(_det(kind="endpoint_oscillation", severity="critical"), now=1000) is True
