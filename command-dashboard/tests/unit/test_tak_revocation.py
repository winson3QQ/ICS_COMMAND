# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""#318 services/tak_revocation — ICS 直寫 TAK postgres 真撤銷的單元測試。

不碰真 postgres / pg8000：monkeypatch `_connect` 回 fake 連線（記 SQL + 控 exists/raise），驗：
未配置跳過、無 fingerprint 跳過、無既有 row→INSERT、有→UPDATE、寫錯/連錯 best-effort 不 raise + 關連線。
"""

import pytest

from services import tak_revocation


class _FakeConn:
    def __init__(self, exists=False, raise_on=None):
        self.calls: list[tuple[str, dict]] = []
        self._exists = exists
        self._raise_on = raise_on
        self.closed = False

    def run(self, sql, **params):
        self.calls.append((sql, params))
        if self._raise_on and self._raise_on in sql:
            raise RuntimeError("boom")
        if sql.strip().upper().startswith("SELECT"):
            return [[1]] if self._exists else []
        return None

    def close(self):
        self.closed = True


@pytest.fixture
def _cfg(monkeypatch):
    monkeypatch.setattr(tak_revocation.config, "TAK_DB_HOST", "tak-database")
    monkeypatch.setattr(tak_revocation.config, "TAK_DB_PASSWORD", "pw")


def test_not_configured(monkeypatch):
    monkeypatch.setattr(tak_revocation.config, "TAK_DB_HOST", "")
    assert tak_revocation.revoke_in_tak("AA:BB") == {"ok": False, "reason": "tak-db-not-configured"}


def test_no_fingerprint(_cfg):
    assert tak_revocation.revoke_in_tak("  ") == {"ok": False, "reason": "no-fingerprint"}


def test_insert_when_absent(_cfg, monkeypatch):
    fc = _FakeConn(exists=False)
    monkeypatch.setattr(tak_revocation, "_connect", lambda: fc)
    out = tak_revocation.revoke_in_tak("AA:BB:CC", "dev-1")
    assert out == {"ok": True, "reason": "revoked-in-tak"}
    sqls = " ".join(s for s, _ in fc.calls)
    assert "SELECT id FROM certificate" in sqls and "INSERT INTO certificate" in sqls
    ins = next(p for s, p in fc.calls if s.strip().startswith("INSERT"))
    assert ins["h"] == "AA:BB:CC" and ins["s"] == "CN=dev-1"  # hash + subject=CN=callsign
    assert fc.closed


def test_update_when_exists(_cfg, monkeypatch):
    fc = _FakeConn(exists=True)
    monkeypatch.setattr(tak_revocation, "_connect", lambda: fc)
    out = tak_revocation.revoke_in_tak("AA:BB")
    assert out["ok"] is True
    sqls = " ".join(s for s, _ in fc.calls)
    assert "UPDATE certificate SET revocation_date" in sqls and "INSERT" not in sqls  # 冪等：既有→UPDATE


def test_best_effort_on_write_error(_cfg, monkeypatch):
    fc = _FakeConn(exists=False, raise_on="INSERT")
    monkeypatch.setattr(tak_revocation, "_connect", lambda: fc)
    out = tak_revocation.revoke_in_tak("AA:BB")
    assert out["ok"] is False and out["reason"].startswith("tak-db-error:")
    assert fc.closed  # finally 關連線（即使錯）


def test_best_effort_on_connect_error(_cfg, monkeypatch):
    def _boom():
        raise OSError("no route to host")

    monkeypatch.setattr(tak_revocation, "_connect", _boom)
    out = tak_revocation.revoke_in_tak("AA:BB")
    assert out["ok"] is False and out["reason"].startswith("tak-db-error:")  # 連不上也不 raise
