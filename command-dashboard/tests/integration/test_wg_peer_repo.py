# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""integration/test_wg_peer_repo.py — #434 WG peer 帳本 + IP pool（m034 / wg_peer_repo）。

涵蓋：循序配號（.2 起、跳 .0/.1）、撤銷釋出後空號重用、by-callsign / by-pubkey 撤銷、list。
"""

from __future__ import annotations

import pytest

from core import config
from repositories import wg_peer_repo as repo

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _subnet(monkeypatch):
    monkeypatch.setattr(config, "WG_SUBNET_PREFIX", "10.13.13.")


def test_sequential_allocation_from_2(tmp_db):
    assert repo.allocate_and_record("PUBA", "dev-a", "admin") == "10.13.13.2/32"  # .0/.1 保留
    assert repo.allocate_and_record("PUBB", "dev-b", "admin") == "10.13.13.3/32"


def test_freed_ip_reused(tmp_db):
    repo.allocate_and_record("PUBA", "dev-a", "admin")  # .2
    repo.allocate_and_record("PUBB", "dev-b", "admin")  # .3
    assert repo.revoke_by_callsign("dev-a", "admin") == ["PUBA"]  # 釋出 .2
    assert repo.allocate_and_record("PUBC", "dev-c", "admin") == "10.13.13.2/32"  # 重用最低空號


def test_revoke_by_pubkey(tmp_db):
    repo.allocate_and_record("PUBX", "dev-x", "admin")
    repo.revoke_by_pubkey("PUBX", "admin")
    assert all(p["pubkey"] != "PUBX" for p in repo.list_peers(status="active"))
    allp = repo.list_peers()
    assert any(p["pubkey"] == "PUBX" and p["status"] == "revoked" and p["revoked_by"] == "admin" for p in allp)


def test_list_peers_filter(tmp_db):
    repo.allocate_and_record("P1", "d1", "admin")
    repo.allocate_and_record("P2", "d2", "admin")
    repo.revoke_by_pubkey("P1", "admin")
    assert len(repo.list_peers()) == 2
    assert len(repo.list_peers(status="active")) == 1


def test_revoke_callsign_no_active_returns_empty(tmp_db):
    assert repo.revoke_by_callsign("ghost", "admin") == []


def test_unique_active_address_enforced(tmp_db):
    """配號 race 的最後防線：同一 active address 不可有兩列（m034 partial unique index）。
    allocate_and_record 的重試正是靠捕捉此 IntegrityError。"""
    import sqlite3

    from core.database import get_conn

    repo.allocate_and_record("P1", "d1", "admin")  # .2 active
    with pytest.raises(sqlite3.IntegrityError), get_conn() as conn:
        conn.execute(
            "INSERT INTO wg_peers(pubkey,address,callsign,operator,status,created_at) "
            "VALUES('P2','10.13.13.2/32','d2','admin','active','t')"
        )
