import asyncio  # noqa: E402 — P1-12b L3 pre-destructive backup
import os  # noqa: E402 — #315 TAK_DEVICE_CA_DIR 路徑檢查

import structlog  # noqa: E402
from fastapi import APIRouter, HTTPException, Request, Response

import core.config as config
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
from core.input_safety import validate_no_unsafe_strings
from repositories._helpers import audit
from repositories.account_cert_repo import (
    account_id_for_username,
    bind_cert,
    is_cert_active,
    is_valid_cert_cn,
    list_certs,
    purge_revoked_certs,
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
    FactionClassifyIn,
    FactionOverrideIn,
    PiNodeCreateIn,
    PinResetIn,
    RetentionToggleIn,
    RoleUpdateIn,
    SuspendAllIn,
)
from services import faction_service  # #343 紅藍隔離 admin 分類
from services.realtime_hub import cop_hub  # issue #29 PR-G1b：reset 後廣播 resync

log = structlog.get_logger()

router = APIRouter(prefix="/api/admin", tags=["account-admin"])


SUBORDINATE_ROLES = frozenset({ROLE_OPERATOR, ROLE_OBSERVER})


def _session_role(session: dict) -> str | None:
    return role_zh_to_en(session.get("role"), session.get("role_detail"))


def _check_system_admin(request: Request) -> dict:
    return require_role(ROLE_SYSADMIN)(validate_session(request))


async def _require_reset_confirm(request: Request) -> None:
    """P1-12b OP-2：不可逆操作強制 body 帶 {"confirm": "RESET"}（422）。

    在 _check_system_admin 之後呼叫，故 RBAC（403）仍先於 body 驗證 —— 不依賴
    前端 dialog，後端硬擋誤觸。
    """
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict) or body.get("confirm") != "RESET":
        raise HTTPException(422, '不可逆操作：需在 body 帶 {"confirm": "RESET"} 確認')


async def _pre_destructive_backup(trigger: str) -> str | None:
    """P1-12b L3：不可逆操作前 best-effort 整包備份。無金鑰（dev）→ None；
    失敗不擋操作（記 warning + audit 留 None）。"""
    from core.config import DATA_DIR
    from services import user_data_backup_service as uds

    if not uds.key_available():
        return None
    try:
        res = await asyncio.to_thread(uds.create_backup, DATA_DIR, DATA_DIR / "backups", trigger=trigger)
        return res.path.name
    except Exception:
        log.warning("pre_destructive_backup_failed", msg=f"{trigger} 前備份失敗（best-effort）", exc_info=True)
        return None


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


def _require_not_last_sysadmin(username: str, *, will_remain_sysadmin: bool = False) -> None:
    """#354 防自鎖：系統內最後一個 status=active 且 role=sysadmin 的帳號，不得被
    降級 / 停用 / 封存（否則零管理能力，只能 shell 直操 DB 救回）。

    與 suspend_all_accounts（account_repo.py，#153）排除發起者本人同源防呆，
    補上單筆 role/status/delete 漏掉的同一條守門。不分是否改自己 —— 只在「會把
    管理能力歸零」那一刻擋下；多 sysadmin 時不受影響。

    will_remain_sysadmin=True（改角色但新角色仍是 sysadmin）→ 放行，因不減少 active
    sysadmin 數。sysadmin 判定用正規 role_zh_to_en（不沿用 is_first_run_required 的
    手寫 SQL predicate），與本 router 其餘 role 判定一致。
    """
    if will_remain_sysadmin:
        return
    active_sysadmins = [
        a
        for a in get_all_accounts()  # 已濾掉 archived
        if (a.get("status") or "active") == "active" and _session_role(a) == ROLE_SYSADMIN
    ]
    if len(active_sysadmins) <= 1 and any(a["username"] == username for a in active_sysadmins):
        raise HTTPException(409, "不可降級／停用／封存系統內最後一個有效系統管理員（會導致自鎖）")


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
    _require_not_last_sysadmin(username)  # #354 防自鎖（archive 會移除該 sysadmin）
    if not delete_account(username, sess["username"]):
        raise HTTPException(404, "account not found")
    return {"ok": True}


