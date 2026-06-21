"""#275 wave 3 — per-device 裝置憑證綁定（mTLS 第二因子）。

撤銷採 App 層綁定撤銷：status='active' → 'revoked'，login + check_session 查本表，
撤銷後下一個 request 立即失效（不依賴 CRL 分發）。見 security_policies §2.8。
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.database import get_conn

from ._helpers import audit


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _public(row) -> dict:
    return dict(row)


def is_valid_cert_cn(cert_cn: str) -> bool:
    """CN 合法性（綁定/發證前驗）。拒：空、逗號（會破壞 nginx `[^,]+` CN 抽取 →
    後端看到的 CN 與綁定值不符）、前導 `-`（傳給 step CLI 會被當旗標，flag injection）、
    控制字元/引號。允許字母（含 CJK）、數字、空白與 - _ . @。長度 1–64。"""
    cn = (cert_cn or "").strip()
    if not cn or len(cn) > 64:
        return False
    if cn[0] == "-" or "," in cn:
        return False
    return all(c.isalnum() or c in " -_.@" or ord(c) > 127 for c in cn)


def account_id_for_username(username: str) -> int | None:
    """解析 username → account id（get_account 不回 id，cert 綁定需要 FK 整數）。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM accounts WHERE username=? AND COALESCE(status,'active') != 'archived'",
            (username,),
        ).fetchone()
    return row["id"] if row else None


def bind_cert(account_id: int, cert_cn: str, label: str | None, operator: str) -> dict:
    """為帳號綁定一張裝置憑證 CN。CN 已有 active 綁定 → 拋 ValueError（唯一索引亦會擋）。"""
    cert_cn = (cert_cn or "").strip()
    if not cert_cn:
        raise ValueError("cert_cn 不可為空")
    with get_conn() as conn:
        acct = conn.execute("SELECT username FROM accounts WHERE id=?", (account_id,)).fetchone()
        if acct is None:
            raise LookupError("account not found")
        existing = conn.execute(
            "SELECT id FROM account_certs WHERE cert_cn=? AND status='active'", (cert_cn,)
        ).fetchone()
        if existing is not None:
            raise ValueError("cert_cn 已被 active 綁定")
        cur = conn.execute(
            "INSERT INTO account_certs (account_id, cert_cn, label, status, issued_at) VALUES (?, ?, ?, 'active', ?)",
            (account_id, cert_cn, label, _iso_now()),
        )
        cert_id = cur.lastrowid
        row = conn.execute("SELECT * FROM account_certs WHERE id=?", (cert_id,)).fetchone()
    # #306：首張綁定即落持久旗標 → mTLS bootstrap 窗口永久關（purge 清不掉本旗標，真單向閂；
    # 不依賴 account_certs row 存在）。idempotent，每次 bind 寫一律安全（只會更關、不會開）。
    from .config_repo import set_config

    set_config("mtls_bootstrap_done", "1", operator)
    audit(
        operator,
        None,
        "cert_bind",
        "account_certs",
        str(cert_id),
        {"account_id": account_id, "cert_cn": cert_cn, "label": label},
    )
    return _public(row)


