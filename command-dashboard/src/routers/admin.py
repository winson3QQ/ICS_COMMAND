# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
import asyncio  # noqa: E402 — P1-12b L3 pre-destructive backup
import os  # noqa: E402 — #315 TAK_DEVICE_CA_DIR 路徑檢查
import threading  # noqa: E402 — #369 最後 sysadmin 守門並發序列化

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
from core.pin_policy import generate_temp_pin  # #348-F5 P2b
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
    create_account,
    delete_account,
    get_account,
    get_all_accounts,
    is_valid_account_role,
    set_default_pin_flag,
    suspend_all_accounts,
    update_account_display_name,
    update_account_pin,
    update_account_role,
    update_account_status,
)
from repositories.audit_repo import get_audit_log
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
    DisplayNameUpdateIn,
    FactionClassifyIn,
    FactionOverrideIn,
    PiNodeCreateIn,
    RetentionToggleIn,
    RoleUpdateIn,
    SuspendAllIn,
    TakRevokeByFingerprintIn,
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


# #369：最後 sysadmin 守門的 check→mutate 跨兩個自動提交連線、無共享交易，FastAPI 同步端點
# 在 anyio threadpool 並發執行 → 兩個並發降級各看到「還有 2 個」皆通過 → 歸零（#354 要防的自鎖）。
# 單行程指揮部以 module-level 鎖序列化「count 檢查 + 變更提交」臨界區即可（admin 變更稀少、零競爭）；
# 跨三出口（role/status/delete）共用同一把鎖，因 delete A + 降級 B 也會互競。role 分類仍走 Python
# role_zh_to_en（zh/en/alias 混合，難以純 SQL 原子化），故選鎖而非條件 UPDATE。
_SYSADMIN_GUARD_LOCK = threading.Lock()


