from __future__ import annotations

import asyncio
import time
from collections import deque


class RateLimiter:
    """Async sliding-window limiter: at most `rate` acquisitions per `per` seconds.

    bitFlyer documents per-IP and per-endpoint budgets in five-minute windows:

      - Same IP across all endpoints: 500 / 5 min
      - Private API (general):        500 / 5 min
      - Order endpoints:              300 / 5 min
      - Small orders (size ≤ 0.1 BTC): 100 / 1 min

    The default below mirrors the per-IP global cap (500 in 300 s ≈ 1.66 req/s).
    Use a separate limiter (and pass it to a separate `HttpClient`) if you want
    to gate order traffic distinctly:

        order_limiter = RateLimiter(rate=300, per=300.0)
    """

    def __init__(self, rate: int = 500, per: float = 300.0) -> None:
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
