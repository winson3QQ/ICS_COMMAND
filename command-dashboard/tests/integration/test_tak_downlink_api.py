"""P2-13(A)（#176）— routers/tak.py `POST /api/tak/downlink` 下行指令端點。

驗 RBAC（COMMAND_ROLES）+ **強制 audit（audit-first）** + send_cot 配線。
send_cot 走真 :8089 連線 → 一律 monkeypatch 攔截（不碰網路），驗「建了什麼 CoT、有沒有送」。
"""

from __future__ import annotations

import pytest

from auth.role_enum import ROLE_OBSERVER_ZH, ROLE_OPERATOR_ZH
from core.database import get_conn
from repositories.account_repo import create_account
from services import tak_downlink


def _cmd(**over):
    body = {"type": "a-f-G", "lat": 25.03, "lon": 121.56, "callsign": "CMD-1", "remarks": "推進"}
    body.update(over)
    return body


def _login(client, username, pin="1234"):
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


@pytest.fixture
def captured_cot(monkeypatch):
    """攔 send_cot → 記下 CoT 字串，不碰網路。"""
    sent: list[str] = []

    async def _fake_send(cot_xml: str) -> None:
        sent.append(cot_xml)

    monkeypatch.setattr(tak_downlink, "send_cot", _fake_send)
    return sent


@pytest.fixture
def tak_enabled(monkeypatch):
    """#222：downlink/share 端點 + _resync_tak_if_shared 皆受 TAK 開關（effective_enabled）閘控。
    測「啟用」行為時打開——persisted 未設（測試 DB 無該 config row）→ 回退 config.TAK_ENABLED。"""
    monkeypatch.setattr("core.config.TAK_ENABLED", True)


@pytest.fixture
def tak_disabled(monkeypatch):
    """#222：明確強制停用（不靠 env 預設），驗停用時出向被擋。"""
    monkeypatch.setattr("core.config.TAK_ENABLED", False)


def _audit_rows(uid):
    with get_conn() as conn:
        return conn.execute(
            "SELECT operator, detail FROM audit_log WHERE action_type='TAK_DOWNLINK' AND target_id=?",
            (uid,),
        ).fetchall()