def _require_not_last_sysadmin(username: str, *, will_remain_sysadmin: bool = False) -> None:
    """#354 防自鎖：系統內最後一個 status=active 且 role=sysadmin 的帳號，不得被
    降級 / 停用 / 封存（否則零管理能力，只能 shell 直操 DB 救回）。

    並發安全（#369）：本函式只做讀取計數；呼叫端必須在 `_SYSADMIN_GUARD_LOCK` 內
    同時涵蓋「本檢查 + 後續變更提交」，否則並發降級多個 sysadmin 仍可繞過（TOCTOU）。

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
    with get_conn() as conn:
        cnt = conn.execute(
            "SELECT COUNT(*) as c FROM accounts WHERE COALESCE(status, 'active') != 'archived'"
        ).fetchone()["c"]
        schema_ver = get_schema_version(conn)
    return {"active_accounts": cnt, "schema_version": schema_ver}


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
    # display_name 會被 account 列表拼進 innerHTML（auth.js admLoadAccounts）→ XSS sink，落 disk 前擋。
    if body.display_name:
        validate_no_unsafe_strings(body.display_name, label="display_name", max_len=64)
    # #348-F5 P2b：admin 不再自設初始 PIN → 系統產隨機臨時 PIN，一次性回傳供轉交（不落 plaintext、
    # 不寫 audit）。使用者首登被 P2a 閘強制改。
    temp_pin = generate_temp_pin(body.username)
    try:
        result = create_account(
            body.username,
            temp_pin,
            body.role,
            body.display_name,
            body.role_detail,
            sess["username"],
            require_pin_change=True,  # #348-F5 P2a：首登強制改
        )
    except Exception as e:
        raise HTTPException(409, f"account create failed: {e}") from e
    return {**result, "temp_pin": temp_pin}  # temp_pin：僅此一次，前端顯示後即無法再取得


@router.delete("/accounts/{username}")
def delete_acct(username: str, request: Request):
    sess = _check_account_manager(request)
    _require_commander_target_allowed(sess, username)
    # #369：守門檢查 + 刪除提交須在同一鎖內原子化（防並發降級繞過）。
    with _SYSADMIN_GUARD_LOCK:
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
    # #369：守門檢查 + 狀態提交同鎖原子化（設回 active 不觸發守門，但提交一律在鎖內，序列化成本可忽略）。
    with _SYSADMIN_GUARD_LOCK:
        if body.status == "suspended":
            _require_not_last_sysadmin(username)  # #354 防自鎖（設回 active 不擋）
        if not update_account_status(username, body.status, sess["username"]):
            raise HTTPException(404, "account not found")
    return {"ok": True}


@router.put("/accounts/{username}/pin")
def reset_pin(username: str, request: Request):
    # #348-F5 P2b：admin reset 不再自設 → 系統產隨機臨時 PIN、標記首登強制改、一次性回傳供轉交
    # （不落 plaintext、不寫 audit）。對齊 create_acct，杜絕 admin 得知/保留使用者最終 PIN。
    sess = _check_account_manager(request)
    _require_commander_target_allowed(sess, username)
    temp_pin = generate_temp_pin(username)
    if not update_account_pin(username, temp_pin, sess["username"]):
        raise HTTPException(404, "account not found")
    set_default_pin_flag(username)  # P2a 閘：使用者下次登入須改掉此臨時 PIN
    return {"ok": True, "temp_pin": temp_pin}


@router.put("/accounts/{username}/role")
def update_role(username: str, body: RoleUpdateIn, request: Request):
    sess = _check_account_manager(request)
    _require_commander_target_allowed(sess, username)
    if not is_valid_account_role(body.role, body.role_detail):
        raise HTTPException(422, "role invalid")
    _require_commander_new_role_allowed(sess, body.role, body.role_detail)
    # #354 防自鎖：降走最後一個 sysadmin 才擋；新角色仍是 sysadmin 則放行。
    # #369：守門檢查 + 角色提交同鎖原子化（防兩個並發降級各看到「還有 2 個」皆通過 → 歸零）。
    with _SYSADMIN_GUARD_LOCK:
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
    # #348 GAP2：prod 下 audit_log 為 append-only（不隨 reset 清，保課責軌；引擎層觸發器亦擋）；
    # dev 仍清，便於開發期反覆 reset/改試（使用者拍板）。
    if config.IS_PROD:
        tables = [t for t in tables if t != "audit_log"]
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
    # ⚠ #369 review 殘留（narrow，未納本鎖）：suspend-all 不在 _SYSADMIN_GUARD_LOCK 內，且其
    # 自排除保的是「發起者帳號」非「最後一個 sysadmin」。並發下若發起者 S1 同時被他人 demote 成
    # operator，suspend-all 仍排除 S1（已 operator）卻停掉最後的 sysadmin S2 → 可達零 sysadmin。
    # 修需 suspend-all 事後 re-assert「≥1 active sysadmin」（非僅自排除），屬獨立 follow-up、非
    # #369（並發降級）範圍。觸發極窄（須 SUSPEND_ALL 確認串 + 同瞬間 demote 發起者）。
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


@router.get("/audit-chain/verify", tags=["account-admin"])
def audit_chain_verify(request: Request):
    """#372（#348-F3）：稽核 hash 鏈完整性驗證端點。接上 verify_audit_chain（原 runtime 零
    caller＝死驗證器），滿足 NIST AU-9(3)「真的有在驗」。回 {ok,total,broken_at,reason}。
    限 sysadmin（路徑落 /api/admin/ → SYSADMIN_ONLY，並再 _check_system_admin 深一層）。
    注意：未 keyed（純 SHA-256）→ 偵測意外損毀＋天真竄改；抗「DB 寫權者改列並補算下游鏈」需
    keyed HMAC + key off-box（延實機，#372 B 部分／同 #226）。"""
    _check_system_admin(request)
    from core.audit_chain import verify_audit_chain

    with get_conn() as conn:
        return verify_audit_chain(conn)


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
    # #398 review：保留給 ICS 自身/管理身分的 callsign 不可被裝置證流程佔用——否則發證的 usermod -f
    # 會改寫 ICS 自己的 TAK 連線/管理 cert、之後 revoke 還會 usermod -D 把它刪掉（毀 ICS 控制面）。
    if _is_infra_callsign(cn):
        raise HTTPException(422, f"callsign「{cn}」為 ICS 保留身分，不可用於裝置證")
    if not config.TAK_DEVICE_CONNECT_HOST:
        raise HTTPException(503, "對外 TAK 位址未設（部署層設 TAK_DEVICE_CONNECT_HOST）")
    # #315：TAK 裝置證由 TAK 自己的 CA（ICS-TAK-SVC-CA）簽——TAK 只信它（非 step-ca）。
    ca_dir = config.TAK_DEVICE_CA_DIR
    if not ca_dir or not os.path.isfile(os.path.join(ca_dir, "tak-ca.key")):
        raise HTTPException(503, "TAK 裝置 CA 未備（部署層設 TAK_DEVICE_CA_DIR，含 tak-ca.pem/key）")
    # #429：配置代理 enrollment（TAK_ENROLL_URL + admin cert）→ 證走 TAK :8446 signClient（進帳本、
    # 對齊 #401/#318）；未配置 → 回退 #315 offline 簽（裝置體驗相同，差在證源/帳本）。
    from services import tak_enrollment
    from services.cert_issuance import CertIssuanceError
    from services.tak_device_cert import build_device_package

    use_enroll = tak_enrollment.is_configured()
    try:
        pkg, serial, fingerprint = build_device_package(
            cn,
            mode,
            config.TAK_DEVICE_CONNECT_HOST,
            config.TAK_DEVICE_CONNECT_PORT,
            ca_dir,
            use_enrollment=use_enroll,
        )
    except CertIssuanceError as e:
        raise HTTPException(502, f"發證失敗：{e}") from e
    # #431：兩種發證模式都須把證 fingerprint 綁進 TAK 名冊（registrar `usermod -f`），與證源無關。
    # enrollment 模式（#429）的 signClient 只「發證」、**不會把 fingerprint 寫回 user entry**——漏綁則帳號
    # 停在密碼認證（非證綁定）、reconcile 對不上帳本 fingerprint（顯示「未同步」）、且證不可撤（#318
    # 撤銷需 cert hash）。offline 模式（#344）本就靠 enroll_device 綁；故統一在此呼叫，不再依 use_enroll 分流。
    # best-effort：registrar 未配置/沒跑/逾時 → 跳過、不擋發證（裝置仍拿證、落待補；reason 進 audit）。
    # #398：先 enroll 拿結果，盤點才存得了 enroll_status（清單顯示有沒有同步上 TAK）。
    from services.tak_user_enroll import enroll_device

    enroll = enroll_device(cn, fingerprint, config.TAK_ENROLL_DEFAULT_GROUP)
    enroll_status = "ok" if enroll.get("enrolled") else (enroll.get("reason") or "skipped")
    enroll_status = "".join(c for c in enroll_status if c.isascii() and c.isprintable())[:200] or "skipped"
    # #317 盤點 + #398：記 fingerprint（= TAK managed-user 鍵，供比對混用）+ enroll_status（同步結果）。
    from repositories.tak_device_cert_repo import record_issued

    record_issued(cn, serial, mode, sess["username"], fingerprint=fingerprint, enroll_status=enroll_status)
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
    # #344：X-TAK-Enroll-Status 讓前端提示「已同步 / 未註冊（待補）」，避免靜默失敗（enroll_status 已於上方算，
    # 已濾成 latin-1 安全的 ASCII——registrar-error 夾帶 usermod 輸出，沿用本函式既有戒慎免 header 500）。
    return Response(
        content=pkg,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_safe}-dp.zip\"; filename*=UTF-8''{encoded}",
            "X-TAK-Enroll-Status": enroll_status,
        },
    )


