# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""帳號 display_name 編輯 + XSS 防護。

Bug：admin 改不了已建帳號的顯示名稱 —— 前端 admSaveEdit 讀了輸入框卻沒送出，
後端也沒有對應端點。修法：新增 PUT /api/admin/accounts/{username}/display-name
（account-manager only）。

Security：display_name 會被 account 列表拼進 innerHTML（auth.js admLoadAccounts）→
XSS sink，新端點 + create 路徑都用 validate_no_unsafe_strings 擋在落 disk 前。
"""

from __future__ import annotations

from auth.role_enum import ROLE_OPERATOR_ZH
from repositories.account_repo import create_account


def _login(client, username: str = "admin", pin: str = "1234") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def _display_name_of(client, headers, username: str) -> str | None:
    r = client.get("/api/admin/accounts", headers=headers)
    assert r.status_code == 200, r.text
    for a in r.json():
        if a["username"] == username:
            return a.get("display_name")
    raise AssertionError(f"account {username} not found")


# ── 主功能：改 display_name 並持久 ────────────────────────────────


def test_admin_can_update_display_name(client):
    create_account("dn_target", "5678", ROLE_OPERATOR_ZH, "舊名", "operator")
    headers = _login(client)
    r = client.put(
        "/api/admin/accounts/dn_target/display-name",
        headers=headers,
        json={"display_name": "新顯示名稱"},
    )
    assert r.status_code == 200, r.text
    assert _display_name_of(client, headers, "dn_target") == "新顯示名稱"


def test_update_display_name_nonexistent_returns_404(client):
    headers = _login(client)
    r = client.put(
        "/api/admin/accounts/ghost_user/display-name",
        headers=headers,
        json={"display_name": "x"},
    )
    assert r.status_code == 404


# ── RBAC：operator 不能改 ────────────────────────────────────────


def test_operator_cannot_update_display_name(client):
    create_account("dn_op", "5678", ROLE_OPERATOR_ZH, "Operator", "operator")
    headers = _login(client, "dn_op", "5678")
    r = client.put(
        "/api/admin/accounts/dn_op/display-name",
        headers=headers,
        json={"display_name": "自己改"},
    )
    assert r.status_code == 403


# ── XSS：update + create 兩路徑都擋 ──────────────────────────────


def test_update_display_name_rejects_script_tag(client):
    create_account("dn_xss", "5678", ROLE_OPERATOR_ZH, "OK", "operator")
    headers = _login(client)
    r = client.put(
        "/api/admin/accounts/dn_xss/display-name",
        headers=headers,
        json={"display_name": "<script>alert(1)</script>"},
    )
    assert r.status_code == 422, r.text
    # 不可落 disk：仍是原值
    assert _display_name_of(client, headers, "dn_xss") == "OK"


def test_create_account_rejects_xss_display_name(client):
    headers = _login(client)
    r = client.post(
        "/api/admin/accounts",
        headers=headers,
        json={
            "username": "dn_create_xss",
            "pin": "5678",
            "role": "操作員",
            "role_detail": "operator",
            "display_name": "<img src=x onerror=alert(1)>",
        },
    )
    assert r.status_code == 422, r.text


def test_update_display_name_accepts_normal_zh(client):
    create_account("dn_ok", "5678", ROLE_OPERATOR_ZH, "init", "operator")
    headers = _login(client)
    r = client.put(
        "/api/admin/accounts/dn_ok/display-name",
        headers=headers,
        json={"display_name": "醫療組長 (前線)"},
    )
    assert r.status_code == 200, r.text
