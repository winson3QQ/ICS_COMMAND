# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""test_integration.py — 端到端：cloned key（endpoint 並存）→ critical 告警落帳（#447 DoD）。"""

from alert import AlertManager
from monitor import run_once
from store import Store
from test_detect import CFG

PK = "PEERpubkeyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _dump(ip: str, hs: int) -> str:
    iface = "PRIV0000000000000000000000000000000000000=\tPUB000000000000000000000000000000000000000=\t51820\toff\n"
    peer = f"{PK}\t(none)\t{ip}:51820\t10.13.13.2/32\t{hs}\t1000\t1000\toff\n"
    return iface + peer


def _drain(am, alertable, ts):
    fired = False
    for d in alertable:
        if am.handle(d, ts):
            fired = True
    return fired


def test_cloned_key_oscillation_yields_critical_alert():
    s = Store(":memory:")
    s.init()
    am = AlertManager(s, CFG, deliver=lambda *a: True)

    t0 = 1_719_600_000
    # poll 1：來源 IP A（首見、單一 IP → 無告警）
    _drain(am, run_once(s, CFG, t0, _dump("203.0.113.5", t0)), t0)
    assert s.conn.execute("SELECT COUNT(*) c FROM alerts").fetchone()["c"] == 0

    # poll 2（+30s）：A→B 單次變更 → 視為漫遊，**還不該**升 critical（無重訪）。
    t1 = t0 + 30
    _drain(am, run_once(s, CFG, t1, _dump("198.51.100.9", t1)), t1)
    assert s.conn.execute("SELECT COUNT(*) c FROM alerts WHERE severity='critical'").fetchone()["c"] == 0

    # poll 3（+60s、仍在窗內）：B→A **重訪** → cloned-key 擺盪 → critical。
    t2 = t0 + 60
    fired = _drain(am, run_once(s, CFG, t2, _dump("203.0.113.5", t2)), t2)
    assert fired
    row = s.conn.execute("SELECT kind, severity FROM alerts ORDER BY id DESC LIMIT 1").fetchone()
    assert row["kind"] == "endpoint_oscillation"
    assert row["severity"] == "critical"

    kinds = {r["kind"] for r in s.conn.execute("SELECT kind FROM events").fetchall()}
    assert "endpoint_oscillation" in kinds


def test_server_private_key_never_persisted():
    # 端到端確認：interface 行的私鑰不進任何表。
    s = Store(":memory:")
    s.init()
    run_once(s, CFG, 1_719_600_000, _dump("203.0.113.5", 1_719_600_000))
    for table in ("peer_state", "endpoint_history", "events", "alerts"):
        blob = repr(s.conn.execute(f"SELECT * FROM {table}").fetchall())
        assert "PRIV0000" not in blob