# #398 B：TAK 上非 dashboard-發的 cert-user（ICS 自身連線 / 管理 cert）——對帳時標 infra 非殭屍。
_TAK_INFRA_USERS = frozenset({"ics-cot", "ics-tak-admin"})

# #404：REST-only infra——只打 Marti REST（subscriptions/all、update-groups…，ROLE_ADMIN gate）、
# **不訂閱 :8089 串流**，故其 __ANON__ 群**不洩漏串流資料 = 良性**，且移除唯一群會 bounce 回（usermod
# 行為）→ 不列為隔離破口、不給 strip 鈕。⚠ ics-cot 雖也是 infra 但**是 streaming producer**，其 __ANON__
# 仍是真破口，**不在此豁免**（差別＝會不會 stream，非 infra 與否）。
_TAK_REST_ONLY_INFRA = frozenset({"ics-tak-admin"})


def _is_infra_callsign(cn: str | None) -> bool:
    """是否為 ICS 保留身分（不可發/撤/移除）。**大小寫不敏感**——review 硬化：若 TAK usermod 視
    `ICS-COT`==`ics-cot`，精確比對會被大小寫變體繞過去刪掉 ics-cot；統一 lower 比對堵死。"""
    return (cn or "").strip().lower() in _TAK_INFRA_USERS


