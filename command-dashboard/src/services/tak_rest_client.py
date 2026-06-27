# SPDX-License-Identifier: LicenseRef-Proprietary
# Copyright © 2026 HUANG, JEN-SHENG. All Rights Reserved.
"""
tak_rest_client.py — TAK Server Marti REST API 抽象層（P2-11，#138）

定位：指揮部後端「**主動查**」TAK Server :8443 Marti REST 的共用 client
（對照 tak_service :8089 是**被動收** CoT 串流）。P2-12~P2-19 所有 REST 功能
（在線人員 poll、影像源、mission 等）共用此層，避免每個 item 重寫 auth / retry / poll。

認證（**規格更正**，見 memory「tak-server-marti-cert-not-oauth」）：
官方 TAK Server 5.7 Marti :8443 走 **client cert（mTLS）**，**非 OAuth2**（OAuth2 是
OpenTAKServer 才有）。複用 core/config 的 TAK_CLIENT_CERT/KEY/CAFILE（與 :8089 同一套 step-ca）。

HTTP client：aiohttp（pytak with_takproto 已帶；CLAUDE.md「先找先例」—— pytak
MartiTXWorker/MartiRXWorker 同樣 aiohttp + cert）。

設計：`build_marti_ssl_context` / `TakRestClient`（ssl_context 注入，純邏輯可 mock）/
`build_tak_rest_client`（factory，整合 config cert）—— 對齊 tak_service 的
build_subscribe_config + subscribe 分離模式。
"""

import asyncio
import inspect
import json
import logging
import ssl

import aiohttp

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 10.0
_BACKOFF_BASE_S = 0.5
_BACKOFF_MAX_S = 30.0  # 指數退避上限（對齊 tak_service.subscribe backoff_max）


class TakRestError(Exception):
    """Marti REST 請求錯誤（HTTP 非 2xx / 重試耗盡 / JSON 解析失敗）。"""


async def _backoff_sleep(attempt: int) -> None:
    """指數退避：base·2^attempt，封頂 _BACKOFF_MAX_S（連線錯 / 5xx 重試共用）。"""
    await asyncio.sleep(min(_BACKOFF_BASE_S * 2**attempt, _BACKOFF_MAX_S))


def _join_url(base_url: str, path: str) -> str:
    """path 一律當 base_url 下的**相對路徑**接合，**不容絕對 URL 覆寫 host/protocol**。

    杜絕 SSRF footgun（security-review #138 forward-looking）：未來消費方若誤把
    TAK server 回傳的 URL 直接當 path，也只會接在 base_url 後（連線失敗），不會被
    導去任意 host。Marti REST 全在單一 base_url 下，無跨 host 需求。
    """
    if not path.startswith("/"):
        path = "/" + path
    return f"{base_url}{path}"


def build_marti_ssl_context(
    *,
    client_cert: str,
    client_key: str,
    cafile: str | None = None,
    allow_insecure_tls: bool = False,
) -> ssl.SSLContext:
    """建 Marti REST（mTLS）用 SSLContext：client cert 自證 + cafile 驗 server。

    對齊 tak_service.build_subscribe_config 的 cert 語意（同一套 step-ca）：
    - cafile 有 → 驗 server 憑證（正式部署必填）
    - cafile 無 + allow_insecure_tls → 關 server 驗證（dev/PoC，有 MITM 風險，warn）
    - cafile 無 + 未顯式 insecure → raise（不默默裸奔）

    client cert 須含**完整鏈**（leaf + intermediate），對齊 :8089 fullchain 要求。
    """
    if cafile:
        ctx = ssl.create_default_context(cafile=cafile)
    elif allow_insecure_tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        log.warning("[tak-rest] allow_insecure_tls：不驗 server 憑證（MITM 風險，僅 dev）")
    else:
        raise ValueError(
            "build_marti_ssl_context：須提供 cafile 驗 server 憑證；"
            "dev 無 CA 時須顯式 allow_insecure_tls=True（關閉驗證，有 MITM 風險）"
        )
    ctx.load_cert_chain(certfile=client_cert, keyfile=client_key)  # mTLS 自證
    return ctx


