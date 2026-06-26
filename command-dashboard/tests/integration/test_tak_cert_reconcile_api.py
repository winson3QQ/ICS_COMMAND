"""#398 Slice 2 — TAK cert 對帳 + 撤銷連動 deregister 端點。

測試環境無 TAK_ENROLL_QUEUE_DIR（registrar 未配置）→ reconcile/deregister 回 best-effort
not-configured；本檔驗 RBAC、回應結構、撤銷的 deregister 連動 + 被取代證不誤殺現行。
"""

from __future__ import annotations

import pytest

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


def test_revoke_writes_tak_revocation_with_fingerprint(client, auth, monkeypatch):
    """#318：撤銷現行證 → 呼叫 revoke_in_tak(fingerprint, callsign)，結果進回應 + audit。"""
    from services import tak_revocation

    captured: dict = {}

    def _fake(fp, callsign=None):
        captured["fp"], captured["cs"] = fp, callsign
        return {"ok": True, "reason": "revoked-in-tak"}

    monkeypatch.setattr(tak_revocation, "revoke_in_tak", _fake)
    rec = record_issued("rev-318", "S1", "atak", "admin", fingerprint="FP:318", enroll_status="ok")
    r = client.post(f"/api/admin/tak/device-certs/{rec['id']}/revoke", headers=auth)
    assert r.status_code == 200
    assert r.json()["tak_revoke"] == "revoked-in-tak"
    assert captured == {"fp": "FP:318", "cs": "rev-318"}  # 按 fingerprint 精準 + 帶 callsign 當 subject


def test_revoke_infra_skips_tak_revocation(client, auth, monkeypatch):
    """#318：infra（ics-cot）絕不寫 TAK DB 撤銷（毀 ICS 自身 TAK 控制面）。"""
    from services import tak_revocation

    monkeypatch.setattr(tak_revocation, "revoke_in_tak", lambda *a, **k: pytest.fail("infra 不該真撤"))
    rec = record_issued("ics-cot", "S1", "atak", "admin", fingerprint="X", enroll_status="ok")
    r = client.post(f"/api/admin/tak/device-certs/{rec['id']}/revoke", headers=auth)
    assert r.status_code == 200 and r.json()["tak_revoke"] == "skipped-infra"


def test_revoke_no_fingerprint_skips_tak_revocation(client, auth, monkeypatch):
    """#318：升級前 NULL fingerprint → 無 hash 可撤，跳過（不呼叫 revoke_in_tak）。"""
    from services import tak_revocation

    monkeypatch.setattr(tak_revocation, "revoke_in_tak", lambda *a, **k: pytest.fail("無 fp 不該真撤"))
    rec = record_issued("nofp-318", "S1", "atak", "admin", fingerprint=None, enroll_status="ok")
    r = client.post(f"/api/admin/tak/device-certs/{rec['id']}/revoke", headers=auth)
    assert r.status_code == 200 and r.json()["tak_revoke"] == "no-fingerprint"


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
    rows = {u["callsign"]: u for u in body["tak_users"]}
    assert {k: v["status"] for k, v in rows.items()} == {
        "blue-01": "matched",
        "red-01": "mismatch",
        "GGW": "zombie",
        "ics-cot": "infra",
    }
    # #401 enrich：ICS 發的帶 ics_cert_id（供撤銷）；殭屍/infra 無。
    assert rows["blue-01"]["ics_cert_id"] is not None and rows["red-01"]["ics_cert_id"] is not None
    assert rows["GGW"]["ics_cert_id"] is None and rows["ics-cot"]["ics_cert_id"] is None
    # ics_unsynced 現為物件（callsign/cert_id/non_ascii）。
    uns = {c["callsign"]: c for c in body["ics_unsynced"]}
    assert "only-ics" in uns and uns["only-ics"]["cert_id"] is not None and uns["only-ics"]["non_ascii"] is False


def test_reconcile_unsynced_marks_non_ascii(client, auth, monkeypatch):
    from services import tak_user_enroll

    record_issued("山豬", "S1", "atak", "admin", fingerprint=None, enroll_status="non-ascii-callsign")
    monkeypatch.setattr(tak_user_enroll, "reconcile_tak_users", lambda: {"ok": True, "reason": "ok", "users": []})
    body = client.get("/api/admin/tak/device-certs/reconcile", headers=auth).json()
    uns = {c["callsign"]: c for c in body["ics_unsynced"]}
    assert uns["山豬"]["non_ascii"] is True


