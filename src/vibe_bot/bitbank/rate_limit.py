from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    """Async sliding-window limiter: at most `rate` acquisitions per `per` seconds.

    bitbank documents two REST budgets:
      - QUERY  endpoints: ~10 req/s
      - UPDATE endpoints: ~6  req/s   (orders, cancels, withdrawals)

    We default to 10/s — pass `rate=6` to a separate limiter if you want to gate
    UPDATE traffic separately. Sliding window matches bitbank's "N per second"
    framing better than a token bucket.
    """

    def __init__(self, rate: int = 10, per: float = 1.0) -> None:
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
