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
    assert captured_cot == []


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
    assert captured_cot == []           # 未送
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
    r = client.put(
        f"/api/cop/entities/{uid}", json={"lat": 24.5, "lon": 120.9}, headers={**auth, "If-Match": "1"}
    )
    assert r.status_code == 200          # cop 編輯本身不受影響
    assert len(captured_cot) == 1        # 停用後 move 不重推


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
    _create_entity(client, auth)                       # 另一顆，不分享
    client.post(f"/api/tak/share/{shared}", headers=auth)  # 廣播（captured_cot[0]）
    # 模擬斷線期間移動（cop PUT 改座標；此處不論有無重推，重點是 reconcile 拿當前位置）
    client.put(f"/api/cop/entities/{shared}", json={"lat": 23.9, "lon": 120.1}, headers={**auth, "If-Match": "1"})
    captured_cot.clear()

    pushed = asyncio.run(tak_resync.reconcile_shared_outbound())
    assert pushed == 1                                  # 只重推已分享那顆
    assert len(captured_cot) == 1 and shared in captured_cot[0]
    assert "23.9" in captured_cot[0]                    # 重推的是**當前**（移動後）座標