@router.put("/accounts/{username}/status")
def update_status(username: str, body: AccountStatusIn, request: Request):
    sess = _check_account_manager(request)
    _require_commander_target_allowed(sess, username)
    if body.status not in ("active", "suspended"):
        raise HTTPException(422, "status must be active or suspended")
    if body.status == "suspended":
        _require_not_last_sysadmin(username)  # #354 防自鎖（設回 active 不擋）
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
    # #354 防自鎖：降走最後一個 sysadmin 才擋；新角色仍是 sysadmin 則放行
    _require_not_last_sysadmin(
        username,
        will_remain_sysadmin=role_zh_to_en(body.role, body.role_detail) == ROLE_SYSADMIN,
    )
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
    await _require_reset_confirm(request)  # OP-2
    pre_backup = await _pre_destructive_backup("pre-reset-db")  # L3
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
        # #237：通聯（chats，P2-07 #129 加表時漏進清單）—— ICS-214 通聯 PII，reset 須清，
        # 否則髒起點 + AAR 時間軸混入上場舊通聯。
        "chats",
        # #343：紅藍 client 分類（per-exercise）—— reset 須清，否則新場沿用舊分類。
        "client_faction",
    ]
    with get_conn() as conn:
        for table in tables:
            try:
                conn.execute(f"DELETE FROM {table}")  # nosec B608
            except Exception:
                pass
    audit(sess["username"], None, "db_reset", "system", "all", {"tables": tables, "pre_backup": pre_backup})
    # issue #29 PR-G1b：cop_entities 被 raw SQL 清空、不會自動發 per-entity WS delete。
    # 廣播 resync → 各 client 重新 GET /api/cop/entities 對帳（清掉 server 已無者），
    # 否則其他瀏覽器的事件/圖釘殘留到手動 reload。exercise_id=None → 廣播給所有連線。
    await cop_hub.broadcast_all({"op": "resync"})  # P1-14：strict wants 後改 broadcast_all 確保全連線收到
    return {"ok": True, "cleared_tables": tables, "pre_backup": pre_backup}


@router.post("/reset-exercise", tags=["system"])
async def reset_exercise(request: Request):
    sess = _check_system_admin(request)
    await _require_reset_confirm(request)  # OP-2
    pre_backup = await _pre_destructive_backup("pre-reset-exercise")  # L3
    ex_tables = ["ttx_injects", "exercises", "resource_snapshots", "aar_entries", "ai_recommendations", "exercise_kpis"]
    # issue #29 PR-G1b：cop_entities 有 exercise_id，演習重設一併清演習場域的 COP 圖釘
    # （事件/route/polygon）。tracks/links 無 exercise_id（references uid ON DELETE CASCADE）；
    # PRAGMA foreign_keys=ON，故刪 cop_entities 時 tracks/links 自動級聯，無 orphan。
    # #237：chats 有 exercise_id 欄 → 同 cop_entities 走 exercise-scoped 清除（實戰 NULL 池保留）。
    data_tables = ["snapshots", "events", "decisions", "manual_records", "audit_log", "cop_entities", "chats"]
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
    audit(sess["username"], None, "exercise_reset", "system", "all", {"cleared": cleared, "pre_backup": pre_backup})
    # P1-14：strict wants 後改 broadcast_all 確保全連線收到；同 reset-db：各 client 對帳清掉演習場域圖釘
    await cop_hub.broadcast_all({"op": "resync"})
    return {"ok": True, "cleared": cleared, "pre_backup": pre_backup}


@router.post("/suspend-all")
def suspend_all(body: SuspendAllIn, request: Request):
    sess = _check_system_admin(request)
    # OP-1（#153）：不可逆批次停權強制確認字串（後端把關，不依賴前端 dialog 防誤點）。
    if body.confirm != "SUSPEND_ALL":
        raise HTTPException(422, 'confirm 必須為 "SUSPEND_ALL"（不可逆批次停權確認）')
    # suspend_all_accounts 已排除發起者本人（防自鎖，見 account_repo）。
    count = suspend_all_accounts(sess["username"])
    return {"ok": True, "suspended_count": count}


