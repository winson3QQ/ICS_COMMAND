"""
tests/security/test_trusted_ingest.py — HMAC-SHA256 Trusted Ingest 驗證測試

Coverage:
  AC-1  POST /api/snapshots 無 X-ICS-Signature → 401
  AC-2  POST /api/sync/push 無 X-ICS-Signature → 401
  AC-3  timestamp 超 ±5 min → 401 reason=skew
  AC-4  相同 nonce 重送 → 401 reason=replay（unit）
  AC-5  body 篡改後用原 signature → 401 reason=tampered（unit）
  AC-6  無效 key_id → 401 reason=unknown_key
  AC-7  合法 key_id + 正確 signature → 200
  AC-8c replay 拒絕寫 audit_ingest_rejected reason=replay
  AC-10 audit_log 記錄 key_id + source_unit + accept/reject reason
  AC-12 trusted_keys schema 含全部欄位 + status CHECK 約束（unit）
"""

import hashlib
import hmac as _hmac
import json
import sqlite3
import time
import uuid
from urllib.parse import parse_qsl, urlencode

import pytest


# ─── 輔助函式：計算合法的 HMAC 簽名 headers ──────────────────────────────────

def _make_hmac_headers(
    key_id: str,
    secret: str,
    method: str,
    path: str,
    body_bytes: bytes,
    query: str = "",
    timestamp_ms: str | None = None,
    nonce: str | None = None,
) -> dict:
    """依 Decision-2 canonical string 計算 X-ICS-* headers。"""
    ts = timestamp_ms if timestamp_ms is not None else str(int(time.time() * 1000))
    nc = nonce if nonce is not None else str(uuid.uuid4())

    # query canonical
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        query_canonical = urlencode(sorted(pairs))
    else:
        query_canonical = ""

    body_hash = hashlib.sha256(body_bytes).hexdigest()

    canonical = "\n".join([
        method.upper(),
        path,
        query_canonical,
        ts,
        nc,
        body_hash,
    ])

    sig = _hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return {
        "X-ICS-Key-Id"    : key_id,
        "X-ICS-Timestamp" : ts,
        "X-ICS-Nonce"     : nc,
        "X-ICS-Signature" : sig,
    }


# ─── 常數 ──────────────────────────────────────────────────────────────────

_TEST_KEY_ID = "test-key-001"
_TEST_SECRET = "a" * 64  # 64 hex chars

_VALID_SNAPSHOT = {
    "v": 1,
    "type": "shelter",
    "snapshot_id": "snap-hmac-test-001",
    "t": "2026-04-26T00:00:00Z",
    "src": "shelter_pi",
}

_VALID_SYNC_PUSH = {
    "source_unit": "shelter",
    "sync_start_ts": "2026-04-26T00:00:00Z",
}


# ─── Fixture：在測試 DB 插入 HMAC 測試金鑰 ──────────────────────────────────

@pytest.fixture
def authed_client(client):
    """擴展 client fixture，在測試 DB 插入 HMAC 測試金鑰。
    回傳 (client, key_id, secret) — 此模組使用自己的 key，與 conftest hmac_client 區分。
    """
    from core.database import get_conn
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO trusted_keys (key_id, secret, status) VALUES (?, ?, 'active')",
        (_TEST_KEY_ID, _TEST_SECRET),
    )
    conn.commit()
    conn.close()
    return client, _TEST_KEY_ID, _TEST_SECRET


def _sign(key_id: str, secret: str, method: str, path: str, body_bytes: bytes,
          query: str = "", timestamp_ms: str | None = None, nonce: str | None = None) -> dict:
    """內部簽名函式（接受 bytes，與 conftest 的 sign 函式區分）。"""
    return _make_hmac_headers(key_id, secret, method, path, body_bytes, query,
                              timestamp_ms, nonce)


# ─── AC-1：POST /api/snapshots 無 X-ICS-Signature → 401 ─────────────────────

