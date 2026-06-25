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


def record_issued(
    callsign: str,
    serial: str | None,
    mode: str,
    operator: str,
    fingerprint: str | None = None,
    enroll_status: str | None = None,
) -> dict:
    """發證成功後記一列。callsign 可重複（每發一張一列）。

    #398：fingerprint（該證 SHA-256，= TAK managed-user 鍵）+ enroll_status（發證當下 enroll 結果）
    一併存——讓清單顯示「TAK 認哪張」「有沒有同步上 TAK」。fingerprint **不進 audit**（敏感）。
    """
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO tak_device_certs "
            "(callsign, serial, mode, operator, status, issued_at, fingerprint, enroll_status) "
            "VALUES (?, ?, ?, ?, 'active', ?, ?, ?)",
            (callsign, serial, mode, operator, _iso_now(), fingerprint, enroll_status),
        )
        row = conn.execute("SELECT * FROM tak_device_certs WHERE id=?", (cur.lastrowid,)).fetchone()
    audit(
        operator,
        None,
        "tak_device_cert_record",
        "tak_device_certs",
        str(cur.lastrowid),
        {"callsign": callsign, "serial": serial, "mode": mode, "enroll_status": enroll_status},
    )
    return dict(row)


def list_device_certs() -> list[dict]:
    """列出所有發過的 TAK 裝置證（含已撤銷，盤點/audit 用），新到舊。"""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM tak_device_certs ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def has_other_active_cert(callsign: str, cert_id: int) -> bool:
    """#398 A：同 callsign 是否還有**其他** active 證（id 不同）。

    撤銷時用以決定是否從 TAK deregister：TAK managed user 1:1、ICS 卻可能多列同名 active（重發未撤）。
    此時 ICS 無法確知 TAK 現行綁哪張 fingerprint（要 reconcile 才知）→ **保守：只有「這是該 callsign
    唯一 active 證」才 deregister**；還有其他 active 同名證就只標撤銷、不動 TAK（避免誤殺現行 / 把舊證
    留成孤兒）。reconcile 視圖會把這種模糊態暴露給 admin 收斂。
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM tak_device_certs WHERE callsign=? AND id!=? AND status='active' LIMIT 1",
            (callsign, cert_id),
        ).fetchone()
    return row is not None


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