# ── #401：POST /tak/users/{callsign}/deregister（直接移除 TAK 殭屍）──
def test_deregister_user_requires_sysadmin(client, auth):
    create_account("op_dr", "1234", ROLE_OPERATOR_ZH, "Op DR", "operator")
    assert client.post("/api/admin/tak/users/GGW/deregister", headers=_login(client, "op_dr")).status_code == 403


def test_deregister_infra_user_blocked(client, auth):
    assert client.post("/api/admin/tak/users/ics-cot/deregister", headers=auth).status_code == 422
    assert client.post("/api/admin/tak/users/ics-tak-admin/deregister", headers=auth).status_code == 422
    # review 硬化：大小寫變體也擋（防 TAK usermod 若大小寫不敏感被繞過刪 ics-cot）。
    assert client.post("/api/admin/tak/users/ICS-COT/deregister", headers=auth).status_code == 422
    assert client.post("/api/admin/tak/users/Ics-Tak-Admin/deregister", headers=auth).status_code == 422


def test_deregister_invalid_callsign_422(client, auth):
    assert client.post("/api/admin/tak/users/-bad/deregister", headers=auth).status_code == 422


def test_deregister_valid_reaches_service_503_when_unconfigured(client, auth):
    # 合法非 infra callsign → 走到 deregister_device（測試環境無 queue → not-configured → 503，非 422/500）。
    assert client.post("/api/admin/tak/users/GGW/deregister", headers=auth).status_code == 503


def test_revoke_unknown_404(client, auth):
    assert client.post("/api/admin/tak/device-certs/99999/revoke", headers=auth).status_code == 404


# ── #404：reconcile in_anon 旗標 + POST /tak/users/{callsign}/strip-anon ──


def test_reconcile_flags_in_anon_isolation_gap(client, auth, monkeypatch):
    """#404：群含 __ANON__ 或空群（runtime 落 __ANON__）→ in_anon=True + 列入 anon_users；
    named 群 → False；groups=None（舊式 registrar）→ False（不誤判）。"""
    from services import tak_user_enroll

    def _fake():
        return {
            "ok": True,
            "reason": "ok",
            "users": [
                {"callsign": "ics-cot", "fingerprint": "CF", "groups": ["red", "blue", "neutral"]},  # 安全
                {"callsign": "ics-tak-admin", "fingerprint": "AF", "groups": ["__ANON__"]},  # 卡 __ANON__
                {"callsign": "selfclosed", "fingerprint": "SF", "groups": []},  # 空群 → runtime __ANON__
                {"callsign": "legacy", "fingerprint": "LF", "groups": None},  # 舊式未知 → 不誤判
            ],
        }

    monkeypatch.setattr(tak_user_enroll, "reconcile_tak_users", _fake)
    body = client.get("/api/admin/tak/device-certs/reconcile", headers=auth).json()
    rows = {u["callsign"]: u for u in body["tak_users"]}
    assert rows["ics-cot"]["in_anon"] is False
    assert rows["ics-tak-admin"]["in_anon"] is True
    assert rows["selfclosed"]["in_anon"] is True
    assert rows["legacy"]["in_anon"] is False
    # #404：ics-tak-admin（REST-only infra）在 __ANON__ 但**良性豁免**——in_anon=True 但 anon_exempt=True，
    # 不列入 anon_users（真破口）；selfclosed 非 infra → 真破口、不豁免。
    assert rows["ics-tak-admin"]["anon_exempt"] is True
    assert rows["selfclosed"]["anon_exempt"] is False
    assert body["anon_users"] == ["selfclosed"]  # 排序、只列**該修的**破口（admin 豁免）


def test_reconcile_surfaces_online_anon(client, auth, monkeypatch):
    """#404：在線匿名連線（__ANON__ 且非名冊）列入 online_anon；非匿名/名冊內的不列。"""
    from services import tak_group_sync, tak_user_enroll

    def _rec():
        return {
            "ok": True,
            "reason": "ok",
            "users": [{"callsign": "ics-cot", "fingerprint": "CF", "groups": ["red", "blue", "neutral"]}],
        }

    async def _online():
        return [
            {"client_uid": "AC4B", "username": "3QQ-itak", "groups": ["__ANON__"]},  # 匿名+非名冊 → 列
            {"client_uid": "", "username": "ics-cot", "groups": ["red", "blue", "neutral"]},  # 非匿名 → 不列
        ]

    monkeypatch.setattr(tak_user_enroll, "reconcile_tak_users", _rec)
    monkeypatch.setattr(tak_group_sync, "list_online_subscriptions", _online)
    body = client.get("/api/admin/tak/device-certs/reconcile", headers=auth).json()
    assert body["online_anon"] == [{"client_uid": "AC4B", "username": "3QQ-itak", "groups": ["__ANON__"]}]