class TestNoSignatureReturns401:
    def test_snapshot_no_signature_returns_401(self, authed_client):
        """AC-1：無 HMAC headers → 401 no_sig。"""
        c, _, _ = authed_client
        body = json.dumps(_VALID_SNAPSHOT).encode()
        r = c.post("/api/snapshots",
                   content=body,
                   headers={"Content-Type": "application/json"})
        assert r.status_code == 401, f"期待 401，收到 {r.status_code}: {r.text}"
        detail = r.json().get("detail", {})
        assert detail.get("reason") == "no_sig"

    # AC-2
    def test_sync_push_no_signature_returns_401(self, authed_client):
        """AC-2：無 HMAC headers → 401 no_sig。"""
        c, _, _ = authed_client
        body = json.dumps(_VALID_SYNC_PUSH).encode()
        r = c.post("/api/sync/push",
                   content=body,
                   headers={"Content-Type": "application/json"})
        assert r.status_code == 401, f"期待 401，收到 {r.status_code}: {r.text}"
        detail = r.json().get("detail", {})
        assert detail.get("reason") == "no_sig"


# ─── AC-3：timestamp 超 ±5 min → 401 reason=skew ────────────────────────────

def test_timestamp_skew_rejected(authed_client):
    """AC-3：timestamp 比現在早 10 分鐘 → 401 reason=skew。"""
    c, key_id, secret = authed_client
    body = json.dumps(_VALID_SNAPSHOT).encode()

    # 往回 600,001 ms（剛好超過 5 分鐘容差）
    old_ts = str(int(time.time() * 1000) - 600_001)
    headers = _make_hmac_headers(key_id, secret, "POST", "/api/snapshots", body,
                                 timestamp_ms=old_ts)
    headers["Content-Type"] = "application/json"
    r = c.post("/api/snapshots", content=body, headers=headers)

    assert r.status_code == 401, f"期待 401，收到 {r.status_code}: {r.text}"
    assert r.json().get("detail", {}).get("reason") == "skew"


# ─── AC-4：nonce replay（unit test）─────────────────────────────────────────

def test_nonce_replay_rejected():
    """AC-4：check_and_store_nonce 同一 nonce 呼叫兩次，第二次回 False。"""
    from repositories.nonce_repo import check_and_store_nonce

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE nonce_cache (nonce TEXT PRIMARY KEY, created_at INTEGER NOT NULL)"
    )
    now_ms = int(time.time() * 1000)
    nonce = str(uuid.uuid4())

    first  = check_and_store_nonce(conn, nonce, now_ms)
    second = check_and_store_nonce(conn, nonce, now_ms)

    assert first  is True,  "首次應回 True（nonce 尚未使用）"
    assert second is False, "重送應回 False（replay 攻擊）"
    conn.close()


# ─── AC-5：body 篡改（unit test）────────────────────────────────────────────

