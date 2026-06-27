# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
from __future__ import annotations

from auth.role_enum import ROLE_OPERATOR_ZH, ROLE_SYSADMIN_ZH
from repositories.account_repo import create_account


def _login(client, username: str = "admin", pin: str = "1234") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


class TestSysadminSessionGate:
    """#384：admin 後台僅靠 session 角色 RBAC 把關；舊的 X-Admin-PIN 已移除、後端不再讀（inert）。"""

    def test_sysadmin_session_passes(self, client):
        r = client.get("/api/admin/accounts", headers=_login(client))
        assert r.status_code == 200

    def test_no_session_is_rejected(self, client):
        # 無 session → 401（即使帶已廢的 X-Admin-PIN header 也一樣，header 已無作用）
        r = client.get("/api/admin/accounts", headers={"X-Admin-PIN": "1234"})
        assert r.status_code == 401

    def test_stray_admin_pin_header_is_ignored(self, client):
        # 帶任意 X-Admin-PIN 不影響結果——session 角色才是唯一 gate
        headers = _login(client)
        headers["X-Admin-PIN"] = "000000"
        r = client.get("/api/admin/accounts", headers=headers)
        assert r.status_code == 200


class TestLegacyAdminPinCleanup:
    """#384 review：升級殘留的 admin_pin* config 列開機一次性刪除，
    避免低權角色經 GET /api/config/{key} 讀到已廢的 PIN hash（縱深退步）。"""

    def test_cleanup_deletes_legacy_admin_pin_rows(self, client):
        from repositories.config_repo import cleanup_legacy_admin_pin_config, get_config, set_config

        set_config("admin_pin", '{"hash":"x","salt":"y"}', None)
        set_config("admin_pin_failed_count", "3", None)
        set_config("admin_pin_locked_until", "2030-01-01T00:00:00Z", None)
        assert get_config("admin_pin") is not None
        deleted = cleanup_legacy_admin_pin_config()
        assert deleted == 3
        assert get_config("admin_pin") is None
        assert get_config("admin_pin_failed_count") is None
        assert get_config("admin_pin_locked_until") is None

    def test_observer_cannot_read_admin_pin_after_cleanup(self, client):
        # 殘列已刪 → GET /api/config/admin_pin 回 value=None（無 hash 可洩）
        from repositories.config_repo import cleanup_legacy_admin_pin_config, set_config

        set_config("admin_pin", '{"hash":"secret","salt":"s"}', None)
        cleanup_legacy_admin_pin_config()
        r = client.get("/api/config/admin_pin", headers=_login(client))
        assert r.status_code == 200
        assert r.json()["value"] is None


class TestRoleGate:
    def _operator_headers(self, client):
        create_account("op_user", "5678", ROLE_OPERATOR_ZH, "Operator", "operator")
        return _login(client, "op_user", "5678")

    def test_operator_without_admin_pin_gets_403(self, client):
        r = client.get("/api/admin/accounts", headers=self._operator_headers(client))
        assert r.status_code == 403

    def test_operator_with_admin_pin_still_gets_403(self, client):
        headers = self._operator_headers(client)
        headers["X-Admin-PIN"] = "1234"
        r = client.get("/api/admin/accounts", headers=headers)
        assert r.status_code == 403

    def test_operator_cannot_escalate_own_role(self, client):
        headers = self._operator_headers(client)
        r = client.put(
            "/api/admin/accounts/op_user/role",
            headers=headers,
            json={"role": "系統管理員", "role_detail": "sysadmin"},
        )
        assert r.status_code == 403


class TestAdminBoundary:
    def test_delete_nonexistent_account_returns_404(self, client):
        r = client.delete("/api/admin/accounts/ghost_user", headers=_login(client))
        assert r.status_code == 404


# ── RT-L4（#153）：audit-log limit 服務端 clamp ──────────────────────────────


def _audit_status(client):
    return client.get("/api/admin/audit-log?limit=999999", headers=_login(client))


class TestAuditLogLimitClamp:
    def test_huge_limit_clamped_not_error(self, client):
        # ?limit=999999 不該全控 → 服務端 clamp 1000；端點正常回 list（非 500/慢查詢爆）。
        r = _audit_status(client)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_negative_limit_clamped_to_zero(self, client):
        # 負 limit 在 SQLite `LIMIT -1` 會變「無上限」破口；clamp max(0,..) → 回空 list。
        r = client.get("/api/admin/audit-log?limit=-1", headers=_login(client))
        assert r.status_code == 200
        assert r.json() == []


# ── OP-1（#153）：suspend-all 不自鎖 + 強制確認字串 ─────────────────────────


def _status(username: str):
    from core.database import get_conn

    with get_conn() as conn:
        row = conn.execute("SELECT status FROM accounts WHERE username=?", (username,)).fetchone()
    return row["status"] if row else None


class TestSuspendAllSelfLock:
    def test_suspend_all_requires_confirm_string(self, client):
        # 後端強制 confirm（不依賴前端 dialog）；缺 / 錯 → 422，且不執行停權。
        h = _login(client)
        assert client.post("/api/admin/suspend-all", json={}, headers=h).status_code == 422
        assert client.post("/api/admin/suspend-all", json={"confirm": "x"}, headers=h).status_code == 422
        assert _status("admin") == "active"  # 沒被執行

    def test_suspend_all_excludes_operator_self(self, client):
        create_account("victim_op", "1234", ROLE_OPERATOR_ZH, "Victim", "operator")
        h = _login(client)  # admin = 發起者
        r = client.post("/api/admin/suspend-all", json={"confirm": "SUSPEND_ALL"}, headers=h)
        assert r.status_code == 200, r.text
        assert _status("admin") == "active"  # 發起者沒被自鎖
        assert _status("victim_op") == "suspended"  # 其他人被停權
        # 發起者 session 仍可操作 admin API（沒進「需主機 shell 救」狀態）
        assert client.get("/api/admin/accounts", headers=h).status_code == 200


