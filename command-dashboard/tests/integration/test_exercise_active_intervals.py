# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/integration/test_exercise_active_intervals.py — #267 Slice 1：演習活躍時段 log。

set_active 開一筆區間、archive 關一筆；重開→多區間聯集；ts_in_active_window 判時間戳是否落在活躍區間
（含開放區間 deactivated_at NULL=仍活躍）。供後刀 scope 解析「CoT 時間戳算不算這場」。
"""

import pytest

from core.database import get_conn
from repositories import exercise_repo

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


def _mk():
    return exercise_repo.create_exercise({"name": "drill", "type": "ttx"})["id"]


def test_active_opens_interval_archive_closes():
    eid = _mk()
    exercise_repo.update_exercise_status(eid, "active", "admin")
    iv = exercise_repo.list_active_intervals(eid)
    assert len(iv) == 1 and iv[0]["activated_at"] and iv[0]["deactivated_at"] is None  # 開放中
    exercise_repo.update_exercise_status(eid, "archived", "admin")
    iv = exercise_repo.list_active_intervals(eid)
    assert len(iv) == 1 and iv[0]["deactivated_at"] is not None  # 已關閉


def test_reopen_makes_union_of_intervals():
    eid = _mk()
    exercise_repo.update_exercise_status(eid, "active", "admin")
    exercise_repo.update_exercise_status(eid, "archived", "admin")
    exercise_repo.update_exercise_status(eid, "active", "admin")  # 重開
    iv = exercise_repo.list_active_intervals(eid)
    assert len(iv) == 2  # 多區間聯集
    assert iv[1]["deactivated_at"] is None  # 第二段仍開放


def test_ts_in_closed_window():
    eid = _mk()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO exercise_active_intervals (exercise_id, activated_at, deactivated_at) VALUES (?,?,?)",
            (eid, "2026-06-28T10:00:00Z", "2026-06-28T11:00:00Z"),
        )
        conn.commit()
    assert exercise_repo.ts_in_active_window(eid, "2026-06-28T10:30:00Z") is True  # 區間內
    assert exercise_repo.ts_in_active_window(eid, "2026-06-28T09:30:00Z") is False  # 區間前
    assert exercise_repo.ts_in_active_window(eid, "2026-06-28T11:30:00Z") is False  # 區間後


def test_ts_in_open_interval_always_active():
    eid = _mk()
    exercise_repo.update_exercise_status(eid, "active", "admin")  # 開放區間（deactivated NULL）
    assert exercise_repo.ts_in_active_window(eid, "2099-01-01T00:00:00Z") is True  # 仍活躍 → 之後都算


def test_failed_set_active_opens_no_interval():
    """mutex 衝突（已有 active）→ set_active 失敗 → 不開區間。"""
    e1, e2 = _mk(), _mk()
    exercise_repo.update_exercise_status(e1, "active", "admin")
    with pytest.raises(ValueError):
        exercise_repo.update_exercise_status(e2, "active", "admin")  # 撞 mutex
    assert exercise_repo.list_active_intervals(e2) == []  # e2 無區間
