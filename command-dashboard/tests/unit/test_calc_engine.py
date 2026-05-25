"""
unit/test_calc_engine.py — calc_engine.py 純函式測試

所有測試不依賴 DB、不依賴時間（盡量），快速可重複執行。
"""

import pytest
from datetime import datetime, timezone, timedelta

pytestmark = pytest.mark.unit


def _iso(minutes_ago: float) -> str:
    """產生 N 分鐘前的 ISO 8601 UTC 字串"""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class TestFreshness:
    def test_recent_is_ok(self):
        from calc_engine import freshness
        t = _iso(1)  # 1 分鐘前
        r = freshness(t, "medical")
        assert r["level"] == "ok"

    def test_warn_boundary(self):
        from calc_engine import freshness
        t = _iso(7)  # 7 分鐘前，預設 warn_min=5
        r = freshness(t, "medical")
        assert r["level"] in ("warn", "crit", "lkp")

    def test_crit_boundary(self):
        from calc_engine import freshness
        t = _iso(16)  # 16 分鐘前，預設 crit_min=15
        r = freshness(t, "medical")
        assert r["level"] in ("crit", "lkp")

    def test_lkp_very_old(self):
        from calc_engine import freshness
        t = _iso(30)  # 30 分鐘前，必定 LKP
        r = freshness(t, "medical")
        assert r["level"] == "lkp"

    def test_minutes_field_is_positive(self):
        from calc_engine import freshness
        t = _iso(5)
        r = freshness(t, "shelter")
        assert r["minutes"] > 0

    def test_invalid_time_returns_lkp(self):
        from calc_engine import freshness
        r = freshness("not-a-time", "medical")
        # 解析失敗回傳 999 分鐘 → lkp
        assert r["level"] == "lkp"


class TestMedicalPressureIndex:
    def test_no_data_returns_zero(self):
        from calc_engine import medical_pressure_index
        r = medical_pressure_index(None, None, None, None)
        assert r["index"] == 0.0
        assert r["level"] in ("normal", "warning", "critical")

    def test_full_beds_raises_pressure(self):
        from calc_engine import medical_pressure_index
        medical = {"waiting_count": 10, "bed_used": 19, "bed_total": 20}
        r = medical_pressure_index(medical, None, None, None)
        # waiting=10, remaining=1 → wait_pressure=10
        assert r["index"] > 5

    def test_empty_beds_no_waiting_low_pressure(self):
        from calc_engine import medical_pressure_index
        medical = {"waiting_count": 0, "bed_used": 0, "bed_total": 20}
        r = medical_pressure_index(medical, None, None, None)
        assert r["index"] == 0.0

    def test_components_present(self):
        from calc_engine import medical_pressure_index
        r = medical_pressure_index(None, None, None, None)
        assert "components" in r
        assert "waiting" in r["components"]

    def test_forward_casualties_add_pressure(self):
        from calc_engine import medical_pressure_index
        forward = {"casualties_red": 5, "extra": {}}
        r = medical_pressure_index(None, None, forward, None)
        # 5 × 0.8 = 4.0
        assert r["components"]["forward"] == pytest.approx(4.0)


class TestTrend:
    def test_insufficient_data(self):
        from calc_engine import trend
        r = trend([], "bed_used")
        assert r["confidence"] == "insufficient"
        assert r["rate"] is None

    def test_upward_trend(self):
        from calc_engine import trend
        # 最新在前，10分鐘內從5增到15
        snaps = [
            {"snapshot_time": _iso(0), "bed_used": 15},
            {"snapshot_time": _iso(10), "bed_used": 5},
        ]
        r = trend(snaps, "bed_used")
        assert r["direction"] == "up"
        assert r["rate"] > 0

    def test_flat_trend(self):
        from calc_engine import trend
        snaps = [
            {"snapshot_time": _iso(0), "bed_used": 10},
            {"snapshot_time": _iso(10), "bed_used": 10},
        ]
        r = trend(snaps, "bed_used")
        assert r["direction"] == "flat"


class TestMinutesAgo:
    def test_recent(self):
        from calc_engine import minutes_ago
        t = _iso(5)
        m = minutes_ago(t)
        assert 4.5 < m < 5.5

    def test_invalid_returns_999(self):
        from calc_engine import minutes_ago
        assert minutes_ago("garbage") == 999.0
