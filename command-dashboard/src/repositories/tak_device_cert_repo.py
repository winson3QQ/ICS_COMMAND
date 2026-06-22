"""#317 — dashboard 發出的 TAK 裝置證盤點（ICS 自建 SoT；TAK 不記 offline 證）。

`status='revoked'` = **帳面 flag，不 enforce**（裝置仍能連 TAK，真撤銷見 #318 CRL）。
`serial` 為 CRL 前置（#318 revoke 該 serial）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.database import get_conn

from ._helpers import audit


def _iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_issued(callsign: str, serial: str | None, mode: str, operator: str) -> dict:
    """發證成功後記一列。callsign 可重複（每發一張一列）。"""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tak_device_certs (callsign, serial, mode, operator, status, issued_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (callsign, serial, mode, operator, _iso_now()),
        )
        row = conn.execute("SELECT * FROM tak_device_certs WHERE id=?", (cur.lastrowid,)).fetchone()
    audit(
        operator,
        None,
        "tak_device_cert_record",
        "tak_device_certs",
        str(cur.lastrowid),
        {"callsign": callsign, "serial": serial, "mode": mode},
    )
    return dict(row)


def list_device_certs() -> list[dict]:
    """列出所有發過的 TAK 裝置證（含已撤銷，盤點/audit 用），新到舊。"""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM tak_device_certs ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def mark_revoked(cert_id: int, operator: str) -> dict | None:
    """標記為已撤銷（**帳面 flag，不阻擋連線**——真撤銷見 #318 CRL）。

    回傳被標記列；不存在 / 已撤回 None。
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tak_device_certs WHERE id=? AND status='active'", (cert_id,)).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE tak_device_certs SET status='revoked', revoked_at=?, revoked_by=? WHERE id=?",
            (_iso_now(), operator, cert_id),
        )
        updated = conn.execute("SELECT * FROM tak_device_certs WHERE id=?", (cert_id,)).fetchone()
    audit(
        operator,
        None,
        "tak_device_cert_mark_revoked",
        "tak_device_certs",
        str(cert_id),
        {"callsign": row["callsign"], "serial": row["serial"]},
    )
    return dict(updated)


def delete_record(cert_id: int, operator: str) -> bool:
    """#325：刪除**已撤銷**的盤點紀錄（清理累積 revoked）。

    僅刪 status='revoked'（active 仍代表在用的證，不可刪 → 回 False）。回是否刪到。
    刪的是 ICS 盤點紀錄，非真撤銷（真撤銷=CRL #318）。
    """
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tak_device_certs WHERE id=? AND status='revoked'", (cert_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM tak_device_certs WHERE id=?", (cert_id,))
    audit(
        operator,
        None,
        "tak_device_cert_delete",
        "tak_device_certs",
        str(cert_id),
        {"callsign": row["callsign"], "serial": row["serial"]},
    )
    return True
