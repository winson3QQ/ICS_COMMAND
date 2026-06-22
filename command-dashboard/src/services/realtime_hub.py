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
    """一條 WS 連線 + 它的 exercise 訂閱範圍 + faction 可見性。"""

    __slots__ = ("ws", "exercise_id", "follows_active", "include_standing", "visible_factions")

    def __init__(
        self,
        ws: WebSocket,
        exercise_id,
        follows_active: bool = False,
        include_standing: bool = False,
        visible_factions: frozenset[str] | None = None,
    ):
        self.ws = ws
        self.exercise_id = exercise_id  # int | NULL_SCOPE（client 連線）| None（內部 overview）
        # #265：True＝連線未顯式 pin 歷史場（dashboard 常態）→ active 場切換時就地 rescope；
        # False＝指揮層顯式 ?exercise_id 看歷史 → 切換不動（不可把人從歷史場拉到新 active）。
        self.follows_active = follows_active
        # #267 常駐層疊看：True＝active 場連線**也**收 NULL（常駐/real-world）entity。
        # **限 COMMAND_ROLES**（handshake gate）。不跨演習：仍精確擋別場 M（見 wants）。
        self.include_standing = include_standing
        # #343 紅藍隔離：此連線可見的 faction 集合；None = 全見（sysadmin/白隊 或 開關關）。
        self.visible_factions = visible_factions

    def _faction_ok(self, msg_source: str | None, msg_faction: str | None) -> bool:
        """#343：本連線是否可見此 entity 的 faction。None visible_factions = 全見。
        只有 source='tak' 受過濾（#146 所有權：manual/command 自建恆可見）；
        tak 且 faction 不在集合（含 None fail-closed）→ False。"""
        if self.visible_factions is None:
            return True
        if msg_source != "tak":
            return True
        return msg_faction in self.visible_factions

    def wants(self, msg_exercise_id: int | None, msg_source: str | None = None, msg_faction: str | None = None) -> bool:
        """本連線是否該收到這則 entity 訊息（P1-14 場域 isolation + #343 faction isolation）。

        連線範圍由 resolve_scope 決定（int 或 NULL_SCOPE）：
        - NULL_SCOPE（無 active＝實戰池）→ 只收 exercise_id 為 None 的實戰 entity。
        - int N（active 場 / 指揮層看歷史）→ 收 N 的 entity；若 include_standing 另收 NULL 常駐。
        - None（內部 overview，client 不會是此值）→ 全收。
        場域通過後再過 faction（兩道 AND）。⚠ 控制訊息（reset resync）走 broadcast_all，不經本過濾。
        """
        if not self._faction_ok(msg_source, msg_faction):
            return False
        if self.exercise_id is NULL_SCOPE:
            return msg_exercise_id is None
        if self.exercise_id is None:
            return True
        if msg_exercise_id == self.exercise_id:
            return True
        # #267 常駐層疊看（限 COMMAND）：active 場連線也收 NULL 常駐 entity。仍精確擋別場
        # M（M≠N、M≠None）→ 演習↔演習隔離不變（#265），只放寬 active↔常駐。
        if self.include_standing and msg_exercise_id is None:
            return True
        return False


class CopHub:
    """COP WS 廣播中樞（單 worker in-process）。"""

    def __init__(self) -> None:
        self._conns: set[_Conn] = set()
        self._lock = asyncio.Lock()

    async def connect(
        self,
        ws: WebSocket,
        exercise_id,
        follows_active: bool = False,
        include_standing: bool = False,
        visible_factions: frozenset[str] | None = None,
    ) -> _Conn:
        # exercise_id：int（某場）| NULL_SCOPE（實戰池）| None（內部 overview）
        # follows_active：True＝跟隨 active 場（切換時就地 rescope，見 rescope_active）
        # include_standing：True＝active 場也疊收 NULL 常駐 entity（限 COMMAND，#267）
        # visible_factions：#343 此連線可見 faction（None＝全見/開關關），handshake 依角色定
        conn = _Conn(ws, exercise_id, follows_active, include_standing, visible_factions)
        async with self._lock:
            self._conns.add(conn)
        return conn

    async def rescope_active(self, new_scope) -> None:
        """active 場切換時，把所有「跟隨 active」的連線就地重綁到新 scope（#265）。

        根除「WS scope 在 handshake 當下凍結」整類問題——不靠 client 重連（重連有
        cache-clear 空窗 + 雙 socket race，正是 #265 症狀）。new_scope 由呼叫端以
        `current_exercise_id() or NULL_SCOPE` 算妥（int＝某場 / NULL_SCOPE＝無 active 實戰池）。
        顯式 pin 歷史場的指揮層連線（follows_active=False）不動。"""
        async with self._lock:
            for c in self._conns:
                if c.follows_active:
                    c.exercise_id = new_scope

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

    async def broadcast(
        self,
        message: dict,
        exercise_id: int | None = None,
        source: str | None = None,
        faction: str | None = None,
    ) -> None:
        """把 entity message push 給所有符合 exercise + faction filter（wants）的連線。
        source/faction 供 #343 紅藍過濾（只 source='tak' 受 faction 限；caller 從 entity 帶入）。"""
        async with self._lock:
            targets = [c for c in self._conns if c.wants(exercise_id, source, faction)]
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