def test_strip_anon_requires_sysadmin(client, auth):
    create_account("op_sa", "1234", ROLE_OPERATOR_ZH, "Op SA", "operator")
    assert client.post("/api/admin/tak/users/GGW/strip-anon", headers=_login(client, "op_sa")).status_code == 403


def test_strip_anon_invalid_callsign_422(client, auth):
    assert client.post("/api/admin/tak/users/-bad/strip-anon", headers=auth).status_code == 422


def test_strip_anon_unconfigured_503(client, auth):
    # 無 enroll queue → reconcile 回 not-configured → 讀不到 roster → 503（非 500/422）。
    assert client.post("/api/admin/tak/users/GGW/strip-anon", headers=auth).status_code == 503


def test_strip_anon_user_not_in_roster_404(client, auth, monkeypatch):
    from services import tak_user_enroll

    monkeypatch.setattr(tak_user_enroll, "reconcile_tak_users", lambda: {"ok": True, "reason": "ok", "users": []})
    assert client.post("/api/admin/tak/users/GGW/strip-anon", headers=auth).status_code == 404


def test_strip_anon_empty_fingerprint_409(client, auth, monkeypatch):
    """#404 review：升級前 NULL fingerprint → usermod -r 無 -f 可帶 → 清楚 409，非 opaque registrar 503。"""
    from services import tak_user_enroll

    def _rec():
        return {
            "ok": True,
            "reason": "ok",
            "users": [{"callsign": "legacy-x", "fingerprint": "", "groups": ["__ANON__"]}],
        }

    monkeypatch.setattr(tak_user_enroll, "reconcile_tak_users", _rec)
    assert client.post("/api/admin/tak/users/legacy-x/strip-anon", headers=auth).status_code == 409


def test_strip_anon_success_passes_fingerprint(client, auth, monkeypatch):
    from services import tak_user_enroll

    def _rec():
        return {
            "ok": True,
            "reason": "ok",
            "users": [{"callsign": "ics-cot", "fingerprint": "A2:10:7F", "groups": ["__ANON__", "red"]}],
        }

    monkeypatch.setattr(tak_user_enroll, "reconcile_tak_users", _rec)
    captured: dict = {}

    def _fake_strip(cs, fp):
        captured["cs"], captured["fp"] = cs, fp
        return {"ok": True, "reason": "stripped"}

    monkeypatch.setattr(tak_user_enroll, "strip_anon_group", _fake_strip)
    r = client.post("/api/admin/tak/users/ics-cot/strip-anon", headers=auth)
    assert r.status_code == 200 and r.json()["strip_anon"] == "stripped"
    # 端點須把 reconcile 取到的 fingerprint 傳給 strip（usermod -r 需 -f 不動憑證）。
    assert captured == {"cs": "ics-cot", "fp": "A2:10:7F"}


# ── #318 Slice 3：撤銷 backfill（把既有 ICS-已撤+有 fp 的證一次推進 TAK）──────────────
def test_backfill_pushes_revoked_with_fingerprint(client, auth, monkeypatch):
    """已撤+有 fp → 推；infra 跳過；無 fp 計 skipped；冪等靠 revoke_in_tak。"""
    from repositories.tak_device_cert_repo import mark_revoked
    from services import tak_revocation

    monkeypatch.setattr(tak_revocation, "is_configured", lambda: True)
    pushed: list = []

    def _fake(fp, callsign=None):
        pushed.append(callsign)
        return {"ok": True, "reason": "revoked-in-tak"}

    monkeypatch.setattr(tak_revocation, "revoke_in_tak", _fake)
    for cs, fp in [("bf-a", "FP:A"), ("bf-b", "FP:B"), ("ics-cot", "FP:INFRA")]:
        rec = record_issued(cs, "S", "atak", "admin", fingerprint=fp, enroll_status="ok")
        mark_revoked(rec["id"], "admin")
    nofp = record_issued("bf-nofp", "S", "atak", "admin", fingerprint=None, enroll_status="ok")
    mark_revoked(nofp["id"], "admin")

    body = client.post("/api/admin/tak/revocations/backfill", headers=auth).json()
    assert body["ok"] is True
    assert body["pushed"] == 2  # bf-a, bf-b（infra 跳過、nofp 不在有-fp 清單）
    assert body["total_with_fingerprint"] == 3  # a, b, infra
    assert body["skipped_no_fingerprint"] == 1  # bf-nofp
    assert set(pushed) == {"bf-a", "bf-b"} and "ics-cot" not in pushed  # infra 絕不推


