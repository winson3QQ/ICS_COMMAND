from fastapi import APIRouter, HTTPException, Request, Response

from auth.role_enum import (
    ROLE_COMMANDER,
    ROLE_OBSERVER,
    ROLE_OPERATOR,
    ROLE_SYSADMIN,
    require_role,
    role_zh_to_en,
)
from auth.service import validate_session
from core.database import get_conn, get_schema_version
import core.config as config
from core.input_safety import validate_no_unsafe_strings
from repositories._helpers import audit
from repositories.account_cert_repo import (
    account_id_for_username,
    bind_cert,
    is_cert_active,
    is_valid_cert_cn,
    list_certs,
    revoke_cert,
)
from repositories.account_repo import (
    clear_default_pin_flag,
    create_account,
    delete_account,
    get_account,
    get_all_accounts,
    is_valid_account_role,
    suspend_all_accounts,
    update_account_display_name,
    update_account_pin,
    update_account_role,
    update_account_status,
)
from repositories.audit_repo import get_audit_log
from repositories.config_repo import get_config, set_admin_pin
from repositories.pi_node_repo import (
    create_pi_node,
    delete_pi_node,
    list_pi_nodes,
    revoke_pi_node_key,
)
from schemas.admin import (
    AccountCertBindIn,
    AccountCreateIn,
    AccountStatusIn,
    AdminPinIn,
    DisplayNameUpdateIn,
    PiNodeCreateIn,
    PinResetIn,
    RoleUpdateIn,
    SuspendAllIn,
    RetentionToggleIn,
)
from services.realtime_hub import cop_hub  # issue #29 PR-G1b：reset 後廣播 resync

router = APIRouter(prefix="/api/admin", tags=["account-admin"])


SUBORDINATE_ROLES = frozenset({ROLE_OPERATOR, ROLE_OBSERVER})


def _session_role(session: dict) -> str | None:
    return role_zh_to_en(session.get("role"), session.get("role_detail"))


def _check_system_admin(request: Request) -> dict:
    return require_role(ROLE_SYSADMIN)(validate_session(request))


def _check_admin_pin(request: Request) -> dict:
    return _check_system_admin(request)


def _check_account_manager(request: Request) -> dict:
    return require_role(ROLE_SYSADMIN, ROLE_COMMANDER)(validate_session(request))


def _is_commander(session: dict) -> bool:
    return _session_role(session) == ROLE_COMMANDER


def _normalized_request_role(role: str | None, role_detail: str | None = None) -> str | None:
    return role_zh_to_en(role, role_detail)


def _require_commander_new_role_allowed(session: dict, role: str | None, role_detail: str | None = None) -> None:
    requested_role = _normalized_request_role(role, role_detail)
    if _is_commander(session) and requested_role not in SUBORDINATE_ROLES:
        raise HTTPException(403, "commander may manage subordinate accounts only")


def _require_commander_target_allowed(session: dict, username: str) -> dict:
    target = get_account(username)
    if target is None:
        raise HTTPException(404, "account not found")
    if _is_commander(session) and _session_role(target) not in SUBORDINATE_ROLES:
        raise HTTPException(403, "commander may manage subordinate accounts only")
    return target


@router.get("/status", tags=["account-admin"])
def admin_status(request: Request):
    _check_system_admin(request)
    raw = get_config("admin_pin")
    with get_conn() as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) as c FROM accounts WHERE COALESCE(status, 'active') != 'archived'"
        ).fetchone()["c"]
        schema_ver = get_schema_version(conn)
    return {"admin_pin_setup": raw is not None, "active_accounts": cnt, "schema_version": schema_ver}


@router.get("/schema-migrations", tags=["account-admin"])
def list_migrations(request: Request):
    _check_system_admin(request)
    with get_conn() as conn:
        rows = conn.execute("SELECT version, name, applied_at FROM schema_migrations ORDER BY version").fetchall()
    return [dict(r) for r in rows]


@router.get("/accounts")
def list_accounts(request: Request):
    sess = _check_account_manager(request)
    accounts = get_all_accounts()
    if _is_commander(sess):
        return [a for a in accounts if _session_role(a) in SUBORDINATE_ROLES]
    return accounts