# ── #354：最後一個 active sysadmin 不得被降級/停用/封存（單筆版自鎖防呆）──────


class TestLastSysadminGuard:
    """#354：補上 suspend-all（#153）已有、但單筆 role/status/delete 漏掉的同一條守門。
    只在「會把 active sysadmin 數歸零」那一刻擋下，回 409；多 sysadmin 時不受影響。"""

    def test_last_sysadmin_cannot_self_demote_role(self, client):
        r = client.put(
            "/api/admin/accounts/admin/role",
            headers=_login(client),
            json={"role": ROLE_OPERATOR_ZH, "role_detail": "operator"},
        )
        assert r.status_code == 409, r.text
        assert _status("admin") == "active"  # 未被改動

    def test_last_sysadmin_cannot_self_suspend(self, client):
        r = client.put(
            "/api/admin/accounts/admin/status",
            headers=_login(client),
            json={"status": "suspended"},
        )
        assert r.status_code == 409, r.text
        assert _status("admin") == "active"

    def test_last_sysadmin_cannot_self_archive(self, client):
        r = client.delete("/api/admin/accounts/admin", headers=_login(client))
        assert r.status_code == 409, r.text
        assert _status("admin") == "active"  # 未被 soft-delete

    def test_two_sysadmins_demote_one_allowed_then_last_blocked(self, client):
        create_account("admin2", "5678", ROLE_SYSADMIN_ZH, "Admin2", "sysadmin")
        h = _login(client)
        # 兩 sysadmin，降其一 → 允許
        r1 = client.put(
            "/api/admin/accounts/admin2/role",
            headers=h,
            json={"role": ROLE_OPERATOR_ZH, "role_detail": "operator"},
        )
        assert r1.status_code == 200, r1.text
        # 只剩 admin 一個 sysadmin，再降 → 擋
        r2 = client.put(
            "/api/admin/accounts/admin/role",
            headers=h,
            json={"role": ROLE_OPERATOR_ZH, "role_detail": "operator"},
        )
        assert r2.status_code == 409, r2.text

    def test_concurrent_demote_two_sysadmins_lock_keeps_one(self, client, monkeypatch):
        """#369 TOCTOU：兩個 sysadmin 並發降級 → 鎖序列化臨界區 + 守門 → 恰一成功一 409，
        不歸零。用 monkeypatch 在第一筆角色變更提交處塞 event，逼 T1 持鎖期間 T2 必須等鎖，
        證明序列化非靠時序運氣。無鎖時 T2 的守門會看到「還有 2 個」而放行 → 兩者皆 200 → 歸零，
        本測試的 200/409 + 至少一存活斷言即失敗。"""
        import threading
        import time

        from auth.role_enum import ROLE_SYSADMIN, role_zh_to_en
        from repositories import account_repo
        from routers import admin as admin_router

        create_account("admin2", "5678", ROLE_SYSADMIN_ZH, "Admin2", "sysadmin")
        h = _login(client)  # 共 2 個 sysadmin：admin + admin2

        real_update = account_repo.update_account_role
        first_inside = threading.Event()
        release = threading.Event()
        seen: list[str] = []

        def slow_update(username, *a, **k):
            seen.append(username)
            if len(seen) == 1:  # 第一筆：卡在臨界區內（持鎖），逼第二筆等鎖
                first_inside.set()
                release.wait(timeout=5)
            return real_update(username, *a, **k)

        monkeypatch.setattr(admin_router, "update_account_role", slow_update)

        results: dict[str, int] = {}

        def demote(u):
            r = client.put(
                f"/api/admin/accounts/{u}/role",
                headers=h,
                json={"role": ROLE_OPERATOR_ZH, "role_detail": "operator"},
            )
            results[u] = r.status_code

        t1 = threading.Thread(target=demote, args=("admin2",))
        t2 = threading.Thread(target=demote, args=("admin",))
        t1.start()
        assert first_inside.wait(timeout=5), "T1 應已進入臨界區"
        t2.start()
        time.sleep(0.3)  # 讓 T2 抵達鎖；持鎖期間不該完成
        assert "admin" not in results, "T2 不應在 T1 持鎖期間完成（序列化證明）"
        release.set()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert results.get("admin2") == 200, results
        assert results.get("admin") == 409, results  # 降到剩一個 → 守門擋下
        remaining = [
            a
            for a in account_repo.get_all_accounts()
            if (a.get("status") or "active") == "active"
            and role_zh_to_en(a.get("role"), a.get("role_detail")) == ROLE_SYSADMIN
        ]
        assert len(remaining) >= 1, f"並發降級後不得歸零 sysadmin，剩 {len(remaining)}"

    def test_role_change_keeping_sysadmin_not_blocked(self, client):
        # 新角色仍是 sysadmin（will_remain_sysadmin）→ 不減少 active sysadmin 數 → 放行
        r = client.put(
            "/api/admin/accounts/admin/role",
            headers=_login(client),
            json={"role": ROLE_SYSADMIN_ZH, "role_detail": "sysadmin"},
        )
        assert r.status_code == 200, r.text

    def test_guard_does_not_block_non_sysadmin_target(self, client):
        # 即使僅一個 sysadmin，停用非 sysadmin 帳號不受守門影響（不誤傷）
        create_account("op_user", "5678", ROLE_OPERATOR_ZH, "Operator", "operator")
        r = client.put(
            "/api/admin/accounts/op_user/status",
            headers=_login(client),
            json={"status": "suspended"},
        )
        assert r.status_code == 200, r.text
        assert _status("op_user") == "suspended"
