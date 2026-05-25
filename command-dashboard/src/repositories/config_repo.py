import json
from datetime import UTC, datetime, timedelta

from core.database import get_conn

from ._helpers import audit, hash_pin, now_utc, verify_pin

# ── C2-D Admin PIN 鎖定參數 ──────────────────────────────────────
ADMIN_LOCKOUT_THRESHOLD = 5      # 連續失敗次數上限
ADMIN_LOCKOUT_MINUTES   = 30     # 鎖定時長（分鐘）


def get_config(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_config(key: str, value: str, operator: str | None = None):
    now = now_utc()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, value, now))
    if operator:
        audit(operator, None, "config_updated", "config", key, {"value": value})


def _del_config(key: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM config WHERE key=?", (key,))


def verify_admin_pin(pin: str) -> bool:
    raw = get_config("admin_pin")
    if not raw:
        return False
    data = json.loads(raw)
    return verify_pin(pin, data["hash"], data["salt"])


def set_admin_pin(new_pin: str, operator: str):
    h, s = hash_pin(new_pin)
    set_config("admin_pin", json.dumps({"hash": h, "salt": s}), operator)


def ensure_default_admin_pin(token_dir: str | None = None) -> str | None:
    """首次啟動：若 admin_pin 未設定，產生隨機 6 位 PIN。

    回傳產生的 PIN（首次）或 None（已設定）。
    PIN 同時寫入 ~/.ics/admin_pin_token（chmod 600）。
    """
    import logging
    import os
    import secrets

    if get_config("admin_pin"):
        return None

    admin_pin = f"{secrets.randbelow(1_000_000):06d}"
    set_admin_pin(admin_pin, "system")

    target_dir = token_dir or os.path.expanduser("~/.ics")
    token_file = os.path.join(target_dir, "admin_pin_token")
    try:
        os.makedirs(target_dir, mode=0o700, exist_ok=True)
        with open(token_file, "w") as f:
            f.write(admin_pin + "\n")
        os.chmod(token_file, 0o600)
    except OSError:
        token_file = "(寫檔失敗，請從 console 紀錄取得)"

    import structlog as _structlog
    _log = _structlog.get_logger()
    _log.warning(
        "admin_pin_initialized",
        msg="ICS 首次啟動：Admin PIN 已產生，請在管理員面板登入後立刻修改",
        detail={"token_file": token_file},
    )
    return admin_pin


# ── C2-D Admin PIN 鎖定 ───────────────────────────────────────────

def get_admin_pin_lock_status() -> dict:
    """回傳 {locked: bool, locked_until: str | None, failed_count: int}"""
    locked_until_raw = get_config("admin_pin_locked_until")
    failed_count     = int(get_config("admin_pin_failed_count") or "0")
    if locked_until_raw:
        locked_until = datetime.fromisoformat(locked_until_raw.replace("Z", "+00:00"))
        if datetime.now(UTC) < locked_until:
            return {"locked": True, "locked_until": locked_until_raw, "failed_count": failed_count}
        # 鎖定已過期，清除
        _del_config("admin_pin_locked_until")
        _del_config("admin_pin_failed_count")
    return {"locked": False, "locked_until": None, "failed_count": failed_count}


def record_admin_pin_failure() -> dict:
    """記錄一次失敗，超過門檻則鎖定。回傳 lock status。"""
    status     = get_admin_pin_lock_status()
    new_count  = status["failed_count"] + 1
    locked_until = None
    if new_count >= ADMIN_LOCKOUT_THRESHOLD:
        locked_until = (
            datetime.now(UTC) + timedelta(minutes=ADMIN_LOCKOUT_MINUTES)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        set_config("admin_pin_locked_until", locked_until)
        audit("system", None, "admin_pin_lockout", "config", "admin_pin",
              {"failed_count": new_count, "locked_until": locked_until})
    set_config("admin_pin_failed_count", str(new_count))
    return {"locked": locked_until is not None, "locked_until": locked_until,
            "failed_count": new_count}


def reset_admin_pin_failures():
    """登入成功後清除失敗計數。"""
    _del_config("admin_pin_failed_count")
    _del_config("admin_pin_locked_until")