def test_backfill_not_configured_skips_write(client, auth, monkeypatch):
    """TAK DB 未配置 → 不寫、回 tak-db-not-configured，仍誠實報 skipped_no_fingerprint。"""
    from repositories.tak_device_cert_repo import mark_revoked
    from services import tak_revocation

    monkeypatch.setattr(tak_revocation, "is_configured", lambda: False)
    monkeypatch.setattr(tak_revocation, "revoke_in_tak", lambda *a, **k: pytest.fail("未配置不該寫 TAK"))
    rec = record_issued("bf-x", "S", "atak", "admin", fingerprint=None, enroll_status="ok")
    mark_revoked(rec["id"], "admin")

    body = client.post("/api/admin/tak/revocations/backfill", headers=auth).json()
    assert body["ok"] is False and body["reason"] == "tak-db-not-configured"
    assert body["pushed"] == 0 and body["skipped_no_fingerprint"] == 1


def test_backfill_requires_sysadmin(client):
    """sysadmin only（/api/admin/* → SYSADMIN_ONLY）。"""
    create_account("op_bf", "1234", ROLE_OPERATOR_ZH, "Op BF", "operator")
    assert client.post("/api/admin/tak/revocations/backfill", headers=_login(client, "op_bf")).status_code == 403


# ── #318 Slice 3 part③：按 fingerprint 撤盤點外/非 dashboard 發的證 ──────────────
_FP32 = ":".join(["AB"] * 32)  # 合法 SHA-256 冒號分隔大寫（32 段）


def test_revoke_by_fingerprint_writes_tak(client, auth, monkeypatch):
    from services import tak_revocation

    monkeypatch.setattr(tak_revocation, "is_configured", lambda: True)
    monkeypatch.setattr(tak_revocation, "infra_fingerprints", lambda: set())
    cap: dict = {}

    def _fake(fp, callsign=None):
        cap["fp"], cap["cs"] = fp, callsign
        return {"ok": True, "reason": "revoked-in-tak"}

    monkeypatch.setattr(tak_revocation, "revoke_in_tak", _fake)
    r = client.post(
        "/api/admin/tak/revocations/by-fingerprint", headers=auth, json={"fingerprint": _FP32, "callsign": "rogue-1"}
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["fingerprint"] == _FP32
    assert cap == {"fp": _FP32, "cs": "rogue-1"}


def test_revoke_by_fingerprint_bad_format_422(client, auth):
    r = client.post(
        "/api/admin/tak/revocations/by-fingerprint", headers=auth, json={"fingerprint": "not-a-fingerprint"}
    )
    assert r.status_code == 422


def test_revoke_by_fingerprint_blocks_infra(client, auth, monkeypatch):
    """禁撤 infra（按 hash 比對）——純 fingerprint 撤銷躲不過 callsign 閘，故按 hash 擋。"""
    from services import tak_revocation

    monkeypatch.setattr(tak_revocation, "infra_fingerprints", lambda: {_FP32})
    monkeypatch.setattr(tak_revocation, "revoke_in_tak", lambda *a, **k: pytest.fail("infra 不該撤"))
    r = client.post("/api/admin/tak/revocations/by-fingerprint", headers=auth, json={"fingerprint": _FP32})
    assert r.status_code == 403


def test_revoke_by_fingerprint_not_configured(client, auth, monkeypatch):
    from services import tak_revocation

    monkeypatch.setattr(tak_revocation, "infra_fingerprints", lambda: set())
    monkeypatch.setattr(tak_revocation, "is_configured", lambda: False)
    monkeypatch.setattr(tak_revocation, "revoke_in_tak", lambda *a, **k: pytest.fail("未配置不該寫"))
    r = client.post("/api/admin/tak/revocations/by-fingerprint", headers=auth, json={"fingerprint": _FP32})
    assert r.status_code == 200 and r.json() == {"ok": False, "reason": "tak-db-not-configured", "fingerprint": _FP32}


def test_revoke_by_fingerprint_requires_sysadmin(client):
    create_account("op_rbf", "1234", ROLE_OPERATOR_ZH, "Op RBF", "operator")
    r = client.post(
        "/api/admin/tak/revocations/by-fingerprint", headers=_login(client, "op_rbf"), json={"fingerprint": _FP32}
    )
    assert r.status_code == 403
