from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from core.config import AUTH_EXEMPT_EXACT, AUTH_EXEMPT_PREFIXES
from repositories._helpers import audit

from .role_enum import allowed_roles_for, is_role_allowed
from .service import check_session

# #348-F5 P2a：帳號 is_default_pin=1（待改 admin 給的初始 PIN）時，session 僅准走這些路徑，其餘
# API 回 423 → server-side 真強制改 PIN（不只靠前端 must_change_pin flow）。改完 default 清即解。
_PIN_CHANGE_ALLOWED = frozenset(
    {
        ("POST", "/api/auth/change-initial-pin"),
        ("POST", "/api/auth/logout"),
        ("GET", "/api/auth/me"),
        ("GET", "/api/auth/heartbeat"),
        ("GET", "/api/session/status"),
    }
)


def _is_pin_change_allowed(method: str, path: str) -> bool:
    """改初始 PIN 期間放行的路徑（與 first_run_gate 白名單一致，含 admin 改 PIN reset 端點）。"""
    if (method, path) in _PIN_CHANGE_ALLOWED:
        return True
    # 改 PIN：PUT /api/admin/accounts/<user>/pin（first-admin/README 流程經此；reset_pin 清 default）
    if method == "PUT" and path.startswith("/api/admin/accounts/") and path.endswith("/pin"):
        return True
    return False


def _audit_session_failure(event: str, session: dict | None, request: Request) -> None:
    audit(
        (session or {}).get("username", "unknown"),
        None,
        event,
        "sessions",
        (session or {}).get("token", ""),
        {
            "reason": event,
            "decision": "deny",
            "consequence": "request rejected",
            "request": {"method": request.method, "path": request.url.path},
        },
    )


async def auth_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method

    if (method, path) in AUTH_EXEMPT_EXACT:
        return await call_next(request)
    if any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
        return await call_next(request)
    if path == "/":
        return await call_next(request)
    # P1-02：ingress 主路徑 + 舊路徑別名（向後相容 ICS_DMAS Pi client）
    if path.startswith("/api/ingress/pi-node/") or path.startswith("/api/pi-push/"):
        return await call_next(request)
    if method == "POST" and path == "/api/snapshots":
        return await call_next(request)
    if method == "POST" and path == "/api/sync/push":
        return await call_next(request)
    # RT-H1（#153）：移除 `GET /api/snapshots/{type}` 的匿名豁免 —— 該路由無呼叫者
    # （dashboard 走 service 層 get_snapshots），孤兒豁免讓資源快照（床位/傷亡聚合）匿名可讀。
    # 移除後走預設 READ_ROLES gate。POST /api/snapshots 的 HMAC 豁免（上方，Pi push 命脈）保留。
    if path in ("/api/health", "/api/status"):
        return await call_next(request)

    if path.startswith("/api/"):
        token = request.headers.get("X-Session-Token")
        if not token:
            return JSONResponse({"detail": "missing session"}, status_code=401)
        touch_session = path != "/api/session/status"
        sess, failure = check_session(token, request=request, touch=touch_session)
        if failure or sess is None:
            if failure and failure.get("event") and failure.get("audit", True):
                _audit_session_failure(failure["event"], failure.get("session"), request)
            return JSONResponse({"detail": "invalid session"}, status_code=401)

        allowed = allowed_roles_for(method, path)
        if allowed is not None and not is_role_allowed(sess, allowed):
            audit(
                sess["username"],
                None,
                "ROLE_DENIED",
                "http_endpoint",
                f"{method} {path}",
                {
                    "reason": "role_not_allowed",
                    "decision": "deny",
                    "consequence": "request rejected",
                    "request": {"method": method, "path": path},
                    "allowed_roles": sorted(allowed),
                    "role": sess.get("role"),
                    "role_detail": sess.get("role_detail"),
                },
            )
            return JSONResponse({"detail": "role denied"}, status_code=403)

        # #348-F5 P2a：per-account 強制改初始 PIN（在授權通過後）。非改 PIN 白名單路徑時才查
        # is_default_pin（白名單先短路、省 DB），待改即 423。改完（clear_default_pin_flag）即解。
        if not _is_pin_change_allowed(method, path):
            from repositories.account_repo import account_needs_pin_change

            if account_needs_pin_change(sess["username"]):
                return JSONResponse(
                    {"detail": "須先修改初始 PIN", "code": "PIN_CHANGE_REQUIRED"},
                    status_code=423,
                )

        request.state.session = sess

    return await call_next(request)
