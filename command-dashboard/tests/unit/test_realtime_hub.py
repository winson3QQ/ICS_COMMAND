"""
tests/unit/test_realtime_hub.py — issue #29 PR-D：CopHub 廣播邏輯單測

不經 HTTP / WS，直接測 hub：
- exercise filter（_Conn.wants）矩陣
- broadcast 只送符合 filter 的連線
- broadcast 對死連線容錯並就地剔除
- close_all 清空
"""

import asyncio

from repositories._helpers import NULL_SCOPE
from services.realtime_hub import CopHub, _Conn


class _FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send_json(self, msg):
        self.sent.append(msg)

    async def close(self):
        self.closed = True


class _BrokenWS(_FakeWS):
    async def send_json(self, msg):
        raise RuntimeError("dead socket")


def test_conn_wants_filter_matrix():
    # P1-14 strict isolation：
    # None（內部 overview，client 不會是此值）→ 訂閱全部
    assert _Conn(None, None).wants(5) is True
    assert _Conn(None, None).wants(None) is True
    # 指定 exercise=1 → **只**收 1（不再收全域 None；修正 GET/WS 不一致）
    assert _Conn(None, 1).wants(1) is True
    assert _Conn(None, 1).wants(None) is False
    assert _Conn(None, 1).wants(2) is False
    # NULL_SCOPE（無 active＝實戰池）→ 只收 exercise_id None 的實戰 entity
    assert _Conn(None, NULL_SCOPE).wants(None) is True
    assert _Conn(None, NULL_SCOPE).wants(1) is False


def test_broadcast_filters_and_drops_dead():
    async def run():
        hub = CopHub()
        good, broken = _FakeWS(), _BrokenWS()
        await hub.connect(good, None)
        await hub.connect(broken, None)
        await hub.broadcast({"op": "create", "uid": "x"}, exercise_id=None)
        assert good.sent == [{"op": "create", "uid": "x"}]
        assert hub.connection_count() == 1  # 死連線被剔除

    asyncio.run(run())


def test_broadcast_respects_exercise_filter():
    async def run():
        hub = CopHub()
        a, b = _FakeWS(), _FakeWS()
        await hub.connect(a, 1)
        await hub.connect(b, 2)
        await hub.broadcast({"m": 1}, exercise_id=1)
        assert a.sent == [{"m": 1}]
        assert b.sent == []  # exercise 2 訂閱者不收 exercise 1 的訊息

    asyncio.run(run())


def test_strict_isolation_global_entity_not_leaked_to_exercise_sub():
    # P1-14 strict isolation：實戰(None) entity **不**洩漏給某場(exercise=1)訂閱者；
    # 只有 NULL_SCOPE（實戰池）訂閱者收得到。修正舊「global 送到 exercise sub」的不一致。
    async def run():
        hub = CopHub()
        ex_sub, real_sub = _FakeWS(), _FakeWS()
        await hub.connect(ex_sub, 1)
        await hub.connect(real_sub, NULL_SCOPE)
        await hub.broadcast({"m": "real"}, exercise_id=None)  # 實戰 entity
        assert ex_sub.sent == []                  # 某場訂閱者不收實戰
        assert real_sub.sent == [{"m": "real"}]   # 實戰池訂閱者收得到

    asyncio.run(run())


def test_broadcast_all_reaches_every_connection():
    # reset resync 走 broadcast_all → 不論 scope 全部收到（strict wants 後仍能全域對帳）
    async def run():
        hub = CopHub()
        a, b, c = _FakeWS(), _FakeWS(), _FakeWS()
        await hub.connect(a, 1)
        await hub.connect(b, 2)
        await hub.connect(c, NULL_SCOPE)
        await hub.broadcast_all({"op": "resync"})
        assert a.sent == b.sent == c.sent == [{"op": "resync"}]

    asyncio.run(run())


def test_close_all_clears():
    async def run():
        hub = CopHub()
        w = _FakeWS()
        await hub.connect(w, None)
        await hub.close_all()
        assert w.closed is True
        assert hub.connection_count() == 0

    asyncio.run(run())
