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


# ── XSS hardening（security review 衍生）─────────────────────────
# α PR 把 map_config 寫入下放給 operator 後，pre-existing 渲染端 HTML sink（innerHTML /
# bindTooltip / openModal title）變 operator → commander/sysadmin 提權路徑。Backend 加
# recursive string validator 把 payload 擋在落 disk 前。


def test_map_config_rejects_script_tag_in_label(client):
    """operator 嘗試在 zone.label 注入 <script> → 422，不落 disk。"""
    create_account("op_xss1", "1234", ROLE_OPERATOR_ZH, "Operator XSS1", "operator")
    token = _login(client, "op_xss1", "1234")
    payload = {
        "maps": {
            "outdoor": {
                "zones": [{"id": "z1", "label": "<script>alert(1)</script>"}],
            }
        }
    }
    r = client.post("/api/map_config", json=payload, headers=_auth_header(token))
    assert r.status_code == 422, r.text


def test_map_config_rejects_img_onerror_in_event_code(client):
    """attribute injection 變體：<img onerror>"""
    create_account("op_xss2", "1234", ROLE_OPERATOR_ZH, "Operator XSS2", "operator")
    token = _login(client, "op_xss2", "1234")
    payload = {
        "maps": {
            "outdoor": {
                "zones": [
                    {"id": "z1", "event_code": "<img src=x onerror=alert(1)>"},
                ]
            }
        }
    }
    r = client.post("/api/map_config", json=payload, headers=_auth_header(token))
    assert r.status_code == 422, r.text


def test_map_config_rejects_javascript_url_in_flow_label(client):
    """javascript: URL 在 flow.label（cop.js openModal title 用 innerHTML）"""
    create_account("op_xss3", "1234", ROLE_OPERATOR_ZH, "Operator XSS3", "operator")
    token = _login(client, "op_xss3", "1234")
    payload = {
        "maps": {
            "outdoor": {
                "flows": [{"id": "f1", "label": "javascript:fetch('//evil')"}],
            }
        }
    }
    r = client.post("/api/map_config", json=payload, headers=_auth_header(token))
    assert r.status_code == 422, r.text


def test_map_config_rejects_html_entity_encoding(client):
    """HTML entity encoded payload（&lt;script&gt;）會被瀏覽器 decode → 也擋"""
    create_account("op_xss4", "1234", ROLE_OPERATOR_ZH, "Operator XSS4", "operator")
    token = _login(client, "op_xss4", "1234")
    payload = {
        "maps": {
            "outdoor": {
                "zones": [{"id": "z1", "label": "&lt;script&gt;alert(1)&lt;/script&gt;"}],
            }
        }
    }
    r = client.post("/api/map_config", json=payload, headers=_auth_header(token))
    assert r.status_code == 422, r.text


def test_map_config_rejects_oversize_string(client):
    """單一 string 超過 512 char → 422（防隱蔽 payload + 防 disk fill）"""
    create_account("op_xss5", "1234", ROLE_OPERATOR_ZH, "Operator XSS5", "operator")
    token = _login(client, "op_xss5", "1234")
    payload = {
        "maps": {
            "outdoor": {"zones": [{"id": "z1", "label": "A" * 600}]},
        }
    }
    r = client.post("/api/map_config", json=payload, headers=_auth_header(token))
    assert r.status_code == 422, r.text


def test_map_config_rejects_oversize_body(client):
    """整個 body > 256 KB → 413"""
    create_account("op_xss6", "1234", ROLE_OPERATOR_ZH, "Operator XSS6", "operator")
    token = _login(client, "op_xss6", "1234")
    # 約 300 KB JSON（很多正常但無意義的 zones）
    huge_zones = [{"id": f"z{i}", "label": f"normal-{i}"} for i in range(8000)]
    payload = {"maps": {"outdoor": {"zones": huge_zones}}}
    r = client.post("/api/map_config", json=payload, headers=_auth_header(token))
    assert r.status_code == 413, r.text


def test_map_config_accepts_normal_zh_label(client):
    """中文 + 數字 + 半形 + 中文標點 → 200（false positive 防護）"""
    create_account("op_ok", "1234", ROLE_OPERATOR_ZH, "Operator OK", "operator")
    token = _login(client, "op_ok", "1234")
    payload = {
        "maps": {
            "outdoor": {
                "zones": [
                    {
                        "id": "node_medical_2",
                        "label": "醫療組 (前線 B)",
                        "sub": "支援帳篷 #3",
                        "lat": 24.821,
                        "lng": 121.018,
                    },
                ]
            }
        }
    }
    r = client.post("/api/map_config", json=payload, headers=_auth_header(token))
    assert r.status_code == 200, r.text
