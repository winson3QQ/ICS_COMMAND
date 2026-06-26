"""event_taxonomy API + RBAC（P1-10d 地基，issue #60/#66）。

- GET /api/event_taxonomy：READ_ROLES（observer 也能讀，前端渲染事件需要）
- POST /api/event_taxonomy：SYSADMIN_ONLY（admin 編輯器 #66）
- 無效 body → 400
"""

from __future__ import annotations

from auth.role_enum import ROLE_OBSERVER_ZH, ROLE_OPERATOR_ZH, ROLE_SYSADMIN_ZH
from repositories.account_repo import create_account


def _login(client, u, p):
    r = client.post("/api/auth/login", json={"username": u, "pin": p})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def _valid_payload():
    return {
        "version": 1,
        "groups": [{"key": "security", "label": "安全威脅", "order": 1}],
        "events": [
            {
                "key": "explosive",
                "label": "疑似爆裂物",
                "group": "security",
                "icon": "explosive",
                "abbr": "爆",
                "severity": "critical",
                "defaultAssigned": "forward",
                "cot_type": "a-h-G",
            }
        ],
    }


def test_observer_can_get_taxonomy(client):
    """READ_ROLES：observer 讀得到（含 seed 內容）。"""
    create_account("obs_tax", "1234", ROLE_OBSERVER_ZH, "Obs", "observer")
    h = _login(client, "obs_tax", "1234")
    r = client.get("/api/event_taxonomy", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body.get("events"), list) and isinstance(body.get("groups"), list)


def test_operator_cannot_post_taxonomy(client):
    """編輯限 sysadmin：operator POST → 403。"""
    create_account("op_tax", "1234", ROLE_OPERATOR_ZH, "Op", "operator")
    h = _login(client, "op_tax", "1234")
    r = client.post("/api/event_taxonomy", json=_valid_payload(), headers=h)
    assert r.status_code == 403, r.text


def test_sysadmin_can_post_and_get_reflects(client):
    # #66 PR-A：superset 檢查禁移除既有 key → 真實編輯流程是 GET 現況、改、整包 POST。
    create_account("sa_tax", "1234", ROLE_SYSADMIN_ZH, "SA", "sysadmin")
    h = _login(client, "sa_tax", "1234")
    cur = client.get("/api/event_taxonomy", headers=h).json()
    cur["events"][0]["label"] = "改過的標籤"
    r = client.post("/api/event_taxonomy", json=cur, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    got = client.get("/api/event_taxonomy", headers=h).json()
    assert got["events"][0]["label"] == "改過的標籤"


def test_post_rejects_removed_key_and_bad_severity(client):
    """#66 PR-A：移除既有 key（superset 違規）/ 非法 severity → 400（API 層）。"""
    create_account("sa_tax4", "1234", ROLE_SYSADMIN_ZH, "SA4", "sysadmin")
    h = _login(client, "sa_tax4", "1234")
    cur = client.get("/api/event_taxonomy", headers=h).json()
    # 移除一個既有 event → superset 違規
    dropped = {**cur, "events": cur["events"][1:]}
    assert client.post("/api/event_taxonomy", json=dropped, headers=h).status_code == 400
    # 非法 severity
    bad = {**cur, "events": [{**cur["events"][0], "severity": "bogus"}, *cur["events"][1:]]}
    assert client.post("/api/event_taxonomy", json=bad, headers=h).status_code == 400


def test_invalid_body_rejected(client):
    create_account("sa_tax2", "1234", ROLE_SYSADMIN_ZH, "SA2", "sysadmin")
    h = _login(client, "sa_tax2", "1234")
    r = client.post("/api/event_taxonomy", json={"version": 1}, headers=h)  # 缺 events/groups
    assert r.status_code == 400, r.text


def test_deeply_nested_json_rejected_not_500(client):
    """深層巢狀 JSON（json.loads 爆 RecursionError）→ 400，不是 500 DoS（review #68 MED）。"""
    create_account("sa_tax3", "1234", ROLE_SYSADMIN_ZH, "SA3", "sysadmin")
    h = {**_login(client, "sa_tax3", "1234"), "Content-Type": "application/json"}
    n = 15000
    payload = ("[" * n) + ("]" * n)  # ~30KB，遠低於 256KB 上限，但深度爆 recursion
    r = client.post("/api/event_taxonomy", content=payload, headers=h)
    assert r.status_code == 400, r.text
