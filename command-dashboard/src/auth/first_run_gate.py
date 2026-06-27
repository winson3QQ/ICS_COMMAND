# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
auth/first_run_gate.py — 首次設定 gate（C1-A）

對應規格：commercialization §C1-A
「設定完成前所有 API 回傳 423 Locked」

實務折衷（嚴格全擋會讓 operator 連登入改 PIN 都做不到）：
white-list 「登入 + 改 PIN + 基本入口」，其他擋下。
operator 流程：
  1. 看 console / ~/.ics/first_run_token 拿到隨機 PIN
  2. 訪問前端登入頁（white-listed）
  3. login API 回傳 must_change_pin=true
  4. 強制跳改 PIN 頁，呼叫 PUT /api/admin/accounts/admin/pin（white-listed）
  5. 改 PIN 成功 → is_default_pin=0 → gate 解除，所有 API 開放
"""

from fastapi import Request
from fastapi.responses import JSONResponse

# 即使 first-setup 未完成也允許的 path（完整匹配）
_WHITELIST_EXACT = frozenset(
    {
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/change-initial-pin"),  # first-run 改 PIN，不需 admin PIN
        ("GET", "/api/auth/me"),
        ("GET", "/api/auth/heartbeat"),
        ("GET", "/api/session/status"),
        ("GET", "/api/health"),
        ("GET", "/api/status"),
        ("GET", "/api/version"),  # 前端啟動時 fetch，first-run 期間也需可讀
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
        ("GET", "/"),
    }
)

# Path 前綴白名單
_WHITELIST_PREFIXES = (
    "/static/",
    # 改 PIN：PUT /api/admin/accounts/{username}/pin
    # PIN 路徑含動態 username，用前綴 + 結尾判斷
)


def _is_whitelisted(method: str, path: str) -> bool:
    if (method, path) in _WHITELIST_EXACT:
        return True
    for prefix in _WHITELIST_PREFIXES:
        if path.startswith(prefix):
            return True
    # 改 PIN：PUT /api/admin/accounts/<user>/pin
    if method == "PUT" and path.startswith("/api/admin/accounts/") and path.endswith("/pin"):
        return True
    # CSP violation report 也放行（不該被 first-run 擋）
    if method == "POST" and path == "/api/security/csp-report":
        return True
    return False


async def first_run_gate_middleware(request: Request, call_next):
    """若 is_first_run_required → 非 whitelist 路徑回 423。"""
    # lazy import：避免測試環境啟動時 DB 未就緒
    try:
        from repositories.account_repo import is_first_run_required

        if is_first_run_required():
            if not _is_whitelisted(request.method, request.url.path):
                return JSONResponse(
                    status_code=423,
                    content={
                        "detail": "首次設定未完成。請先以初始 PIN 登入並修改密碼。",
                        "code": "FIRST_RUN_REQUIRED",
                    },
                )
    except Exception:
        # DB / 表尚未建好（極早期啟動），不阻擋
        pass
    return await call_next(request)