@router.post("/accounts")
def create_acct(body: AccountCreateIn, request: Request):
    sess = _check_account_manager(request)
    if not is_valid_account_role(body.role, body.role_detail):
        raise HTTPException(422, "role invalid")
    _require_commander_new_role_allowed(sess, body.role, body.role_detail)
    if len(body.pin) < 4 or len(body.pin) > 6 or not body.pin.isdigit():
        raise HTTPException(422, "PIN must be 4-6 digits")
    # display_name 會被 account 列表拼進 innerHTML（auth.js admLoadAccounts）→ XSS sink，落 disk 前擋。
    if body.display_name:
        validate_no_unsafe_strings(body.display_name, label="display_name", max_len=64)
    try:
        return create_account(
            body.username,
            body.pin,
            body.role,
            body.display_name,
            body.role_detail,
            sess["username"],
        )
    except Exception as e:
        raise HTTPException(409, f"account create failed: {e}") from e


@router.delete("/accounts/{username}")
def delete_acct(username: str, request: Request):
    sess = _check_account_manager(request)
    _require_commander_target_allowed(sess, username)
    if not delete_account(username, sess["username"]):
        raise HTTPException(404, "account not found")
    return {"ok": True}


@router.put("/accounts/{username}/status")
def update_status(username: str, body: AccountStatusIn, request: Request):
    sess = _check_account_manager(request)
    _require_commander_target_allowed(sess, username)
    if body.status not in ("active", "suspended"):
        raise HTTPException(422, "status must be active or suspended")
    if not update_account_status(username, body.status, sess["username"]):
        raise HTTPException(404, "account not found")
    return {"ok": True}


@router.put("/accounts/{username}/pin")
def reset_pin(username: str, body: PinResetIn, request: Request):
    sess = _check_account_manager(request)
    _require_commander_target_allowed(sess, username)
    if len(body.new_pin) < 4 or len(body.new_pin) > 6 or not body.new_pin.isdigit():
        raise HTTPException(422, "PIN must be 4-6 digits")
    if not update_account_pin(username, body.new_pin, sess["username"]):
        raise HTTPException(404, "account not found")
    clear_default_pin_flag(username)
    return {"ok": True}


@router.put("/accounts/{username}/role")
def update_role(username: str, body: RoleUpdateIn, request: Request):
    sess = _check_account_manager(request)
    _require_commander_target_allowed(sess, username)
    if not is_valid_account_role(body.role, body.role_detail):
        raise HTTPException(422, "role invalid")
    _require_commander_new_role_allowed(sess, body.role, body.role_detail)
    if not update_account_role(username, body.role, sess["username"], body.role_detail):
        raise HTTPException(404, "account not found")
    return {"ok": True}


@router.put("/accounts/{username}/display-name")
def update_display_name(username: str, body: DisplayNameUpdateIn, request: Request):
    sess = _check_account_manager(request)
    _require_commander_target_allowed(sess, username)
    # display_name 會被 account 列表拼進 innerHTML（XSS sink）→ 落 disk 前擋。
    validate_no_unsafe_strings(body.display_name, label="display_name", max_len=64)
    if not update_account_display_name(username, body.display_name, sess["username"]):
        raise HTTPException(404, "account not found")
    return {"ok": True}


@router.put("/pin")
def change_pin(body: AdminPinIn, request: Request):
    sess = _check_system_admin(request)
    if len(body.new_pin) < 4 or len(body.new_pin) > 6 or not body.new_pin.isdigit():
        raise HTTPException(422, "PIN must be 4-6 digits")
    set_admin_pin(body.new_pin, sess["username"])
    return {"ok": True}


@router.post("/reset-db", tags=["system"])
async def reset_db(request: Request):
    sess = _check_system_admin(request)
    tables = [
        "snapshots",
        "events",
        "decisions",
        "predictions",
        "manual_records",
        "sync_log",
        "pi_received_batches",
        "audit_log",
        "ttx_injects",
        "exercises",
        "resource_snapshots",
        "aar_entries",
        "ai_recommendations",
        # issue #29 PR-G1b：COP 即時同步表（事件/route/polygon/＋標記 圖釘）。
        # 全清＝乾淨起點，並修「reset 清不到 cop_entities → 事件記錄沒了但圖釘留孤兒」的破口。
        "cop_entity_tracks",
        "cop_entity_links",
        "cop_entities",
    ]
    with get_conn() as conn:
        for table in tables:
            try:
                conn.execute(f"DELETE FROM {table}")  # nosec B608
            except Exception:
                pass
    audit(sess["username"], None, "db_reset", "system", "all", {"tables": tables})
    # issue #29 PR-G1b：cop_entities 被 raw SQL 清空、不會自動發 per-entity WS delete。
    # 廣播 resync → 各 client 重新 GET /api/cop/entities 對帳（清掉 server 已無者），
    # 否則其他瀏覽器的事件/圖釘殘留到手動 reload。exercise_id=None → 廣播給所有連線。
    await cop_hub.broadcast_all({"op": "resync"})  # P1-14：strict wants 後改 broadcast_all 確保全連線收到
    return {"ok": True, "cleared_tables": tables}


