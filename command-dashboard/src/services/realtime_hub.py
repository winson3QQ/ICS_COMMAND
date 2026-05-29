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

_log = logging.getLogger(__name__)

_SEND_TIMEOUT_S = 5.0  # 單一 client send 逾時即視為死連線，避免拖垮整個 broadcast / HTTP response


class _Conn:
    """一條 WS 連線 + 它的 exercise 訂閱範圍。"""

    __slots__ = ("ws", "exercise_id")

    def __init__(self, ws: WebSocket, exercise_id: int | None):
        self.ws = ws
        self.exercise_id = exercise_id

    def wants(self, msg_exercise_id: int | None) -> bool:
        """本連線是否該收到這則訊息。

        - 連線未指定 exercise（exercise_id=None）→ 訂閱全部（指揮台總覽）。
        - 連線指定某 exercise N → 收 N 的 entity，以及無 exercise 歸屬（None）的全域 entity。
        """
        if self.exercise_id is None:
            return True
        return msg_exercise_id is None or msg_exercise_id == self.exercise_id


class CopHub:
    """COP WS 廣播中樞（單 worker in-process）。"""

    def __init__(self) -> None:
        self._conns: set[_Conn] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, exercise_id: int | None) -> _Conn:
        conn = _Conn(ws, exercise_id)
        async with self._lock:
            self._conns.add(conn)
        return conn

    async def disconnect(self, conn: _Conn) -> None:
        async with self._lock:
            self._conns.discard(conn)

    async def broadcast(self, message: dict, exercise_id: int | None = None) -> None:
        """把 message push 給所有符合 exercise filter 的連線。

        對每條連線獨立 try / timeout；單一死連線不影響其他 client，也不阻塞呼叫端
        （HTTP handler）。送失敗 / 逾時的連線就地剔除。
        """
        async with self._lock:
            targets = [c for c in self._conns if c.wants(exercise_id)]
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