def _is_rest_only_infra(cn: str | None) -> bool:
    """#404：REST-only infra（admin cert）—— __ANON__ 良性、不算破口（見 _TAK_REST_ONLY_INFRA）。"""
    return (cn or "").strip().lower() in _TAK_REST_ONLY_INFRA


@router.get("/tak/device-certs", tags=["account-admin"])
def list_tak_device_certs(request: Request):
    """#317：列出 dashboard 發過的 TAK 裝置證（盤點）。sysadmin only。"""
    _check_system_admin(request)
    from repositories.tak_device_cert_repo import list_device_certs

    return list_device_certs()


@router.post("/tak/device-certs/{cert_id}/revoke", tags=["account-admin"])
def revoke_tak_device_cert(cert_id: int, request: Request):
    """#317 標記已撤銷 + #398 A：撤銷現行證 → 從 TAK **真 deregister**（usermod -D，不再只是帳面 flag）。

    sysadmin only。被取代的舊證（同 callsign 有更新 active 證）只標撤銷、不 deregister（避免誤殺現行）。
    deregister 走 registrar best-effort：失敗不擋撤銷（ICS 帳面已撤；reason 進回應 + audit）。
    """
    sess = _check_system_admin(request)
    from repositories.tak_device_cert_repo import has_other_active_cert, mark_revoked

    result = mark_revoked(cert_id, sess["username"])
    if result is None:
        raise HTTPException(404, "active tak device cert not found")
    callsign = result["callsign"]
    # #398 A：撤銷現行（唯一 active）證 → 從 TAK 真 deregister。但：
    #  · 同 callsign 還有其他 active 證 → 模糊（不確定 TAK 綁哪張）→ 只標撤銷、不動 TAK（skipped-ambiguous）。
    #  · infra 證（ics-cot/ics-tak-admin）→ 絕不 deregister（保護 ICS 自身 TAK 控制面；registrar 另有防線）。
    if _is_infra_callsign(callsign):
        deregister = {"ok": False, "reason": "skipped-infra"}
    elif has_other_active_cert(callsign, cert_id):
        deregister = {"ok": False, "reason": "skipped-ambiguous"}
    else:
        from services.tak_user_enroll import deregister_device

        deregister = deregister_device(callsign)
    # #318 層2 真撤銷：寫 TAK `certificate` 表 → 該證 :8089 串流/:8443 REST **連都連不進**（非僅 deregister
    # 降匿名）。**按 fingerprint 精準** → 不受 has_other_active_cert（callsign 粗粒度）影響，即使同 callsign
    # 多張 active 也只撤這張 hash。infra（ics-cot/ics-tak-admin）絕不撤（毀 ICS 自身 TAK 控制面）。
    fp = result.get("fingerprint")
    if _is_infra_callsign(callsign):
        tak_revoke = {"ok": False, "reason": "skipped-infra"}
    elif not fp:
        tak_revoke = {"ok": False, "reason": "no-fingerprint"}  # 升級前 NULL fingerprint → 無 hash 可撤
    else:
        from services.tak_revocation import revoke_in_tak

        tak_revoke = revoke_in_tak(fp, callsign)
    audit(
        sess["username"],
        None,
        "tak_device_cert_deregister",
        "tak",
        result["callsign"],
        {"cert_id": cert_id, "deregister": deregister.get("reason"), "tak_revoke": tak_revoke.get("reason")},
    )
    return {**result, "deregister": deregister.get("reason"), "tak_revoke": tak_revoke.get("reason")}