# ── 紅藍 faction 分類（#343；prefix /api/admin → allowed_roles_for 自動 SYSADMIN_ONLY）──


@router.get("/factions/clients", tags=["faction"])
def faction_clients(request: Request, exercise_id: int | None = None):
    """列本場觀測到的連線 client（producer）+ 目前分類（admin 右 tab 資料源）。exercise_id 省略=實戰池。"""
    _check_system_admin(request)
    return {"clients": faction_service.list_clients(exercise_id)}


@router.post("/factions/classify", tags=["faction"])
async def faction_classify(body: FactionClassifyIn, request: Request):
    """指派 / 改 client 陣營 → upsert + 重解析名下 entity + resync 廣播（commander 視圖即時增減）。"""
    sess = _check_system_admin(request)
    return await faction_service.classify(
        body.exercise_id, body.client_key, body.faction, body.callsign, sess["username"]
    )


@router.post("/factions/entity-override", tags=["faction"])
async def faction_entity_override(body: FactionOverrideIn, request: Request):
    """對單一 entity 手動點陣營（iTAK 繪圖等無 producer 物件）+ resync。"""
    sess = _check_system_admin(request)
    return await faction_service.override_entity(body.uid, body.faction, sess["username"])


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

    audit(
        sess["username"], None, "RETENTION_TOGGLE", "config", "retention.tracks_ttl_enabled", {"enabled": body.enabled}
    )
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
        raise HTTPException(409, str(e)) from e


@router.post("/accounts/{username}/certs/issue", tags=["account-admin"])
def issue_account_cert(username: str, body: AccountCertBindIn, request: Request, fmt: str = "p12"):
    """#275 wave B-2：線上發證（選項 i 安全版）。後端呼叫 step-ca daemon 簽證（CA 鑰不進
    後端）→ 自動綁定 CN ↔ 帳號 → 回傳 p12 下載。未配置 step-ca 時回 503（改走離線簽 + 綁定）。

    fmt：`p12`（預設，桌機）或 `mobileconfig`（#312，iOS 描述檔，內嵌密碼免手打）。"""
    sess = _check_system_admin(request)
    account_id = _account_id_or_404(username)
    if not config.step_ca_configured():
        raise HTTPException(503, "線上發證未配置；請用 deploy/step-ca 離線簽 + 手動綁定")
    if fmt not in ("p12", "mobileconfig"):
        raise HTTPException(422, "fmt 須為 p12 或 mobileconfig")
    cn = _validated_cert_cn(body)
    if is_cert_active(cn):
        raise HTTPException(409, "此 CN 已被有效綁定")
    from services.cert_issuance import CertIssuanceError, build_mobileconfig, fetch_root_ca_pem, issue_p12

    try:
        p12, p12_pass = issue_p12(cn)
        root_pem = fetch_root_ca_pem() if fmt == "mobileconfig" else None
    except CertIssuanceError as e:
        raise HTTPException(502, f"發證失敗：{e}") from e
    bind_cert(account_id, cn, body.label, sess["username"])
    # #307：密碼不進 audit / log（與 p12/描述檔同走 TLS 回管理者）。
    audit(
        sess["username"],
        None,
        "cert_issue",
        "account_certs",
        cn,
        {"account_id": account_id, "cert_cn": cn, "online": True, "fmt": fmt},
    )
    # cn 已過 is_valid_cert_cn（無逗號/控制字元）；filename 再收斂為 alnum+-_.
    safe = "".join(c for c in cn if c.isalnum() or c in "-_.") or "client"
    if fmt == "mobileconfig":
        # #312：iOS 描述檔，密碼已內嵌（不另回 X-P12-Password）。x-forwarded-host 經 nginx。
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "ics"
        mc = build_mobileconfig(cn, p12, p12_pass, root_pem, f"https://{host}/")
        return Response(
            content=mc,
            media_type="application/x-apple-aspen-config",
            headers={"Content-Disposition": f'attachment; filename="{safe}.mobileconfig"'},
        )
    # X-P12-Password：同源回應，前端可直接讀 header 顯示密碼（#307 衍生子缺口）。
    return Response(
        content=p12,
        media_type="application/x-pkcs12",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}.p12"',
            "X-P12-Password": p12_pass,
        },
    )


