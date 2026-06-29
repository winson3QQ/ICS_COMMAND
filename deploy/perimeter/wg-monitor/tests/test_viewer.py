# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""test_viewer.py — viewer 輔助函式 + HTML 跳脫（防注入，縱深）。"""

import time

import viewer
from detect import Detection
from store import Store


def test_ago():
    now = 1_000_000
    assert viewer._ago(0, now) == "—"
    assert viewer._ago(now - 30, now) == "30s 前"
    assert viewer._ago(now - 120, now) == "2m 前"
    assert viewer._ago(now - 7200, now) == "2h 前"


def test_bytes():
    assert viewer._bytes(512) == "512 B"
    assert viewer._bytes(2048) == "2.0 KiB"
    assert viewer._bytes(5 * 1024 * 1024) == "5.0 MiB"


def test_render_escapes_db_values(tmp_path):
    # endpoint/summary 雖為內部資料，仍須 html.escape（縱深，security-review 關注）。
    db = tmp_path / "v.db"
    s = Store(str(db))
    s.init()
    s.add_alert(
        int(time.time()),
        Detection("endpoint_oscillation", "PK", "critical", "<script>alert(1)</script>", {"x": "<b>"}),
        "critical",
    )
    s.close()
    viewer.DB = str(db)
    out = viewer.render()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "<script>alert(1)</script>" not in out  # 原始未跳脫不得出現


def test_render_empty_db_does_not_crash(tmp_path):
    db = tmp_path / "empty.db"
    Store(str(db)).init()
    viewer.DB = str(db)
    out = viewer.render()
    assert "尚無 peer" in out and "尚無告警" in out
