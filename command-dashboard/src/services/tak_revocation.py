"""services/tak_revocation.py — #318 層2 真撤銷：ICS 直連 TAK Server postgres 寫 `certificate` 表。

reality check（2026-06-26，GitHub #318，活機端到端實證）：
- TAK 對 CA 信任的證 **TLS 不拒**、deregister（usermod -D）只把證降成匿名（`__ANON__`）仍連得上。
- 唯一「連都連不進」= **撤銷**：`certificate` 表有該證 SHA-256 hash + `revocation_date`，且 CoreConfig
  `<auth x509checkRevocation="true">` → `X509Authenticator.findOneByHash` 命中即 `RevokedException`。
- **CRL（`<tls><crl crlFile>`）只擋 :8443 Tomcat、不擋 :8089 串流（主威脅）** → 故走 DB 撤銷。
- TAK `certadmin` REST 無「補登 offline 證」端點（只管 TAK 自發證）→ 只能**直寫 postgres**
  （ICS 與 tak-database 同 docker net，:5432 可達）。

**按 fingerprint 精準**（非 deregister 的 callsign 粗粒度）：同 callsign 多張 active 證也只撤這一張 hash。

best-effort：未配置 / 連不上 / 寫錯 → 回 `{ok: False, reason}`，**絕不 raise**（不拖垮 ICS 撤銷帳面）。
耦合（隔離於本 adapter）：schema 綁 TAK `certificate` 表（TAK 升版需回歸驗）+ TAK DB 憑證
（⚠ 汰 dev `takdevpass123`，threat_model §8.4）。lazy import `pg8000`：未配置時不 import。
"""

from __future__ import annotations

import logging

from core import config

log = logging.getLogger(__name__)


def is_configured() -> bool:
    """是否已配置 TAK DB 連線（未配置 → 撤銷僅 ICS 帳面 + deregister，不寫 TAK enforce）。"""
    return bool(config.TAK_DB_HOST and config.TAK_DB_PASSWORD)


def _cert_sha256_fingerprint(pem_path: str) -> str | None:
    """算 PEM 證 leaf 的 SHA-256 fingerprint（冒號分隔大寫）= `openssl -fingerprint -sha256` = TAK
    `certificate.hash` 鍵。純 stdlib（`ssl.PEM_cert_to_DER_cert` 取首證 DER + `hashlib`），讀不到回 None。"""
    import hashlib
    import ssl

    try:
        with open(pem_path) as f:
            der = ssl.PEM_cert_to_DER_cert(f.read())
        h = hashlib.sha256(der).hexdigest().upper()
        return ":".join(h[i : i + 2] for i in range(0, len(h), 2))
    except Exception:
        return None


def infra_fingerprints() -> set[str]:
    """ICS 自身 infra 證（ics-cot/admin/read/write）的 fingerprint 集——**禁撤**：撤這些會毀 ICS 對 TAK
    的控制面（feed 斷、Marti 管理斷）。#318 Slice 3 part③ by-fingerprint 撤銷的安全閘（callsign 比對擋不到
    純 fingerprint 撤銷，故此處按 hash 擋）。讀不到的證略過（best-effort）。"""
    out: set[str] = set()
    for p in (
        config.TAK_CLIENT_CERT,
        config.TAK_MARTI_ADMIN_CERT,
        config.TAK_MARTI_READ_CERT,
        config.TAK_MARTI_WRITE_CERT,
    ):
        if p:
            fp = _cert_sha256_fingerprint(p)
            if fp:
                out.add(fp)
    return out


def _connect():
    """建 pg8000 連線（lazy import；caller 負責 close）。"""
    import pg8000.native  # lazy：未配置 TAK_DB_* 的部署不需此套件

    return pg8000.native.Connection(
        host=config.TAK_DB_HOST,
        port=config.TAK_DB_PORT,
        database=config.TAK_DB_NAME,
        user=config.TAK_DB_USER,
        password=config.TAK_DB_PASSWORD,
        timeout=8,
    )


def revoke_in_tak(fingerprint: str, callsign: str | None = None) -> dict:
    """把證的 SHA-256 fingerprint（冒號分隔大寫 = TAK `certificate.hash` 鍵）標記撤銷進 TAK DB。

    冪等：該 hash 已有 row → `UPDATE revocation_date`；無 → INSERT 最小必要欄（撤銷檢查只看 hash +
    revocation_date，subject/dates 填佔位）。回 `{ok, reason}`。best-effort 不 raise。
    """
    if not is_configured():
        return {"ok": False, "reason": "tak-db-not-configured"}
    fp = (fingerprint or "").strip()
    if not fp:
        return {"ok": False, "reason": "no-fingerprint"}
    subject = f"CN={callsign}" if callsign else "CN=ICS-REVOKED"
    conn = None
    try:
        conn = _connect()
        existing = conn.run("SELECT id FROM certificate WHERE hash = :h LIMIT 1", h=fp)
        if existing:
            conn.run("UPDATE certificate SET revocation_date = now() WHERE hash = :h", h=fp)
        else:
            # id 為序列預設、不指定；revocation 檢查只用 hash+revocation_date，其餘 NOT NULL 欄填佔位。
            conn.run(
                "INSERT INTO certificate (subject_dn, user_dn, issuance_date, effective_date, "
                "expiration_date, revocation_date, certificate, hash) "
                "VALUES (:s, :u, now(), now(), now() + interval '100 years', now(), :c, :h)",
                s=subject,
                u=subject,
                c=f"ICS-318-revocation:{fp}",
                h=fp,
            )
        log.info("[tak-revocation] revoked %s… in TAK DB", fp[:17])
        return {"ok": True, "reason": "revoked-in-tak"}
    except Exception as exc:  # pg8000 errors / 網路 / schema 變動 → best-effort，不拖垮 ICS 撤銷
        log.warning("[tak-revocation] revoke_in_tak 失敗（best-effort）：%s", exc)
        return {"ok": False, "reason": f"tak-db-error:{str(exc)[:100]}"}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
