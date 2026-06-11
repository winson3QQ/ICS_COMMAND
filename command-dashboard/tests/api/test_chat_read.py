"""
api/test_chat_read.py — GET /api/chat 通聯唯讀 API（#213 b1）

驗：
  - READ_ROLES 可讀（含 observer——#213 #1 裁示：observer 做 audit 需看當前場通聯）
  - 回傳 shape + chronological 升序（最新 limit 筆、oldest→newest）
  - message escape 保真（後端防線穿透讀路徑不被反解）
  - exercise scope 隔離（operator/observer 鎖 active 場；歷史場 ?exercise_id 限 COMMAND_ROLES）
  - 時間窗 from/to
  - truncated 探測（>limit 才報，且保留最新 limit）
  - group（聊天室）原樣回傳，供前端泛型生成 room 標籤/chips

通聯經 POST /api/tak/events 推 b-t-f 落 chats（走 ingest_cot_event 分流，與生產同路徑）。
"""

import pytest

pytestmark = pytest.mark.api

# operator_auth / observer_auth / commander_auth 在 tests/api/conftest.py；client/auth/active_exercise 在 tests/conftest.py


def _push_chat(client, auth, uid, message, t, group=None, callsign=None):
    """經 POST /api/tak/events 推一筆 b-t-f GeoChat（active 場下 → 綁場落 chats）。

    b-t-f 走分流回 None，POST 回 status='skipped'（非 ingested），但 chat row 已建——
    故不 assert response status 字串，只確認 HTTP 200。
    """
    body = {
        "uid": uid, "type": "b-t-f", "time": t, "start": t,
        "stale": "2099-01-01T00:00:00Z", "how": "h-g-i-g-o", "lat": 24.1, "lon": 120.6,
        "remarks": message,
    }
    if callsign:
        body["callsign"] = callsign
    if group:
        body["detail"] = {"__chat": {"chatroom": group, "senderCallsign": callsign or uid}}
    r = client.post("/api/tak/events", json=body, headers=auth)
    assert r.status_code == 200, r.text


def _mk_exercise(client, auth, name):
    return client.post("/api/exercises", json={"name": name, "type": "ttx"}, headers=auth).json()


# ── 正常查詢 + shape ─────────────────────────────────────────────────────────


def test_chat_returns_ascending_with_shape(client, auth, active_exercise):
    _push_chat(client, auth, "C1", "集結點 A", "2026-06-05T04:00:00Z", group="All Chat Rooms", callsign="ALPHA-1")
    _push_chat(client, auth, "C2", "已到位", "2026-06-05T04:00:10Z", group="Alpha", callsign="BRAVO-2")
    data = client.get("/api/chat", headers=auth).json()
    assert data["meta"]["count"] == 2
    assert data["meta"]["truncated"] is False
    msgs = [c["message"] for c in data["chats"]]
    assert msgs == ["集結點 A", "已到位"]  # chronological 升序（oldest→newest）
    c0 = data["chats"][0]
    assert set(c0) >= {"id", "sender_uid", "callsign", "message", "group", "lat", "lon", "t"}
    assert c0["callsign"] == "ALPHA-1"
    assert c0["group"] == "All Chat Rooms"  # 聊天室原樣回傳（前端泛型生成 chips）


def test_chat_group_passthrough_distinct_rooms(client, auth, active_exercise):
    """不同聊天室原樣回傳，前端可從 distinct group 動態生成 filter chips（不寫死房間清單）。"""
    _push_chat(client, auth, "C1", "m1", "2026-06-05T04:00:00Z", group="All Chat Rooms")
    _push_chat(client, auth, "C2", "m2", "2026-06-05T04:00:01Z", group="O/C")
    _push_chat(client, auth, "C3", "m3", "2026-06-05T04:00:02Z", group=None)  # DM/無房間
    rooms = {c["group"] for c in client.get("/api/chat", headers=auth).json()["chats"]}
    assert rooms == {"All Chat Rooms", "O/C", None}


# ── escape 保真（後端防線穿透讀路徑）─────────────────────────────────────────


def test_chat_message_escaped_through_read(client, auth, active_exercise):
    """message 入庫已 html.escape；讀回仍是 escaped 文字（前端純文字渲染、不反解）。"""
    _push_chat(client, auth, "X", "<script>alert(1)</script>", "2026-06-05T04:00:00Z")
    msg = client.get("/api/chat", headers=auth).json()["chats"][0]["message"]
    assert "<script>" not in msg
    assert "&lt;script&gt;" in msg


# ── RBAC：READ_ROLES（含 observer）可讀 ──────────────────────────────────────