@router.post("/tak/revocations/backfill", tags=["account-admin"])
def backfill_tak_revocations(request: Request):
    """#318 Slice 3：把所有 ICS 已撤 + 有 fingerprint 的證一次推進 TAK `certificate` 表。

    補洞——#318 / Slice 2 上線前撤的證當時只設 ICS 帳面 flag、沒寫 TAK，故 TAK 端從不擋（dogfood 揭露）。
    冪等（`revoke_in_tak` exists→UPDATE/else INSERT）。sysadmin only + 強制 audit。
    無 fingerprint 的證無 hash 可撤 → 回 `skipped_no_fingerprint` 計數（需重發，UI 另標示）。
    ⚠ 對已快取（在線/近期認證）的證，撤銷實際生效仍需重啟 TAK（reality check 定案，§8.3）。
    """
    sess = _check_system_admin(request)
    from repositories.tak_device_cert_repo import count_revoked_null_fingerprint, list_revoked_with_fingerprint
    from services.tak_revocation import is_configured, revoke_in_tak

    skipped_no_fp = count_revoked_null_fingerprint()
    if not is_configured():
        return {"ok": False, "reason": "tak-db-not-configured", "pushed": 0, "skipped_no_fingerprint": skipped_no_fp}

    rows = list_revoked_with_fingerprint()
    pushed, errors = 0, []
    for r in rows:
        callsign = r.get("callsign")
        if _is_infra_callsign(callsign):  # infra 證絕不撤（毀 ICS 自身 TAK 控制面）
            continue
        res = revoke_in_tak(r.get("fingerprint"), callsign)
        if res.get("ok"):
            pushed += 1
        else:
            errors.append({"callsign": callsign, "reason": res.get("reason")})
    result = {
        "ok": True,
        "pushed": pushed,
        "total_with_fingerprint": len(rows),
        "skipped_no_fingerprint": skipped_no_fp,
        "errors": errors,
    }
    audit(
        sess["username"],
        None,
        "tak_revocations_backfill",
        "tak",
        "*",
        {
            "pushed": pushed,
            "total_with_fingerprint": len(rows),
            "skipped_no_fingerprint": skipped_no_fp,
            "errors": len(errors),
        },
    )
    return result


@router.post("/tak/revocations/by-fingerprint", tags=["account-admin"])
def revoke_tak_by_fingerprint(body: TakRevokeByFingerprintIn, request: Request):
    """#318 Slice 3 part③：按 SHA-256 fingerprint 直接撤**盤點外/非 dashboard 發**的證。

    緣由：TAK API 不吐連線證的 hash（`clientEndPoints`/`contacts`/`certadmin` 皆無）→ ICS 無法自動發現
    不明連線證。操作員自行從裝置證取 fingerprint（`openssl x509 -in cert.pem -noout -fingerprint -sha256`）
    或從留存包，貼進來 → 寫 TAK `certificate` 表。sysadmin only + 強制 audit。
    **安全閘**：格式驗證（冒號分隔大寫 32 段）+ **禁撤 infra**（按 hash 比對 ics-cot/admin/read/write，
    純 fingerprint 撤銷躲不過 callsign 閘，故按 hash 擋——撤這些會毀 ICS 對 TAK 控制面）。
    ⚠ 在線/快取證需重啟 TAK 才即時生效（part④ SOP）。
    """
    import re

    sess = _check_system_admin(request)
    fp = (body.fingerprint or "").strip().upper()
    if not re.fullmatch(r"([0-9A-F]{2}:){31}[0-9A-F]{2}", fp):
        raise HTTPException(422, "fingerprint 須為 SHA-256 冒號分隔大寫（32 段，如 AB:CD:…:EF）")
    from services.tak_revocation import infra_fingerprints, is_configured, revoke_in_tak

    if fp in infra_fingerprints():
        raise HTTPException(403, "禁撤基礎設施證（ics-cot/admin）—— 會毀 ICS 對 TAK 的控制面")
    if not is_configured():
        return {"ok": False, "reason": "tak-db-not-configured", "fingerprint": fp}
    callsign = (body.callsign or "").strip() or None
    res = revoke_in_tak(fp, callsign)
    audit(
        sess["username"],
        None,
        "tak_revoke_by_fingerprint",
        "tak",
        fp,
        {"callsign": callsign, "reason": res.get("reason")},
    )
    return {"ok": res.get("ok"), "reason": res.get("reason"), "fingerprint": fp}


