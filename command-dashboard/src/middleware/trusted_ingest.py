"""
middleware/trusted_ingest.py — HMAC-SHA256 ingest 驗證（Depends）

掛法：
  @router.post("", dependencies=[Depends(verify_hmac)])

Validation order（per Decision-5）：
  1. Bearer auth（全域 middleware，不在此）
  2. verify_hmac（此 Depends）
  3. Router logic

Canonical String（Decision-2）：
  method.upper() + "\\n" +
  path           + "\\n" +
  query_sorted   + "\\n" +   # sorted key=value&key=value, "" if empty
  timestamp_ms   + "\\n" +
  nonce          + "\\n" +
  sha256_hex(body_bytes)
"""

import hashlib
import hmac
import json
import sqlite3
import time
from urllib.parse import parse_qsl, urlencode

from fastapi import Depends, HTTPException, Request

from core.audit_chain import compute_next_hash_prev
from core.config import HMAC_TIMESTAMP_SKEW_MS
from core.database import get_db
from repositories.nonce_repo import check_and_store_nonce

# ── Audit helper（不依賴 _helpers.audit，避免循環引入）─────────────────────


def _write_audit(
    conn: sqlite3.Connection,
    action_type: str,
    operator: str,
    device_id: str,
    target_table: str,
    target_id: str,
    detail: dict,
) -> None:
    # GAP-AUDIT-04: hash_prev 對齊 prev record canonical hash (NIST AU-9(3))
    hash_prev = compute_next_hash_prev(conn)
    conn.execute(
        """INSERT INTO audit_log
               (operator, device_id, action_type, target_table, target_id, detail, hash_prev)
           VALUES (?,?,?,?,?,?,?)""",
        (operator, device_id, action_type, target_table, target_id, json.dumps(detail, ensure_ascii=False), hash_prev),
    )
    conn.commit()


# ── Canonical String ────────────────────────────────────────────────────────


def _query_canonical(query_string: str) -> str:
    """query string → sorted key=value&key=value。無 query 時回傳空字串。"""
    if not query_string:
        return ""
    pairs = parse_qsl(query_string, keep_blank_values=True)
    return urlencode(sorted(pairs))


def _body_sha256(body_bytes: bytes) -> str:
    return hashlib.sha256(body_bytes).hexdigest()


def _canonical_string(method: str, path: str, query: str, timestamp_ms: str, nonce: str, body_bytes: bytes) -> str:
    return "\n".join(
        [
            method.upper(),
            path,
            _query_canonical(query),
            timestamp_ms,
            nonce,
            _body_sha256(body_bytes),
        ]
    )


def _verify_signature(secret: str, canonical: str, signature: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


# ── Main Depends ────────────────────────────────────────────────────────────


async def verify_hmac(
    request: Request,
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """HMAC-SHA256 ingest 驗證 Depends。驗證失敗一律 raise HTTPException(401)。"""
    route_path = request.url.path
    source_ip = request.client.host if request.client else "unknown"
    now_ms = int(time.time() * 1000)

    # ── 1. Header 存在性檢查 ──────────────────────────────────────────────
    key_id = request.headers.get("X-ICS-Key-Id", "")
    timestamp = request.headers.get("X-ICS-Timestamp", "")
    nonce = request.headers.get("X-ICS-Nonce", "")
    signature = request.headers.get("X-ICS-Signature", "")

    if not all([key_id, timestamp, nonce, signature]):
        _write_audit(
            conn,
            "audit_ingest_rejected",
            key_id or "unknown",
            source_ip,
            route_path,
            "none",
            {"reason": "no_sig", "request_id": "none"},
        )
        raise HTTPException(status_code=401, detail={"reason": "no_sig"})

    # ── 2. key_id 查 trusted_keys ─────────────────────────────────────────
    row = conn.execute(
        "SELECT secret FROM trusted_keys WHERE key_id = ? AND status = 'active'",
        (key_id,),
    ).fetchone()
    if not row:
        _write_audit(
            conn,
            "audit_ingest_rejected",
            key_id,
            source_ip,
            route_path,
            nonce,
            {"reason": "unknown_key", "request_id": nonce},
        )
        raise HTTPException(status_code=401, detail={"reason": "unknown_key"})
    secret = row[0]

    # ── 3. Timestamp skew ─────────────────────────────────────────────────
    try:
        ts_ms = int(timestamp)
    except ValueError:
        _write_audit(
            conn, "audit_ingest_rejected", key_id, source_ip, route_path, nonce, {"reason": "skew", "request_id": nonce}
        )
        raise HTTPException(status_code=401, detail={"reason": "skew"}) from None

    if abs(now_ms - ts_ms) > HMAC_TIMESTAMP_SKEW_MS:
        _write_audit(
            conn, "audit_ingest_rejected", key_id, source_ip, route_path, nonce, {"reason": "skew", "request_id": nonce}
        )
        raise HTTPException(status_code=401, detail={"reason": "skew"})

    # ── 4. Nonce replay 檢查 ──────────────────────────────────────────────
    # check_and_store_nonce 同步執行 Lazy Expiry
    if not check_and_store_nonce(conn, nonce, now_ms):
        _write_audit(
            conn,
            "audit_ingest_rejected",
            key_id,
            source_ip,
            route_path,
            nonce,
            {"reason": "replay", "request_id": nonce},
        )
        raise HTTPException(status_code=401, detail={"reason": "replay"})

    # ── 5. HMAC 驗證 ──────────────────────────────────────────────────────
    body_bytes = await request.body()  # Starlette 內建快取，router 端 json() 可重讀
    canonical = _canonical_string(
        request.method,
        request.url.path,
        request.url.query,
        timestamp,
        nonce,
        body_bytes,
    )
    if not _verify_signature(secret, canonical, signature):
        _write_audit(
            conn,
            "audit_ingest_rejected",
            key_id,
            source_ip,
            route_path,
            nonce,
            {"reason": "tampered", "request_id": nonce},
        )
        raise HTTPException(status_code=401, detail={"reason": "tampered"})

    # ── 6. 成功：寫 audit_ingest_accepted ────────────────────────────────
    # source_unit 從 body 取（best-effort，不影響驗證結果）
    try:
        body_json = json.loads(body_bytes)
        source_unit = body_json.get("unit", source_ip)
    except Exception:
        source_unit = source_ip

    _write_audit(
        conn,
        "audit_ingest_accepted",
        key_id,
        source_unit,
        route_path,
        nonce,
        {"request_id": nonce, "body_hash": _body_sha256(body_bytes)},
    )
