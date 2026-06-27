# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
integration/test_account_repo.py — 帳號 CRUD 與認證流程

直接操作 SQLite（透過 tmp_db fixture），不起 HTTP server。
"""

import pytest

pytestmark = pytest.mark.integration


class TestCreateAccount:
    def test_create_and_retrieve(self, tmp_db):
        from repositories.account_repo import create_account, get_all_accounts

        acc = create_account("alice", "5678", "操作員", "前進組", "operator")
        assert acc["username"] == "alice"
        accounts = get_all_accounts()
        found = next((a for a in accounts if a["username"] == "alice"), None)
        assert found is not None
        assert found["role"] == "操作員"

    def test_duplicate_username_raises(self, tmp_db):
        import sqlite3

        from repositories.account_repo import create_account

        create_account("bob", "1234", "操作員", "", "operator")
        with pytest.raises((sqlite3.IntegrityError, Exception)):
            create_account("bob", "9999", "操作員", "", "operator")

    def test_pin_not_stored_in_plaintext(self, tmp_db):
        import sqlite3

        from repositories.account_repo import create_account

        create_account("carol", "mypin", "操作員", "", "operator")
        conn = sqlite3.connect(str(tmp_db))
        row = conn.execute("SELECT pin_hash FROM accounts WHERE username='carol'").fetchone()
        conn.close()
        assert row[0] != "mypin"


class TestVerifyLogin:
    def test_correct_credentials(self, tmp_db):
        from repositories.account_repo import create_account, verify_login

        create_account("dave", "correct", "操作員", "", "operator")
        acc, reason = verify_login("dave", "correct")
        assert acc is not None and reason == "ok"
        assert acc["username"] == "dave"

    def test_wrong_pin(self, tmp_db):
        from repositories.account_repo import create_account, verify_login

        create_account("eve", "right", "操作員", "", "operator")
        acc, reason = verify_login("eve", "wrong")
        assert acc is None and reason == "bad_pin"

    def test_nonexistent_user(self, tmp_db):
        from repositories.account_repo import verify_login

        acc, reason = verify_login("ghost", "1234")
        assert acc is None and reason == "no_user"

    def test_pin_not_in_response(self, tmp_db):
        from repositories.account_repo import create_account, verify_login

        create_account("frank", "secret", "操作員", "", "operator")
        acc, reason = verify_login("frank", "secret")
        assert reason == "ok"
        assert "pin_hash" not in acc
        assert "pin_salt" not in acc


class TestEnsureDefaultAdmin:
    def test_creates_admin_if_empty(self, tmp_db):
        from repositories.account_repo import ensure_default_admin, get_all_accounts

        ensure_default_admin()
        accounts = get_all_accounts()
        admin = next((a for a in accounts if a["username"] == "admin"), None)
        assert admin is not None
        from auth.role_enum import ROLE_SYSADMIN_ZH

        assert admin["role"] == ROLE_SYSADMIN_ZH

    def test_idempotent(self, tmp_db):
        from repositories.account_repo import ensure_default_admin, get_all_accounts

        ensure_default_admin()
        ensure_default_admin()
        accounts = get_all_accounts()
        admin_rows = [a for a in accounts if a["username"] == "admin"]
        assert len(admin_rows) == 1

    def test_default_pin_works(self, tmp_db):
        from repositories.account_repo import ensure_default_admin, verify_login

        ensure_default_admin()
        acc, reason = verify_login("admin", "1234")
        assert acc is not None and reason == "ok"


class TestUpdateAccountStatus:
    def test_suspend_and_login_fails(self, tmp_db):
        from repositories.account_repo import create_account, update_account_status, verify_login

        create_account("target", "1234", "操作員", "", "operator")
        update_account_status("target", "suspended", operator="admin")
        acc, reason = verify_login("target", "1234")
        assert acc is None and reason == "suspended"


class TestLoginLockoutReset:
    def test_successful_login_resets_failed_count(self, tmp_db):
        """連續 3 次錯誤 PIN 後，正確登入 → failed_login_count 歸零、locked_until 清除"""
        from core.database import get_conn
        from repositories.account_repo import create_account, verify_login

        create_account("reset_user", "correct", "操作員", "", "operator")

        # 累積 3 次錯誤
        for _ in range(3):
            acc, reason = verify_login("reset_user", "wrong")
            assert acc is None
            assert reason == "bad_pin"

        # 正確登入
        acc, reason = verify_login("reset_user", "correct")
        assert acc is not None
        assert reason == "ok"

        # DB 確認歸零
        conn = get_conn()
        row = conn.execute(
            "SELECT failed_login_count, locked_until FROM accounts WHERE username=?",
            ("reset_user",),
        ).fetchone()
        conn.close()

        assert row["failed_login_count"] == 0
        assert row["locked_until"] is None
