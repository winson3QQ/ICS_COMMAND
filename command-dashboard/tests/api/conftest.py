"""tests/api/conftest.py — api 層共用 fixtures。

角色 auth fixtures（operator/observer/commander）原本在 test_exercise_scoping.py 與
test_tracks_query.py 各複製一份；抽到此處共用，避免 role/pin/create_account 簽章演進時
要同步多處（code-review #123 衍生）。`client`/`auth`/`active_exercise` 在 tests/conftest.py。
"""

import pytest

from repositories.account_repo import create_account


def _login(client, username, pin):
    r = client.post("/api/auth/login", json={"username": username, "pin": pin})
    assert r.status_code == 200, r.text
    return {"X-Session-Token": r.json()["session_id"]}


@pytest.fixture
def operator_auth(client):
    create_account("op1", "5678", "操作員", "前進組", "operator")
    return _login(client, "op1", "5678")


@pytest.fixture
def observer_auth(client):
    create_account("ob1", "5678", "觀察員", "", "observer")
    return _login(client, "ob1", "5678")


@pytest.fixture
def commander_auth(client):
    create_account("cmd1", "5678", "指揮官", "", "commander")
    return _login(client, "cmd1", "5678")
