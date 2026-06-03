"""
services/realtime_hub.py — COP entity 即時廣播中樞（issue #29 PR-D）

in-process WebSocket 連線登記表 + per-entity broadcast。entity 經 routers/cop.py
建立 / 更新 / 刪除後，server push `{op, uid, version_clock, entity}` 給所有訂閱連線，
取代 commit 58bb5d4 已 revert 的整檔 5s polling。

設計約束（**單一 uvicorn worker**）：
- broadcaster set 活在 process 記憶體裡。多 worker（--workers N>1 / gunicorn）下每個
  worker 各有獨立 hub，broadcast 只到「剛好連到同一 worker」的 client → 跨 worker 漏幀。
- 故 run 設定一律 pin `--workers 1`（start_pi.sh / systemd / start_mac 用 --reload 本就單 process）。
  水平擴張需把 hub 換成 Redis pub/sub —— Wave 7 federation 再做。
- 啟動時無法可靠自我偵測 worker 數（uvicorn --workers 不設 env），故以 run 設定 + 本註解
  為 SoT；若日後改 gunicorn，WEB_CONCURRENCY 可加 assert。

idempotency：version_clock 單調遞增，client 端 per-uid last-write-wins merge →
重複 / 亂序 / 重連 replay 均安全（與 PR-A repo CAS 同一把鎖的計數）。
"""

import asyncio
import logging

from fastapi import WebSocket

from repositories._helpers import NULL_SCOPE

_log = logging.getLogger(__name__)

_SEND_TIMEOUT_S = 5.0  # 單一 client send 逾時即視為死連線，避免拖垮整個 broadcast / HTTP response


class _Conn:
    """一條 WS 連線 + 它的 exercise 訂閱範圍。"""

    __slots__ = ("ws", "exercise_id")

    def __init__(self, ws: WebSocket, exercise_id):
        self.ws = ws
        self.exercise_id = exercise_id  # int | NULL_SCOPE（client 連線）| None（內部 overview）

    def wants(self, msg_exercise_id: int | None) -> bool:
        """本連線是否該收到這則 entity 訊息（P1-14 strict isolation）。

        連線範圍由 resolve_scope 決定（int 或 NULL_SCOPE）：
        - NULL_SCOPE（無 active＝實戰池）→ 只收 exercise_id 為 None 的實戰 entity。
        - int N（active 場 / 指揮層看歷史）→ **只收 N 的 entity**（exact；不再收 None 全域）。
        - None（內部 overview，client 不會是此值）→ 全收。
        ⚠ 控制訊息（reset resync）走 broadcast_all，不經本過濾。
        """
        if self.exercise_id is NULL_SCOPE:
            return msg_exercise_id is None
        if self.exercise_id is None:
            return True
        return msg_exercise_id == self.exercise_id


class CopHub:
    """COP WS 廣播中樞（單 worker in-process）。"""

    def __init__(self) -> None:
        self._conns: set[_Conn] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, exercise_id) -> _Conn:
        # exercise_id：int（某場）| NULL_SCOPE（實戰池）| None（內部 overview）
        conn = _Conn(ws, exercise_id)
        async with self._lock:
            self._conns.add(conn)
        return conn

    async def disconnect(self, conn: _Conn) -> None:
        async with self._lock:
            self._conns.discard(conn)

    async def _send(self, targets: list[_Conn], message: dict) -> None:
        """送 message 給 targets；對每條獨立 try / timeout，死連線就地剔除。"""
        dead: list[_Conn] = []
        for c in targets:
            try:
                await asyncio.wait_for(c.ws.send_json(message), timeout=_SEND_TIMEOUT_S)
            except Exception as e:  # noqa: BLE001 — 任何送失敗都當死連線剔除
                _log.debug("cop_hub broadcast 到某連線失敗，剔除：%s", e)
                dead.append(c)
        if dead:
            async with self._lock:
                for c in dead:
                    self._conns.discard(c)

    async def broadcast(self, message: dict, exercise_id: int | None = None) -> None:
        """把 entity message push 給所有符合 exercise filter（wants）的連線。"""
        async with self._lock:
            targets = [c for c in self._conns if c.wants(exercise_id)]
        await self._send(targets, message)

    async def broadcast_all(self, message: dict) -> None:
        """把控制訊息（如 reset resync）push 給**所有**連線，不經 exercise filter。
        P1-14：strict wants 後，reset 的 resync 不能再靠 exercise_id=None 命中全部，
        故走本 method 確保每條連線都收到、各自重新對帳。"""
        async with self._lock:
            targets = list(self._conns)
        await self._send(targets, message)

    async def close_all(self) -> None:
        """shutdown 時關閉所有連線（lifespan teardown 呼叫）。"""
        async with self._lock:
            conns = list(self._conns)
            self._conns.clear()
        for c in conns:
            try:
                await c.ws.close()
            except Exception:  # noqa: BLE001
                pass

    def connection_count(self) -> int:
        """目前連線數（測試 / 健康檢查用）。"""
        return len(self._conns)


# module-level singleton（單 worker → 單 hub）
cop_hub = CopHub()