@router.get("/ca/root", tags=["account-admin"])
def download_root_ca(request: Request):
    """#327：下載 step-ca root CA PEM —— 桌機信任 ICS server 證用。
    （macOS：鑰匙圈「系統」設永遠信任；Windows：匯入「受信任的根憑證授權單位」。）
    sysadmin only；未配置 step-ca → 503。root CA 為公開憑證（非私鑰），無敏感資料。"""
    _check_system_admin(request)
    from services.cert_issuance import CertIssuanceError, fetch_root_ca_pem

    try:
        pem = fetch_root_ca_pem()
    except CertIssuanceError as e:
        raise HTTPException(503, f"取 root CA 失敗：{e}") from e
    return Response(
        content=pem,
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="ics-root-ca.pem"'},
    )


# 註：本路由須宣告在 /certs/{cert_id} 之前——否則 "revoked" 會先撞 {cert_id:int} 路由
# 而被 422 攔下（literal path 必須贏過 int param）。
@router.delete("/accounts/{username}/certs/revoked", tags=["account-admin"])
def purge_account_revoked_certs(username: str, request: Request):
    """#307 缺口 2：清除此帳號所有已撤銷的裝置憑證列（免 DB 介入）。audit log 保留。"""
    sess = _check_system_admin(request)
    account_id = _account_id_or_404(username)
    count = purge_revoked_certs(account_id, sess["username"])
    return {"purged": count}


@router.delete("/accounts/{username}/certs/{cert_id}", tags=["account-admin"])
def revoke_account_cert(username: str, cert_id: int, request: Request):
    sess = _check_system_admin(request)
    account_id = _account_id_or_404(username)
    result = revoke_cert(cert_id, sess["username"], account_id=account_id)
    if result is None:
        raise HTTPException(404, "active cert binding not found")
    return result


