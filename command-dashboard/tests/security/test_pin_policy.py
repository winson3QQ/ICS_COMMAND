"""tests/security/test_pin_policy.py — #348-F5 P1 PIN/密語強度策略

鎖 validate_pin_strength（NIST 800-63B 對齊）：min6/max128、拒全同/連續/常見/==帳號名、
接受數字 PIN 與長密語。登入不溯及（只驗 hash）由 integration 測。
"""

import pytest
from fastapi import HTTPException

from core.pin_policy import MAX_LEN, validate_pin_strength


def _rejects(pin, username=None):
    with pytest.raises(HTTPException) as e:
        validate_pin_strength(pin, username)
    assert e.value.status_code == 422


class TestPinPolicy:
    def test_accepts_strong_pin_and_passphrase(self):
        validate_pin_strength("739104")  # 6 位數字 PIN（非序列/全同/常見）
        validate_pin_strength("correct-horse-7")  # 長密語
        validate_pin_strength("X9k2mq")  # 混合

    def test_rejects_too_short(self):
        _rejects("12")
        _rejects("12345")  # 5 < 6

    def test_rejects_too_long(self):
        _rejects("a" * (MAX_LEN + 1))

    def test_rejects_all_same_char(self):
        _rejects("000000")
        _rejects("aaaaaa")

    def test_rejects_sequential(self):
        _rejects("123456")
        _rejects("654321")
        _rejects("abcdef")

    def test_rejects_common_weak(self):
        _rejects("password")
        _rejects("qwerty")
        _rejects("PASSWORD")  # 不分大小寫

    def test_rejects_equals_username(self):
        _rejects("alice7", username="alice7")
        _rejects("ALICE7", username="alice7")  # 不分大小寫

    def test_non_str_rejected(self):
        _rejects(None)


def test_existing_short_pin_still_logs_in(client):
    """無回歸守門（#348-F5 P1 不溯及）：策略前建立的短 PIN（repo-level create_account 繞過
    validator，模擬既有帳號）仍可登入——登入只驗 hash、不套強度策略。防未來誤把策略加到登入路徑。"""
    from repositories.account_repo import create_account

    create_account("legacy_short", "1234", "操作員", "", "operator")  # 4 位，策略前
    r = client.post("/api/auth/login", json={"username": "legacy_short", "pin": "1234"})
    assert r.status_code == 200, r.text  # 既有短 PIN 不被新策略鎖在門外
