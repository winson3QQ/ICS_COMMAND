# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""integration/test_audit_append_only.py — #348 GAP2：prod audit_log 引擎層 append-only

驗：prod（ensure_audit_append_only(True)）下 audit_log 的 UPDATE/DELETE 被觸發器 RAISE 擋下、
列保留；dev（False）下移除觸發器、可刪（開發期可 reset）。切換 prod→dev 觸發器確實移除。
"""

import sqlite3

import pytest

pytestmark = pytest.mark.integration


def _insert_audit():
    from repositories._helpers import audit

    audit("tester", None, "test_event", "x", "1", {})


def test_prod_blocks_update_and_delete(tmp_db):
    from core.database import ensure_audit_append_only, get_conn

    _insert_audit()
    ensure_audit_append_only(True)  # prod：建 append-only 觸發器
    with get_conn() as conn, pytest.raises(sqlite3.Error):
        conn.execute("UPDATE audit_log SET operator='hacker'")
    with get_conn() as conn, pytest.raises(sqlite3.Error):
        conn.execute("DELETE FROM audit_log")
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert n >= 1, "稽核列應被保留（append-only）"


def test_dev_allows_delete(tmp_db):
    from core.database import ensure_audit_append_only, get_conn

    _insert_audit()
    ensure_audit_append_only(False)  # dev：移除觸發器
    with get_conn() as conn:
        conn.execute("DELETE FROM audit_log")  # 不應 raise
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert n == 0


def test_toggle_prod_then_dev_removes_triggers(tmp_db):
    from core.database import ensure_audit_append_only, get_conn

    ensure_audit_append_only(True)
    ensure_audit_append_only(False)  # 切回 dev → 應移除觸發器
    _insert_audit()
    with get_conn() as conn:
        conn.execute("DELETE FROM audit_log")  # dev 後可刪、不 raise
