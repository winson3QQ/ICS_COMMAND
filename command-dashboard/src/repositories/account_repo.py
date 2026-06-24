from __future__ import annotations

from datetime import UTC, datetime, timedelta

from auth.role_enum import (
    COMMAND_ROLES,
    ROLE_SYSADMIN,
    ROLE_SYSADMIN_ZH,
    normalize_role_pair,
    role_zh_to_en,
)
from core.database import get_conn

from ._helpers import audit, hash_pin, now_utc, pin_needs_rehash, verify_pin

LOCKOUT_THRESHOLD = 5
LOCKOUT_DURATION_MIN = 15


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_to_dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _is_locked(row: dict) -> bool:
    locked_until = row.get("locked_until")
    if not locked_until:
        return False
    return datetime.now(UTC) < _iso_to_dt(locked_until)


def _public_account(row: dict) -> dict:
    row.pop("pin_hash", None)
    row.pop("pin_salt", None)
    row["role"], row["role_detail"] = normalize_role_pair(row.get("role"), row.get("role_detail"))
    return row


def create_account(
    username: str,
    pin: str,
    role: str = "\u64cd\u4f5c\u54e1",
    display_name: str | None = None,
    role_detail: str | None = None,
    operator: str = "admin",
    require_pin_change: bool = False,
) -> dict:
    # #348-F5 P2a：require_pin_change=True → is_default_pin=1（首登強制改）。
    # 強制由 auth_middleware per-account 閘做；預設 False，repo caller/測試不變。
    role, role_detail = normalize_role_pair(role, role_detail)
    pin_hash, pin_salt = hash_pin(pin)
    now = now_utc()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO accounts
               (username, pin_hash, pin_salt, role, role_detail, display_name, status, created_at, is_default_pin)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                username,
                pin_hash,
                pin_salt,
                role,
                role_detail,
                display_name,
                "active",
                now,
                1 if require_pin_change else 0,
            ),
        )
    audit(operator, None, "account_created", "accounts", username, {"role": role, "role_detail": role_detail})
    return {
        "username": username,
        "role": role,
        "role_detail": role_detail,
        "display_name": display_name,
        "status": "active",
        "created_at": now,
    }


def get_all_accounts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT username, role, role_detail, display_name, status, created_at, updated_at
                 FROM accounts
                WHERE COALESCE(status, 'active') != 'archived'
                ORDER BY created_at"""
        ).fetchall()
    return [_public_account(dict(r)) for r in rows]


def get_account(username: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT username, role, role_detail, display_name, status, created_at, updated_at
                 FROM accounts
                WHERE username=?
                  AND COALESCE(status, 'active') != 'archived'""",
            (username,),
        ).fetchone()
    return _public_account(dict(row)) if row else None


def update_account_status(username: str, status: str, operator: str) -> bool:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE accounts SET status=?, updated_at=? WHERE username=? AND COALESCE(status, 'active') != 'archived'",
            (status, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "account_status_updated", "accounts", username, {"status": status})
    return cur.rowcount > 0


def update_account_pin(username: str, new_pin: str, operator: str) -> bool:
    pin_hash, pin_salt = hash_pin(new_pin)
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE accounts
                  SET pin_hash=?, pin_salt=?, updated_at=?
                WHERE username=? AND COALESCE(status, 'active') != 'archived'""",
            (pin_hash, pin_salt, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "account_pin_reset", "accounts", username, {})
    return cur.rowcount > 0


def update_account_role(
    username: str,
    role: str,
    operator: str,
    role_detail: str | None = None,
) -> bool:
    role, role_detail = normalize_role_pair(role, role_detail)
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE accounts
                  SET role=?, role_detail=?, updated_at=?
                WHERE username=? AND COALESCE(status, 'active') != 'archived'""",
            (role, role_detail, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "account_role_updated", "accounts", username, {"role": role, "role_detail": role_detail})
    return cur.rowcount > 0


def update_account_display_name(username: str, display_name: str, operator: str) -> bool:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE accounts
                  SET display_name=?, updated_at=?
                WHERE username=? AND COALESCE(status, 'active') != 'archived'""",
            (display_name, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "account_display_name_updated", "accounts", username, {"display_name": display_name})
    return cur.rowcount > 0


def delete_account(username: str, operator: str) -> bool:
    now = now_utc()
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE accounts
                  SET status='archived', deleted_at=?, updated_at=?
                WHERE username=? AND COALESCE(status, 'active') != 'archived'""",
            (now, now, username),
        )
    if cur.rowcount:
        audit(operator, None, "ACCOUNT_ARCHIVED", "accounts", username, {"decision": "soft_delete"})
    return cur.rowcount > 0


def suspend_all_accounts(operator: str) -> int:
    now = now_utc()
    with get_conn() as conn:
        # OP-1（#153）：排除發起者本人 —— 否則執行後零 active 帳號，系統進入「需主機 shell
        # 直操 DB 才能解救」的自鎖狀態（操作失誤門檻低、不可逆）。
        cur = conn.execute(
            "UPDATE accounts SET status='suspended', updated_at=? WHERE status='active' AND username != ?",
            (now, operator),
        )
    audit(operator, None, "all_accounts_suspended", "accounts", "*", {"excluded_self": operator})
    return cur.rowcount


