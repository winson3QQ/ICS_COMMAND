# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
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
# (subscribe_task, mission_poll_task|None, presence_beacon_task|None, stop_event)。#506 M1 / #507 P4：
# mission poll 與 presence beacon 背景 task 與訂閱 task 共用同一 stop_event、同生共死（start 一起建、
# stop 一起收）。未配置 mission feed / presence OFF 時對應槽為 None。is_running 仍以 subscribe task（[0]）為準。
_handle: tuple[asyncio.Task, asyncio.Task | None, asyncio.Task | None, asyncio.Event] | None = None
_lock = asyncio.Lock()
# #222：on_connect resync/對帳互斥——flap（server 反覆關開）時上一輪未跑完就跳過，避免疊跑。
_on_connect_lock = asyncio.Lock()


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


def is_configured() -> bool:
    """連線參數（:8089 streaming URL + client cert/key）是否齊備。

    與開關**正交**：config 屬**部署層**（`issue-tak-certs.sh` 簽憑證 + env 餵
    `config.TAK_*`），admin UI **不設定、只消費開關**。供 `/api/tak/status` 區分兩種
    『開了卻不綠』：① `enabled 且 !configured` → 部署未備妥連線參數（非 admin 在 UI 能修，
    屬部署/ops）；② `enabled 且 configured 但連不上` → 網路 / 憑證 / TAK server 問題。
    """
    return bool(config.TAK_COT_URL and config.TAK_CLIENT_CERT and config.TAK_CLIENT_KEY)


def set_persisted_enabled(enabled: bool) -> None:
    """持久化開關選擇（重啟後維持）。不在此 audit —— 由 endpoint 以 TAK_CONNECTION_TOGGLE
    audit-first 記錄（避免與 config_updated 雙記）。"""
    config_repo.set_config(_CONFIG_KEY, "true" if enabled else "false")


async def _on_reconnect_resync_reconcile() -> None:
    """TAK socket 每次 (重)連上時跑：inbound resync（補 server 有、ICS 沒有）→ outbound 對帳
    （補 ICS 改了、斷線沒送出的 shared 標記）。由 `tak_service.subscribe` 的 on_connect 觸發——
    **含 subscribe loop 內部自動重連**（#222：TAK server 不穩定關開時 subscribe 內部重連不會走
    `start()`，故掛 on_connect 才補得到，不能只靠 start）。outbound 排 inbound 之後＝ICS 端為
    最後權威，不被 inbound 的 server 舊快照蓋回。互斥：flap 疊跑時跳過上一輪未完者。
    """
    if _on_connect_lock.locked():
        return  # 上一輪 resync/對帳還在跑（flap）→ 跳過避免疊跑
    async with _on_connect_lock:
        try:
            from services import tak_missions, tak_resync

            await tak_resync.resync_on_connect()  # 內部已吞例外 + 受 TAK_RESYNC_ON_CONNECT 開關
            await tak_resync.reconcile_shared_outbound()  # 內部 best-effort + 未配置 no-op
            await tak_missions.run_mission_sync()  # #506 M1：on-connect 即時同步 mission（未配置 no-op）
        except Exception:  # noqa: BLE001 — 背景 resync/對帳/mission 絕不拖垮訂閱
            log.warning("[tak] 重連 resync/reconcile/mission 背景任務異常", exc_info=True)


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
            # #222：resync/對帳掛在 subscribe 的 on_connect（每次 socket (重)連上都觸發，含
            # subscribe loop 內部自動重連）——而非只在 start()。否則 TAK server 不穩定關開時，
            # subscribe 內部重連不走 start()，斷線期間 shared 標記的移動就補不回現場端。
            task = asyncio.create_task(
                tak_service.subscribe(cfg, stop_event=stop_event, on_connect=_on_reconnect_resync_reconcile)
            )
        except Exception:  # noqa: BLE001 — TAK 選配，啟動失敗只 log 不擋呼叫端
            log.warning("[tak] CoT 訂閱啟動失敗（config/import/task）", exc_info=True)
            return False
        # #506 M1：mission 背景週期 poll（配了 TAK_MISSION_FEEDS 才起；共用 stop_event）。
        # 啟動失敗不擋訂閱（mission 消費為附加能力，失敗只 log）。
        poll_task = None
        try:
            from services import tak_missions

            if tak_missions.mission_sync_enabled():
                poll_task = asyncio.create_task(tak_missions.mission_poll_loop(stop_event))
                log.info("[tak] mission 背景 poll task 啟動：%s", tak_missions.configured_mission_names())
        except Exception:  # noqa: BLE001 — mission poll 為附加能力，啟動失敗不擋訂閱
            log.warning("[tak] mission poll task 啟動失敗", exc_info=True)
        # #507 P4：presence beacon（配了 TAK_PRESENCE_ENABLED 才起；共用 stop_event）。
        # 啟動失敗不擋訂閱（presence 為附加下行能力，失敗只 log）。
        beacon_task = None
        try:
            from services import tak_downlink

            if tak_downlink.presence_enabled():
                beacon_task = asyncio.create_task(tak_downlink.presence_beacon_loop(stop_event))
                log.info("[tak] presence beacon task 啟動：callsign=%s", config.TAK_PRESENCE_CALLSIGN)
        except Exception:  # noqa: BLE001 — presence 為附加能力，啟動失敗不擋訂閱
            log.warning("[tak] presence beacon task 啟動失敗", exc_info=True)
        _handle = (task, poll_task, beacon_task, stop_event)
        log.info("[tak] CoT 訂閱背景 task 啟動：%s", config.TAK_COT_URL)
        return True


async def stop() -> bool:
    """軟停（stop_event）+ cancel + await 回收（避免 pending task 殘留警告）。
    未在跑 → no-op 回 False。回傳『本次是否停了』。"""
    global _handle
    async with _lock:
        if _handle is None:
            return False
        task, poll_task, beacon_task, stop_event = _handle
        _handle = None
        stop_event.set()  # 軟停：停止重連 + 喚醒 poll/beacon loop 的 wait
        task.cancel()  # 硬停：中斷卡在 readcot 的 await
        with suppress(asyncio.CancelledError):
            await task
        if poll_task is not None:  # #506 M1：mission poll task 同收（避免 pending task 殘留）
            poll_task.cancel()
            with suppress(asyncio.CancelledError):
                await poll_task
        if beacon_task is not None:  # #507 P4：presence beacon task 同收
            beacon_task.cancel()
            with suppress(asyncio.CancelledError):
                await beacon_task
        log.info("[tak] CoT 訂閱背景 task 已停止")
        return True


async def start_if_enabled() -> None:
    """開機鉤子（main.py lifespan 呼叫）：依 effective_enabled() 決定是否啟動。"""
    if effective_enabled():
        await start()
