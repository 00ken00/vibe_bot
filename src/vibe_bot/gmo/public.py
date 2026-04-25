from __future__ import annotations

from .http import HttpClient
from .models import (
    Kline,
    KlineInterval,
    Orderbook,
    Status,
    SymbolRule,
    Ticker,
    TradeList,
)


class PublicClient:
    """REST client for GMO Coin public endpoints (no auth required)."""

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

    async def status(self) -> Status:
        data = await self._http.public("GET", "/v1/status")
        return Status.model_validate(data)

    async def ticker(self, symbol: str | None = None) -> list[Ticker]:
        params = {"symbol": symbol} if symbol else None
        data = await self._http.public("GET", "/v1/ticker", params=params)
        return [Ticker.model_validate(d) for d in data]

    async def orderbook(self, symbol: str) -> Orderbook:
        data = await self._http.public("GET", "/v1/orderbooks", params={"symbol": symbol})
        return Orderbook.model_validate(data)

    async def trades(
        self,
        symbol: str,
        *,
        page: int | None = None,
        count: int | None = None,
    ) -> TradeList:
        params: dict = {"symbol": symbol}
        if page is not None:
            params["page"] = page
        if count is not None:
            params["count"] = count
        data = await self._http.public("GET", "/v1/trades", params=params)
        return TradeList.model_validate(data)

    async def klines(
        self, symbol: str, interval: KlineInterval, date: str
    ) -> list[Kline]:
        """`date` is YYYYMMDD for intraday intervals or YYYY for daily+."""
        params = {"symbol": symbol, "interval": interval, "date": date}
        data = await self._http.public("GET", "/v1/klines", params=params)
        return [Kline.model_validate(d) for d in data]

    async def symbols(self) -> list[SymbolRule]:
        data = await self._http.public("GET", "/v1/symbols")
        return [SymbolRule.model_validate(d) for d in data]
