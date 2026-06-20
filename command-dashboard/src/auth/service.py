"""
Server-side session service.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request

import core.config as config
from core.database import get_conn

from .role_enum import normalize_role_pair

log = logging.getLogger(__name__)

SESSION_TIMEOUT = config.SESSION_TIMEOUT
IDLE_TIMEOUT = config.IDLE_TIMEOUT
WARNING_THRESHOLD_SECONDS = config.WARNING_THRESHOLD_SECONDS


EVENT_IDLE_KICKED = "IDLE_KICKED"
EVENT_SESSION_EXPIRED = "SESSION_EXPIRED"
EVENT_BINDING_MISMATCH_IP = "BINDING_MISMATCH_IP"
EVENT_BINDING_MISMATCH_UA = "BINDING_MISMATCH_UA"
EVENT_BINDING_MISMATCH_CERT = "BINDING_MISMATCH_CERT"  # #275 mTLS cert-bound session


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_dt(value: str | None) -> datetime:
    if not value:
        return datetime.fromtimestamp(0, tz=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host


def _ip_prefix(ip: str | None) -> str | None:
    if not ip:
        return None
    parts = ip.split(".")
    if len(parts) == 4:
        return ".".join(parts[:3])
    return ip


def _ua_family(ua: str | None) -> str | None:
    if not ua:
        return None
    ua_l = ua.lower()
    for marker in ("edg", "chrome", "firefox", "safari", "curl", "python", "testclient"):
        if marker in ua_l:
            return marker
    return ua_l.split("/", 1)[0][:40]


def _request_binding(request: Request | None) -> tuple[str | None, str | None]:
    ip = _client_ip(request)
    ua = request.headers.get("User-Agent") if request is not None else None
    return ip, _ua_family(ua)


def client_cert_cn(request: Request | None) -> str | None:
    """mTLS 反代（nginx）注入的 client 憑證 CN（#275）。`X-Client-Cert-CN`。"""
    if request is None:
        return None
    return (request.headers.get("X-Client-Cert-CN") or "").strip() or None


def client_cert_verified(request: Request | None) -> bool:
    """nginx `ssl_verify_client` 結果為 SUCCESS（憑證已過 CA 驗證）。`X-Client-Cert-Verify`。"""
    if request is None:
        return False
    return (request.headers.get("X-Client-Cert-Verify") or "").upper() == "SUCCESS"


def _session_dict(row) -> dict:
    d = dict(row)
    d["role"], d["role_detail"] = normalize_role_pair(d.get("role"), d.get("role_detail"))
    return d


