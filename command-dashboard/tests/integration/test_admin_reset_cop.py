"""
tests/integration/test_admin_reset_cop.py — issue #29 PR-G1b

鎖住：`/api/admin/reset-db` 一併清掉 cop_entities（事件位置圖釘 cutover 後存於此表）。
修補既有破口——reset 從前只清 events 表、不清 cop_entities，導致事件記錄沒了但地圖
圖釘留孤兒。reset-exercise 則只清演習場域（exercise_id 非空）的 cop_entities。
"""

from __future__ import annotations


def _login(client, username: str = "admin", pin: str = "1234") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def _create_cop(client, headers, **fields) -> dict:
    body = {"type": "a-u-G", "lat": 24.8, "lon": 121.0, "callsign": "EVT"}
    body.update(fields)
    r = client.post("/api/cop/entities", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def test_reset_db_broadcasts_resync_to_ws_clients(client):
    """reset 後其他 client 不會收 per-entity delete（raw SQL），改靠 {op:'resync'} 廣播
    觸發全量對帳。鎖住：WS 訂閱者在 reset-db 後收到 op=resync。"""
    h = _login(client)
    tok = h["X-Session-Token"]
    url = "/api/cop/ws/updates"
    with client.websocket_connect(url, subprotocols=["ics-cop-v1", f"ics.session.{tok}"]) as ws:
        assert ws.receive_json()["op"] == "hello"
        assert client.post("/api/admin/reset-db", headers=h, json={"confirm": "RESET"}).status_code == 200
        assert ws.receive_json()["op"] == "resync"


def test_reset_db_clears_cop_entities(client):
    h = _login(client)
    # 建兩顆 COP entity（模擬事件圖釘 + route）。顯式 uid 避免測試環境 uuid 決定性碰撞。
    _create_cop(client, h, uid="manual:evt-a", attributes={"kind": "event", "event_id": "ev-1"})
    _create_cop(client, h, uid="manual:rte-a", attributes={"kind": "route", "vertices": [[24.8, 121.0], [24.9, 121.1]]})
    assert len(client.get("/api/cop/entities", headers=h).json()["entities"]) == 2

    r = client.post("/api/admin/reset-db", headers=h, json={"confirm": "RESET"})
    assert r.status_code == 200, r.text
    assert "cop_entities" in r.json()["cleared_tables"]
    # reset 後 COP 圖釘全清（不留孤兒）
    assert client.get("/api/cop/entities", headers=h).json()["entities"] == []


def test_reset_exercise_clears_only_exercise_scoped_cop(client):
    from repositories.exercise_repo import create_exercise

    h = _login(client)
    ex = create_exercise({"name": "G1b-reset-test", "type": "ttx"})  # 有效 exercise_id（FK）
    # 演習場域圖釘（exercise_id 非空）vs 正式圖釘（exercise_id 空）。顯式 uid 避免碰撞。
    _create_cop(
        client, h, uid="manual:ex-evt", exercise_id=ex["id"], attributes={"kind": "event", "event_id": "ex-evt"}
    )
    real = _create_cop(client, h, uid="manual:real-evt", attributes={"kind": "event", "event_id": "real-evt"})

    r = client.post("/api/admin/reset-exercise", headers=h, json={"confirm": "RESET"})
    assert r.status_code == 200, r.text

    remaining = client.get("/api/cop/entities", headers=h).json()["entities"]
    uids = {e["uid"] for e in remaining}
    assert real["uid"] in uids  # 正式圖釘保留
    assert all(e.get("exercise_id") is None for e in remaining)  # 演習場域圖釘已清


# ── #237：reset 須一併清「通聯（chats）」——P2-07 加表時漏進清單（髒起點 + AAR 混場 + PII 殘留）──


def _chat_senders() -> set[str]:
    from core.database import get_conn

    with get_conn() as conn:
        return {r[0] for r in conn.execute("SELECT sender_uid FROM chats").fetchall()}


def test_reset_db_clears_chats(client):
    """#237：reset-db 須清 chats（原漏 → reset 後舊通聯獨活）。"""
    from repositories.chat_repo import insert_chat
    from schemas.chat import ChatIn

    h = _login(client)
    insert_chat(ChatIn(sender_uid="GeoChat.dev.room.g1", callsign="A1", message="hi", exercise_id=None))
    assert _chat_senders()  # 確有資料

    r = client.post("/api/admin/reset-db", headers=h, json={"confirm": "RESET"})
    assert r.status_code == 200, r.text
    assert "chats" in r.json()["cleared_tables"]
    assert _chat_senders() == set()  # 全清


def test_reset_exercise_clears_only_exercise_scoped_chats(client):
    """#237：reset-exercise 只清演習場域 chats（exercise_id 非空），實戰 NULL 池保留（同 cop_entities）。"""
    from repositories.chat_repo import insert_chat
    from repositories.exercise_repo import create_exercise
    from schemas.chat import ChatIn

    h = _login(client)
    ex = create_exercise({"name": "chat-reset-test", "type": "ttx"})
    insert_chat(ChatIn(sender_uid="GeoChat.dev.room.ex", message="ex msg", exercise_id=ex["id"]))
    insert_chat(ChatIn(sender_uid="GeoChat.dev.room.real", message="real msg", exercise_id=None))

    r = client.post("/api/admin/reset-exercise", headers=h, json={"confirm": "RESET"})
    assert r.status_code == 200, r.text

    senders = _chat_senders()
    assert "GeoChat.dev.room.real" in senders  # 實戰 NULL 池保留
    assert "GeoChat.dev.room.ex" not in senders  # 演習場域清掉
