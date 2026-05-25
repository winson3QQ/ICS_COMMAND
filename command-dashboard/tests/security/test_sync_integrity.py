"""
tests/security/test_sync_integrity.py — 同步完整性測試

涵蓋：
  - replay / duplicate sync：同一 snapshot_id 重複推送
  - out-of-order sync：亂序時間戳快照推送

設計決策：
  - upsert_snapshot 使用 INSERT OR IGNORE（idempotent by snapshot_id）
  - 排序依 snapshot_time，非 received_at → 亂序不影響「最新」判斷
"""

import pytest

# ─────────────────────────────────────────────────────────────────
# Replay / Duplicate Sync
# ─────────────────────────────────────────────────────────────────

_BASE_SNAP = {
    "v": 3,
    "type": "shelter",
    "snapshot_id": "replay-test-001",
    "t": "2026-04-24T08:00:00Z",
    "src": "test-pi",
    "node_type": "shelter",
    "bed_used": 10,
    "bed_total": 50,
}


class TestReplayDuplicateSync:
    def test_first_insert_returns_inserted_true(self, tmp_db):
        """首次推送：inserted=True"""
        from repositories.snapshot_repo import upsert_snapshot
        result = upsert_snapshot({**_BASE_SNAP})
        assert result["inserted"] is True
        assert result["snapshot_id"] == "replay-test-001"

    def test_duplicate_returns_inserted_false(self, tmp_db):
        """相同 snapshot_id 二次推送：inserted=False（idempotent）"""
        from repositories.snapshot_repo import upsert_snapshot
        upsert_snapshot({**_BASE_SNAP})
        result = upsert_snapshot({**_BASE_SNAP})
        assert result["inserted"] is False

    def test_duplicate_does_not_increase_db_count(self, tmp_db):
        """重複推送不增加 DB 筆數"""
        from repositories.snapshot_repo import upsert_snapshot
        from core.database import get_conn
        upsert_snapshot({**_BASE_SNAP})
        upsert_snapshot({**_BASE_SNAP})
        with get_conn() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) as c FROM snapshots WHERE snapshot_id='replay-test-001'"
            ).fetchone()["c"]
        assert cnt == 1

    def test_duplicate_with_modified_data_ignored(self, tmp_db):
        """相同 snapshot_id 但不同 bed_used → 第二筆被忽略，原始資料保留"""
        from repositories.snapshot_repo import upsert_snapshot, get_snapshots
        upsert_snapshot({**_BASE_SNAP, "bed_used": 10})
        upsert_snapshot({**_BASE_SNAP, "bed_used": 99})  # 惡意篡改或重送
        rows = get_snapshots("shelter")
        match = [r for r in rows if r["snapshot_id"] == "replay-test-001"]
        assert len(match) == 1
        assert match[0]["bed_used"] == 10  # 原始值保留

    def test_duplicate_via_api_both_return_200(self, hmac_client):
        """透過 API 重複推送：兩次都回 HTTP 200（不拋錯）"""
        c, sign = hmac_client
        snap = {
            "v": 3, "type": "shelter", "snapshot_id": "api-replay-001",
            "t": "2026-04-24T09:00:00Z", "src": "test",
            "bed_used": 5, "bed_total": 50,
        }
        body_bytes, hdrs = sign("POST", "/api/snapshots", snap)
        r1 = c.post("/api/snapshots", content=body_bytes, headers=hdrs)
        # 第二次使用新 nonce（sign 每次呼叫都產生新 nonce）
        body_bytes2, hdrs2 = sign("POST", "/api/snapshots", snap)
        r2 = c.post("/api/snapshots", content=body_bytes2, headers=hdrs2)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json()["inserted"] is True
        assert r2.json()["inserted"] is False


# ─────────────────────────────────────────────────────────────────
# Out-of-Order Sync
# ─────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────
# 單一 Payload 內重複 snapshot_id（Within-Payload Duplicate）
# ─────────────────────────────────────────────────────────────────

