"""
tests/unit/test_cop_track_wiring.py — P2-06a（#120）：CoT 軌跡寫入接線

鎖住的不變式：
- 位置持久化（create/update）後寫一筆 cop_entity_tracks（先前 insert_cop_track 零 caller，表恆空）
- per-uid 5s min-interval 抽樣：距上一筆 < 5s 跳過（基準 = CoT event time）
- 較舊 / 重送 event 被丟 → 不寫軌跡
- best-effort：軌跡寫入失敗不擋 ingest（見 _record_track 容錯）
- 設計 B：tracks 不存 exercise_id，靠 uid JOIN cop_entities 取得（cascade 靠 uid FK）
"""

import asyncio

import pytest

from core.database import get_conn
from repositories import cop_entity_repo, exercise_repo
from schemas.tak import CoTEventIn
from services import cop_service


@pytest.fixture(autouse=True)
def _db(tmp_db):
    """ingest 走真 DB（cop_entities + cop_entity_tracks），需 init 過的 tmp DB。"""
    yield


@pytest.fixture(autouse=True)
def _silence_broadcast(monkeypatch):
    """攔截 cop_hub.broadcast（async），不真開 WS。本檔不驗廣播內容。"""

    async def _fake(message, exercise_id=None):
        return None

    monkeypatch.setattr(cop_service.cop_hub, "broadcast", _fake)


def _event(uid: str = "TRK-1", *, time: str, **overrides) -> CoTEventIn:
    base = {
        "uid": uid,
        "type": "a-f-G-U-C",
        "time": time,
        "start": time,
        "stale": "2099-01-01T00:00:00Z",
        "how": "m-g",
        "lat": 24.137,
        "lon": 120.687,
    }
    base.update(overrides)
    return CoTEventIn(**base)


def _ingest(event: CoTEventIn):
    return asyncio.run(cop_service.ingest_cot_event(event))


# ── 1. create 後寫第一筆軌跡 ──────────────────────────────────────────────────


def test_create_writes_first_track():
    _ingest(_event(time="2026-06-05T04:00:00Z", lat=24.1, lon=120.6))
    tracks = cop_entity_repo.list_cop_tracks("TRK-1")
    assert len(tracks) == 1
    assert tracks[0]["uid"] == "TRK-1"
    assert tracks[0]["t"] == "2026-06-05T04:00:00Z"
    assert (tracks[0]["lat"], tracks[0]["lon"]) == (24.1, 120.6)


# ── 2. heading / speed 從 CoT <track course/speed> 帶入軌跡 ───────────────────


def test_track_carries_heading_speed():
    _ingest(
        _event(
            time="2026-06-05T04:00:00Z",
            detail={"track": {"course": "90", "speed": "5"}},
        )
    )
    tracks = cop_entity_repo.list_cop_tracks("TRK-1")
    assert len(tracks) == 1
    assert tracks[0]["heading_deg"] == 90.0
    assert tracks[0]["speed_mps"] == 5.0


# ── 3. 5s 抽樣：< 5s 的第二筆跳過（但 entity 仍 update）───────────────────────


def test_throttle_skips_within_5s():
    _ingest(_event(time="2026-06-05T04:00:00Z", lat=24.0, lon=120.0))
    row = _ingest(_event(time="2026-06-05T04:00:03Z", lat=25.0, lon=121.0))  # +3s
    # entity 有 update（位置即時同步不受抽樣影響）
    assert row["version_clock"] == 2
    assert (row["lat"], row["lon"]) == (25.0, 121.0)
    # 但軌跡只留第一筆（第二筆 <5s 抽掉）
    tracks = cop_entity_repo.list_cop_tracks("TRK-1")
    assert len(tracks) == 1
    assert tracks[0]["t"] == "2026-06-05T04:00:00Z"


# ── 4. 5s 抽樣：間隔達 5s 寫入第二筆 ─────────────────────────────────────────


def test_allows_after_5s():
    _ingest(_event(time="2026-06-05T04:00:00Z"))
    _ingest(_event(time="2026-06-05T04:00:05Z"))  # +5s，邊界寫入
    tracks = cop_entity_repo.list_cop_tracks("TRK-1")
    assert len(tracks) == 2
    assert [t["t"] for t in tracks] == [
        "2026-06-05T04:00:00Z",
        "2026-06-05T04:00:05Z",
    ]


