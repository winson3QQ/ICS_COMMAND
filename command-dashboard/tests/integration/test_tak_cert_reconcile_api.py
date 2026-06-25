"""#398 Slice 2 — TAK cert 對帳 + 撤銷連動 deregister 端點。

測試環境無 TAK_ENROLL_QUEUE_DIR（registrar 未配置）→ reconcile/deregister 回 best-effort
not-configured；本檔驗 RBAC、回應結構、撤銷的 deregister 連動 + 被取代證不誤殺現行。
"""

from __future__ import annotations

from auth.role_enum import ROLE_OPERATOR_ZH
from repositories.account_repo import create_account
from repositories.tak_device_cert_repo import record_issued


def _login(client, username: str, pin: str = "1234") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def test_reconcile_requires_sysadmin(client, auth):
    create_account("op_rec", "1234", ROLE_OPERATOR_ZH, "Op Rec", "operator")
    assert client.get("/api/admin/tak/device-certs/reconcile", headers=_login(client, "op_rec")).status_code == 403
    r = client.get("/api/admin/tak/device-certs/reconcile", headers=auth)
    assert r.status_code == 200
    body = r.json()
    # 測試環境無 enroll queue → ok=False，但結構完整（前端據此顯示「對帳未配置」）。
    assert body["ok"] is False and body["tak_users"] == [] and body["ics_unsynced"] == []


def test_revoke_returns_deregister_field(client, auth):
    rec = record_issued("rev-09", "S1", "atak", "admin", fingerprint="AA:BB", enroll_status="ok")
    r = client.post(f"/api/admin/tak/device-certs/{rec['id']}/revoke", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "revoked"
    # 撤現行證 → 嘗試 deregister（測試環境無 queue → enroll-not-configured），帶回結果欄。
    assert body["deregister"] == "enroll-not-configured"


def test_revoke_ambiguous_multi_active_skips_deregister(client, auth):
    # 同 callsign 有多張 active（重發未撤）→ 撤任一張都不 deregister（ICS 不確定 TAK 綁哪張，保守不動）。
    a = record_issued("dup-x", "S1", "atak", "admin", fingerprint="A", enroll_status="ok")
    record_issued("dup-x", "S2", "atak", "admin", fingerprint="B", enroll_status="ok")
    r = client.post(f"/api/admin/tak/device-certs/{a['id']}/revoke", headers=auth)
    assert r.status_code == 200
    assert r.json()["deregister"] == "skipped-ambiguous"


def test_revoke_infra_cert_never_deregisters(client, auth):
    # review：infra callsign（ics-cot）就算 ICS 有列也絕不 deregister（保護 ICS 自身 TAK 控制面）。
    rec = record_issued("ics-cot", "S1", "atak", "admin", fingerprint="X", enroll_status="ok")
    r = client.post(f"/api/admin/tak/device-certs/{rec['id']}/revoke", headers=auth)
    assert r.status_code == 200 and r.json()["deregister"] == "skipped-infra"


def test_issue_infra_callsign_rejected(client, auth):
    # review：保留身分不可發裝置證（infra 檢查早於 CA 503）。
    r = client.post("/api/admin/tak/device-cert?callsign=ics-tak-admin&mode=atak", headers=auth)
    assert r.status_code == 422


def test_reconcile_classifies_matched_mismatch_zombie_infra_unsynced(client, auth, monkeypatch):
    # review 覆蓋缺口：mock TAK 端回傳 → 驗 endpoint 的分類交叉比對。
    from services import tak_user_enroll

    record_issued("blue-01", "S1", "atak", "admin", fingerprint="MATCH", enroll_status="ok")
    record_issued("red-01", "S1", "atak", "admin", fingerprint="ICSFP", enroll_status="ok")  # vs TAK 不同 → mismatch
    record_issued("only-ics", "S1", "atak", "admin", fingerprint="Z", enroll_status="ok")  # TAK 無 → unsynced

    def _fake_reconcile():
        return {
            "ok": True,
            "reason": "ok",
            "users": [
                {"callsign": "blue-01", "fingerprint": "MATCH"},  # matched
                {"callsign": "red-01", "fingerprint": "TAKFP"},  # mismatch（ICS 記 ICSFP）
                {"callsign": "GGW", "fingerprint": "ZF"},  # zombie（ICS 無 active）
                {"callsign": "ics-cot", "fingerprint": "CF"},  # infra
            ],
        }

    monkeypatch.setattr(tak_user_enroll, "reconcile_tak_users", _fake_reconcile)
    r = client.get("/api/admin/tak/device-certs/reconcile", headers=auth)
    assert r.status_code == 200
    body = r.json()
    by = {u["callsign"]: u["status"] for u in body["tak_users"]}
    assert by == {"blue-01": "matched", "red-01": "mismatch", "GGW": "zombie", "ics-cot": "infra"}
    assert "only-ics" in body["ics_unsynced"]


def test_revoke_unknown_404(client, auth):
    assert client.post("/api/admin/tak/device-certs/99999/revoke", headers=auth).status_code == 404