class TakRestClient:
    """TAK Server Marti REST 共用 client（cert mTLS + retry + rate-limit + poll）。

    ssl_context 由 caller 注入（production 走 build_tak_rest_client factory 帶 cert；
    測試注入不需 cert 的 context → 純邏輯可 mock）。

    用法（消費方 P2-12+）：
        client = build_tak_rest_client(base_url=..., client_cert=..., ...)
        data = await client.get_json("/Marti/api/clientEndPoints")
        await client.close()

    或定時 poll：
        await client.poll("/Marti/api/clientEndPoints", on_data, interval_s=30, stop_event=ev)
    """

    def __init__(
        self,
        base_url: str,
        ssl_context: ssl.SSLContext,
        *,
        min_interval_s: float = 1.0,
        max_retries: int = 3,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not base_url:
            raise ValueError("TakRestClient：base_url 必填（TAK_MARTI_URL）")
        self._base_url = base_url.rstrip("/")
        self._ssl = ssl_context
        self._min_interval_s = min_interval_s
        self._max_retries = max(1, max_retries)
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: aiohttp.ClientSession | None = None
        self._last_request_t: float = 0.0  # rate-limit 基準（event loop monotonic）
        # 保護 session 建立 + rate-limit 的 read-modify-write（多 poll/查詢共用同一
        # client 並發時，否則會建多個 session 洩漏 / throttle 被繞過，review #138-2/3）
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        async with self._lock:
            if self._session is None or self._session.closed:
                connector = aiohttp.TCPConnector(ssl=self._ssl)
                self._session = aiohttp.ClientSession(connector=connector, timeout=self._timeout)
            return self._session

    async def _rate_limit(self) -> None:
        """確保兩次請求間隔 ≥ min_interval_s（self-throttle，免 poll 太密打爆 server）。
        鎖內 read-sleep-write → 並發請求序列化，throttle 在並發下仍有效（review #138-2）。"""
        async with self._lock:
            loop = asyncio.get_running_loop()
            wait = self._min_interval_s - (loop.time() - self._last_request_t)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_t = loop.time()

    async def _fetch(self, path: str, params: dict | None) -> tuple[int, str]:
        """單次 HTTP GET → (status, body text)。抽出供測試注入（mock 免真 server）。"""
        session = await self._ensure_session()
        url = _join_url(self._base_url, path)
        async with session.get(url, params=params) as resp:
            return resp.status, await resp.text()

    async def get_text(self, path: str, params: dict | None = None) -> str | None:
        """GET path → 回原始 body 文字。rate-limit + 指數退避重試（連線錯 / 5xx）。

        非 JSON 端點用（P2-14 resync 的 `/cot/sa` 回 `<events>` XML）。JSON 端點走 get_json。
        回傳：body 文字；空 body（204 / 空）回 None。
        raise TakRestError：4xx（用戶端錯，不重試）/ 重試耗盡。
        """
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            await self._rate_limit()
            try:
                status, text = await self._fetch(path, params)
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:  # 耗盡前最後一次不白等（review #138-1）
                    await _backoff_sleep(attempt)
                continue
            if status < 300:
                return text if text.strip() else None  # 204 / 空 body：成功但無內容（review #138-4）
            if 400 <= status < 500:
                # 用戶端錯（401 cert 沒權 / 404 端點不對）→ 不重試，立即拋。
                # ★ /cot/sa gotcha：大時間窗回 BAD_REQUEST(400)，與真 auth 拒共用此頁
                #   （memory tak-marti-authz-model）→ caller 別把 400 一律當 auth 壞，須用小窗。
                raise TakRestError(f"Marti {path} HTTP {status}（用戶端錯，不重試）：{text[:200]}")
            # 5xx → 暫時性，重試
            last_exc = TakRestError(f"Marti {path} HTTP {status}：{text[:200]}")
            if attempt < self._max_retries - 1:
                await _backoff_sleep(attempt)
        raise TakRestError(f"Marti {path} 重試 {self._max_retries} 次耗盡") from last_exc

    async def get_json(self, path: str, params: dict | None = None):
        """GET path → 解析 JSON。rate-limit + 指數退避重試（連線錯 / 5xx）。

        回傳：解析後 JSON（dict / list）；空 body 回 None。
        raise TakRestError：4xx（用戶端錯，不重試）/ 重試耗盡 / JSON 解析失敗。
        """
        text = await self.get_text(path, params)
        if text is None:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise TakRestError(f"Marti {path} 回應非 JSON：{text[:200]}") from exc

    async def put_json(self, path: str, body: dict):
        """PUT path（JSON body）→ 回應 JSON（或空 body→None）。rate-limit + 退避重試（連線錯 / 5xx）。

        #344：寫 group（`/user-management/api/update-groups`）用。獨立於 GET 路徑（不動 _fetch）。
        raise TakRestError：4xx（用戶端錯，不重試）/ 重試耗盡。
        """
        last_exc: Exception | None = None
        url = _join_url(self._base_url, path)
        for attempt in range(self._max_retries):
            await self._rate_limit()
            try:
                session = await self._ensure_session()
                async with session.put(url, json=body) as resp:
                    status, text = resp.status, await resp.text()
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    await _backoff_sleep(attempt)
                continue
            if status < 300:
                if not text.strip():
                    return None
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return None  # 2xx 但非 JSON（部分端點回空/純文字）→ 視為成功無內容
            if 400 <= status < 500:
                raise TakRestError(f"Marti PUT {path} HTTP {status}（用戶端錯，不重試）：{text[:200]}")
            last_exc = TakRestError(f"Marti PUT {path} HTTP {status}：{text[:200]}")
            if attempt < self._max_retries - 1:
                await _backoff_sleep(attempt)
        raise TakRestError(f"Marti PUT {path} 重試 {self._max_retries} 次耗盡") from last_exc

    async def post_json(self, path: str, body: dict):
        """POST path（JSON body）→ 回應 JSON（或空 body→None）。語意同 put_json，差在動詞。

        #429：建帳號（`/user-management/api/new-user`）用。raise TakRestError：4xx（不重試）/ 重試耗盡。
        """
        last_exc: Exception | None = None
        url = _join_url(self._base_url, path)
        for attempt in range(self._max_retries):
            await self._rate_limit()
            try:
                session = await self._ensure_session()
                async with session.post(url, json=body) as resp:
                    status, text = resp.status, await resp.text()
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    await _backoff_sleep(attempt)
                continue
            if status < 300:
                if not text.strip():
                    return None
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return None  # 2xx 但非 JSON → 視為成功無內容
            if 400 <= status < 500:
                raise TakRestError(f"Marti POST {path} HTTP {status}（用戶端錯，不重試）：{text[:200]}")
            last_exc = TakRestError(f"Marti POST {path} HTTP {status}：{text[:200]}")
            if attempt < self._max_retries - 1:
                await _backoff_sleep(attempt)
        raise TakRestError(f"Marti POST {path} 重試 {self._max_retries} 次耗盡") from last_exc

    async def poll(
        self,
        path: str,
        callback,
        *,
        interval_s: float,
        stop_event: asyncio.Event,
        params: dict | None = None,
    ) -> None:
        """定時 poll path → 把 JSON 餵 callback，直到 stop_event set。

        best-effort：單次 poll 失敗只 log.warning 不中斷迴圈（對齊 subscribe 容錯）。
        callback 可為 sync 或 async。等待用 stop_event.wait(timeout) → stop 即時生效
        （非死等整個 interval）。

        生命週期：poll **不** close session（避免重啟 poll 要重建）；caller 負責在
        結束 / cancel 時 `await client.close()`（建議 try/finally 包 poll task）。
        """
        while not stop_event.is_set():
            try:
                data = await self.get_json(path, params)
                res = callback(data)
                if inspect.isawaitable(res):  # 容 coroutine 與其他 awaitable（對齊 tak_service）
                    await res
            except Exception as exc:  # noqa: BLE001 — best-effort poll 不因單次失敗中斷
                log.warning("[tak-rest] poll %s 失敗（不中斷）：%s", path, exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
            except TimeoutError:
                pass  # 到 interval → 下一輪

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()


def build_tak_rest_client(
    *,
    base_url: str,
    client_cert: str,
    client_key: str,
    cafile: str | None = None,
    allow_insecure_tls: bool = False,
    min_interval_s: float = 1.0,
    max_retries: int = 3,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> TakRestClient:
    """factory：建 cert SSLContext + TakRestClient（整合 core/config TAK_* 用）。

    消費方（P2-12+）一般呼叫本 factory，不直接 new TakRestClient（除非測試注入 ssl）。
    """
    ctx = build_marti_ssl_context(
        client_cert=client_cert,
        client_key=client_key,
        cafile=cafile,
        allow_insecure_tls=allow_insecure_tls,
    )
    return TakRestClient(
        base_url,
        ctx,
        min_interval_s=min_interval_s,
        max_retries=max_retries,
        timeout_s=timeout_s,
    )