@router.post("/tak/device-cert", tags=["account-admin"])
def issue_tak_device_cert(request: Request, callsign: str, mode: str = "atak"):
    """#315 P2-26 L2：dashboard 線上發 TAK 裝置證 data package（ATAK/iTAK）。

    證由 TAK 自己的 CA（ICS-TAK-SVC-CA，offline，TAK_DEVICE_CA_DIR）簽——TAK 只信它、非 step-ca
    （與 ICS 登入證隔離，doctrine 見 threat_model §8.3）。回 .zip（p12 + truststore + pref，密碼內嵌）。
    sysadmin only；每張強制 audit。未設對外位址 / CA dir → 503。"""
    sess = _check_system_admin(request)
    if mode not in ("atak", "aware"):
        raise HTTPException(422, "mode 須為 atak 或 aware")
    cn = (callsign or "").strip()
    validate_no_unsafe_strings(cn, label="callsign")
    if not is_valid_cert_cn(cn):
        raise HTTPException(422, "callsign 不合法（不可含逗號、不可 - 開頭，限字母/數字/空白/-_.@）")
    if not config.TAK_DEVICE_CONNECT_HOST:
        raise HTTPException(503, "對外 TAK 位址未設（部署層設 TAK_DEVICE_CONNECT_HOST）")
    # #315：TAK 裝置證由 TAK 自己的 CA（ICS-TAK-SVC-CA）簽——TAK 只信它（非 step-ca）。
    ca_dir = config.TAK_DEVICE_CA_DIR
    if not ca_dir or not os.path.isfile(os.path.join(ca_dir, "tak-ca.key")):
        raise HTTPException(503, "TAK 裝置 CA 未備（部署層設 TAK_DEVICE_CA_DIR，含 tak-ca.pem/key）")
    from services.cert_issuance import CertIssuanceError
    from services.tak_device_cert import build_device_package

    try:
        pkg, serial, fingerprint = build_device_package(
            cn, mode, config.TAK_DEVICE_CONNECT_HOST, config.TAK_DEVICE_CONNECT_PORT, ca_dir
        )
    except CertIssuanceError as e:
        raise HTTPException(502, f"發證失敗：{e}") from e
    # #317：盤點記錄（ICS 自建 SoT；TAK 不記 offline 證）+ serial（#318 CRL 前置）。
    from repositories.tak_device_cert_repo import record_issued

    record_issued(cn, serial, mode, sess["username"])
    # #344：發證即註冊 TAK managed user + 初始群 neutral（fail-closed）→ 之後紅藍分類走 REST update-groups。
    # best-effort：registrar 未配置/沒跑/逾時 → 跳過、不擋發證（裝置仍拿到證、落匿名待補；reason 進 audit + header）。
    from services.tak_user_enroll import enroll_device

    enroll = enroll_device(cn, fingerprint)
    # 強制 audit（不得 best-effort）：誰發了哪個 callsign 的證 + enrollment 結果。私鑰/密碼/fingerprint 不進 audit。
    audit(
        sess["username"],
        None,
        "tak_device_cert_issue",
        "tak",
        cn,
        {
            "callsign": cn,
            "mode": mode,
            "serial": serial,
            "connect_host": config.TAK_DEVICE_CONNECT_HOST,
            "enroll": enroll.get("reason"),
            "enroll_group": enroll.get("group"),
        },
    )
    # #324：filename 須 latin-1 安全（HTTP header 限制）。Python `isalnum()` 對中文回 True，
    # 不能用來濾——非 ASCII 進 header → uvicorn UnicodeEncodeError → 500（且證已記/audit = 幽靈列）。
    # → ASCII-only fallback `filename=` + RFC5987 `filename*` 保留原（含中文）檔名給支援的 client。
    import urllib.parse

    ascii_safe = "".join(c for c in cn if c.isascii() and (c.isalnum() or c in "-_.")) or "device"
    encoded = urllib.parse.quote(f"{cn}-dp.zip")
    # #344：前端據此提示「現場群已同步 / 未註冊（待補）」，避免靜默失敗。header 限 latin-1 →
    # 防禦性濾成 ASCII（registrar-error 夾帶 usermod 輸出，沿用本函式既有 latin-1 戒慎免 500）。
    enroll_status = "ok" if enroll.get("enrolled") else (enroll.get("reason") or "skipped")
    enroll_status = "".join(c for c in enroll_status if c.isascii() and c.isprintable())[:200] or "skipped"
    return Response(
        content=pkg,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_safe}-dp.zip\"; filename*=UTF-8''{encoded}",
            "X-TAK-Enroll-Status": enroll_status,
        },
    )


@router.get("/tak/device-certs", tags=["account-admin"])
def list_tak_device_certs(request: Request):
    """#317：列出 dashboard 發過的 TAK 裝置證（盤點）。sysadmin only。"""
    _check_system_admin(request)
    from repositories.tak_device_cert_repo import list_device_certs

    return list_device_certs()


@router.post("/tak/device-certs/{cert_id}/revoke", tags=["account-admin"])
def revoke_tak_device_cert(cert_id: int, request: Request):
    """#317：標記裝置證為已撤銷（**帳面 flag，不阻擋連線**——真撤銷見 #318 CRL）。sysadmin only。"""
    sess = _check_system_admin(request)
    from repositories.tak_device_cert_repo import mark_revoked

    result = mark_revoked(cert_id, sess["username"])
    if result is None:
        raise HTTPException(404, "active tak device cert not found")
    return result


@router.delete("/tak/device-certs/{cert_id}", tags=["account-admin"])
def delete_tak_device_cert(cert_id: int, request: Request):
    """#325：刪除**已撤銷**的裝置證盤點紀錄（清理累積 revoked）。sysadmin only、強制 audit。

    僅允許刪 status='revoked'（active 仍代表一張在用的證，刪了即失去盤點 → 拒）。
    刪的是 ICS 盤點紀錄，**非真撤銷**（真撤銷=CRL #318，本表 status 僅帳面 flag）。"""
    sess = _check_system_admin(request)
    from repositories.tak_device_cert_repo import delete_record

    if not delete_record(cert_id, sess["username"]):
        raise HTTPException(404, "revoked tak device cert not found（僅能刪已撤銷紀錄，active 不可刪）")
    return {"deleted": cert_id}
