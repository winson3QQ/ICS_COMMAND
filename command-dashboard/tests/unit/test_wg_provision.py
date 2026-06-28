# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""unit/test_wg_provision.py — #434 ICS 端 WG peer 控制面（services/wg_provision）。

涵蓋：pubkey/allowed-ip 驗證防線、未配置/壞輸入 best-effort 回傳、佇列協定 round-trip（fake registrar
thread 讀請求檔驗格式 + 回結果）、撤除、逾時。
"""

from __future__ import annotations

import glob
import os
import threading
import time

import pytest

from core import config
from services import wg_provision

pytestmark = pytest.mark.unit

# 真實格式的 WireGuard pubkey（43 字 +「=」，取自 wg pubkey 輸出）。
GOOD_PUB = "QYmrHH0LAMWzAK+tWAfhYxJw5TizlgrSYyf0Lm+LTj0="


class TestValidation:
    def test_pubkey(self):
        assert wg_provision.is_valid_pubkey(GOOD_PUB)
        assert not wg_provision.is_valid_pubkey("NOT_A_KEY")
        assert not wg_provision.is_valid_pubkey("")
        assert not wg_provision.is_valid_pubkey(GOOD_PUB[:-1])  # 缺「=」

    def test_allowed_ip(self, monkeypatch):
        monkeypatch.setattr(config, "WG_SUBNET_PREFIX", "10.13.13.")
        assert wg_provision.is_valid_allowed_ip("10.13.13.50/32")
        assert not wg_provision.is_valid_allowed_ip("10.13.13.1/32")  # .1 保留（server）
        assert not wg_provision.is_valid_allowed_ip("10.13.13.0/32")  # .0 保留
        assert not wg_provision.is_valid_allowed_ip("10.99.0.1/32")  # 子網外（路由污染防線）
        assert not wg_provision.is_valid_allowed_ip("10.13.13.50")  # 缺 /32
        assert not wg_provision.is_valid_allowed_ip("10.13.13.50/24")  # 非 /32


class TestBestEffort:
    def test_not_configured(self, monkeypatch):
        monkeypatch.setattr(config, "WG_QUEUE_DIR", "")
        assert wg_provision.add_peer(GOOD_PUB, "10.13.13.50/32")["reason"] == "wg-not-configured"
        assert wg_provision.remove_peer(GOOD_PUB)["reason"] == "wg-not-configured"

    def test_bad_input_rejected_before_queue(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "WG_QUEUE_DIR", str(tmp_path))
        assert wg_provision.add_peer("BAD", "10.13.13.50/32")["reason"] == "bad-pubkey"
        assert wg_provision.add_peer(GOOD_PUB, "10.99.0.1/32")["reason"] == "bad-ip"
        # 壞輸入不應寫出任何請求檔
        assert not glob.glob(str(tmp_path / "requests" / "*.req"))

    def test_timeout_when_no_registrar(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "WG_QUEUE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "WG_PEER_TIMEOUT_S", 1.0)
        r = wg_provision.add_peer(GOOD_PUB, "10.13.13.50/32")
        assert r["ok"] is False and r["reason"] == "timeout"
        # 逾時應清掉沒人讀的請求檔
        assert not glob.glob(str(tmp_path / "requests" / "*.req"))


def _fake_registrar(qdir, response, captured):
    """模擬 ics-wg 容器：輪詢請求檔 → 記下內容 → 回結果檔 → 刪請求。"""
    req_dir = os.path.join(qdir, "requests")
    res_dir = os.path.join(qdir, "results")
    for _ in range(100):
        reqs = glob.glob(os.path.join(req_dir, "*.req"))
        if reqs:
            with open(reqs[0], encoding="ascii") as f:
                captured.append(f.read())
            rid = os.path.basename(reqs[0])[:-4]
            os.makedirs(res_dir, exist_ok=True)
            with open(os.path.join(res_dir, rid + ".res"), "w", encoding="ascii") as f:
                f.write(response)
            os.unlink(reqs[0])
            return
        time.sleep(0.02)


class TestRoundTrip:
    def test_add_peer_ok_and_format(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "WG_QUEUE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "WG_PEER_TIMEOUT_S", 5.0)
        os.makedirs(tmp_path / "requests", exist_ok=True)
        captured: list[str] = []
        t = threading.Thread(target=_fake_registrar, args=(str(tmp_path), "OK add 10.13.13.50/32", captured))
        t.start()
        r = wg_provision.add_peer(GOOD_PUB, "10.13.13.50/32", label="dev-a")
        t.join(timeout=5)
        assert r["ok"] is True and r["allowed_ip"] == "10.13.13.50/32"
        # 協定：4 行 pubkey/allowed_ip/op/label
        assert captured == [f"{GOOD_PUB}\n10.13.13.50/32\nadd\ndev-a\n"]

    def test_remove_peer_ok(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "WG_QUEUE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "WG_PEER_TIMEOUT_S", 5.0)
        os.makedirs(tmp_path / "requests", exist_ok=True)
        captured: list[str] = []
        t = threading.Thread(target=_fake_registrar, args=(str(tmp_path), "OK remove", captured))
        t.start()
        r = wg_provision.remove_peer(GOOD_PUB)
        t.join(timeout=5)
        assert r["ok"] is True and r["reason"] == "removed"
        assert captured == [f"{GOOD_PUB}\n\nremove\n\n"]

    def test_registrar_error_surfaced(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "WG_QUEUE_DIR", str(tmp_path))
        monkeypatch.setattr(config, "WG_PEER_TIMEOUT_S", 5.0)
        os.makedirs(tmp_path / "requests", exist_ok=True)
        captured: list[str] = []
        t = threading.Thread(target=_fake_registrar, args=(str(tmp_path), "ERR bad-ip", captured))
        t.start()
        r = wg_provision.add_peer(GOOD_PUB, "10.13.13.50/32")
        t.join(timeout=5)
        assert r["ok"] is False and "registrar:ERR bad-ip" in r["reason"]


class TestKeygen:
    def test_gen_keypair_format(self):
        priv, pub = wg_provision.gen_keypair()
        # base64 32 bytes → 44 字含「=」；pub 通過 wg pubkey 正則。
        assert len(priv) == 44 and priv.endswith("=")
        assert wg_provision.is_valid_pubkey(pub)

    def test_gen_keypair_unique(self):
        assert wg_provision.gen_keypair()[0] != wg_provision.gen_keypair()[0]

    def test_build_device_conf(self, monkeypatch):
        monkeypatch.setattr(config, "WG_SUBNET_PREFIX", "10.13.13.")
        priv, _ = wg_provision.gen_keypair()
        conf = wg_provision.build_device_conf(priv, "10.13.13.50/32", GOOD_PUB, "1.2.3.4:51820")
        assert f"PrivateKey = {priv}" in conf
        assert "Address = 10.13.13.50/32" in conf
        assert f"PublicKey = {GOOD_PUB}" in conf
        assert "Endpoint = 1.2.3.4:51820" in conf
        assert "AllowedIPs = 10.13.13.0/24" in conf  # 預設＝整個 VPN 子網
        assert "PersistentKeepalive = 25" in conf

    def test_qr_png(self):
        png = wg_provision.qr_png("[Interface]\nPrivateKey = x\n")
        assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic
        assert len(png) > 100