@router.post("/reset-exercise", tags=["system"])
async def reset_exercise(request: Request):
    sess = _check_system_admin(request)
    ex_tables = ["ttx_injects", "exercises", "resource_snapshots", "aar_entries",
                 "ai_recommendations", "exercise_kpis"]
    # issue #29 PR-G1b：cop_entities 有 exercise_id，演習重設一併清演習場域的 COP 圖釘
    # （事件/route/polygon）。tracks/links 無 exercise_id（references uid ON DELETE CASCADE）；
    # PRAGMA foreign_keys=ON，故刪 cop_entities 時 tracks/links 自動級聯，無 orphan。
    data_tables = ["snapshots", "events", "decisions", "manual_records", "audit_log", "cop_entities"]
    cleared = {}
    with get_conn() as conn:
        for table in ex_tables:
            try:
                cur = conn.execute(f"DELETE FROM {table}")  # nosec B608
                cleared[table] = cur.rowcount
            except Exception:
                pass
        for table in data_tables:
            try:
                cur = conn.execute(f"DELETE FROM {table} WHERE exercise_id IS NOT NULL")  # nosec B608
                cleared[table] = cur.rowcount
            except Exception:
                pass
    audit(sess["username"], None, "exercise_reset", "system", "all", {"cleared": cleared})
    await cop_hub.broadcast_all({"op": "resync"})  # P1-14：strict wants 後改 broadcast_all 確保全連線收到  # 同 reset-db：各 client 對帳清掉演習場域圖釘
    return {"ok": True, "cleared": cleared}


@router.post("/suspend-all")
def suspend_all(body: SuspendAllIn, request: Request):
    sess = _check_system_admin(request)
    # OP-1（#153）：不可逆批次停權強制確認字串（後端把關，不依賴前端 dialog 防誤點）。
    if body.confirm != "SUSPEND_ALL":
        raise HTTPException(422, 'confirm 必須為 "SUSPEND_ALL"（不可逆批次停權確認）')
    # suspend_all_accounts 已排除發起者本人（防自鎖，見 account_repo）。
    count = suspend_all_accounts(sess["username"])
    return {"ok": True, "suspended_count": count}


@router.get("/audit-log")
def audit_log(request: Request, limit: int = 100):
    _check_system_admin(request)
    # RT-L4（#153）：服務端 clamp，呼叫端不得全控 limit。上限 1000 防 ?limit=999999 慢查詢/
    # 記憶體壓力；下限 0 防負值（SQLite `LIMIT -1` = 無上限，負數會反成「全撈」破口）。
    limit = max(0, min(limit, 1000))
    return get_audit_log(limit)


@router.get("/pi-nodes", tags=["pi-nodes"])
def list_nodes(request: Request):
    _check_system_admin(request)
    return list_pi_nodes()


@router.post("/pi-nodes", tags=["pi-nodes"])
def create_node(body: PiNodeCreateIn, request: Request):
    _check_system_admin(request)
    allowed = ("shelter", "medical", "forward", "security")
    if body.unit_id not in allowed:
        raise HTTPException(422, f"unit_id must be one of {allowed}")
    try:
        return create_pi_node(body.unit_id, body.label)
    except Exception as e:
        if "UNIQUE" in str(e) or "PRIMARY" in str(e):
            raise HTTPException(409, f"unit_id '{body.unit_id}' already exists") from e
        raise


@router.delete("/pi-nodes/{unit_id}", tags=["pi-nodes"])
def delete_node(unit_id: str, request: Request):
    _check_system_admin(request)
    if not delete_pi_node(unit_id):
        raise HTTPException(404, "pi node not found")
    return {"ok": True}


@router.post("/pi-nodes/{unit_id}/rekey", tags=["pi-nodes"])
def rekey_node(unit_id: str, request: Request):
    _check_system_admin(request)
    result = revoke_pi_node_key(unit_id)
    if not result:
        raise HTTPException(404, "pi node not found")
    return result


# ── 軌跡 PII retention 開關（P2-20 收尾 / #207，threat_model §8.4 政策乙案）──────


@router.get("/retention", tags=["account-admin"])
def get_retention(request: Request):
    """retention 狀態（sysadmin；中央 gate /api/admin/ = SYSADMIN_ONLY 已涵蓋，
    endpoint 內 _check_system_admin 為雙保險，同本檔慣例）。"""
    _check_system_admin(request)
    from core import config as _cfg
    from services import retention_service

    return {
        "tracks_ttl_enabled": retention_service.ttl_enabled(),
        "tracks_ttl_days": _cfg.TRACKS_TTL_DAYS,
    }


