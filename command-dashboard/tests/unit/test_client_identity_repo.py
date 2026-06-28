# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""tests/unit/test_client_identity_repo.py — #344 uid→CN（cert username）對照 repo。"""

import pytest

from repositories import client_identity_repo

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _db(tmp_db):
    yield


def test_upsert_and_get():
    n = client_identity_repo.upsert_many({"uid1": "alpha", "uid2": "bravo"})
    assert n == 2
    assert client_identity_repo.get_username("uid1") == "alpha"
    assert client_identity_repo.get_username("uid2") == "bravo"
    assert client_identity_repo.get_username("nope") is None


def test_upsert_overwrites_on_conflict():
    client_identity_repo.upsert_many({"uid1": "old"})
    client_identity_repo.upsert_many({"uid1": "new"})  # 同 uid 後寫覆蓋
    assert client_identity_repo.get_username("uid1") == "new"


def test_upsert_skips_empty():
    assert client_identity_repo.upsert_many({"": "x", "uid": "", "ok": "cn"}) == 1
    assert client_identity_repo.get_username("ok") == "cn"
    assert client_identity_repo.get_username("") is None


def test_uids_for_username_multi():
    # 同 CN 多 uid（裝置換過 uid）→ 全列出，供分類傳播到各 uid 的 entity
    client_identity_repo.upsert_many({"old": "dev", "new": "dev", "other": "x"})
    assert set(client_identity_repo.uids_for_username("dev")) == {"old", "new"}
    assert client_identity_repo.uids_for_username("x") == ["other"]
    assert client_identity_repo.uids_for_username("none") == []