def verify_login(username: str, pin: str, bypass_lockout: bool = False) -> tuple[dict | None, str]:
    """#275 wave 4 鎖定-DoS 緩解：bypass_lockout=True（呼叫端確認此 request 出示了綁定本
    帳號的有效 mTLS 裝置憑證）時，帳號鎖定不擋、錯 PIN 也不再上鎖——攻擊者無裝置證仍可
    鎖（反爆破保留），但**持本人裝置的合法使用者永不被鎖死**（解 §8.6 鎖死 admin 之患）。

    #295 鎖定-DoS 續解：高權帳號（指揮官/系統管理員，COMMAND_ROLES）在 **mTLS 強制部署下不硬鎖**
    ——防「戰時對 C2 帳號連送錯 PIN 鎖死 15 分」可用性攻擊。理據：mTLS 下 PIN 僅第二因子，登入成功
    仍需綁定本帳號裝置證才能建 session，故移除硬鎖不開爆破缺口。**非 mTLS 部署（ICS_MTLS_REQUIRED
    =false，預設）PIN 即足以建 session → 高權仍維持硬鎖以保反爆破**（此時無 C2 鎖死 DoS 的作戰前提）。
    失敗計數一律累計、跨閾值記稽核事件（保留偵測）。per-source 漸進延遲/節流待真實 IP 還原（#280）另解。"""
    # 動態讀（非 import 期綁定）→ 測試可 monkeypatch core.config.ICS_MTLS_REQUIRED。
    from core.config import ICS_MTLS_REQUIRED

    with get_conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE username=?", (username,)).fetchone()
        if not row:
            # #348-F15：抹平 no_user 的「零-KDF 旁路」——帳號不存在原直接 return（不跑 PBKDF2），與
            # 存在帳號錯 PIN 跑 600k 的巨大時間差＝存在性 oracle（no_user 與 bad_pin 同回 401，計時是
            # 唯一洩漏）。補一次 hash_pin(pin)（600k＝現行 default，新帳號與 rehash 後皆此值）抹平。
            # ⚠ 殘留（已知、非完全常數時間，review #379）：legacy-100k 帳號（pre-wave-4 遷移、尚未登入
            #   rehash）verify 僅 100k → 比 600k dummy 快，仍可被計時微弱區分。**fresh 佈署無此類帳號**、
            #   且每次成功登入即透明 rehash 至 600k 收斂；完全閉合需把 verify_pin 補到固定 600k（過度，未做）。
            # CPU：no_user 噴 600k 由 /api/auth/login 限流（auth_rate_limit 10/min/IP）界定，非放大破口。
            hash_pin(pin)
            return None, "no_user"
        d = dict(row)
        if d.get("status") == "archived":
            return None, "archived"
        if d.get("status") != "active":
            return None, "suspended"
        # #295：高權帳號**僅在 mTLS 強制下**不硬鎖（cert 為真正第二因子）；非 mTLS 或一般帳號維持原鎖定。
        _high_priv = ICS_MTLS_REQUIRED and role_zh_to_en(d.get("role"), d.get("role_detail")) in COMMAND_ROLES
        _no_hard_lock = bypass_lockout or _high_priv
        if _is_locked(d) and not _no_hard_lock:
            return None, "locked"
        if not verify_pin(pin, d["pin_hash"], d["pin_salt"]):
            new_count = (d.get("failed_login_count") or 0) + 1
            locked_until = None
            if new_count >= LOCKOUT_THRESHOLD and not _no_hard_lock:
                locked_until = (datetime.now(UTC) + timedelta(minutes=LOCKOUT_DURATION_MIN)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )
            conn.execute(
                "UPDATE accounts SET failed_login_count=?, locked_until=? WHERE username=?",
                (new_count, locked_until, username),
            )
            conn.commit()
            if locked_until:
                audit(username, None, "account_locked", "accounts", username, {"failed_count": new_count})
                return None, "locked"
            # #295：高權帳號跨閾值但未硬鎖 → 記抑制事件（保留偵測、無 DoS）；僅在剛跨閾值記一次避免洗版。
            if _high_priv and not bypass_lockout and new_count == LOCKOUT_THRESHOLD:
                audit(
                    username,
                    None,
                    "lockout_suppressed_high_priv",
                    "accounts",
                    username,
                    {"failed_count": new_count},
                )
            return None, "bad_pin"
        # 成功：清鎖定 + 透明升級 PIN hash 迭代數（舊 100k → 600k）
        if pin_needs_rehash(d["pin_hash"]):
            new_hash, new_salt = hash_pin(pin)
            conn.execute(
                "UPDATE accounts SET pin_hash=?, pin_salt=?, failed_login_count=0, "
                "locked_until=NULL, last_login=? WHERE username=?",
                (new_hash, new_salt, _iso_now(), username),
            )
        else:
            conn.execute(
                "UPDATE accounts SET failed_login_count=0, locked_until=NULL, last_login=? WHERE username=?",
                (_iso_now(), username),
            )
        conn.commit()
    return _public_account(d), "ok"