@router.get("/tak/device-certs/reconcile", tags=["account-admin"])
def reconcile_tak_device_certs(request: Request):
    """#398 B：對帳 ICS 紀錄 vs TAK 實際 managed users（registrar 讀 UserAuthenticationFile）。sysadmin only。

    回 {ok, reason, tak_users:[{callsign, fingerprint, status}], ics_unsynced:[callsign]}。
    status：matched（fingerprint 相符）/ mismatch（ICS active 但 fingerprint 不同=混用）/
    unknown（ICS active 但 fingerprint 未知，升級前）/ zombie（TAK 有、ICS 無 active=刪過/殘留）/
    infra（ICS 自身/管理 cert，非 dashboard 發的裝置證）。
    """
    _check_system_admin(request)
    from repositories.tak_device_cert_repo import list_device_certs
    from services.tak_user_enroll import reconcile_tak_users

    rec = reconcile_tak_users()
    if not rec["ok"]:
        return {"ok": False, "reason": rec["reason"], "tak_users": [], "ics_unsynced": []}
    # callsign → 該 callsign 的 ICS active 證列（新到舊；list_device_certs 已 ORDER BY id DESC）。
    active_by_callsign: dict[str, list[dict]] = {}
    for c in (c for c in list_device_certs() if c["status"] == "active"):
        active_by_callsign.setdefault(c["callsign"], []).append(c)

    def _ics_match(cs: str, tak_fp: str) -> dict | None:
        """挑該 callsign 用於顯示/撤銷的 ICS 證：優先 fingerprint 相符那張，否則最新一張。"""
        rows = active_by_callsign.get(cs)
        if not rows:
            return None
        return next((r for r in rows if r.get("fingerprint") == tak_fp), rows[0])

    tak_callsigns: set[str] = set()
    annotated = []
    for u in rec["users"]:
        cs, fp = u["callsign"], u["fingerprint"]
        tak_callsigns.add(cs)
        rows = active_by_callsign.get(cs)
        fps = {r.get("fingerprint") for r in rows} if rows else None
        known = {x for x in fps if x} if fps else set()
        if _is_infra_callsign(cs) and not rows:
            status = "infra"  # ICS 自身/管理 cert，非裝置證流程，鎖死保護
        elif not rows:
            status = "zombie"  # TAK 有、ICS 無 active → 殭屍（可直接 deregister）
        elif fp in fps:
            status = "matched"
        elif known:
            status = "mismatch"  # 有具體已記 fingerprint 但都不符 → 真混用（優先於 unknown）
        else:
            status = "unknown"  # 只有升級前 NULL fingerprint，無從斷定
        m = _ics_match(cs, fp)
        # #404：per-user 群清單 + in_anon 旗標。groups=None（舊式 registrar 未回群）→ False（不誤判）；
        # 含 __ANON__ 或空群（runtime 落 __ANON__）→ True（producer 與任何 CA 證同頻＝隔離破口）。
        # anon_exempt＝在 __ANON__ 但屬 REST-only infra（admin，不 stream）→ 良性、不算破口、不給 strip 鈕。
        groups = u.get("groups")
        in_anon = groups is not None and ("__ANON__" in groups or len(groups) == 0)
        anon_exempt = in_anon and _is_rest_only_infra(cs)
        annotated.append(
            {
                "callsign": cs,
                "fingerprint": fp,
                "status": status,
                "groups": groups,
                "in_anon": in_anon,
                "anon_exempt": anon_exempt,
                # ICS 對應（供前端撤銷；殭屍/infra 無 → None）：
                "ics_cert_id": (m or {}).get("id"),
                "mode": (m or {}).get("mode"),
                "issued_at": (m or {}).get("issued_at"),
            }
        )
    # ICS 有 active、TAK 卻無（發了沒上 TAK，最典型=中文 callsign）。帶 cert_id 供撤銷 + 非 ASCII 旗標。
    ics_unsynced = [
        {"callsign": c["callsign"], "cert_id": c["id"], "non_ascii": not c["callsign"].isascii()}
        for cs, rows in active_by_callsign.items()
        if cs not in tak_callsigns
        for c in rows
    ]
    ics_unsynced.sort(key=lambda x: x["callsign"])
    # #404：卡 __ANON__ 的**真破口** managed user（producer 落匿名群 = 與任何 CA 證同頻，可注入/竊聽）。
    # 排除 anon_exempt（REST-only infra admin，__ANON__ 良性）→ 面板警示只算該修的；ics-tak-admin 仍
    # 在 tak_users 列出（in_anon=True/anon_exempt=True），前端標良性、不紅、不給鈕（誠實但不誤導）。
    anon_users = sorted(u["callsign"] for u in annotated if u["in_anon"] and not u["anon_exempt"])
    # #404：在線**匿名**連線（CA 信任但不在名冊）——reconcile 只讀名冊，看不到匿名連入的裝置（被刪
    # 帳號/未授權仍掛著的最該盯對象）。補查在線視圖 subscriptions/all，篩出 __ANON__ 且非名冊 user。
    # best-effort：未配置 admin cert / 查錯 → []。sync route 在 threadpool（無 running loop）→ asyncio.run 安全
    # （asyncio 已於模組頂 import）。
    from services.tak_group_sync import list_online_subscriptions

    roster = {u["callsign"] for u in annotated}
    try:
        online = asyncio.run(list_online_subscriptions())
    except Exception:  # online_anon 為附加診斷——任何錯（巢狀 loop RuntimeError、逃逸例外）都不得炸面板
        online = []
    online_anon = [
        o for o in online if "__ANON__" in (o.get("groups") or []) and (o.get("username") or "") not in roster
    ]
    return {
        "ok": True,
        "reason": "ok",
        "tak_users": annotated,
        "ics_unsynced": ics_unsynced,
        "anon_users": anon_users,
        "online_anon": online_anon,
    }


