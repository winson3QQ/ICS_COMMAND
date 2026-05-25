"""
tests/security/test_sync_extended.py — /api/sync/push 三 Pass 同步測試

涵蓋（補充 test_sync_integrity.py 針對 /api/snapshots 的測試）：
  - Idempotency：同 payload 兩次 push → 結果一致
  - Replay：已存在的 snapshot_id 在 sync push 中 → Pass 1 UPDATE（不新增）
  - Out-of-order：舊時間戳快照透過 sync push → 不取代最新快照
  - 空 payload：無 snapshots/events → 正常回傳，DB 不變
  - 未知 unit：source_unit 不在已知列表 → 仍接受（容錯設計）

設計備忘：
  POST /api/sync/push 無 session auth（機對機設計）。
  TI-01 後需要 HMAC-SHA256 headers。使用 hmac_client fixture 提供簽名。
  Pass 1 對已存在 snapshot_id 執行 UPDATE（不同於 /api/snapshots 的 INSERT OR IGNORE）。
"""

import pytest

_SYNC_URL = "/api/sync/push"


def _signed_post(c, sign, url, body_dict):
    """用 hmac_client sign 函式簽名後發送 POST 請求。"""
    body_bytes, hdrs = sign("POST", url, body_dict)
    return c.post(url, content=body_bytes, headers=hdrs)


def _make_sync_payload(source_unit: str, snapshots: list = None,
                       events: list = None) -> dict:
    return {
        "source_unit": source_unit,
        "sync_start_ts": "2026-04-24T06:00:00Z",
        "device_id": "test-pi-001",
        "snapshots": snapshots or [],
        "events": events or [],
        "decisions": [],
    }


def _base_snap(snap_id: str, t: str = "2026-04-24T08:00:00Z",
               bed_used: int = 10) -> dict:
    return {
        "snapshot_id": snap_id,
        "node_type": "shelter",
        "t": t,
        "source": "sync_recovery",
        "bed_used": bed_used,
        "bed_total": 50,
    }


# ─────────────────────────────────────────────────────────────────
# 空 Payload
# ─────────────────────────────────────────────────────────────────

class TestSyncEmptyPayload:
    def test_empty_sync_push_returns_200(self, hmac_client):
        """空 payload（無 snapshots/events）→ 200，不崩潰"""
        c, sign = hmac_client
        r = _signed_post(c, sign, _SYNC_URL, _make_sync_payload("shelter"))
        assert r.status_code == 200

    def test_empty_sync_does_not_change_db(self, hmac_client, tmp_db):
        """空 payload 不增加任何 snapshots"""
        from core.database import get_conn
        c, sign = hmac_client
        _signed_post(c, sign, _SYNC_URL, _make_sync_payload("shelter"))
        with get_conn() as conn:
            cnt = conn.execute("SELECT COUNT(*) as c FROM snapshots").fetchone()["c"]
        assert cnt == 0


# ─────────────────────────────────────────────────────────────────
# Idempotency
# ─────────────────────────────────────────────────────────────────

