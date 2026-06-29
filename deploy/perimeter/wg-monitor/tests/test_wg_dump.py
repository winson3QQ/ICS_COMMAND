# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""test_wg_dump.py — parser 測試，重點：**丟棄含 server 私鑰的 interface 行**。"""

from wg_dump import parse_endpoint, parse_wg_dump

# 真實 `wg show wg0 dump` 形狀：第一行 interface（4 欄、含私鑰），其後 peer（8 欄）。
SAMPLE = (
    "SERVERPRIVKEYsecret00000000000000000=\tSERVERPUBKEY000000000000000000000000=\t51820\toff\n"
    "PEERpubkeyAAAAAAAAAAAAAAAAAAAAAAAAAA=\t(none)\t203.0.113.5:51820\t10.13.13.2/32\t1719600000\t12345\t67890\toff\n"
    "PEERpubkeyBBBBBBBBBBBBBBBBBBBBBBBBBB=\t(none)\t(none)\t10.13.13.3/32\t0\t0\t0\toff\n"
)


def test_drops_interface_line_with_private_key():
    samples = parse_wg_dump(SAMPLE)
    # 只有兩個 peer；interface 行（含私鑰）被濾掉。
    assert len(samples) == 2
    blob = repr(samples)
    assert "SERVERPRIVKEY" not in blob  # 私鑰絕不出現在解析結果
    assert "51820\toff" not in blob


def test_peer_fields_parsed():
    a, b = parse_wg_dump(SAMPLE)
    assert a.pubkey.startswith("PEERpubkeyA")
    assert a.endpoint_ip == "203.0.113.5"
    assert a.endpoint_port == 51820
    assert a.last_handshake == 1719600000
    assert a.rx_bytes == 12345
    assert a.tx_bytes == 67890
    # 從未握手的 peer：endpoint (none) → None、handshake 0。
    assert b.endpoint_ip is None
    assert b.last_handshake == 0


def test_empty_and_blank_lines():
    assert parse_wg_dump("") == []
    assert parse_wg_dump("\n\n") == []


def test_parse_endpoint_variants():
    assert parse_endpoint("(none)") == (None, None, None)
    assert parse_endpoint("1.2.3.4:51820") == ("1.2.3.4", 51820, "1.2.3.4:51820")
    ip, port, raw = parse_endpoint("[2001:db8::1]:51820")
    assert ip == "2001:db8::1" and port == 51820 and raw == "[2001:db8::1]:51820"