@router.post("/retention", tags=["account-admin"])
def set_retention(body: RetentionToggleIn, request: Request):
    """開/關軌跡 TTL 清理。audit-first（RETENTION_TOGGLE，個資刪除政策變更須留痕）。
    開啟時立即跑一次清理（不等每日排程），回傳本次刪除筆數。"""
    sess = _check_system_admin(request)
    from services import retention_service

    audit(sess["username"], None, "RETENTION_TOGGLE", "config",
          "retention.tracks_ttl_enabled", {"enabled": body.enabled})
    retention_service.set_ttl_enabled(body.enabled)
    deleted = retention_service.cleanup_expired_tracks() if body.enabled else 0
    return {"ok": True, "enabled": body.enabled, "deleted_now": deleted}


# ── #275 wave 3：per-device 裝置憑證綁定生命週期（sysadmin only）─────────────
# cert 簽發（step-ca）在主機外執行（deploy/step-ca/issue-client-cert.sh）；本 API 管的是
# 「CN ↔ 帳號」的綁定/撤銷（App 層第二因子授權）。撤銷即時失效（check_session 查表）。


def _account_id_or_404(username: str) -> int:
    account_id = account_id_for_username(username)
    if account_id is None:
        raise HTTPException(404, "account not found")
    return account_id


def _validated_cert_cn(body: AccountCertBindIn) -> str:
    """綁定/發證共用：HTML/JS escape 防護 + CN 合法性（逗號/前導 dash/字元集）。"""
    validate_no_unsafe_strings(body.cert_cn, body.label or "")
    cn = body.cert_cn.strip()
    if not is_valid_cert_cn(cn):
        raise HTTPException(422, "cert_cn 不合法（不可含逗號、不可 - 開頭，限字母/數字/空白/-_.@）")
    return cn


@router.get("/accounts/{username}/certs", tags=["account-admin"])
def list_account_certs(username: str, request: Request):
    _check_system_admin(request)
    return list_certs(_account_id_or_404(username))


@router.post("/accounts/{username}/certs", tags=["account-admin"])
def bind_account_cert(username: str, body: AccountCertBindIn, request: Request):
    sess = _check_system_admin(request)
    account_id = _account_id_or_404(username)
    cn = _validated_cert_cn(body)
    try:
        return bind_cert(account_id, cn, body.label, sess["username"])
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/accounts/{username}/certs/issue", tags=["account-admin"])
def issue_account_cert(username: str, body: AccountCertBindIn, request: Request):
    """#275 wave B-2：線上發證（選項 i 安全版）。後端呼叫 step-ca daemon 簽證（CA 鑰不進
    後端）→ 自動綁定 CN ↔ 帳號 → 回傳 p12 下載。未配置 step-ca 時回 503（改走離線簽 + 綁定）。"""
    sess = _check_system_admin(request)
    account_id = _account_id_or_404(username)
    if not config.step_ca_configured():
        raise HTTPException(503, "線上發證未配置；請用 deploy/step-ca 離線簽 + 手動綁定")
    cn = _validated_cert_cn(body)
    if is_cert_active(cn):
        raise HTTPException(409, "此 CN 已被有效綁定")
    from services.cert_issuance import CertIssuanceError, issue_p12
    try:
        p12 = issue_p12(cn)
    except CertIssuanceError as e:
        raise HTTPException(502, f"發證失敗：{e}")
    bind_cert(account_id, cn, body.label, sess["username"])
    audit(sess["username"], None, "cert_issue", "account_certs", cn,
          {"account_id": account_id, "cert_cn": cn, "online": True})
    # cn 已過 is_valid_cert_cn（無逗號/控制字元）；filename 再收斂為 alnum+-_.
    safe = "".join(c for c in cn if c.isalnum() or c in "-_.") or "client"
    return Response(
        content=p12,
        media_type="application/x-pkcs12",
        headers={"Content-Disposition": f'attachment; filename="{safe}.p12"'},
    )


@router.delete("/accounts/{username}/certs/{cert_id}", tags=["account-admin"])
def revoke_account_cert(username: str, cert_id: int, request: Request):
    sess = _check_system_admin(request)
    account_id = _account_id_or_404(username)
    result = revoke_cert(cert_id, sess["username"], account_id=account_id)
    if result is None:
        raise HTTPException(404, "active cert binding not found")
    return result
