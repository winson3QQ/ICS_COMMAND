# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#287 H2：TTX router 授權回歸測試。

原漏洞：`allowed_roles_for` 無 `/api/ttx/` case → 落預設（GET=READ/POST=WRITE），
observer 可讀任意場 inject 腳本、operator 可 push 注入事件·決策到任意演習場（含已歸檔）。
修法：`/api/ttx/` 整組鎖 COMMAND_ROLES（比照 /api/exercises/，inject=待推送演習腳本，
參演者不應預 see）。

fixtures：operator_auth / observer_auth / commander_auth 見 tests/api/conftest.py；
auth(=admin/sysadmin)、active_exercise 見 tests/conftest.py。
"""


def test_observer_cannot_read_injects(client, observer_auth, active_exercise):
    r = client.get(f"/api/ttx/exercises/{active_exercise['id']}/injects", headers=observer_auth)
    assert r.status_code == 403


def test_operator_cannot_read_injects(client, operator_auth, active_exercise):
    r = client.get(f"/api/ttx/exercises/{active_exercise['id']}/injects", headers=operator_auth)
    assert r.status_code == 403


def test_operator_cannot_bulk_create_injects(client, operator_auth, active_exercise):
    r = client.post(
        f"/api/ttx/exercises/{active_exercise['id']}/injects",
        json={"injects": []},
        headers=operator_auth,
    )
    assert r.status_code == 403


def test_operator_cannot_push_inject(client, operator_auth, active_exercise):
    r = client.post(
        f"/api/ttx/exercises/{active_exercise['id']}/injects/whatever/push",
        headers=operator_auth,
    )
    assert r.status_code == 403


def test_operator_cannot_load_scenario(client, operator_auth, active_exercise):
    r = client.post(
        f"/api/ttx/scenarios/anything/load?exercise_id={active_exercise['id']}",
        headers=operator_auth,
    )
    assert r.status_code == 403


def test_commander_can_read_injects(client, commander_auth, active_exercise):
    r = client.get(f"/api/ttx/exercises/{active_exercise['id']}/injects", headers=commander_auth)
    assert r.status_code == 200


def test_sysadmin_can_bulk_create_injects(client, auth, active_exercise):
    r = client.post(
        f"/api/ttx/exercises/{active_exercise['id']}/injects",
        json={"injects": []},
        headers=auth,
    )
    assert r.status_code == 200
