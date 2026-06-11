"""tak_runtime — TAK :8089 訂閱背景 task 的 runtime 控制器（P2-24 / #164）。

把訂閱 task 生命週期從 main.py lifespan 區域變數**升成模組級控制器**，讓
`POST /api/tak/connection` 能在 runtime 啟用/停用、**不重啟 process**（取代「靠
啟動時 `TAK_ENABLED` env、改需重啟」的痛點）。lifespan 與 toggle endpoint **共用同一個
handle**（單一真相），否則 toggle 停的是另一個 task、lifespan 起的停不掉。

啟用狀態**持久化**於 config 表 key `tak.connection_enabled`；開機優先序：**持久值優先，
未設則回退 `config.TAK_ENABLED` env**（env 退化為初始預設）。

並行安全：start/stop 以 `asyncio.Lock` 互斥，避免兩次 toggle 競態建出雙 task。
"""

import asyncio
import sqlite3
from contextlib import suppress

import structlog

from core import config
from repositories import config_repo

log = structlog.get_logger()

_CONFIG_KEY = "tak.connection_enabled"
_handle: tuple[asyncio.Task, asyncio.Event] | None = None
_lock = asyncio.Lock()


def is_running() -> bool:
    """訂閱 task 是否存在且未結束。"""
    return _handle is not None and not _handle[0].done()


def persisted_enabled() -> bool | None:
    """config 表的持久選擇；未設 → None（代表回退 env 預設）。

    防禦：`config` 表不存在 / 暫時鎖（如純單元測試直呼 `tak_status()` 未建 DB、或 DB 忙）
    → 視同未設、回退 env。**唯讀的狀態查詢不該因 DB 小瑕疵而 500**（set_* 寫入不在此例，
    仍會 surface 錯誤）。"""
    try:
        raw = config_repo.get_config(_CONFIG_KEY)
    except sqlite3.OperationalError:
        return None
    if raw is None:
        return None
    return raw.strip().lower() == "true"


def effective_enabled() -> bool:
    """目前『應否連線』= 持久值優先，未設回退 `TAK_ENABLED` env。供開機與 status 用。"""
    persisted = persisted_enabled()
    return config.TAK_ENABLED if persisted is None else persisted


def set_persisted_enabled(enabled: bool) -> None:
    """持久化開關選擇（重啟後維持）。不在此 audit —— 由 endpoint 以 TAK_CONNECTION_TOGGLE
    audit-first 記錄（避免與 config_updated 雙記）。"""
    config_repo.set_config(_CONFIG_KEY, "true" if enabled else "false")


async def start() -> bool:
    """啟動 :8089 CoT 訂閱 task。已在跑 → no-op 回 False。

    config/連線錯誤**只 log、不 raise**（TAK 是選配外部來源，啟動失敗不該擋呼叫端 /
    不擋 app 開機，沿襲 P2-03 #107 紀律）。回傳『本次是否新啟動』。
    """
    global _handle
    async with _lock:
        if is_running():
            return False
        try:
            from services import tak_service

            cfg = tak_service.build_subscribe_config(
                cot_url=config.TAK_COT_URL,
                client_cert=config.TAK_CLIENT_CERT,
                client_key=config.TAK_CLIENT_KEY,
                cafile=config.TAK_CAFILE,
                allow_insecure_tls=config.TAK_ALLOW_INSECURE_TLS,
            )
            stop_event = asyncio.Event()
            task = asyncio.create_task(tak_service.subscribe(cfg, stop_event=stop_event))
        except Exception:  # noqa: BLE001 — TAK 選配，啟動失敗只 log 不擋呼叫端
            log.warning("[tak] CoT 訂閱啟動失敗（config/import/task）", exc_info=True)
            return False
        _handle = (task, stop_event)
        log.info("[tak] CoT 訂閱背景 task 啟動：%s", config.TAK_COT_URL)

        # P2-14 (C)（#173/#194）：(重)連線後背景補一次 Marti 權威 resync——:8089 串流不重播
        # 既有靜態標記，重啟/斷線會漏 server 已持久化的 marker。fire-and-forget，失敗只 log
        # 不影響訂閱（resync_on_connect 內部已吞例外 + 受 TAK_RESYNC_ON_CONNECT 開關）。
        from services import tak_resync

        asyncio.create_task(tak_resync.resync_on_connect())
        return True


async def stop() -> bool:
    """軟停（stop_event）+ cancel + await 回收（避免 pending task 殘留警告）。
    未在跑 → no-op 回 False。回傳『本次是否停了』。"""
    global _handle
    async with _lock:
        if _handle is None:
            return False
        task, stop_event = _handle
        _handle = None
        stop_event.set()  # 軟停：停止重連
        task.cancel()  # 硬停：中斷卡在 readcot 的 await
        with suppress(asyncio.CancelledError):
            await task
        log.info("[tak] CoT 訂閱背景 task 已停止")
        return True


async def start_if_enabled() -> None:
    """開機鉤子（main.py lifespan 呼叫）：依 effective_enabled() 決定是否啟動。"""
    if effective_enabled():
        await start()
