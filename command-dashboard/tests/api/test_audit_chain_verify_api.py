"""
tests/api/test_audit_chain_verify_api.py — #372（#348-F3）稽核鏈驗證端點

鎖「verify_audit_chain 已接上 runtime」（原 finding：runtime 零 caller＝死驗證器）：
  - sysadmin GET /api/admin/audit-chain/verify → 200 + 報告（ok/total/broken_at/reason）
  - 竄改一列 → 端點回 ok=False + broken_at
  - 非 sysadmin（operator）/ 未登入 → 擋下（RBAC SYSADMIN_ONLY，非 200）

單元層 verify 邏輯由 tests/test_audit_hash_chain.py 覆蓋；本檔只驗「端點 wiring + 授權」。
"""


def _login(client, username: str = "admin", pin: str = "1234") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def test_verify_endpoint_sysadmin_ok(client):
    """sysadmin 取得驗證報告：200 + ok=True（fresh DB 鏈完整）。"""
    r = client.get("/api/admin/audit-chain/verify", headers=_login(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"ok", "total", "broken_at", "reason"}
    assert body["ok"] is True
    assert body["broken_at"] is None


def test_verify_endpoint_detects_tampering(client):
    """竄改一筆 audit_log detail → 端點回 ok=False + broken_at 指向斷點。"""
    from core.audit_chain import compute_next_hash_prev
    from core.database import get_conn

    # 寫兩筆「經 hook」鏈記錄（第二筆 hash_prev 對齊第一筆），再竄改第一筆 → 斷鏈。
    ids = []
    with get_conn() as conn:
        for i in range(2):
            hp = compute_next_hash_prev(conn)
            cur = conn.execute(
                "INSERT INTO audit_log(operator,device_id,action_type,target_table,target_id,"
                "detail,exercise_id,created_at,correlation_id,hash_prev) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    f"u{i}",
                    "d",
                    "event",
                    "events",
                    str(i),
                    f'{{"i":{i}}}',
                    None,
                    f"2026-06-24T10:0{i}:00Z",
                    f"cid-{i}",
                    hp,
                ),
            )
            ids.append(cur.lastrowid)
        conn.commit()
        conn.execute("UPDATE audit_log SET detail=? WHERE id=?", ('{"x":1}', ids[0]))
        conn.commit()

    r = client.get("/api/admin/audit-chain/verify", headers=_login(client))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False
    assert body["broken_at"] is not None


def test_verify_endpoint_rejects_non_sysadmin(client):
    """operator session 打 sysadmin-only 端點 → 非 200（RBAC SYSADMIN_ONLY，#370 default-deny）。"""
    from repositories.account_repo import create_account

    create_account("auditop", "5678", "操作員", "", "operator")
    r = client.get("/api/admin/audit-chain/verify", headers=_login(client, "auditop", "5678"))
    assert r.status_code != 200
    assert r.status_code in (401, 403)


def test_verify_endpoint_rejects_unauthenticated(client):
    """無 session → 擋下（非 200）。"""
    r = client.get("/api/admin/audit-chain/verify")
    assert r.status_code != 200
