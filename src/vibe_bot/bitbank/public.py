from __future__ import annotations

from .http import HttpClient
from .models import (
    Candlestick,
    CandleType,
    CircuitBreakInfo,
    Depth,
    SpotPairStatus,
    Ticker,
    TransactionList,
)


class PublicClient:
    """REST client for bitbank public endpoints (no auth required).

    Public host is `https://public.bitbank.cc` and does NOT use a `/v1` prefix
    (unlike the private host). Pair codes are lowercase, e.g. `btc_jpy`.
    """

    def __init__(self, http: HttpClient | None = None) -> None:
        self._http = http or HttpClient()
        self._owns_http = http is None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "PublicClient":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        del exc_type, exc, tb
        await self.aclose()

    async def ticker(self, pair: str) -> Ticker:
        data = await self._http.public("GET", f"/{pair}/ticker")
        return Ticker.model_validate(data)

    async def tickers(self) -> list[Ticker]:
        data = await self._http.public("GET", "/tickers")
        return [Ticker.model_validate(d) for d in data]

    async def tickers_jpy(self) -> list[Ticker]:
        data = await self._http.public("GET", "/tickers_jpy")
        return [Ticker.model_validate(d) for d in data]

    async def depth(self, pair: str) -> Depth:
        data = await self._http.public("GET", f"/{pair}/depth")
        return Depth.model_validate(data)

    async def transactions(self, pair: str, date: str | None = None) -> TransactionList:
        """Latest 60 if `date` omitted; otherwise YYYYMMDD for a specific day."""
        path = f"/{pair}/transactions" + (f"/{date}" if date else "")
        data = await self._http.public("GET", path)
        return TransactionList.model_validate(data)

    async def candlestick(
        self, pair: str, candle_type: CandleType, date: str
    ) -> Candlestick:
        """`date` is YYYYMMDD for intervals ≤1hour, YYYY for 4hour and above."""
        data = await self._http.public(
            "GET", f"/{pair}/candlestick/{candle_type}/{date}"
        )
        # bitbank wraps the row as {"candlestick": [{...}], "timestamp": ...}
        if isinstance(data, dict) and "candlestick" in data:
            inner = data["candlestick"]
            row = inner[0] if isinstance(inner, list) and inner else inner
            payload = dict(row)
            payload["timestamp"] = data.get("timestamp")
            return Candlestick.model_validate(payload)
        return Candlestick.model_validate(data)

    async def circuit_break_info(self, pair: str) -> CircuitBreakInfo:
        data = await self._http.public("GET", f"/{pair}/circuit_break_info")
        return CircuitBreakInfo.model_validate(data)

    async def spot_status(self) -> list[SpotPairStatus]:
        """Per-pair spot status. Lives on the PRIVATE host but is unauthenticated."""
        from .http import PRIVATE_BASE
        data = await self._http.public("GET", "/v1/spot/status", base=PRIVATE_BASE)
        items = data.get("statuses") if isinstance(data, dict) else data
        return [SpotPairStatus.model_validate(d) for d in (items or [])]

    async def spot_pairs(self) -> list[dict]:
        """Per-pair trading rules (fee tiers, order constraints, leverage…).

        Returned as raw dicts because the schema is wide and frequently extended;
        consumers can pluck the fields they care about without forcing a model
        bump every time bitbank adds one."""
        from .http import PRIVATE_BASE
        data = await self._http.public("GET", "/v1/spot/pairs", base=PRIVATE_BASE)
        if isinstance(data, dict) and "pairs" in data:
            return list(data["pairs"])
        return list(data) if isinstance(data, list) else []