def list_certs(account_id: int) -> list[dict]:
    """列出帳號所有憑證綁定（含已撤銷，供管理面稽核）。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM account_certs WHERE account_id=? ORDER BY issued_at DESC",
            (account_id,),
        ).fetchall()
    return [_public(r) for r in rows]


def revoke_cert(cert_id: int, operator: str, account_id: int | None = None) -> dict | None:
    """撤銷一張憑證綁定（App 層即時失效）。回傳被撤紀錄；不存在/已撤回 None。

    account_id 給定時一併比對擁有者（防越權撤到別帳號的證；查詢即原子過濾，不先改後檢）。
    """
    with get_conn() as conn:
        if account_id is not None:
            row = conn.execute(
                "SELECT * FROM account_certs WHERE id=? AND account_id=? AND status='active'",
                (cert_id, account_id),
            ).fetchone()
        else:
            row = conn.execute("SELECT * FROM account_certs WHERE id=? AND status='active'", (cert_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE account_certs SET status='revoked', revoked_at=? WHERE id=?",
            (_iso_now(), cert_id),
        )
        updated = conn.execute("SELECT * FROM account_certs WHERE id=?", (cert_id,)).fetchone()
    audit(
        operator,
        None,
        "cert_revoke",
        "account_certs",
        str(cert_id),
        {"account_id": row["account_id"], "cert_cn": row["cert_cn"]},
    )
    return _public(updated)


def purge_revoked_certs(account_id: int, operator: str) -> int:
    """清除帳號所有 status='revoked' 的綁定列（#307 缺口 2：UI 清死記錄，免 DB 介入）。

    只刪已撤銷列（active 不動）；audit log 為獨立表、不受影響（問責痕跡保留）。
    回傳清除筆數。account_id 綁定查詢，不會誤刪他帳號的列。
    """
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, cert_cn FROM account_certs WHERE account_id=? AND status='revoked'",
            (account_id,),
        ).fetchall()
        if not rows:
            return 0
        conn.execute(
            "DELETE FROM account_certs WHERE account_id=? AND status='revoked'",
            (account_id,),
        )
    audit(
        operator,
        None,
        "cert_purge_revoked",
        "account_certs",
        str(account_id),
        {"account_id": account_id, "count": len(rows), "cert_cns": [r["cert_cn"] for r in rows]},
    )
    return len(rows)


def is_cert_active(cert_cn: str) -> bool:
    """該 CN 是否仍有 active 綁定（check_session 用：撤銷後活躍 session 立即失效）。"""
    cert_cn = (cert_cn or "").strip()
    if not cert_cn:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM account_certs WHERE cert_cn=? AND status='active' LIMIT 1",
            (cert_cn,),
        ).fetchone()
    return row is not None


def cert_active_for_account(account_id: int, cert_cn: str) -> bool:
    """該 CN 是否為「此帳號」的 active 綁定（login 第二因子驗證）。"""
    cert_cn = (cert_cn or "").strip()
    if not cert_cn:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM account_certs WHERE account_id=? AND cert_cn=? AND status='active' LIMIT 1",
            (account_id, cert_cn),
        ).fetchone()
    return row is not None


def is_mtls_bootstrap() -> bool:
    """#306：全新部署 mTLS bootstrap 窗口 —— 首位 admin 尚未綁過任何裝置證。

    此窗口內允許首位 admin 在 `ICS_MTLS_REQUIRED=true` 下，用 nginx 已 CA 驗證的證
    登入並綁定第一張裝置證（解雞生蛋：綁證要先登入、登入要先綁證），免手動翻 env。

    **真單向閂**：以持久旗標 `mtls_bootstrap_done`（首張綁定時落於 config 表）為主判據——
    綁過第一張即永久關窗，**`purge_revoked_certs` 清不掉本旗標**（不像 account_certs row 會被
    purge 實刪而誤重開窗＝安全洞，security-review #306 V1）。僅整碟/卷 wipe（連 config 一併清）
    才回到 fresh。輔以 accounts==1 + account_certs 空（pre-旗標相容）。多帳號亦永久關窗。
    仍需 first-run PIN + CA-signed cert 雙重前提。
    """
    from .config_repo import get_config

    if get_config("mtls_bootstrap_done"):  # 持久旗標：綁過第一張即關、purge 清不掉
        return False
    with get_conn() as conn:
        n_acct = conn.execute(
            "SELECT COUNT(*) AS c FROM accounts WHERE COALESCE(status,'active') != 'archived'"
        ).fetchone()["c"]
        n_cert = conn.execute("SELECT COUNT(*) AS c FROM account_certs").fetchone()["c"]  # 任何狀態
    return n_acct == 1 and n_cert == 0
