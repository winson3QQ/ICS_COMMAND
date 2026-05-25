"""
core/rate_limit.py — 通用 Fixed-Window Rate Limiter（C1-F）

設計原則：
  - 橫切關注點（cross-cutting concern），不放在個別 router 內
  - Thread-safe：使用 threading.Lock（FastAPI 在單一 process 內多 thread 執行）
  - 窗口重設：per-key，進窗口第一個 request 時開始計時
  - 不依賴外部 Redis / Memcached，適合 Pi 單機部署

預留 throttle callback（C1-D audit log 用）：
  limiter = FixedWindowLimiter(limit=60, window_sec=60,
                               on_throttle=lambda key, count: audit_log.write(...))

使用範例：
  _csp_limiter = FixedWindowLimiter(limit=60, window_sec=60)

  @router.post("/some-endpoint")
  async def handler(request: Request):
      if not _csp_limiter.check(request.client.host or "unknown"):
          return Response(status_code=429)
      ...

NOTE：此 limiter 為 in-process 狀態，重啟後計數歸零。
  對 CSP report 端點可接受（報告洪水短暫重啟即止）。
  高可用場景改用 Redis-backed limiter（C2-F 範圍）。
"""

import threading
import time
from collections.abc import Callable


class FixedWindowLimiter:
    """
    Fixed-Window Rate Limiter。
    每個 key 在 window_sec 秒內最多允許 limit 次請求。
    超過回傳 False；未超過回傳 True 並計數。
    """

    def __init__(
        self,
        limit: int,
        window_sec: int,
        on_throttle: Callable[[str, int], None] | None = None,
    ) -> None:
        self._limit      = limit
        self._window_sec = window_sec
        self._on_throttle = on_throttle
        # { key: (window_start_ts, count) }
        self._buckets: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        """
        回傳 True 表示允許；False 表示已超限（呼叫方回 429）。
        """
        now = time.monotonic()
        with self._lock:
            entry = self._buckets.get(key)
            if entry is None or (now - entry[0]) >= self._window_sec:
                # 新窗口
                self._buckets[key] = (now, 1)
                return True
            window_start, count = entry
            if count >= self._limit:
                if self._on_throttle:
                    try:
                        self._on_throttle(key, count)
                    except Exception:
                        pass  # callback 不得影響主流程
                return False
            self._buckets[key] = (window_start, count + 1)
            return True

    def reset(self, key: str) -> None:
        """測試用：重設指定 key 的計數。"""
        with self._lock:
            self._buckets.pop(key, None)
