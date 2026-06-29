# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""test_detect.py — 偵測規則純邏輯測試。"""

import config as config_mod
from detect import PriorState, detect_peer, is_online, next_baseline
from wg_dump import PeerSample

CFG = config_mod.Config(
    iface="wg0",
    poll_interval_s=20,
    db_path=":memory:",
    offline_after_s=180,
    oscillation_window_s=180,
    dormant_after_s=86400,
    volume_window_floor=10 * 1024 * 1024,
    volume_spike_factor=8.0,
    volume_learning_samples=20,
    cooldown_s=600,
    escalate_window_s=3600,
    escalate_count=3,
    ntfy_url=None,
    ntfy_token=None,
    ntfy_timeout_s=5,
    retention_days=30,
)

NOW = 1_719_600_000


def _peer(pubkey="PK", ip="203.0.113.5", hs=NOW, rx=1000, tx=1000):
    return PeerSample(
        pubkey=pubkey,
        endpoint=f"{ip}:51820" if ip else None,
        endpoint_ip=ip,
        endpoint_port=51820,
        allowed_ips="10.13.13.2/32",
        last_handshake=hs,
        rx_bytes=rx,
        tx_bytes=tx,
    )


def _prior(**kw):
    base = dict(
        pubkey="PK",
        last_handshake=NOW,
        last_endpoint_ip="203.0.113.5",
        last_rx=1000,
        last_tx=1000,
        last_seen_ts=NOW,
        online=True,
        vol_baseline=0.0,
        vol_samples=0,
    )
    base.update(kw)
    return PriorState(**base)


def _kinds(dets):
    return {d.kind for d in dets}


# ── 主菜：endpoint 來回擺盪（重訪）→ critical ──
def test_oscillation_revisit_is_critical():
    # A→B→A：某 IP 被重訪 = cloned-key；序列長(3) > distinct(2)。
    cur = _peer(ip="203.0.113.5")
    recent = ["203.0.113.5", "198.51.100.9", "203.0.113.5"]
    dets = detect_peer(cur, _prior(), recent, NOW, CFG)
    osc = [d for d in dets if d.kind == "endpoint_oscillation"]
    assert len(osc) == 1
    assert osc[0].severity == "critical"
    assert set(osc[0].detail["endpoints"]) == {"203.0.113.5", "198.51.100.9"}


def test_single_roam_is_not_oscillation():
    # A→B 單次漫遊：2 個 distinct 但無重訪（序列==distinct）→ 不是並存，降為 endpoint_change/info。
    cur = _peer(ip="198.51.100.9")
    prior = _prior(online=True, last_endpoint_ip="203.0.113.5")
    dets = detect_peer(cur, prior, ["203.0.113.5", "198.51.100.9"], NOW, CFG)
    assert "endpoint_oscillation" not in _kinds(dets)
    assert "endpoint_change" in _kinds(dets)  # 漫遊 → info


def test_single_ip_no_oscillation():
    cur = _peer(ip="203.0.113.5")
    dets = detect_peer(cur, _prior(), ["203.0.113.5"], NOW, CFG)
    assert "endpoint_oscillation" not in _kinds(dets)


def test_oscillation_fires_even_on_first_seen():
    # 首見 peer（prior=None）仍應對重訪擺盪報（盜鑰不該因冷啟動被漏）。
    cur = _peer(ip="203.0.113.5")
    dets = detect_peer(cur, None, ["203.0.113.5", "198.51.100.9", "203.0.113.5"], NOW, CFG)
    assert "endpoint_oscillation" in _kinds(dets)


# ── 冷啟動安靜：首見不報 liveness/漫遊 ──
def test_first_seen_quiet_for_liveness():
    cur = _peer()
    dets = detect_peer(cur, None, ["203.0.113.5"], NOW, CFG)
    assert _kinds(dets) == set()  # 單一 IP + 首見 → 完全安靜


