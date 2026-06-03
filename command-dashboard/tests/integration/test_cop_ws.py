"""
tests/integration/test_cop_ws.py — issue #29 PR-D：/api/cop/ws/updates WebSocket 推播

鎖住的不變式：
- handshake auth：token 走 Sec-WebSocket-Protocol（不進 URL/access-log）；缺 / 錯 → close 4401
- 連上 → 先收 hello
- 寫操作（POST/PUT/DELETE）成功後，訂閱者依序收到 op=create/update/delete + 遞增 version_clock
"""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect


def _login(client, username: str = "admin", pin: str = "1234") -> str:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _connect(client, token: str, exercise_id: int | None = None):
    url = "/api/cop/ws/updates"
    if exercise_id is not None:
        url += f"?exercise_id={exercise_id}"
    # client offer 常數協定 + 帶 token 的協定；server echo 常數、從另一個取 token
    return client.websocket_connect(url, subprotocols=["ics-cop-v1", f"ics.session.{token}"])


def test_ws_requires_token(client):
    # 只 offer 常數協定、無 token → 4401
    with (
        pytest.raises(WebSocketDisconnect) as ei,
        client.websocket_connect("/api/cop/ws/updates", subprotocols=["ics-cop-v1"]),
    ):
        pass
    assert ei.value.code == 4401


def test_ws_invalid_token(client):
    with (
        pytest.raises(WebSocketDisconnect) as ei,
        client.websocket_connect("/api/cop/ws/updates", subprotocols=["ics-cop-v1", "ics.session.bogus"]),
    ):
        pass
    assert ei.value.code == 4401


def test_ws_hello_on_connect(client):
    tok = _login(client)
    with _connect(client, tok) as ws:
        msg = ws.receive_json()
        assert msg["op"] == "hello"


def test_ws_receives_create(client):
    tok = _login(client)
    h = {"X-Session-Token": tok}
    with _connect(client, tok) as ws:
        assert ws.receive_json()["op"] == "hello"
        r = client.post(
            "/api/cop/entities",
            json={"type": "a-f-G-U-C", "lat": 1.0, "lon": 1.0, "callsign": "WS1"},
            headers=h,
        )
        assert r.status_code == 201, r.text
        msg = ws.receive_json()
        assert msg["op"] == "create"
        assert msg["entity"]["callsign"] == "WS1"
        assert msg["version_clock"] == 1
        assert msg["uid"] == r.json()["uid"]


def test_ws_broadcasts_exercise_switched_on_activate_and_archive(client):
    # P1-14：他人 activate/archive 演習 → broadcast_all → 所有連線收到 exercise_switched
    #（各 client 據此重新依新 scope 對帳 map/面板/chip）。
    tok = _login(client)
    h = {"X-Session-Token": tok}
    ex = client.post("/api/exercises", json={"name": "WS切場", "type": "ttx"}, headers=h).json()
    with _connect(client, tok) as ws:
        assert ws.receive_json()["op"] == "hello"
        assert client.post(f"/api/exercises/{ex['id']}/activate", json={}, headers=h).status_code == 200
        assert ws.receive_json()["op"] == "exercise_switched"
        assert client.post(f"/api/exercises/{ex['id']}/archive", json={}, headers=h).status_code == 200
        assert ws.receive_json()["op"] == "exercise_switched"


def test_ws_receives_update_then_delete(client):
    tok = _login(client)
    h = {"X-Session-Token": tok}
    with _connect(client, tok) as ws:
        ws.receive_json()  # hello
        uid = client.post(
            "/api/cop/entities",
            json={"type": "a-f-G-U-C", "lat": 1.0, "lon": 1.0},
            headers=h,
        ).json()["uid"]
        assert ws.receive_json()["op"] == "create"  # version 1

        client.put(f"/api/cop/entities/{uid}", json={"callsign": "U"}, headers={**h, "If-Match": "1"})
        m = ws.receive_json()
        assert m["op"] == "update" and m["version_clock"] == 2

        client.delete(f"/api/cop/entities/{uid}", headers={**h, "If-Match": "2"})
        m = ws.receive_json()
        assert m["op"] == "delete" and m["version_clock"] == 3
        assert m["uid"] == uid
