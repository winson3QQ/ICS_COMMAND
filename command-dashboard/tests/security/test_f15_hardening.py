# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/security/test_f15_hardening.py — #348-F15 雜項硬化

- ① 登入計時旁路：帳號不存在時仍跑等量 PBKDF2（dummy hash_pin），消除帳號列舉 oracle。
- ② 備份檔權限：_atomic_write 寫出 0600（POSIX；Windows 略過）。
"""

import stat
import sys

import pytest


def test_no_user_invokes_dummy_pbkdf2(tmp_db, monkeypatch):
    """#348-F15①：verify_login 對不存在帳號仍呼叫 hash_pin（dummy 600k PBKDF2）→ 抹平零-KDF 旁路。
    這是 **call-presence 守門**（防誰刪掉那行回歸），**非計時證明**；legacy-100k 殘留見 verify_login
    註解（fresh 佈署無此類帳號）。stub 攔 hash_pin 證明被呼叫，不實跑 600k 以保測試快速。"""
    from repositories import account_repo

    called: list[int] = []
    monkeypatch.setattr(account_repo, "hash_pin", lambda *a, **k: (called.append(1), ("h", "s"))[1])

    acc, reason = account_repo.verify_login("ghost-nonexistent-xyz", "1234")
    assert acc is None and reason == "no_user"
    assert called, "no_user 路徑必須跑 dummy PBKDF2（hash_pin）以抹平計時旁路"


def test_existing_and_missing_user_both_401_path(tmp_db):
    """回歸：不存在與錯 PIN 皆回非成功（不靠回傳值洩漏存在性；計時由上一測試守）。"""
    from repositories.account_repo import create_account, verify_login

    create_account("realuser", "1234", "操作員", "", "operator")
    assert verify_login("realuser", "9999")[1] == "bad_pin"
    assert verify_login("ghost", "9999")[1] == "no_user"  # 兩者上層都映 401


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX 檔案權限語意；Windows os.chmod 僅唯讀位")
def test_atomic_write_sets_owner_only_perms(tmp_path):
    """#348-F15②：備份 atomic write 落 0600（即使 Fernet 加密，縮小本機讀取面）。"""
    from services.user_data_backup_service import _atomic_write

    p = tmp_path / "sub" / "backup.enc"
    _atomic_write(p, b"ciphertext-blob")
    assert p.read_bytes() == b"ciphertext-blob"
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"備份檔權限應 0600，實得 {oct(mode)}"
