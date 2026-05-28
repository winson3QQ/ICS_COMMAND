"""Issue #24 alpha: map_config RBAC widen — operator 可寫 map_config，upload-image 仍限 COMMAND_ROLES。

源於 dogfood 撞牆：operator 畫 zone/route 後 refresh 消失。Root cause = backend 把
/api/map_config 限 COMMAND_ROLES，operator POST 被 403；frontend 不檢 resp.ok 形成 silent fail。

修法：role_enum.py 拆 case
  - /api/map_config     → WRITE_ROLES（sysadmin + commander + operator）
  - /api/map/upload-image → COMMAND_ROLES（換底圖屬系統設定，operator 不該動）
"""

from __future__ import annotations

import io

from auth.role_enum import (
    ROLE_COMMANDER_ZH,
    ROLE_OBSERVER_ZH,
    ROLE_OPERATOR_ZH,
)
from repositories.account_repo import create_account


def _login(client, username: str, pin: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _auth_header(token: str) -> dict[str, str]:
    return {"X-Session-Token": token}


def _minimal_map_payload() -> dict:
    return {"maps": {"indoor": {"zones": []}, "outdoor": {"zones": []}}}


# ── /api/map_config POST ──────────────────────────────────────────


def test_operator_can_post_map_config(client):
    """alpha 修法主驗收：operator 不再被 403。"""
    create_account("op_map24", "1234", ROLE_OPERATOR_ZH, "Operator Map", "operator")
    token = _login(client, "op_map24", "1234")
    r = client.post(
        "/api/map_config",
        json=_minimal_map_payload(),
        headers=_auth_header(token),
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_commander_can_post_map_config(client):
    """Regression：commander 原本就可寫，修法後仍可。"""
    create_account("cmd_map24", "1234", ROLE_COMMANDER_ZH, "Commander Map", "commander")
    token = _login(client, "cmd_map24", "1234")
    r = client.post(
        "/api/map_config",
        json=_minimal_map_payload(),
        headers=_auth_header(token),
    )
    assert r.status_code == 200, r.text


def test_observer_cannot_post_map_config(client):
    """Observer 是 READ_ROLES only，必須仍被擋。"""
    create_account("obs_map24", "1234", ROLE_OBSERVER_ZH, "Observer Map", "observer")
    token = _login(client, "obs_map24", "1234")
    r = client.post(
        "/api/map_config",
        json=_minimal_map_payload(),
        headers=_auth_header(token),
    )
    assert r.status_code == 403


# ── /api/map/upload-image POST（仍限 COMMAND_ROLES）──────────────


def test_operator_cannot_upload_map_image(client):
    """換底圖屬系統設定，operator 不該動；alpha 必須保持這條紅線。"""
    create_account("op_upload24", "1234", ROLE_OPERATOR_ZH, "Operator Upload", "operator")
    token = _login(client, "op_upload24", "1234")
    r = client.post(
        "/api/map/upload-image",
        files={"file": ("dummy.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
        headers=_auth_header(token),
    )
    assert r.status_code == 403


def test_commander_can_upload_map_image(client):
    """Regression：commander 原本就可換底圖。"""
    create_account("cmd_upload24", "1234", ROLE_COMMANDER_ZH, "Commander Upload", "commander")
    token = _login(client, "cmd_upload24", "1234")
    r = client.post(
        "/api/map/upload-image",
        files={"file": ("dummy.png", io.BytesIO(b"\x89PNG\r\n"), "image/png")},
        headers=_auth_header(token),
    )
    assert r.status_code == 200, r.text
