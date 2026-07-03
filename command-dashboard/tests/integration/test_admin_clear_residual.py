# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tests/integration/test_admin_clear_residual.py — #473-B2 開場選擇性清圖

鎖住：`/api/admin/clear-residual` 清「殘留」（外部週期性 SA：source 非 manual/command
且 archived=0）、保「永久」（指揮部自建 manual/command ＋ archived=1 釘住標記）。方案 A
（使用者 2026-07-03 拍板）：外部鏡像全清，live 裝置週期重報自然重建。

soft-delete 語意（deleted=1 + stale=now）；歷史 row / tracks 不 hard delete。SYSADMIN_ONLY
（路徑落 /api/admin/ 自動歸類，見 test_rbac_route_matrix golden）＋ body confirm（OP-2）。
"""

from __future__ import annotations


def _login(client, username: str = "admin", pin: str = "1234") -> dict[str, str]:
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


def _seed(uid: str, source: str, *, archived: bool = False, faction: str | None = None) -> None:
    """直插一顆 entity（繞過 /api/cop/entities 只能建 manual 的限制，才能造 tak 殘留樣本）。
    stale 設遠未來 → 清圖前本應可見（排除「本來就 stale 濾掉」的干擾）。"""
    from repositories.cop_entity_repo import insert_cop_entity
    from schemas.cop import CoPEntity

    insert_cop_entity(
        CoPEntity(
            uid=uid,
            type="a-u-G",
            time="2026-01-01T00:00:00Z",
            start="2026-01-01T00:00:00Z",
            stale="2099-01-01T00:00:00Z",
            how="m-g",
            lat=24.8,
            lon=121.0,
            source=source,
            archived=archived,
            faction=faction,
        )
    )


def _uids(client, headers) -> set[str]:
    return {e["uid"] for e in client.get("/api/cop/entities", headers=headers).json()["entities"]}


def test_clear_residual_removes_external_keeps_permanent(client):
    """核心契約：清外部週期性殘留（tak archived=0），保指揮部自建（manual/command）＋
    archived=1 釘住標記。"""
    h = _login(client)
    _seed("manual:route-a", "manual")  # 永久：指揮部自建 route
    _seed("command:order-a", "command")  # 永久：下行指令
    _seed("tak:pin-a", "tak", archived=True)  # 永久：CoT <archive/> 釘住放置標記
    _seed("tak:sa-a", "tak")  # 殘留：週期性裝置 SA
    _seed("pi:sa-b", "pi-node")  # 殘留：自有感測週期回報
    assert _uids(client, h) == {"manual:route-a", "command:order-a", "tak:pin-a", "tak:sa-a", "pi:sa-b"}

    r = client.post("/api/admin/clear-residual", headers=h, json={"confirm": "RESET"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cleared"] == 2  # tak:sa-a + pi:sa-b
    assert body["kept"] == 3  # manual + command + archived tak

    # 清完：永久物件全留、殘留全消
    assert _uids(client, h) == {"manual:route-a", "command:order-a", "tak:pin-a"}


def test_clear_residual_is_faction_neutral(client):
    """清的是「來源類別 × archived」，不碰紅藍——紅/藍/未分隊的 tak 殘留一視同仁清掉，
    不成為偷清對方的漏洞。"""
    h = _login(client)
    _seed("tak:red", "tak", faction="red")
    _seed("tak:blue", "tak", faction="blue")
    _seed("tak:null", "tak", faction=None)

    r = client.post("/api/admin/clear-residual", headers=h, json={"confirm": "RESET"})
    assert r.status_code == 200, r.text
    assert r.json()["cleared"] == 3  # 三種 faction 的 tak 殘留全清


def test_clear_residual_requires_confirm(client):
    """OP-2：不帶 {"confirm":"RESET"} → 422（後端硬擋誤觸，不依賴前端 dialog）。"""
    h = _login(client)
    _seed("tak:sa-x", "tak")
    r = client.post("/api/admin/clear-residual", headers=h, json={})
    assert r.status_code == 422, r.text
    # 未確認 → 未清
    assert "tak:sa-x" in _uids(client, h)


def test_clear_residual_broadcasts_resync(client):
    """批量 UPDATE 不發 per-entity WS delete → 靠 {op:'resync'} 廣播觸發各 client 全量對帳。"""
    h = _login(client)
    tok = h["X-Session-Token"]
    _seed("tak:sa-y", "tak")
    url = "/api/cop/ws/updates"
    with client.websocket_connect(url, subprotocols=["ics-cop-v1", f"ics.session.{tok}"]) as ws:
        assert ws.receive_json()["op"] == "hello"
        assert client.post("/api/admin/clear-residual", headers=h, json={"confirm": "RESET"}).status_code == 200
        assert ws.receive_json()["op"] == "resync"
