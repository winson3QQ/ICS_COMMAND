"""
unit/test_helpers.py — repositories/_helpers.py 純函式測試

不碰 DB，全部在記憶體中執行。
"""

import pytest

pytestmark = pytest.mark.unit


class TestHashPin:
    def test_correct_pin_verifies(self):
        from repositories._helpers import hash_pin, verify_pin
        h, s = hash_pin("1234")
        assert verify_pin("1234", h, s) is True

    def test_wrong_pin_fails(self):
        from repositories._helpers import hash_pin, verify_pin
        h, s = hash_pin("1234")
        assert verify_pin("0000", h, s) is False

    def test_same_pin_different_salt(self):
        from repositories._helpers import hash_pin
        h1, s1 = hash_pin("abcd")
        h2, s2 = hash_pin("abcd")
        # 每次產生不同 salt → hash 也不同
        assert s1 != s2
        assert h1 != h2

    def test_deterministic_with_same_salt(self):
        from repositories._helpers import hash_pin, verify_pin
        h, s = hash_pin("test")
        h2, _ = hash_pin("test", salt_hex=s)
        assert h == h2

    def test_empty_pin(self):
        from repositories._helpers import hash_pin, verify_pin
        h, s = hash_pin("")
        assert verify_pin("", h, s) is True
        assert verify_pin("x", h, s) is False


class TestNowUtc:
    def test_returns_iso_format(self):
        from repositories._helpers import now_utc
        t = now_utc()
        assert "T" in t
        assert t.endswith("Z")

    def test_monotonic(self):
        import time
        from repositories._helpers import now_utc
        t1 = now_utc()
        time.sleep(0.01)
        t2 = now_utc()
        assert t2 >= t1


class TestIsoUtc:
    def test_none_returns_none(self):
        from repositories._helpers import iso_utc
        assert iso_utc(None) is None

    def test_valid_iso_passthrough(self):
        from repositories._helpers import iso_utc
        t = "2026-04-23T10:00:00Z"
        assert iso_utc(t) == t

    def test_invalid_returns_original(self):
        from repositories._helpers import iso_utc
        # 不合法的格式不 crash，回傳原始值
        result = iso_utc("not-a-date")
        assert result is not None


class TestAddMinutes:
    def test_adds_positive_minutes(self):
        from repositories._helpers import add_minutes
        result = add_minutes("2026-04-23T10:00:00Z", 30)
        assert "10:30:00" in result or "T10:30" in result

    def test_adds_zero_minutes(self):
        from repositories._helpers import add_minutes
        t = "2026-04-23T10:00:00Z"
        result = add_minutes(t, 0)
        assert "10:00:00" in result

    def test_crosses_hour_boundary(self):
        from repositories._helpers import add_minutes
        result = add_minutes("2026-04-23T10:45:00Z", 20)
        assert "11:05:00" in result