class TestWithinPayloadDuplicate:
    def test_duplicate_snapshot_id_in_single_sync_payload_last_wins(self, hmac_client):
        """
        同一 /api/sync/push payload 內，兩筆相同 snapshot_id：
        Three-Pass 逐筆串行處理 → 第一筆 INSERT，第二筆 UPDATE（last wins）。
        設計備忘：/api/snapshots 是 INSERT OR IGNORE（first wins），
                  /api/sync/push Pass 1 對已存在 row 執行 UPDATE（last wins）。
        """
        c, sign = hmac_client
        payload = {
            "source_unit": "shelter",
            "sync_start_ts": "2026-04-24T07:00:00Z",
            "snapshots": [
                {
                    "v": 3, "type": "shelter",
                    "snapshot_id": "within-payload-001",
                    "t": "2026-04-24T08:00:00Z",
                    "src": "sync", "bed_used": 4, "bed_total": 10,
                },
                {
                    "v": 3, "type": "shelter",
                    "snapshot_id": "within-payload-001",  # 相同 ID，第二筆
                    "t": "2026-04-24T08:00:00Z",
                    "src": "sync", "bed_used": 99, "bed_total": 10,
                },
            ],
            "events": [],
            "manual_records": [],
        }

        body_bytes, hdrs = sign("POST", "/api/sync/push", payload)
        r = c.post("/api/sync/push", content=body_bytes, headers=hdrs)
        assert r.status_code == 200
        # Pass 1 merged=1（第二筆 UPDATE）；只有 1 筆 snapshot 進 DB
        assert r.json()["pass1_merged"] == 1

        # 確認 DB 只有一筆，且值為 last-wins（bed_used=99）
        from core.database import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT bed_used FROM snapshots WHERE snapshot_id='within-payload-001'"
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["bed_used"] == 99, "同一 payload 內重複 snapshot_id → last wins（Three-Pass UPDATE）"


class TestOutOfOrderSync:
    def test_latest_reflects_snapshot_time_not_arrival(self, tmp_db):
        """先推新快照、後推舊快照 → get_latest 仍回傳時間較新的那筆"""
        from repositories.snapshot_repo import upsert_snapshot, get_latest_snapshot
        # 先推 10:00（較新）
        upsert_snapshot({
            **_BASE_SNAP,
            "snapshot_id": "order-new",
            "t": "2026-04-24T10:00:00Z",
            "bed_used": 20,
        })
        # 後推 08:00（較舊，亂序）
        upsert_snapshot({
            **_BASE_SNAP,
            "snapshot_id": "order-old",
            "t": "2026-04-24T08:00:00Z",
            "bed_used": 5,
        })
        latest = get_latest_snapshot("shelter")
        assert latest["snapshot_id"] == "order-new"
        assert latest["bed_used"] == 20

    def test_old_snapshot_does_not_displace_latest(self, tmp_db):
        """舊時間戳快照推入後，最新快照不被取代"""
        from repositories.snapshot_repo import upsert_snapshot, get_latest_snapshot
        upsert_snapshot({
            **_BASE_SNAP,
            "snapshot_id": "current",
            "t": "2026-04-24T12:00:00Z",
            "bed_used": 30,
        })
        upsert_snapshot({
            **_BASE_SNAP,
            "snapshot_id": "stale",
            "t": "2026-04-20T06:00:00Z",  # 4 天前
            "bed_used": 1,
        })
        latest = get_latest_snapshot("shelter")
        assert latest["snapshot_id"] == "current"

    def test_future_timestamp_accepted(self, tmp_db):
        """未來時間戳（時鐘偏移）：系統接受，不拋錯"""
        from repositories.snapshot_repo import upsert_snapshot
        result = upsert_snapshot({
            **_BASE_SNAP,
            "snapshot_id": "future-snap",
            "t": "2030-01-01T00:00:00Z",
        })
        assert result["inserted"] is True
