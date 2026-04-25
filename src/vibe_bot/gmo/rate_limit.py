from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    """Async sliding-window limiter: at most `rate` acquisitions per `per` seconds.

    GMO's REST limit is per-second, per-tier (20 default, 30 high-volume).
    Sliding window beats token bucket here because GMO documents the limit as
    "N requests per second" rather than a sustained rate with bursts.
    """

    def __init__(self, rate: int = 20, per: float = 1.0) -> None:
        self.rate = rate
        self.per = per
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.per
            while self._timestamps and self._timestamps[0] <= cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.rate:
                wait = self._timestamps[0] + self.per - now
                if wait > 0:
                    await asyncio.sleep(wait)
                    now = time.monotonic()
                    cutoff = now - self.per
                    while self._timestamps and self._timestamps[0] <= cutoff:
                        self._timestamps.popleft()
            self._timestamps.append(now)