def test_body_tampered_signature_rejected():
    """AC-5：_verify_signature 對篡改 body 的 canonical 應回 False。"""
    from middleware.trusted_ingest import _verify_signature, _canonical_string

    secret = "b" * 64
    original_body = b'{"unit":"shelter"}'
    tampered_body = b'{"unit":"medical"}'

    # 用 original_body 計算 canonical + 簽名
    canonical_orig = _canonical_string("POST", "/api/snapshots", "", "1000000", "nonce-001", original_body)
    sig = _hmac.new(
        secret.encode("utf-8"),
        canonical_orig.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # 用 tampered_body 算 canonical，但送 original 的 sig → 應驗證失敗
    canonical_tampered = _canonical_string("POST", "/api/snapshots", "", "1000000", "nonce-001", tampered_body)
    result = _verify_signature(secret, canonical_tampered, sig)

    assert result is False, "篡改 body 後用原 signature 應驗證失敗"


# ─── AC-6：unknown key_id → 401 reason=unknown_key ──────────────────────────

def test_unknown_key_id_rejected(authed_client):
    """AC-6：使用不存在的 key_id → 401 unknown_key。"""
    c, _, secret = authed_client
    body = json.dumps(_VALID_SNAPSHOT).encode()
    headers = _make_hmac_headers("nonexistent-key-999", secret,
                                 "POST", "/api/snapshots", body)
    headers["Content-Type"] = "application/json"
    r = c.post("/api/snapshots", content=body, headers=headers)

    assert r.status_code == 401
    assert r.json().get("detail", {}).get("reason") == "unknown_key"


# ─── AC-7：合法 key_id + 正確 signature → 200 ───────────────────────────────

def test_valid_signature_accepted(authed_client):
    """AC-7：正確簽名 + 合法 key_id → 200，資料寫入 DB。"""
    c, key_id, secret = authed_client
    body = json.dumps(_VALID_SNAPSHOT).encode()
    headers = _make_hmac_headers(key_id, secret, "POST", "/api/snapshots", body)
    headers["Content-Type"] = "application/json"
    r = c.post("/api/snapshots", content=body, headers=headers)

    assert r.status_code == 200, f"期待 200，收到 {r.status_code}: {r.text}"


# ─── AC-8c：replay 拒絕 → audit_log reason=replay ──────────────────────────

def test_replay_attempt_creates_audit_entry(authed_client):
    """AC-8c：第二次相同 nonce → 401 replay，audit_log 有 reason=replay。"""
    c, key_id, secret = authed_client
    body_data = {**_VALID_SNAPSHOT, "snapshot_id": "snap-hmac-replay-001"}
    body = json.dumps(body_data).encode()
    shared_nonce = str(uuid.uuid4())

    # 第一次送 → 應 200
    h1 = _make_hmac_headers(key_id, secret, "POST", "/api/snapshots", body,
                             nonce=shared_nonce)
    h1["Content-Type"] = "application/json"
    r1 = c.post("/api/snapshots", content=body, headers=h1)
    assert r1.status_code == 200, f"第一次應 200，實際：{r1.status_code} {r1.text}"

    # 第二次送同一 nonce + 新 timestamp → 應 401 replay
    body2_data = {**_VALID_SNAPSHOT, "snapshot_id": "snap-hmac-replay-002"}
    body2 = json.dumps(body2_data).encode()
    h2 = _make_hmac_headers(key_id, secret, "POST", "/api/snapshots", body2,
                             nonce=shared_nonce)
    h2["Content-Type"] = "application/json"
    r2 = c.post("/api/snapshots", content=body2, headers=h2)
    assert r2.status_code == 401
    assert r2.json().get("detail", {}).get("reason") == "replay"

    # 驗證 audit_log 有 replay 記錄
    from core.database import get_conn
    conn = get_conn()
    row = conn.execute(
        "SELECT detail FROM audit_log WHERE action_type='audit_ingest_rejected'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None, "audit_log 應有 audit_ingest_rejected 記錄"
    detail = json.loads(row[0])
    assert detail.get("reason") == "replay", f"audit detail.reason 應為 replay，實際：{detail}"


# ─── AC-10：audit_log 記錄 key_id + source_unit + reason ─────────────────────

def test_audit_log_records_key_id_and_reason(authed_client):
    """AC-10：成功 ingest 和拒絕 ingest 均寫 audit_log，且欄位正確。"""
    c, key_id, secret = authed_client
    from core.database import get_conn

    # ── 成功案例（audit_ingest_accepted）────────────────────────────────
    body_data = {**_VALID_SNAPSHOT, "snapshot_id": "snap-audit-log-001", "unit": "shelter_pi"}
    body = json.dumps(body_data).encode()
    headers = _make_hmac_headers(key_id, secret, "POST", "/api/snapshots", body)
    headers["Content-Type"] = "application/json"
    r = c.post("/api/snapshots", content=body, headers=headers)
    assert r.status_code == 200

    conn = get_conn()
    row_ok = conn.execute(
        "SELECT operator, detail FROM audit_log"
        " WHERE action_type='audit_ingest_accepted' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert row_ok is not None, "audit_ingest_accepted 應寫入 audit_log"
    assert row_ok["operator"] == key_id, f"operator 應為 key_id，實際：{row_ok['operator']}"
    detail_ok = json.loads(row_ok["detail"])
    assert "request_id" in detail_ok
    assert "body_hash" in detail_ok

    # ── 拒絕案例（audit_ingest_rejected, reason=unknown_key）───────────
    body2 = json.dumps(_VALID_SNAPSHOT).encode()
    bad_headers = _make_hmac_headers("bad-key-999", secret, "POST", "/api/snapshots", body2)
    bad_headers["Content-Type"] = "application/json"
    r2 = c.post("/api/snapshots", content=body2, headers=bad_headers)
    assert r2.status_code == 401

    conn2 = get_conn()
    row_fail = conn2.execute(
        "SELECT operator, detail FROM audit_log"
        " WHERE action_type='audit_ingest_rejected' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn2.close()

    assert row_fail is not None, "audit_ingest_rejected 應寫入 audit_log"
    detail_fail = json.loads(row_fail["detail"])
    assert detail_fail.get("reason") == "unknown_key"


# ─── AC-12：trusted_keys schema 含全部欄位 + CHECK 約束（unit test）────────

def test_trusted_keys_schema_has_required_columns():
    """AC-12：trusted_keys 表結構符合 Decision-3（6 欄位 + status CHECK 約束）。"""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

    import sqlite3 as _sqlite3
    from unittest.mock import patch
    from pathlib import Path as _Path

    # 用 :memory: DB 跑 init_db，只測 schema
    mem_path = _Path(":memory:")
    with patch("core.config.DB_PATH", mem_path), \
         patch("core.database.DB_PATH", mem_path):
        conn = _sqlite3.connect(":memory:")
        conn.row_factory = _sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        # 直接呼叫 _create_tables
        from core.database import _create_tables, _ensure_migrations_table, _migrate
        _create_tables(conn)
        _ensure_migrations_table(conn)
        _migrate(conn)
        conn.commit()

    # 驗證欄位
    cols = {row[1] for row in conn.execute("PRAGMA table_info(trusted_keys)")}
    required_cols = {"key_id", "secret", "status", "created_at", "expires_at", "rotated_from_key_id"}
    missing = required_cols - cols
    assert not missing, f"trusted_keys 缺少欄位：{missing}"

    # 驗證 CHECK 約束：插入非法 status 應 fail
    try:
        conn.execute(
            "INSERT INTO trusted_keys (key_id, secret, status) VALUES ('k1', 's1', 'invalid_status')"
        )
        conn.commit()
        assert False, "CHECK 約束未生效，應拒絕 status='invalid_status'"
    except _sqlite3.IntegrityError:
        pass  # 預期行為

    conn.close()


# ─── AC-15：POST /api/pi-push/{unit_id} 無 HMAC headers → 401 ───────────────
# Option-A scope 擴充：主推送路徑也受 HMAC 保護

_VALID_PI_PUSH = {
    "records": [],
    "pushed_at": "2026-04-27T00:00:00Z",
    "heartbeat": True,
}


class TestPiPushHmacProtection:
    """AC-15：/api/pi-push/{unit_id} HMAC 保護驗證。"""

    def test_pi_push_no_signature_returns_401(self, authed_client):
        """AC-15a：無 X-ICS-Signature → 401 no_sig（HMAC 在 Bearer 之前執行）。"""
        c, _, _ = authed_client
        body = json.dumps(_VALID_PI_PUSH).encode()
        r = c.post(
            "/api/pi-push/shelter",
            content=body,
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer any-token"},
        )
        assert r.status_code == 401, f"期待 401，收到 {r.status_code}: {r.text}"
        detail = r.json().get("detail", {})
        assert detail.get("reason") == "no_sig"

    def test_pi_push_unknown_key_returns_401(self, authed_client):
        """AC-15b：未知 key_id → 401 unknown_key。"""
        c, _, secret = authed_client
        body = json.dumps(_VALID_PI_PUSH).encode()
        headers = _make_hmac_headers("nonexistent-key-xyz", secret,
                                     "POST", "/api/pi-push/shelter", body)
        headers["Content-Type"] = "application/json"
        headers["Authorization"] = "Bearer any-token"
        r = c.post("/api/pi-push/shelter", content=body, headers=headers)
        assert r.status_code == 401
        assert r.json().get("detail", {}).get("reason") == "unknown_key"

    def test_pi_push_replay_returns_401(self, authed_client):
        """AC-15c：相同 nonce 重送 → 401 replay。

        注意：第一次送會通過 HMAC，但 Bearer/pi_nodes 驗證可能回 403（pi_node 不存在）。
        第二次送相同 nonce → HMAC 層先攔截 replay，回 401。
        """
        c, key_id, secret = authed_client
        body = json.dumps(_VALID_PI_PUSH).encode()
        shared_nonce = str(uuid.uuid4())

        # 第一次送（nonce 儲存至 nonce_cache；HMAC 通過後進入 pi_nodes 驗證）
        h1 = _make_hmac_headers(key_id, secret, "POST", "/api/pi-push/shelter", body,
                                 nonce=shared_nonce)
        h1["Content-Type"] = "application/json"
        h1["Authorization"] = "Bearer any-token"
        c.post("/api/pi-push/shelter", content=body, headers=h1)  # 不斷言狀態碼（pi_nodes 未設）

        # 第二次送同一 nonce → HMAC 層攔截 replay
        h2 = _make_hmac_headers(key_id, secret, "POST", "/api/pi-push/shelter", body,
                                 nonce=shared_nonce)
        h2["Content-Type"] = "application/json"
        h2["Authorization"] = "Bearer any-token"
        r2 = c.post("/api/pi-push/shelter", content=body, headers=h2)
        assert r2.status_code == 401
        assert r2.json().get("detail", {}).get("reason") == "replay"