def unlock_account(username: str, operator: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE accounts SET failed_login_count=0, locked_until=NULL WHERE username=?",
            (username,),
        )
    if cur.rowcount:
        audit(operator, None, "account_unlocked", "accounts", username, {})
    return cur.rowcount > 0


def ensure_default_admin(default_pin: str = "1234") -> None:
    with get_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) as c FROM accounts").fetchone()["c"]
    if cnt == 0:
        create_account("admin", default_pin, ROLE_SYSADMIN_ZH, "\u7cfb\u7d71\u7ba1\u7406\u54e1", ROLE_SYSADMIN)
        with get_conn() as conn:
            conn.execute("UPDATE accounts SET is_default_pin=1 WHERE username='admin'")
            conn.commit()


def ensure_initial_admin_token(token_dir: str | None = None) -> str | None:
    import logging
    import os
    import secrets

    with get_conn() as conn:
        cnt = conn.execute("SELECT COUNT(*) as c FROM accounts").fetchone()["c"]
    if cnt > 0:
        return None

    initial_pin = f"{secrets.randbelow(1_000_000):06d}"
    create_account("admin", initial_pin, ROLE_SYSADMIN_ZH, "\u7cfb\u7d71\u7ba1\u7406\u54e1", ROLE_SYSADMIN)
    with get_conn() as conn:
        conn.execute("UPDATE accounts SET is_default_pin=1 WHERE username='admin'")
        conn.commit()

    target_dir = token_dir or os.path.expanduser("~/.ics")
    token_file = os.path.join(target_dir, "first_run_token")
    try:
        os.makedirs(target_dir, mode=0o700, exist_ok=True)
        with open(token_file, "w", encoding="utf-8") as f:
            f.write(initial_pin + "\n")
        os.chmod(token_file, 0o600)
    except OSError:
        logging.getLogger(__name__).warning("Unable to write first-run token", exc_info=True)

    import structlog as _structlog

    _structlog.get_logger().warning(
        "first_run_token_issued",
        msg="Initial admin PIN issued; operator must change it after login.",
        detail={"token_file": token_file},
    )
    return initial_pin


def clear_default_pin_flag(username: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE accounts SET is_default_pin=0 WHERE username=?", (username,))
        conn.commit()
    return cur.rowcount > 0


def set_default_pin_flag(username: str) -> bool:
    # #348-F5 P2b：admin reset 成系統臨時 PIN 後標記待改（首登強制改，對齊 create 的 require_pin_change）。
    with get_conn() as conn:
        cur = conn.execute("UPDATE accounts SET is_default_pin=1 WHERE username=?", (username,))
        conn.commit()
    return cur.rowcount > 0


def is_first_run_required() -> bool:
    """#348-F5 P2a 起收斂為 **bootstrap 專用**：系統剛建、唯一帳號（第一個 admin）且未改 PIN。

    原本「任一 sysadmin is_default_pin=1」會讓 P2a 對**新建帳號**設 is_default_pin=1 時觸發全系統
    first_run_gate 423 鎖死（建第二個帳號＝鎖死所有人）。收斂為「accounts==1 且該帳號為 sysadmin
    且 default」→ 第一個 admin 行為完全不變（#306 bootstrap 不受影響），第 2+ 帳號的強制改 PIN
    改由 **per-account 閘**（auth_middleware + account_needs_pin_change）處理，不再走全系統 gate。

    殘餘風險（依賴 invariant）：fresh deploy 只 seed 唯一 admin（ensure_default_admin /
    ensure_initial_admin_token），故 bootstrap 時 accounts==1 必成立。若未來有路徑在 bootstrap
    同時 seed 第 2 帳號（目前無），全系統 gate 不再觸發 → 安全網即 per-account 閘（含 cop WS）。
    """
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
        if total != 1:
            return False
        row = conn.execute("SELECT is_default_pin, role, role_detail FROM accounts").fetchone()
    if not row or row["is_default_pin"] != 1:
        return False
    return row["role_detail"] == "sysadmin" or row["role"] in ("系統管理員", "admin")


def account_needs_pin_change(username: str) -> bool:
    """#348-F5 P2a：此帳號是否仍持「初始/管理員給的」PIN（is_default_pin=1），須強制改。
    供 auth_middleware per-account 閘 + change-initial-pin 授權判斷。"""
    with get_conn() as conn:
        row = conn.execute("SELECT is_default_pin FROM accounts WHERE username=?", (username,)).fetchone()
    return bool(row) and row["is_default_pin"] == 1


def is_valid_account_role(role: str | None, role_detail: str | None = None) -> bool:
    return role_zh_to_en(role, role_detail) is not None
