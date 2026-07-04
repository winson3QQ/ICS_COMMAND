# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
import structlog
from fastapi import APIRouter, HTTPException, Request, Response

import core.config as config
from auth.service import (
    clear_session_cookie,
    client_cert_cn,
    client_cert_verified,
    create_session,
    destroy_session,
    extract_token,
    session_remaining,
    session_status,
    set_session_cookie,
    validate_session,
)
from core.pin_policy import validate_pin_strength  # #348-F5 P1
from repositories._helpers import audit
from repositories.account_cert_repo import account_id_for_username, cert_active_for_account, is_mtls_bootstrap
from repositories.account_repo import (
    account_needs_pin_change,
    clear_default_pin_flag,
    update_account_pin,
    verify_login,
)
from schemas.auth import ChangeInitialPinIn, LoginIn
from services.security_monitor import security_alert

log = structlog.get_logger()

router = APIRouter(prefix="/api/auth", tags=["認證"])


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    # #275 wave 4 鎖定-DoS 緩解：出示綁定本帳號的有效裝置憑證者，帳號鎖定不擋（合法本人
    # 的裝置永不被攻擊者鎖死；無裝置證者仍受鎖定保護＝反爆破照舊）。在 verify_login 前算，
    # 因鎖定判斷在其內。account_id 由 username 解析（與 verify_login 的帳號查詢正交）。
    bypass_lockout = False
    if config.ICS_MTLS_REQUIRED and client_cert_verified(request):
        _cn = client_cert_cn(request)
        _aid = account_id_for_username(body.username)
        if _cn and _aid and cert_active_for_account(_aid, _cn):
            bypass_lockout = True
    acct, reason = verify_login(body.username, body.pin, bypass_lockout=bypass_lockout)
    if reason == "locked":
        log.warning("login_failed", msg="登入失敗 — 帳號鎖定", user=body.username, detail={"reason": reason})
        # #280 H：帳號鎖定 = 持續爆破訊號（與 IP 無關，可靠）
        security_alert("account_locked", "帳號鎖定（疑似密碼爆破）", user=body.username)
        raise HTTPException(423, "帳號暫時鎖定，請 15 分鐘後再試")
    if reason in {"suspended", "archived"}:
        log.warning("login_failed", msg="登入失敗 — 帳號停權", user=body.username, detail={"reason": reason})
        raise HTTPException(403, "帳號已停權")
    if not acct:
        # no_user 與 bad_pin 同樣訊息（不洩漏帳號是否存在）
        log.warning("login_failed", msg="登入失敗", user=body.username, detail={"reason": reason})
        raise HTTPException(401, "帳號或 PIN 錯誤")
    # #275 mTLS：第二因子（裝置憑證）。MTLS_REQUIRED 時須出示綁定本帳號的 client cert
    # （nginx 已 CA 驗證 → X-Client-Cert-Verify=SUCCESS；CN 須為本帳號 active 綁定）。
    # wave 3：per-device，查 account_certs 表（一帳號可多裝置；撤銷即時失效）。
    # 失敗回與 PIN 錯同樣 401，不洩漏是哪個因子。
    cert_cn = None
    if config.ICS_MTLS_REQUIRED:
        presented = client_cert_cn(request)
        cert_verified = client_cert_verified(request)
        # #306 bootstrap：全新部署（唯一帳號、零綁定）+ 出示 nginx 已 CA 驗證的證 → 放行
        # 用該證登入並暫綁進 session，讓首位 admin 進面板綁第一張證（免手動翻 ICS_MTLS_REQUIRED）。
        # 仍需 first-run PIN（上方 verify_login）+ CA-signed cert 雙重前提；綁定後窗口自動關。
        if cert_verified and presented and is_mtls_bootstrap():
            cert_cn = presented
        elif not cert_verified or not presented or not cert_active_for_account(acct["id"], presented):
            log.warning("login_failed", msg="登入失敗 — 裝置憑證", user=body.username, detail={"reason": "cert"})
            # #280 H：PIN 對但裝置證不符/缺/撤銷 = 盜 PIN 或裝置不符的高訊號
            security_alert("cert_factor_failed", "第二因子（裝置憑證）失敗", user=body.username)
            raise HTTPException(401, "帳號或 PIN 錯誤")
        else:
            cert_cn = presented
    token = create_session(acct, request, cert_cn=cert_cn)
    # #293 階段1：同時種 httpOnly cookie（斷 XSS 竊 token）+ 保留 body session_id（前端相容期照舊）。
    # 階段2 前端改吃 cookie 後，body session_id 才移除（真正斷根，見 #293）。
    set_session_cookie(response, token)
    audit(acct["username"], None, "login", "accounts", acct["username"], {"role": acct["role"]})
    log.info("login_success", msg="登入成功", user=acct["username"], detail={"role": acct["role"]})
    return {
        "ok": True,
        "session_id": token,
        "username": acct["username"],
        "role": acct["role"],
        "role_detail": acct.get("role_detail"),
        "display_name": acct.get("display_name") or acct["username"],
        # C1-A：is_default_pin=1 → 前端強制改 PIN
        "must_change_pin": bool(acct.get("is_default_pin")),
    }


@router.post("/change-initial-pin")
def change_initial_pin(body: ChangeInitialPinIn, request: Request):
    """改初始/admin 給的 PIN（清 is_default_pin）。

    不需要 admin 系統 PIN，只驗目前帳號 PIN 後更新。
    #348-F5 P2a：原限 is_first_run_required（只第一個 admin）→ 改判「此帳號 is_default_pin=1」，
    使**任何 admin 新建、待改初始 PIN 的帳號**皆可走此端點（first-admin bootstrap 仍是其特例）。
    """
    sess = validate_session(request)
    username = sess["username"]

    if not account_needs_pin_change(username):
        raise HTTPException(403, "此帳號無待改的初始 PIN（如需改 PIN 請洽管理員）")

    validate_pin_strength(body.new_pin, username)  # #348-F5 P1：min6 + 擋可預測 + 開放長密語

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
def logout(request: Request, response: Response):
    token = extract_token(request)
    sess = destroy_session(token) if token else None
    if sess:
        audit(sess["username"], None, "SESSION_LOGOUT", "sessions", sess["username"], {})
    clear_session_cookie(response)  # #293：清 httpOnly cookie（server session 已 destroy，cookie 也一併退）
    return {"ok": True}


@router.get("/heartbeat")
def heartbeat(request: Request):
    sess = validate_session(request)
    remaining = session_remaining(extract_token(request) or "")
    return {
        "ok": True,
        "remaining": remaining,
        "username": sess["username"],
        "role": sess["role"],
        "role_detail": sess.get("role_detail"),
        # #293 階段2：新分頁/reload 靠 heartbeat（cookie 認）還原顯示態 → 需帶 display_name 補使用者徽章
        "display_name": sess.get("display_name") or sess["username"],
    }


@router.get("/me")
def me(request: Request):
    sess = validate_session(request)
    return {
        "username": sess["username"],
        "role": sess["role"],
        "role_detail": sess.get("role_detail"),
        "display_name": sess.get("display_name", sess["username"]),
    }


session_router = APIRouter(prefix="/api/session", tags=["session"])


@session_router.get("/status")
def status(request: Request):
    token = extract_token(request) or ""
    return session_status(token)