# ── liveness ──
def test_offline_transition():
    cur = _peer(hs=NOW - 1000)  # 握手老於 offline_after
    dets = detect_peer(cur, _prior(online=True), ["203.0.113.5"], NOW, CFG)
    assert "offline" in _kinds(dets)


def test_online_transition_plain():
    cur = _peer(hs=NOW)
    prior = _prior(online=False, last_handshake=NOW - 200, last_endpoint_ip="203.0.113.5")
    dets = detect_peer(cur, prior, ["203.0.113.5"], NOW, CFG)
    assert "online" in _kinds(dets)
    assert "dormant_reactivation" not in _kinds(dets)


# ── dormant 復活：離線超久 + 新 endpoint → warning ──
def test_dormant_reactivation_new_endpoint():
    cur = _peer(ip="198.51.100.9", hs=NOW)
    prior = _prior(
        online=False,
        last_handshake=NOW - 200000,  # 上次握手 > dormant_after（86400）以前
        last_endpoint_ip="203.0.113.5",
    )
    dets = detect_peer(cur, prior, ["198.51.100.9"], NOW, CFG)
    dr = [d for d in dets if d.kind == "dormant_reactivation"]
    assert len(dr) == 1 and dr[0].severity == "warning"


def test_dormant_same_endpoint_is_plain_online():
    # 沉睡很久但從「同一」endpoint 回來 → 視為一般上線，不報 dormant。
    cur = _peer(ip="203.0.113.5", hs=NOW)
    prior = _prior(online=False, last_handshake=NOW - 200000, last_endpoint_ip="203.0.113.5")
    assert "dormant_reactivation" not in _kinds(detect_peer(cur, prior, ["203.0.113.5"], NOW, CFG))


# ── 單純漫遊：info、不升級 ──
def test_roaming_endpoint_change_is_info():
    cur = _peer(ip="198.51.100.9")
    prior = _prior(online=True, last_endpoint_ip="203.0.113.5")
    dets = detect_peer(cur, prior, ["198.51.100.9"], NOW, CFG)  # 窗內只有新 IP（非並存）
    ec = [d for d in dets if d.kind == "endpoint_change"]
    assert len(ec) == 1 and ec[0].severity == "info"


# ── 流量暴量：學習期後才報 ──
def test_volume_spike_after_learning():
    cur = _peer(rx=1000 + 50 * 1024 * 1024, tx=1000)  # +50 MiB
    prior = _prior(vol_baseline=1_000_000.0, vol_samples=20, last_rx=1000, last_tx=1000)
    dets = detect_peer(cur, prior, ["203.0.113.5"], NOW, CFG)
    assert "volume_spike" in _kinds(dets)


def test_volume_spike_silent_during_learning():
    cur = _peer(rx=1000 + 50 * 1024 * 1024, tx=1000)
    prior = _prior(vol_baseline=1_000_000.0, vol_samples=5, last_rx=1000, last_tx=1000)  # 樣本不足
    assert "volume_spike" not in _kinds(detect_peer(cur, prior, ["203.0.113.5"], NOW, CFG))


def test_volume_counter_reset_no_false_spike():
    # counter reset（cur < prior）→ delta 夾到 0，不誤報。
    cur = _peer(rx=10, tx=10)
    prior = _prior(vol_baseline=1_000_000.0, vol_samples=30, last_rx=999_999, last_tx=999_999)
    assert "volume_spike" not in _kinds(detect_peer(cur, prior, ["203.0.113.5"], NOW, CFG))


# ── 輔助函式 ──
def test_is_online():
    assert is_online(NOW, NOW, 180) is True
    assert is_online(NOW - 1000, NOW, 180) is False
    assert is_online(0, NOW, 180) is False  # 從未握手


def test_next_baseline_ewma():
    prior = _prior(vol_baseline=1000.0, vol_samples=3, last_rx=0, last_tx=0)
    cur = _peer(rx=2000, tx=0)
    baseline, n = next_baseline(prior, cur, alpha=0.5)
    assert n == 4
    assert baseline == 0.5 * 1000.0 + 0.5 * 2000.0  # EWMA