class TestSyncIdempotency:
    def test_same_payload_twice_idempotent(self, hmac_client, tmp_db):
        """相同 sync payload 推兩次 → 快照數量不變（Pass 1 UPDATE，不新增）"""
        from core.database import get_conn
        c, sign = hmac_client
        payload = _make_sync_payload("shelter", snapshots=[
            _base_snap("idem-001"), _base_snap("idem-002")
        ])
        _signed_post(c, sign, _SYNC_URL, payload)
        _signed_post(c, sign, _SYNC_URL, payload)
        with get_conn() as conn:
            cnt = conn.execute("SELECT COUNT(*) as c FROM snapshots").fetchone()["c"]
        assert cnt == 2  # 不是 4

    def test_sync_pass1_updates_existing_snapshot(self, hmac_client, tmp_db):
        """
        Pass 1：已存在的 snapshot_id → UPDATE（資料更新）。
        不同於 /api/snapshots 的 INSERT OR IGNORE（保留原始）。
        """
        from repositories.snapshot_repo import upsert_snapshot, get_snapshots
        c, sign = hmac_client
        # 先用 /api/snapshots 插入（bed_used=5）
        upsert_snapshot({
            "v": 3, "type": "shelter", "snapshot_id": "sync-update-001",
            "t": "2026-04-24T08:00:00Z", "src": "test",
            "node_type": "shelter", "bed_used": 5, "bed_total": 50,
        })
        # 再用 sync push 推送相同 snapshot_id（bed_used=30）
        payload = _make_sync_payload("shelter", snapshots=[
            _base_snap("sync-update-001", bed_used=30)
        ])
        r = _signed_post(c, sign, _SYNC_URL, payload)
        assert r.status_code == 200
        # Pass 1 應 UPDATE → bed_used 變 30
        rows = get_snapshots("shelter")
        match = [s for s in rows if s["snapshot_id"] == "sync-update-001"]
        assert match[0]["bed_used"] == 30

    def test_sync_result_consistent_on_retry(self, hmac_client, tmp_db):
        """sync push 失敗後重試（模擬網路重送）→ 結果與第一次相同"""
        from core.database import get_conn
        c, sign = hmac_client
        payload = _make_sync_payload("shelter", snapshots=[_base_snap("retry-001")])
        r1 = _signed_post(c, sign, _SYNC_URL, payload)
        r2 = _signed_post(c, sign, _SYNC_URL, payload)
        assert r1.status_code == r2.status_code == 200
        # 兩次結果中 p1_merged + p1_added 合計反映同一筆
        with get_conn() as conn:
            cnt = conn.execute("SELECT COUNT(*) as c FROM snapshots WHERE snapshot_id='retry-001'").fetchone()["c"]
        assert cnt == 1


# ─────────────────────────────────────────────────────────────────
# Out-of-Order Sync
# ─────────────────────────────────────────────────────────────────

class TestSyncOutOfOrder:
    def test_old_timestamp_via_sync_does_not_displace_latest(self, hmac_client, tmp_db):
        """
        先 sync push 新快照（10:00），再 sync push 舊快照（08:00）。
        get_latest_snapshot 仍回傳 10:00 那筆。
        """
        from repositories.snapshot_repo import get_latest_snapshot
        c, sign = hmac_client
        # 先推新的
        _signed_post(c, sign, _SYNC_URL, _make_sync_payload("shelter", snapshots=[
            _base_snap("order-new", t="2026-04-24T10:00:00Z", bed_used=40)
        ]))
        # 後推舊的（亂序）
        _signed_post(c, sign, _SYNC_URL, _make_sync_payload("shelter", snapshots=[
            _base_snap("order-old", t="2026-04-20T08:00:00Z", bed_used=1)
        ]))
        latest = get_latest_snapshot("shelter")
        assert latest["snapshot_id"] == "order-new"

    def test_mixed_timestamps_all_stored(self, hmac_client, tmp_db):
        """亂序的多筆快照都應存入 DB（不丟棄舊時間戳）"""
        from core.database import get_conn
        c, sign = hmac_client
        snaps = [
            _base_snap("mix-003", t="2026-04-24T10:00:00Z"),
            _base_snap("mix-001", t="2026-04-24T08:00:00Z"),
            _base_snap("mix-002", t="2026-04-24T09:00:00Z"),
        ]
        _signed_post(c, sign, _SYNC_URL, _make_sync_payload("shelter", snapshots=snaps))
        with get_conn() as conn:
            cnt = conn.execute("SELECT COUNT(*) as c FROM snapshots").fetchone()["c"]
        assert cnt == 3


# ─────────────────────────────────────────────────────────────────
# 未知 Unit 容錯
# ─────────────────────────────────────────────────────────────────

class TestSyncUnknownUnit:
    def test_unknown_source_unit_accepted(self, hmac_client):
        """未知 source_unit → 仍 200（sync 容錯設計，_unit_to_node fallback）"""
        c, sign = hmac_client
        r = _signed_post(c, sign, _SYNC_URL, _make_sync_payload("unknown_unit", snapshots=[
            _base_snap("unknown-001")
        ]))
        assert r.status_code == 200

    def test_sync_without_snapshots_key(self, hmac_client):
        """payload 缺少 snapshots 欄位（只有 source_unit）→ 200 或 422，不崩潰"""
        c, sign = hmac_client
        body = {
            "source_unit": "shelter",
            "sync_start_ts": "2026-04-24T06:00:00Z",
            "device_id": "test",
        }
        r = _signed_post(c, sign, _SYNC_URL, body)
        assert r.status_code in (200, 422)
