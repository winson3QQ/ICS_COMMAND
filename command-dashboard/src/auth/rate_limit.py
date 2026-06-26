# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
auth/rate_limit.py — 認證端點 IP rate limit（C1-A）

設計：
- 範圍：只套 /api/auth/login（其他 endpoint 有 session 保護，rate limit 意義小）
- 上限：10 requests/min/IP（規格 §C1-A）
- 超出：HTTP 429 + audit log

實作：in-memory sliding window（單機 Command N100 足夠；
若未來水平擴展，可改 Redis）。
"""

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import Request
from fastapi.responses import JSONResponse

import core.config as config

# ── 參數 ──────────────────────────────────────────────────────────
RATE_LIMIT_WINDOW_SEC = 60  # 觀察窗 60 秒
RATE_LIMIT_MAX_REQ = 10  # 同一 IP 60 秒內最多 10 次

# ── State（單機 in-memory）──────────────────────────────────────────
_buckets: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def _client_ip(request: Request) -> str:
    """取 client IP。#275 wave 4：反代後信任 nginx 的 X-Real-IP（真實 client）；直連忽略
    可偽造的 X-Forwarded-For（否則攻擊者偽造 IP 繞限速），用實際 peer。見 §8.6。"""
    if config.ICS_BEHIND_PROXY:
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    if request.client:
        return request.client.host
    return "unknown"


def _is_rate_limited(ip: str) -> bool:
    """檢查並記錄一筆。回傳 True 表示已超出限制。"""
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW_SEC
    with _lock:
        bucket = _buckets[ip]
        # 清掉超過窗的舊紀錄
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= RATE_LIMIT_MAX_REQ:
            return True
        bucket.append(now)
        return False


def reset_for_tests() -> None:
    """測試用：清空所有 bucket。"""
    with _lock:
        _buckets.clear()


# ── Middleware ───────────────────────────────────────────────────
async def auth_rate_limit_middleware(request: Request, call_next):
    """套用範圍：POST /api/auth/login。其他路徑直接放行。"""
    if request.method == "POST" and request.url.path == "/api/auth/login":
        ip = _client_ip(request)
        if _is_rate_limited(ip):
            # 不寫 audit log（避免被刷爆 audit_log）；nginx access log 已有紀錄
            return JSONResponse(
                status_code=429,
                content={"detail": "請求過於頻繁，請稍後再試"},
                headers={"Retry-After": str(RATE_LIMIT_WINDOW_SEC)},
            )
    return await call_next(request)
