# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/security/test_suspend_all_sysadmin_floor.py — #375：suspend-all 不得達零 active sysadmin。

suspend-all 排除發起者本人 → 只要發起者仍是 active sysadmin，事後即 ≥1 active sysadmin（floor 保住）。
TOCTOU：發起者被並發 demote（role 改、session 仍 cached sysadmin）→ 鎖內 re-assert 擋下（409），
否則排除已非 sysadmin 的發起者、停掉最後 sysadmin 達零（#354 要防的自鎖）。
"""

from core.database import get_conn
from repositories.account_repo import get_all_accounts


def _admin(accounts):
    return next(a for a in accounts if a["username"] == "admin")


def test_suspend_all_keeps_initiator_active(client, auth):
    """suspend-all 排除發起者 → 事後發起者仍 active（≥1 active sysadmin floor）。"""
    r = client.post("/api/admin/suspend-all", json={"confirm": "SUSPEND_ALL"}, headers=auth)
    assert r.status_code == 200, r.text
    admin = _admin(get_all_accounts())
    assert (admin.get("status") or "active") == "active"


def test_suspend_all_blocked_when_initiator_demoted(client, auth):
    """#375 TOCTOU：發起者帳號被並發 demote（role→operator，session 仍 cached sysadmin）→ suspend-all
    的鎖內 re-assert 擋下（409），不排除已非 sysadmin 的發起者、不達零 sysadmin。"""
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET role='操作員', role_detail=NULL WHERE username='admin'")
        conn.commit()
    r = client.post("/api/admin/suspend-all", json={"confirm": "SUSPEND_ALL"}, headers=auth)
    assert r.status_code == 409, r.text
    # 確認沒有任何帳號被停（block 生效、不歸零）
    assert all((a.get("status") or "active") == "active" for a in get_all_accounts())
