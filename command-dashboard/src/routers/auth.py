import re

import structlog
from fastapi import APIRouter, HTTPException, Request

import core.config as config
from auth.service import (
    client_cert_cn,
    client_cert_verified,
    create_session,
    destroy_session,
    session_remaining,
    session_status,
    validate_session,
)
from repositories._helpers import audit
from repositories.account_repo import (
    clear_default_pin_flag,
    is_first_run_required,
    update_account_pin,
    verify_login,
)
from schemas.auth import ChangeInitialPinIn, LoginIn

log = structlog.get_logger()

router = APIRouter(prefix="/api/auth", tags=["認證"])


@router.post("/login")
def login(body: LoginIn, request: Request):
    acct, reason = verify_login(body.username, body.pin)
    if reason == "locked":
        log.warning("login_failed", msg="登入失敗 — 帳號鎖定",
                    user=body.username,
                    detail={"reason": reason})
        raise HTTPException(423, "帳號暫時鎖定，請 15 分鐘後再試")
    if reason in {"suspended", "archived"}:
        log.warning("login_failed", msg="登入失敗 — 帳號停權",
                    user=body.username,
                    detail={"reason": reason})
        raise HTTPException(403, "帳號已停權")
    if not acct:
        # no_user 與 bad_pin 同樣訊息（不洩漏帳號是否存在）
        log.warning("login_failed", msg="登入失敗",
                    user=body.username,
                    detail={"reason": reason})
        raise HTTPException(401, "帳號或 PIN 錯誤")
    # #275 mTLS：第二因子（裝置憑證）。MTLS_REQUIRED 時須出示綁定本帳號的 client cert
    # （nginx 已 CA 驗證 → X-Client-Cert-Verify=SUCCESS；CN 須等於 account.cert_cn）。
    # 失敗回與 PIN 錯同樣 401，不洩漏是哪個因子。
    cert_cn = None
    if config.ICS_MTLS_REQUIRED:
        if (not client_cert_verified(request)
                or not acct.get("cert_cn")
                or client_cert_cn(request) != acct.get("cert_cn")):
            log.warning("login_failed", msg="登入失敗 — 裝置憑證",
                        user=body.username, detail={"reason": "cert"})
            raise HTTPException(401, "帳號或 PIN 錯誤")
        cert_cn = acct["cert_cn"]
    token = create_session(acct, request, cert_cn=cert_cn)
    audit(acct["username"], None, "login", "accounts", acct["username"],
          {"role": acct["role"]})
    log.info("login_success", msg="登入成功",
             user=acct["username"],
             detail={"role": acct["role"]})
    return {
        "ok":           True,
        "session_id":   token,
        "username":     acct["username"],
        "role":         acct["role"],
        "role_detail":  acct.get("role_detail"),
        "display_name": acct.get("display_name") or acct["username"],
        # C1-A：is_default_pin=1 → 前端強制改 PIN
        "must_change_pin": bool(acct.get("is_default_pin")),
    }


@router.post("/change-initial-pin")
def change_initial_pin(body: ChangeInitialPinIn, request: Request):
    """首次啟動改 PIN（C1-A first-run gate 解除）。

    不需要 admin 系統 PIN，只驗目前帳號 PIN 後更新。
    只在 is_default_pin=1 期間有效（gate 解除後回 403）。
    """
    sess = validate_session(request)
    username = sess["username"]

    if not is_first_run_required():
        raise HTTPException(403, "首次設定已完成，請使用帳號管理改 PIN")

    if not re.match(r'^\d{4,6}$', body.new_pin):
        raise HTTPException(422, "PIN 必須是 4-6 位數字")

    # 驗證目前 PIN（防止 session 被盜用後直接改 PIN）
    acct, reason = verify_login(username, body.current_pin)
    if not acct:
        raise HTTPException(401, "目前 PIN 不正確")

    update_account_pin(username, body.new_pin, username)
    clear_default_pin_flag(username)
    audit(username, None, "initial_pin_changed", "accounts", username, {})
    log.info("initial_pin_changed", msg="首次設定 PIN 修改完成", user=username)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request):
    token = request.headers.get("X-Session-Token")
    sess  = destroy_session(token) if token else None
    if sess:
        audit(sess["username"], None, "SESSION_LOGOUT", "sessions", sess["username"], {})
    return {"ok": True}


@router.get("/heartbeat")
def heartbeat(request: Request):
    sess      = validate_session(request)
    remaining = session_remaining(request.headers.get("X-Session-Token", ""))
    return {"ok": True, "remaining": remaining,
            "username": sess["username"], "role": sess["role"],
            "role_detail": sess.get("role_detail")}


@router.get("/me")
def me(request: Request):
    sess = validate_session(request)
    return {
        "username":     sess["username"],
        "role":         sess["role"],
        "role_detail":  sess.get("role_detail"),
        "display_name": sess.get("display_name", sess["username"]),
    }


session_router = APIRouter(prefix="/api/session", tags=["session"])


@session_router.get("/status")
def status(request: Request):
    token = request.headers.get("X-Session-Token", "")
    return session_status(token)