def test_chat_readable_by_observer(client, auth, active_exercise, observer_auth):
    """#213 #1 裁示：observer（READ_ROLES）做 audit 需看當前場通聯 → 200。"""
    _push_chat(client, auth, "C1", "hi", "2026-06-05T04:00:00Z")
    r = client.get("/api/chat", headers=observer_auth)
    assert r.status_code == 200
    assert r.json()["chats"][0]["message"] == "hi"


def test_chat_readable_by_operator(client, auth, active_exercise, operator_auth):
    _push_chat(client, auth, "C1", "hi", "2026-06-05T04:00:00Z")
    assert client.get("/api/chat", headers=operator_auth).status_code == 200


# ── exercise scope 隔離 ──────────────────────────────────────────────────────


def test_chat_scope_isolation_locks_observer_to_active(client, auth, operator_auth):
    """A 場通聯封存後，operator 看不到（鎖 active B）；?exercise_id=A 被 resolve_scope 忽略；
    指揮層（admin）顯式 ?exercise_id=A 才看得到歷史場。"""
    a = _mk_exercise(client, auth, "A")
    client.post(f"/api/exercises/{a['id']}/activate", json={}, headers=auth)
    _push_chat(client, auth, "CA", "A 場機密通聯", "2026-06-05T04:00:00Z")
    client.post(f"/api/exercises/{a['id']}/archive", json={}, headers=auth)
    b = _mk_exercise(client, auth, "B")
    client.post(f"/api/exercises/{b['id']}/activate", json={}, headers=auth)
    _push_chat(client, auth, "CB", "B 場通聯", "2026-06-05T05:00:00Z")

    # operator（scope=B）：只看到 B，A 場通聯不洩漏
    op = client.get("/api/chat", headers=operator_auth).json()
    assert [c["message"] for c in op["chats"]] == ["B 場通聯"]
    # operator 顯式帶 ?exercise_id=A → resolve_scope 忽略，強制回 active B（不洩 A）
    op_a = client.get(f"/api/chat?exercise_id={a['id']}", headers=operator_auth).json()
    assert [c["message"] for c in op_a["chats"]] == ["B 場通聯"]
    # 指揮層（admin）顯式 ?exercise_id=A → 看得到歷史場 A
    adm_a = client.get(f"/api/chat?exercise_id={a['id']}", headers=auth).json()
    assert [c["message"] for c in adm_a["chats"]] == ["A 場機密通聯"]


# ── 時間窗 from/to ───────────────────────────────────────────────────────────


def test_chat_time_window_filter(client, auth, active_exercise):
    _push_chat(client, auth, "C1", "早", "2026-06-05T04:00:00Z")
    _push_chat(client, auth, "C2", "中", "2026-06-05T04:30:00Z")
    _push_chat(client, auth, "C3", "晚", "2026-06-05T05:00:00Z")
    data = client.get("/api/chat?from=2026-06-05T04:15:00Z&to=2026-06-05T04:45:00Z", headers=auth).json()
    assert [c["message"] for c in data["chats"]] == ["中"]


def test_chat_date_only_to_includes_whole_day(client, auth, active_exercise):
    """純日期 to=YYYY-MM-DD 補當天 23:59:59Z，不漏當天通聯（/tracks #123 review 同雷）。

    含**邊界最後一秒** 23:59:59Z —— 界若未補 Z（'…59' < '…59Z'）會漏掉這筆（code-review 抓到）。
    """
    _push_chat(client, auth, "C1", "早", "2026-06-05T08:00:00Z")
    _push_chat(client, auth, "C2", "最後一秒", "2026-06-05T23:59:59Z")
    data = client.get("/api/chat?from=2026-06-05&to=2026-06-05", headers=auth).json()
    assert [c["message"] for c in data["chats"]] == ["早", "最後一秒"]


# ── truncated 探測（保留最新 limit）──────────────────────────────────────────


def test_chat_truncated_keeps_newest(client, auth, active_exercise):
    for i in range(5):
        _push_chat(client, auth, f"C{i}", f"m{i}", f"2026-06-05T04:0{i}:00Z")
    data = client.get("/api/chat?limit=3", headers=auth).json()
    assert data["meta"]["truncated"] is True
    assert data["meta"]["count"] == 3
    # 取最新 3 筆（m2,m3,m4），仍 chronological 升序顯示
    assert [c["message"] for c in data["chats"]] == ["m2", "m3", "m4"]


def test_chat_not_truncated_at_exact_limit(client, auth, active_exercise):
    """剛好 limit 筆不誤報 truncated（limit+1 探測）。"""
    for i in range(3):
        _push_chat(client, auth, f"C{i}", f"m{i}", f"2026-06-05T04:0{i}:00Z")
    data = client.get("/api/chat?limit=3", headers=auth).json()
    assert data["meta"]["truncated"] is False
    assert data["meta"]["count"] == 3
