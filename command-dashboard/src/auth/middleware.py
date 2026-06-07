from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from core.config import AUTH_EXEMPT_EXACT, AUTH_EXEMPT_PREFIXES
from repositories._helpers import audit

from .role_enum import allowed_roles_for, is_role_allowed
from .service import check_session


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

        request.state.session = sess

    return await call_next(request)