def create_session(
    account: dict, request: Request | None = None, cert_cn: str | None = None
) -> str:
    token = secrets.token_urlsafe(32)
    role, role_detail = normalize_role_pair(account.get("role"), account.get("role_detail"))
    now = _now_iso()
    expires_at = (_now() + timedelta(seconds=SESSION_TIMEOUT)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ip, ua_family = _request_binding(request)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO sessions
               (token, username, role, role_detail, display_name, last_active,
                idle_at, expires_at, ip, user_agent, cert_cn, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')""",
            (
                token,
                account["username"],
                role,
                role_detail,
                account.get("display_name"),
                now,
                now,
                expires_at,
                ip,
                ua_family,
                cert_cn,
            ),
        )
    return token


def validate_session(request: Request) -> dict:
    if hasattr(request.state, "session") and request.state.session:
        return request.state.session
    token = request.headers.get("X-Session-Token")
    if not token:
        raise HTTPException(401, "missing session")
    sess, failure = check_session(token, request=request)
    if failure or sess is None:
        raise HTTPException(401, "invalid session")
    return sess


def check_session(
    token: str,
    request: Request | None = None,
    touch: bool = True,
) -> tuple[dict | None, dict | None]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        if row is None:
            return None, None
        sess = _session_dict(row)
        if sess.get("status") and sess["status"] != "active":
            if sess["status"] == "revoked":
                return None, {
                    "event": None,
                    "session": sess,
                    "audit": False,
                    "reason": "revoked",
                }
            return None, {"event": EVENT_SESSION_EXPIRED, "session": sess}

        now = _now()
        idle_at = min(
            _iso_to_dt(sess.get("idle_at") or sess.get("last_active")),
            _iso_to_dt(sess.get("last_active")),
        )
        expires_at = _iso_to_dt(sess.get("expires_at"))

        if now >= expires_at:
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            return None, {"event": EVENT_SESSION_EXPIRED, "session": sess}
        if (now - idle_at).total_seconds() > min(IDLE_TIMEOUT, SESSION_TIMEOUT):
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            return None, {"event": EVENT_IDLE_KICKED, "session": sess}

        request_ip, request_ua = _request_binding(request)
        stored_ip = sess.get("ip")
        stored_ua = sess.get("user_agent")
        if stored_ip and request_ip and _ip_prefix(stored_ip) != _ip_prefix(request_ip):
            return None, {"event": EVENT_BINDING_MISMATCH_IP, "session": sess}
        if stored_ua and request_ua and stored_ua != request_ua:
            return None, {"event": EVENT_BINDING_MISMATCH_UA, "session": sess}

        # #275 mTLS：cert-bound session。MTLS_REQUIRED 時，session 必須綁定憑證 CN 且
        # 與當前出示的 client cert CN 一致（token 被竊，無對應裝置憑證亦不可用）。
        # 旗標開啟前建立的 session（cert_cn 為空）一律失效，強制帶憑證重新登入。
        # 僅在有 request 可比對時強制：request-less 為內部可信呼叫（無 cert 可出示），
        # 不套 per-request cert 防護，否則背景/狀態驗證會被誤殺。
        if config.ICS_MTLS_REQUIRED and request is not None:
            if not sess.get("cert_cn") or sess.get("cert_cn") != client_cert_cn(request):
                return None, {"event": EVENT_BINDING_MISMATCH_CERT, "session": sess}
            # wave 3：App 層撤銷即時生效——綁定的 CN 一旦被撤（status≠active），
            # 活躍 session 下一個 request 即失效（不必等 token 過期）。
            from repositories.account_cert_repo import is_cert_active
            if not is_cert_active(sess["cert_cn"]):
                return None, {"event": EVENT_BINDING_MISMATCH_CERT, "session": sess}

        if touch:
            now_iso = _now_iso()
            conn.execute(
                "UPDATE sessions SET last_active=?, idle_at=? WHERE token=?",
                (now_iso, now_iso, token),
            )
            sess["last_active"] = now_iso
            sess["idle_at"] = now_iso
        return sess, None


def check_and_touch(token: str) -> dict | None:
    sess, failure = check_session(token)
    return None if failure else sess


def get_session(token: str) -> dict | None:
    sess, failure = check_session(token, touch=False)
    return None if failure else sess


def destroy_session(token: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
        if row:
            conn.execute("UPDATE sessions SET status='revoked' WHERE token = ?", (token,))
            return _session_dict(row)
    return None


def session_remaining(token: str) -> int:
    sess, failure = check_session(token, touch=False)
    if failure or sess is None:
        return 0
    return max(0, int(IDLE_TIMEOUT - (_now() - _iso_to_dt(sess.get("idle_at"))).total_seconds()))


def session_status(token: str) -> dict:
    sess, failure = check_session(token, touch=False)
    if failure or sess is None:
        return {
            "ok": False,
            "valid": False,
            "reason": (failure.get("reason") or failure.get("event")) if failure else "missing",
            "idle_remaining_seconds": 0,
            "absolute_remaining_seconds": 0,
            "warning_threshold_seconds": WARNING_THRESHOLD_SECONDS,
        }
    now = _now()
    return {
        "ok": True,
        "valid": True,
        "username": sess["username"],
        "role": sess["role"],
        "role_detail": sess.get("role_detail"),
        "idle_remaining_seconds": max(0, int(IDLE_TIMEOUT - (now - _iso_to_dt(sess.get("idle_at"))).total_seconds())),
        "absolute_remaining_seconds": max(0, int((_iso_to_dt(sess.get("expires_at")) - now).total_seconds())),
        "warning_threshold_seconds": WARNING_THRESHOLD_SECONDS,
    }


def cleanup_expired_sessions() -> int:
    now_iso = _now_iso()
    # #93(b)：idle cutoff 閾值與 check_session 一致用 min(IDLE_TIMEOUT, SESSION_TIMEOUT)（預設 15 分），
    # 原本誤用 SESSION_TIMEOUT(14h) → abandoned session 卡 14h 才清。改後閒置 15 分即清 + audit。
    # 判定軸用 last_active：本 codebase 每次 touch 都同步寫 last_active=idle_at（見 check_session），
    # 故與 check_session 的 min(idle_at, last_active) 等價；若日後兩者分流更新需同步調整此處。
    idle_secs = min(IDLE_TIMEOUT, SESSION_TIMEOUT)
    idle_cutoff = (_now() - timedelta(seconds=idle_secs)).strftime("%Y-%m-%dT%H:%M:%SZ")
    where = "status='active' AND (expires_at < ? OR last_active < ?)"
    params = (now_iso, idle_cutoff)
    with get_conn() as conn:
        # #93(b)：DELETE ... RETURNING 取「本語句實際刪到」的列（被丟棄 session：切帳號/關頁，token
        # 不再被用，逾時只能靠本批次清，原本不 audit → 登出無痕）。用 RETURNING 而非先 SELECT 再 DELETE：
        # 兩個 cleanup 並發時各自只拿到自己刪到的列（SQLite 寫入序列化），避免同一 session 被重複 audit。
        expired = conn.execute(f"DELETE FROM sessions WHERE {where} RETURNING username", params).fetchall()
    n = len(expired)
    # audit 在 conn 區塊外（audit() 自開連線，避免巢狀 get_conn lock）；best-effort：清理不因記帳失敗中斷。
    # 殘餘窗口：DELETE 已 commit 但 audit 尚未寫入時 crash → 該筆登出無痕（可接受，非安全關鍵路徑）。
    from repositories._helpers import audit

    for row in expired:
        try:
            audit(
                row["username"],
                None,
                "SESSION_EXPIRED",
                "sessions",
                row["username"],
                {"reason": "idle_or_expired_cleanup", "decision": "auto-logout"},
            )
        except Exception:
            # 黙殺會讓「session 已清但 audit 漏記」無從察覺，故至少留 warning（不中斷批次）。
            log.warning("[session] cleanup audit 寫入失敗 user=%s（best-effort）", row["username"], exc_info=True)
    return n