# ── 5. 較舊 / 重送 event 被丟 → 不寫軌跡 ──────────────────────────────────────


def test_older_event_writes_no_track():
    _ingest(_event(time="2026-06-05T04:05:00Z"))
    out = _ingest(_event(time="2026-06-05T04:00:00Z", lat=0.0, lon=0.0))  # out-of-order
    assert out is None
    tracks = cop_entity_repo.list_cop_tracks("TRK-1")
    assert len(tracks) == 1  # 仍只有第一筆，舊 event 沒落軌跡


# ── 6. entity 硬刪 → tracks 隨 uid FK cascade 清除 ───────────────────────────


def test_entity_hard_delete_cascades_tracks():
    _ingest(_event(time="2026-06-05T04:00:00Z"))
    assert len(cop_entity_repo.list_cop_tracks("TRK-1")) == 1
    # 模擬演習硬刪（P1-14 級聯）：硬 DELETE entity，foreign_keys=ON 觸發 cascade
    with get_conn() as conn:
        conn.execute("DELETE FROM cop_entities WHERE uid = ?", ("TRK-1",))
    assert cop_entity_repo.list_cop_tracks("TRK-1") == []


# ── 7. 設計 B：tracks 不存 exercise_id，靠 uid JOIN cop_entities 取得 ──────────


def test_exercise_attribution_via_join():
    ex = exercise_repo.create_exercise({"name": "P2-06a JOIN 驗證"})
    exercise_repo.update_exercise_status(ex["id"], "active", "test")
    # active 場存在 → ingest 的 entity 綁該場
    row = _ingest(_event(time="2026-06-05T04:00:00Z"))
    assert row["exercise_id"] == ex["id"]
    # B：track 自身無 exercise_id 欄位，但可由 uid JOIN cop_entities 取得（= P2-06b 查法）
    with get_conn() as conn:
        joined = conn.execute(
            """
            SELECT t.uid, t.t, e.exercise_id
            FROM cop_entity_tracks t
            JOIN cop_entities e ON t.uid = e.uid
            WHERE e.exercise_id = ?
            """,
            (ex["id"],),
        ).fetchall()
    assert len(joined) == 1
    assert joined[0]["exercise_id"] == ex["id"]


# ── 8. review #1：REST push 未正規化 time（naive）與 aware 軌跡混用不得漏寫 ──────


def test_mixed_tz_naive_time_does_not_drop_track():
    """:8089 路徑寫 aware（...Z）軌跡，REST push（POST /api/tak/events）的 time 未
    正規化、可能是 naive。混用時 _within_min_interval 不得因 aware−naive 相減的
    TypeError 被吞而靜默漏寫（修前：相減在 try 外 + except 漏 TypeError → 漏一筆）。"""
    _ingest(_event(time="2026-06-05T04:00:00Z"))   # aware 第一筆
    _ingest(_event(time="2026-06-05T04:00:06"))    # naive（無 Z），+6s ≥ 間隔 → 應寫
    tracks = cop_entity_repo.list_cop_tracks("TRK-1")
    assert len(tracks) == 2  # 混格式仍正確寫入第二筆，未因 TypeError 漏寫
    assert tracks[1]["t"] == "2026-06-05T04:00:06"


# ── 9. review #7：抽樣間隔可由 config 覆寫 ────────────────────────────────────


def test_interval_configurable(monkeypatch):
    """TRACK_MIN_INTERVAL_S 可覆寫（免改 code 重部署）。調到 10s 後，+6s 應被抽掉。"""
    monkeypatch.setattr(cop_service, "TRACK_MIN_INTERVAL_S", 10.0)
    _ingest(_event(time="2026-06-05T04:00:00Z"))
    _ingest(_event(time="2026-06-05T04:00:06Z"))   # +6s < 10s（覆寫後）→ 跳過
    tracks = cop_entity_repo.list_cop_tracks("TRK-1")
    assert len(tracks) == 1