@router.post("/tak/users/{callsign}/deregister", tags=["account-admin"])
def deregister_tak_user(callsign: str, request: Request):
    """#401：直接從 TAK 移除一個 managed user（usermod -D）——管理「非 dashboard 發」的殭屍帳號。

    sysadmin only。TAK = 此面板的 SoT（此 server 僅 ICS 用）。infra 身分（ics-cot/ics-tak-admin）
    鎖死不可刪（毀 ICS 自身 TAK 控制面）；registrar 端另有獨立 infra 防線。強制 audit。
    """
    sess = _check_system_admin(request)
    cn = (callsign or "").strip()
    if not is_valid_cert_cn(cn):
        raise HTTPException(422, "callsign 不合法")
    if _is_infra_callsign(cn):
        raise HTTPException(422, f"「{cn}」為 ICS 保留身分，不可從 TAK 移除")
    from services.tak_user_enroll import deregister_device

    result = deregister_device(cn)
    audit(sess["username"], None, "tak_user_deregister", "tak", cn, {"deregister": result.get("reason")})
    if not result.get("ok"):
        # registrar 未配置 / 逾時 / 該 user 不存在 → 回 503 帶 reason（前端提示，非靜默）。
        raise HTTPException(503, f"從 TAK 移除失敗：{result.get('reason')}")
    return {"ok": True, "callsign": cn, "deregister": result.get("reason")}


@router.post("/tak/users/{callsign}/strip-anon", tags=["account-admin"])
def strip_anon_tak_user(callsign: str, request: Request):
    """#404：把 TAK managed user 移出 __ANON__ 匿名群（usermod -r -g __ANON__，保留其餘群）——修
    「producer 卡匿名頻道 = 與任何 CA 信任的證同頻（不明證可注入/竊聽 ICS COP）」隔離破口。

    sysadmin only。fingerprint 由 reconcile 取（usermod -r 帶 -f 確保不動憑證）。強制 audit。
    與 deregister 不同，**不擋 infra**——ics-cot 正是要修的對象（ics-tak-admin 僅 __ANON__ → bounce 回，no-op）。
    """
    sess = _check_system_admin(request)
    cn = (callsign or "").strip()
    if not is_valid_cert_cn(cn):
        raise HTTPException(422, "callsign 不合法")
    from services.tak_user_enroll import reconcile_tak_users, strip_anon_group

    # 取該 user 的 fingerprint（usermod -r 需 -f）。讀不到 TAK roster / 查無此 user → 明確錯，不靜默。
    rec = reconcile_tak_users()
    if not rec["ok"]:
        raise HTTPException(503, f"無法讀 TAK roster：{rec['reason']}")
    user = next((u for u in rec["users"] if u["callsign"] == cn), None)
    if user is None:
        raise HTTPException(404, f"TAK 無此 managed user：{cn}")
    fp = user.get("fingerprint") or ""
    if not fp:
        # 升級前 NULL fingerprint：usermod -r 需 -f，無 fp 無法 strip → 清楚回報（非 opaque registrar 503）。
        raise HTTPException(409, f"「{cn}」在 TAK 無 fingerprint 紀錄，無法移出 __ANON__（需重發證 / 重 enroll）")

    result = strip_anon_group(cn, fp)
    audit(sess["username"], None, "tak_user_strip_anon", "tak", cn, {"strip_anon": result.get("reason")})
    if not result.get("ok"):
        raise HTTPException(503, f"移出 __ANON__ 失敗：{result.get('reason')}")
    return {"ok": True, "callsign": cn, "strip_anon": result.get("reason")}


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
