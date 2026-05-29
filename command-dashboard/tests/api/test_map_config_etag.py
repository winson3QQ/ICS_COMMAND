"""β（issue #29）Phase 1：map_config ETag 樂觀鎖 API 測試。

涵蓋：
- GET 回 ETag header（version）
- HEAD method 支援
- POST 寬鬆模式（無 If-Match）→ 仍 bump version
- POST strict 模式無 If-Match → 428
- POST If-Match 相符 → 200 + version+1
- POST If-Match 不符 → 409 + server_body
- 並發 CAS（compare_and_swap）防護

注：所有測試靠 conftest `_isolate_map_config` autouse fixture 把 MAP_CONFIG_PATH/SEED
導到 tmp_path（不污染真實 data/）。
"""

from __future__ import annotations

from auth.role_enum import ROLE_OPERATOR_ZH
from repositories.account_repo import create_account


def _login(client, username: str, pin: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _hdr(token: str, extra: dict | None = None) -> dict:
    h = {"X-Session-Token": token}
    if extra:
        h.update(extra)
    return h


def _payload(extra_zone: str | None = None) -> dict:
    zones = []
    if extra_zone:
        zones.append({"id": extra_zone, "label": extra_zone})
    return {"maps": {"indoor": {"zones": []}, "outdoor": {"zones": zones}}}


# ── GET / HEAD ──────────────────────────────────────────────


def test_get_returns_etag_header(client, auth):
    r = client.get("/api/map_config", headers=auth)
    assert r.status_code == 200
    assert r.headers.get("ETag", "").startswith('W/"')
    assert r.headers.get("Cache-Control") == "no-store"


def test_head_supported(client, auth):
    r = client.request("HEAD", "/api/map_config", headers=auth)
    assert r.status_code == 200
    assert r.headers.get("ETag", "").startswith('W/"')


# ── POST 寬鬆模式（預設 MAP_CONFIG_STRICT_ETAG=0）──────────


def test_post_without_if_match_bumps_version(client, auth):
    """寬鬆模式：無 If-Match 仍接受，version 從 0 → 1。"""
    r0 = client.get("/api/map_config", headers=auth)
    v0 = r0.json().get("version", 0)

    r = client.post("/api/map_config", json=_payload("z1"), headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["version"] == v0 + 1
    assert r.headers.get("ETag") == f'W/"{v0 + 1}"'


def test_post_persists_actor(client, auth):
    """寫入後 updated_by 應為當前 user（admin）。"""
    client.post("/api/map_config", json=_payload("z1"), headers=auth)
    body = client.get("/api/map_config", headers=auth).json()
    assert body.get("updated_by") == "admin"
    assert "updated_at" in body


# ── POST If-Match 樂觀鎖 ────────────────────────────────────


def test_post_if_match_correct(client, auth):
    v0 = client.get("/api/map_config", headers=auth).json().get("version", 0)
    r = client.post(
        "/api/map_config",
        json=_payload("z1"),
        headers=_hdr(auth["X-Session-Token"], {"If-Match": f'W/"{v0}"'}),
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == v0 + 1


def test_post_if_match_stale_returns_409(client, auth):
    """送過時 version → 409 + server_body（含 server version）。"""
    token = auth["X-Session-Token"]
    v0 = client.get("/api/map_config", headers=auth).json().get("version", 0)
    # 先成功寫一次 → version v0+1
    client.post("/api/map_config", json=_payload("z1"), headers=_hdr(token, {"If-Match": f'W/"{v0}"'}))
    # 再用過時的 v0 寫 → 409
    r = client.post(
        "/api/map_config",
        json=_payload("z2"),
        headers=_hdr(token, {"If-Match": f'W/"{v0}"'}),
    )
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["error"] == "version_conflict"
    assert body["server_version"] == v0 + 1
    assert body["your_version"] == v0
    assert "server_body" in body
    # server_body 應含先前寫入的 z1（不是 client 想寫的 z2）
    zones = body["server_body"]["maps"]["outdoor"]["zones"]
    assert any(z.get("id") == "z1" for z in zones)


# ── RBAC 仍生效（operator 可寫，承 α）──────────────────────


def test_operator_can_post_with_etag(client):
    create_account("op_etag", "1234", ROLE_OPERATOR_ZH, "Operator ETag", "operator")
    token = _login(client, "op_etag", "1234")
    v0 = client.get("/api/map_config", headers=_hdr(token)).json().get("version", 0)
    r = client.post(
        "/api/map_config",
        json=_payload("z1"),
        headers=_hdr(token, {"If-Match": f'W/"{v0}"'}),
    )
    assert r.status_code == 200, r.text