def test_downlink_sends_and_audits(client, auth, captured_cot, tak_enabled):
    r = client.post("/api/tak/downlink", json=_cmd(uid="ICS-CMD-T1"), headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "sent" and data["uid"] == "ICS-CMD-T1"
    # 真的送出一筆 CoT，內容對
    assert len(captured_cot) == 1
    assert "ICS-CMD-T1" in captured_cot[0] and "type='a-f-G'" in captured_cot[0].replace('"', "'")
    # 強制 audit 落地
    rows = _audit_rows("ICS-CMD-T1")
    assert len(rows) == 1 and rows[0][0] == "admin"


def test_downlink_autogenerates_uid(client, auth, captured_cot, tak_enabled):
    r = client.post("/api/tak/downlink", json=_cmd(), headers=auth)
    assert r.status_code == 200
    assert r.json()["uid"].startswith("ICS-CMD-")
    assert len(captured_cot) == 1


def test_downlink_requires_auth(client, captured_cot):
    r = client.post("/api/tak/downlink", json=_cmd())
    assert r.status_code == 401
    assert captured_cot == []  # 未送


def test_observer_cannot_downlink(client, captured_cot):
    create_account("obs_dl", "1234", ROLE_OBSERVER_ZH, "Obs DL", "observer")
    r = client.post("/api/tak/downlink", json=_cmd(uid="ICS-CMD-OBS"), headers=_login(client, "obs_dl"))
    assert r.status_code == 403
    assert captured_cot == []
    assert _audit_rows("ICS-CMD-OBS") == []  # 被擋 → 無稽核、無送出


def test_operator_cannot_downlink(client, captured_cot):
    # 下達指令是指揮層動作（COMMAND_ROLES），operator（WRITE_ROLES）不可。
    create_account("op_dl", "1234", ROLE_OPERATOR_ZH, "Op DL", "operator")
    r = client.post("/api/tak/downlink", json=_cmd(uid="ICS-CMD-OP"), headers=_login(client, "op_dl"))
    assert r.status_code == 403
    assert captured_cot == []


def test_downlink_invalid_type_422(client, auth, captured_cot):
    r = client.post("/api/tak/downlink", json=_cmd(type="<script>"), headers=auth)
    assert r.status_code == 422
    assert captured_cot == []


# ── #216：POST /api/tak/chat 出向 GeoChat（指揮部對現場發文字通聯）──────────────────
def _chat_audit_rows(uid):
    with get_conn() as conn:
        return conn.execute(
            "SELECT operator, detail FROM audit_log WHERE action_type='TAK_CHAT_SEND' AND target_id=?",
            (uid,),
        ).fetchall()


def test_chat_sends_and_audits(client, auth, captured_cot, tak_enabled):
    r = client.post("/api/tak/chat", json={"message": "全體注意"}, headers=auth)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "sent" and data["chatroom"] == "All Chat Rooms"
    # 真的送出一筆 b-t-f GeoChat，含訊息
    assert len(captured_cot) == 1
    assert "type='b-t-f'" in captured_cot[0].replace('"', "'") and "全體注意" in captured_cot[0]
    # audit 落地（含 admin 身分），且**不**記訊息內文（PII 走 chats 保留政策）
    rows = _chat_audit_rows(data["uid"])
    assert len(rows) == 1 and rows[0][0] == "admin"
    assert "全體注意" not in (rows[0][1] or "")


def test_chat_dm_routes_to_recipient(client, auth, captured_cot, tak_enabled):
    r = client.post(
        "/api/tak/chat",
        json={"message": "單獨呼叫", "recipient_uid": "ANDROID-9", "recipient_callsign": "BRAVO"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    assert "ANDROID-9" in captured_cot[0] and "BRAVO" in captured_cot[0]


def test_chat_requires_auth(client, captured_cot):
    r = client.post("/api/tak/chat", json={"message": "x"})
    assert r.status_code == 401
    assert captured_cot == []


def test_observer_cannot_chat(client, captured_cot, tak_enabled):
    create_account("obs_chat", "1234", ROLE_OBSERVER_ZH, "Obs Chat", "observer")
    r = client.post("/api/tak/chat", json={"message": "x"}, headers=_login(client, "obs_chat"))
    assert r.status_code == 403
    assert captured_cot == []


def test_operator_cannot_chat(client, captured_cot, tak_enabled):
    # 對外發話＝指揮層動作（COMMAND_ROLES）；operator（WRITE_ROLES）不可（與 downlink 同層）。
    create_account("op_chat", "1234", ROLE_OPERATOR_ZH, "Op Chat", "operator")
    r = client.post("/api/tak/chat", json={"message": "x"}, headers=_login(client, "op_chat"))
    assert r.status_code == 403
    assert captured_cot == []


def test_chat_empty_message_422(client, auth, captured_cot, tak_enabled):
    r = client.post("/api/tak/chat", json={"message": "   "}, headers=auth)
    assert r.status_code == 422
    assert captured_cot == []


def test_chat_unsafe_content_422(client, auth, captured_cot, tak_enabled):
    # 內容白名單（validate_no_unsafe_strings）擋 HTML metachar → 422、未送。
    r = client.post("/api/tak/chat", json={"message": "<script>alert(1)</script>"}, headers=auth)
    assert r.status_code == 422
    assert captured_cot == []


def test_chat_blank_chatroom_falls_back_to_all(client, auth, captured_cot, tak_enabled):
    # #216 review：chatroom 淨化後全空（全是被剝字元）→ 退回 'All Chat Rooms'（非 None）；
    # 送出的 CoT 與 audit 房名一致、不矛盾。
    r = client.post("/api/tak/chat", json={"message": "hi", "chatroom": "&&&"}, headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["chatroom"] == "All Chat Rooms"
    rows = _chat_audit_rows(r.json()["uid"])
    assert len(rows) == 1 and '"All Chat Rooms"' in (rows[0][1] or "")


def test_chat_disabled_409(client, auth, captured_cot, tak_disabled):
    # TAK 開關停用 → 409，未送、未稽核（gate 早於 audit）。
    r = client.post("/api/tak/chat", json={"message": "x"}, headers=auth)
    assert r.status_code == 409
    assert captured_cot == []


def test_chat_send_failure_503_but_audited(client, auth, monkeypatch):
    # audit-first：送出失敗 → 503，但稽核已記發話意圖。
    async def _boom(cot_xml: str) -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr("core.config.TAK_ENABLED", True)
    monkeypatch.setattr(tak_downlink, "send_cot", _boom)
    r = client.post("/api/tak/chat", json={"message": "送不出去"}, headers=auth)
    assert r.status_code == 503
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action_type='TAK_CHAT_SEND'").fetchone()[0]
    assert n == 1


# ── P2-30 part 2（#180）：POST /api/tak/share/{uid} 分享既有 COP 標記到 TAK ──
def _create_entity(client, auth, **over):
    body = {"type": "a-h-G", "lat": 25.03, "lon": 121.56, "callsign": "敵情A"}
    body.update(over)
    r = client.post("/api/cop/entities", json=body, headers=auth)
    assert r.status_code == 201, r.text
    return r.json()["uid"]


def test_share_entity_sends_and_audits(client, auth, captured_cot, tak_enabled):
    uid = _create_entity(client, auth)
    r = client.post(f"/api/tak/share/{uid}", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "shared"
    assert len(captured_cot) == 1 and uid in captured_cot[0]  # entity_to_cot → send_cot
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT operator FROM audit_log WHERE action_type='COP_SHARE_TAK' AND target_id=?", (uid,)
        ).fetchall()
    assert len(rows) == 1 and rows[0][0] == "admin"


def test_share_geometry_entity(client, auth, captured_cot, tak_enabled):
    uid = _create_entity(
        client,
        auth,
        type="u-d-f",
        attributes={"kind": "polygon", "vertices": [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]},
    )
    r = client.post(f"/api/tak/share/{uid}", headers=auth)
    assert r.status_code == 200
    # 幾何分流 → ATAK 原生 <link> 序列 + 填色（#211 格式修正）
    assert "<link point=" in captured_cot[0]
    assert "<fillColor" in captured_cot[0]


def test_share_unknown_uid_404(client, auth, captured_cot, tak_enabled):
    r = client.post("/api/tak/share/NOPE-404", headers=auth)
    assert r.status_code == 404
    assert captured_cot == []


def test_share_malformed_geometry_422_not_500(client, auth, captured_cot, tak_enabled):
    """review #180：畸形幾何 entity（polygon 但 <3 點）分享 → 乾淨 422，非未審計的 500。"""
    uid = _create_entity(
        client, auth, type="u-d-f", attributes={"kind": "polygon", "vertices": [[25.0, 121.0]]}
    )  # 僅 1 點
    r = client.post(f"/api/tak/share/{uid}", headers=auth)
    assert r.status_code == 422
    assert captured_cot == []  # 未送
    with get_conn() as conn:  # 序列化失敗 → 不應留 share 稽核
        n = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action_type='COP_SHARE_TAK' AND target_id=?", (uid,)
        ).fetchone()[0]
    assert n == 0


def test_observer_cannot_share(client, auth, captured_cot):
    uid = _create_entity(client, auth)
    create_account("obs_share", "1234", ROLE_OBSERVER_ZH, "Obs Share", "observer")
    r = client.post(f"/api/tak/share/{uid}", headers=_login(client, "obs_share"))
    assert r.status_code == 403


def test_share_red_tak_entity_blocked_for_blue_when_isolation_on(client, auth, captured_cot, tak_enabled, monkeypatch):
    """#343 security-review：開隔離後，藍方（operator）不可分享其看不到的紅軍 tak entity → 404、未送。
    （否則 share 成存在性 oracle + 對紅軍越權動作）。sysadmin（白隊，vf=None）仍可分享。"""
    from core import config as _cfg
    from repositories import cop_entity_repo
    from schemas.cop import CoPEntity

    monkeypatch.setattr(_cfg, "FACTION_ISOLATION_ENABLED", True)
    cop_entity_repo.insert_cop_entity(
        CoPEntity(
            uid="RED-SHARE-1",
            type="a-h-G",
            time="2026-06-22T00:00:00Z",
            start="2026-06-22T00:00:00Z",
            stale="2099-01-01T00:00:00Z",
            how="h-e",
            lat=25.0,
            lon=121.0,
            source="tak",
            faction="red",
        )
    )
    create_account("op_share", "1234", ROLE_OPERATOR_ZH, "Op Share", "operator")
    r = client.post("/api/tak/share/RED-SHARE-1", headers=_login(client, "op_share"))
    assert r.status_code == 404  # 藍方看不到紅軍 → 不可分享（與 cop.get_entity 一致）
    assert captured_cot == []
    r2 = client.post("/api/tak/share/RED-SHARE-1", headers=auth)  # sysadmin 全見
    assert r2.status_code == 200 and len(captured_cot) == 1


def test_operator_can_share(client, auth, captured_cot, tak_enabled):
    """P2-30 part 3（#180）：分享放寬 WRITE_ROLES —— operator（一線回報敵情）可直推 TAK。
    與 #146 收緊的 POST /api/tak/events 區隔：share 只推既有 cop_entity、仍 audit-first。"""
    uid = _create_entity(client, auth)
    create_account("op_share", "1234", ROLE_OPERATOR_ZH, "Op Share", "operator")
    r = client.post(f"/api/tak/share/{uid}", headers=_login(client, "op_share"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "shared"
    assert len(captured_cot) == 1 and uid in captured_cot[0]
    with get_conn() as conn:  # 放寬後仍強制稽核（含 operator 身分）
        rows = conn.execute(
            "SELECT operator FROM audit_log WHERE action_type='COP_SHARE_TAK' AND target_id=?", (uid,)
        ).fetchall()
    assert len(rows) == 1 and rows[0][0] == "op_share"


def test_operator_cannot_inject_via_tak_events(client, auth):
    """窄洞回歸守門：share 放寬 WRITE_ROLES 不得波及 #146 收緊的 POST /api/tak/events
    （operator 經 events 注入仍須 403，否則繞過 cop 來源守門）。"""
    create_account("op_inject", "1234", ROLE_OPERATOR_ZH, "Op Inject", "operator")
    r = client.post("/api/tak/events", json=_cmd(), headers=_login(client, "op_inject"))
    assert r.status_code == 403


# ── P2-30 part 3（#180）：廣播後即時同步（move 重推；delete 不推 = P2-14 deferred）──
def test_share_marks_shared_tak(client, auth, captured_cot, tak_enabled):
    """廣播成功 → entity 標記 attributes.shared_tak（不 bump version_clock，前端樂觀鎖不失效）。"""
    uid = _create_entity(client, auth)
    assert client.post(f"/api/tak/share/{uid}", headers=auth).status_code == 200
    r = client.get(f"/api/cop/entities/{uid}", headers=auth)
    assert r.json()["attributes"]["shared_tak"] is True
    assert r.json()["version_clock"] == 1  # mark_shared_tak 非-CAS、不 bump


def test_move_shared_entity_resyncs_to_tak(client, auth, captured_cot, tak_enabled):
    """已廣播 entity 移動（PUT lat/lon）→ 即時重推 CoT（新座標），不需再手動廣播（issue 3）。"""
    uid = _create_entity(client, auth)
    client.post(f"/api/tak/share/{uid}", headers=auth)  # captured_cot[0]
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={"lat": 24.5, "lon": 120.9},
        headers={**auth, "If-Match": "1"},
    )
    assert r.status_code == 200
    assert len(captured_cot) == 2  # 廣播 + move 重推
    assert "24.5" in captured_cot[1] and uid in captured_cot[1]


def test_move_shared_entity_via_attributes_preserves_shared_tak(client, auth, captured_cot, tak_enabled):
    """#257：已廣播 entity 經**整包 attributes** PUT（圖形移動改 vertices / label drag 改 label_anchor）
    → server 須保留 shared_tak（前端無此值、不該被覆寫洗掉）→ 重推 CoT 照樣 fire。"""
    uid = _create_entity(
        client,
        auth,
        type="u-d-f",
        attributes={"kind": "polygon", "vertices": [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]},
    )
    client.post(f"/api/tak/share/{uid}", headers=auth)  # captured_cot[0]
    # 模擬前端移動：送整包 attributes（新 vertices）但**不帶 shared_tak**（前端無此值）
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={
            "lat": 24.5,
            "lon": 120.9,
            "attributes": {"kind": "polygon", "vertices": [[24.5, 120.9], [24.6, 120.9], [24.6, 121.0]]},
        },
        headers={**auth, "If-Match": "1"},
    )
    assert r.status_code == 200
    assert len(captured_cot) == 2  # 廣播 + 移動重推（shared_tak 被保留 → _resync 仍 fire）
    g = client.get(f"/api/cop/entities/{uid}", headers=auth)
    assert g.json()["attributes"]["shared_tak"] is True  # 沒被整包覆寫洗掉


def test_put_cannot_self_set_shared_tak(client, auth, captured_cot, tak_enabled):
    """#257 hardening：未廣播 entity，client 不得經 PUT attributes 自設 shared_tak（繞過 share 端點的
    COP_SHARE_TAK audit + send_cot）→ server 一律 pop client 帶的值。"""
    uid = _create_entity(
        client,
        auth,
        type="u-d-f",
        attributes={"kind": "polygon", "vertices": [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]]},
    )
    r = client.put(
        f"/api/cop/entities/{uid}",
        json={
            "attributes": {
                "kind": "polygon",
                "vertices": [[25.0, 121.0], [25.1, 121.0], [25.1, 121.1]],
                "shared_tak": True,
            }
        },
        headers={**auth, "If-Match": "1"},
    )
    assert r.status_code == 200
    g = client.get(f"/api/cop/entities/{uid}", headers=auth)
    assert "shared_tak" not in g.json()["attributes"]  # 被 pop —— client 不能自設
    assert captured_cot == []  # 沒繞過 share 端點推 TAK


def test_delete_shared_entity_does_not_push_tak(client, auth, captured_cot, tak_enabled):
    """已廣播 entity 刪除 → **不推 TAK**（issue 4 = P2-14 deferred）。實證：streaming（t-x-d-d/stale）
    對 server 持久層無效、Marti 無單顆 CoT DELETE → 可靠刪除只在 Mission/DataSync。故刪除僅 ICS 端
    生效，不送無效 CoT 污染 server。captured 只有廣播那一發。"""
    uid = _create_entity(client, auth)
    client.post(f"/api/tak/share/{uid}", headers=auth)  # captured_cot[0]（廣播）
    r = client.delete(f"/api/cop/entities/{uid}", headers={**auth, "If-Match": "1"})
    assert r.status_code == 200
    assert len(captured_cot) == 1  # 刪除未再送任何 CoT


def test_unshared_move_delete_no_tak_push(client, auth, captured_cot, tak_enabled):
    """未廣播 entity（無 shared_tak）move/delete → 不推 TAK（顯式廣播閘門：放置≠上 TAK）。"""
    uid = _create_entity(client, auth)
    client.put(f"/api/cop/entities/{uid}", json={"lat": 24.5, "lon": 120.9}, headers={**auth, "If-Match": "1"})
    client.delete(f"/api/cop/entities/{uid}", headers={**auth, "If-Match": "2"})
    assert captured_cot == []


def test_send_failure_503_but_still_audited(client, auth, monkeypatch):
    """audit-first 紀律：送出失敗 → 503，但稽核已記下達意圖（無未稽核之下達；失敗有跡可循）。"""

    async def _boom(cot_xml: str) -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr("core.config.TAK_ENABLED", True)  # #222：過開關閘才到 send_cot
    monkeypatch.setattr(tak_downlink, "send_cot", _boom)
    r = client.post("/api/tak/downlink", json=_cmd(uid="ICS-CMD-FAIL"), headers=auth)
    assert r.status_code == 503
    # 送出失敗，但稽核已落（audit-first）
    assert len(_audit_rows("ICS-CMD-FAIL")) == 1


# ── #222：出向受 TAK 開關閘控（停用時三條出向路徑皆不送） ──
def test_downlink_disabled_409(client, auth, captured_cot, tak_disabled):
    """TAK 開關停用 → 下達指令 409，未送 CoT、未稽核（gate 在 audit 之前）。"""
    r = client.post("/api/tak/downlink", json=_cmd(uid="ICS-CMD-OFF"), headers=auth)
    assert r.status_code == 409
    assert captured_cot == []  # 未送
    assert _audit_rows("ICS-CMD-OFF") == []  # 未稽核（gate 早於 audit）


def test_share_disabled_409(client, auth, captured_cot, tak_disabled):
    """TAK 開關停用 → 廣播既有標記 409，未送、未稽核（entity 存在也擋）。"""
    # 建 entity 不受 TAK 閘影響（走 /api/cop）；停用下 share 應 409。
    uid = _create_entity(client, auth)
    r = client.post(f"/api/tak/share/{uid}", headers=auth)
    assert r.status_code == 409
    assert captured_cot == []
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action_type='COP_SHARE_TAK' AND target_id=?", (uid,)
        ).fetchone()[0]
    assert n == 0


def test_move_shared_entity_disabled_no_resync(client, auth, captured_cot, monkeypatch):
    """已廣播 entity 在 TAK 停用後移動 → 不重推（cop.py _resync_tak_if_shared gate，P2-24 #164 已備，
    #222 補回歸守門：確認三條出向路全部遵守開關）。"""
    monkeypatch.setattr("core.config.TAK_ENABLED", True)
    uid = _create_entity(client, auth)
    client.post(f"/api/tak/share/{uid}", headers=auth)  # captured_cot[0]（廣播）
    monkeypatch.setattr("core.config.TAK_ENABLED", False)  # 關閉 TAK
    r = client.put(f"/api/cop/entities/{uid}", json={"lat": 24.5, "lon": 120.9}, headers={**auth, "If-Match": "1"})
    assert r.status_code == 200  # cop 編輯本身不受影響
    assert len(captured_cot) == 1  # 停用後 move 不重推


def test_reconcile_outbound_pushes_only_shared(client, auth, captured_cot, tak_enabled, monkeypatch):
    """#222 重連 outbound 對帳（真 DB）：斷線期間移動已分享標記 → 重連對帳把**當前位置**重推；
    未分享的 entity 不推。模擬「分享 → 斷線移動 → 重連」：share 後直接改座標再跑 reconcile。"""
    import asyncio

    from services import tak_resync

    # reconcile 受出向配置守 → 餵齊（send_cot 仍由 captured_cot 攔，不碰網路）。
    monkeypatch.setattr("core.config.TAK_COT_URL", "tls://tak:8089")
    monkeypatch.setattr("core.config.TAK_CLIENT_CERT", "/c.pem")
    monkeypatch.setattr("core.config.TAK_CLIENT_KEY", "/k.pem")

    shared = _create_entity(client, auth)
    _create_entity(client, auth)  # 另一顆，不分享
    client.post(f"/api/tak/share/{shared}", headers=auth)  # 廣播（captured_cot[0]）
    # 模擬斷線期間移動（cop PUT 改座標；此處不論有無重推，重點是 reconcile 拿當前位置）
    client.put(f"/api/cop/entities/{shared}", json={"lat": 23.9, "lon": 120.1}, headers={**auth, "If-Match": "1"})
    captured_cot.clear()

    pushed = asyncio.run(tak_resync.reconcile_shared_outbound())
    assert pushed == 1  # 只重推已分享那顆
    assert len(captured_cot) == 1 and shared in captured_cot[0]
    assert "23.9" in captured_cot[0]  # 重推的是**當前**（移動後）座標
