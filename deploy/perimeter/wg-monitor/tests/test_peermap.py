# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""test_peermap.py — pubkey→callsign 扁平檔解析 + 監測豐富化（B 方案）。"""

import dataclasses

from monitor import run_once
from peermap import load_map
from store import Store
from test_detect import CFG
from test_integration import _dump  # 重用 dump 產生器


def test_load_map_basic(tmp_path):
    p = tmp_path / "m.tsv"
    p.write_text("PK1\tBRAVO-1\nPK2\tALPHA-2\n", encoding="utf-8")
    m = load_map(str(p))
    assert m == {"PK1": "BRAVO-1", "PK2": "ALPHA-2"}


def test_load_map_empty_path_and_missing():
    assert load_map("") == {}
    assert load_map("/no/such/file.tsv") == {}


def test_load_map_skips_malformed(tmp_path):
    p = tmp_path / "m.tsv"
    p.write_text("PK1\tBRAVO-1\n沒有tab的行\n\nPK2\t\n\tONLYCS\n", encoding="utf-8")
    # 無 tab / 空 callsign / 空 pubkey 行一律跳過。
    assert load_map(str(p)) == {"PK1": "BRAVO-1"}


def test_run_once_enriches_detail_with_callsign(tmp_path):
    # peer-map 命中 → detection.detail 帶 callsign（流向 events/alerts/ntfy）。
    pk = "PEERpubkeyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    mp = tmp_path / "m.tsv"
    mp.write_text(f"{pk}\tBRAVO-1\n", encoding="utf-8")
    cfg = dataclasses.replace(CFG, peermap_path=str(mp))

    s = Store(":memory:")
    s.init()
    t0 = 1_719_600_000
    run_once(s, cfg, t0, _dump("203.0.113.5", t0))
    run_once(s, cfg, t0 + 30, _dump("198.51.100.9", t0 + 30))
    run_once(s, cfg, t0 + 60, _dump("203.0.113.5", t0 + 60))  # 重訪 → oscillation

    import json

    detail = s.conn.execute(
        "SELECT detail FROM events WHERE kind='endpoint_oscillation' ORDER BY id DESC LIMIT 1"
    ).fetchone()[0]
    assert json.loads(detail).get("callsign") == "BRAVO-1"


def test_run_once_no_peermap_no_callsign(tmp_path):
    # 無 peer-map（預設）→ detail 不含 callsign（退回只顯 pubkey）。
    s = Store(":memory:")
    s.init()
    t0 = 1_719_600_000
    run_once(s, CFG, t0, _dump("203.0.113.5", t0))
    run_once(s, CFG, t0 + 30, _dump("198.51.100.9", t0 + 30))
    run_once(s, CFG, t0 + 60, _dump("203.0.113.5", t0 + 60))

    import json

    rows = s.conn.execute("SELECT detail FROM events").fetchall()
    assert all("callsign" not in json.loads(r[0]) for r in rows)
