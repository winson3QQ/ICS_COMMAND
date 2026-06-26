# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#288 H3：events / decisions 寫入端點 scope 回歸測試。

原漏洞：讀取（get_ev/get_chain/get_dec）有 resolve_scope，寫入（patch/status/notes/
deadline/decide）只認 id 無 scope → operator 可改/裁示**別演習場（含已歸檔）**的事件與決策
（IDOR + 跨場越權）。
修法：寫入端點與讀取對稱套 resolve_scope；repo 以 scope_clause 把目標 row 限在 scope 內，
跨場 row 視同不存在。

驗證重點：① 當前場日常寫入不受影響（operator 對 active 場 200）；② 跨場寫入被擋；
③ commander 可顯式帶 exercise_id 寫歷史場（與讀取對稱）。

fixtures：operator_auth / commander_auth 見 tests/api/conftest.py；client/auth 見根 conftest。
"""

import pytest


def _mk_exercise(client, auth, name):
    r = client.post("/api/exercises", json={"name": name, "type": "ttx"}, headers=auth)
    assert r.status_code == 200, r.text
    ex = r.json()
    r2 = client.post(f"/api/exercises/{ex['id']}/activate", json={}, headers=auth)
    assert r2.status_code == 200, r2.text
    return ex["id"]


def _mk_event(client, auth, desc):
    r = client.post(
        "/api/events",
        json={"reported_by_unit": "command", "event_type": "test", "description": desc, "operator_name": "sys"},
        headers=auth,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _mk_decision(client, auth, title):
    r = client.post(
        "/api/decisions",
        json={
            "decision_type": "initial",
            "severity": "warning",
            "decision_title": title,
            "impact_description": "x",
            "suggested_action_a": "a",
            "created_by": "sys",
        },
        headers=auth,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.fixture
def two_exercises(client, auth):
    """exA（建 event/decision 後歸檔）+ exB（最終 active）。回傳 (exA, exB, ev_a, ev_b, dec_a)。"""
    ex_a = _mk_exercise(client, auth, "ex-A")
    ev_a = _mk_event(client, auth, "event in A")
    dec_a = _mk_decision(client, auth, "decision in A")
    # mutex：同時只能一個 active，先歸檔 A 才能啟 B
    assert client.post(f"/api/exercises/{ex_a}/archive", json={}, headers=auth).status_code == 200
    ex_b = _mk_exercise(client, auth, "ex-B")  # 此後 active=B
    ev_b = _mk_event(client, auth, "event in B")
    return ex_a, ex_b, ev_a, ev_b, dec_a


# ── 跨場寫入被擋（active=B，operator scope=B，目標在 A）──────────────────────


def test_operator_cannot_patch_cross_exercise_event(client, operator_auth, two_exercises):
    _, _, ev_a, _, _ = two_exercises
    r = client.patch(f"/api/events/{ev_a}", json={"assigned_unit": "hacked"}, headers=operator_auth)
    assert r.status_code == 404


def test_operator_cannot_change_status_cross_exercise(client, operator_auth, two_exercises):
    _, _, ev_a, _, _ = two_exercises
    r = client.patch(f"/api/events/{ev_a}/status?status=in_progress&operator=op", headers=operator_auth)
    assert r.status_code in (400, 404)  # 跨場視同不存在 → 被擋（不得 200）


def test_operator_cannot_note_cross_exercise(client, operator_auth, two_exercises):
    _, _, ev_a, _, _ = two_exercises
    r = client.post(f"/api/events/{ev_a}/notes", json={"text": "x", "operator": "op"}, headers=operator_auth)
    assert r.status_code == 404


def test_operator_cannot_decide_cross_exercise(client, operator_auth, two_exercises):
    _, _, _, _, dec_a = two_exercises
    r = client.post(
        f"/api/decisions/{dec_a}/decide", json={"action": "approved", "decided_by": "op"}, headers=operator_auth
    )
    assert r.status_code in (400, 404)


# ── 當前場日常寫入不受影響（operator 對 active=B 的 event）─────────────────────


def test_operator_can_note_current_exercise(client, operator_auth, two_exercises):
    _, _, _, ev_b, _ = two_exercises
    r = client.post(f"/api/events/{ev_b}/notes", json={"text": "ok", "operator": "op"}, headers=operator_auth)
    assert r.status_code == 200, r.text


# ── commander 可顯式帶 exercise_id 寫歷史場（與讀取對稱）──────────────────────


def test_commander_can_note_historical_with_scope(client, commander_auth, two_exercises):
    ex_a, _, ev_a, _, _ = two_exercises
    r = client.post(
        f"/api/events/{ev_a}/notes?exercise_id={ex_a}",
        json={"text": "cmd review", "operator": "cmd"},
        headers=commander_auth,
    )
    assert r.status_code == 200, r.text
