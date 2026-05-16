from __future__ import annotations

from decimal import Decimal

from .http import HttpClient
from .models import ExchangeStatus, Orderbook, Rate, Ticker, Trade


class PublicClient:
    """REST client for Coincheck public endpoints (no auth required).

    Pair codes are lowercase with an underscore, e.g. `btc_jpy`.
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

    async def ticker(self, pair: str = "btc_jpy") -> Ticker:
        data = await self._http.public("GET", "/api/ticker", params={"pair": pair})
        return Ticker.model_validate(data)

    async def trades(self, pair: str = "btc_jpy") -> list[Trade]:
        data = await self._http.public("GET", "/api/trades", params={"pair": pair})
        rows = data.get("data", data) if isinstance(data, dict) else data
        return [Trade.model_validate(d) for d in (rows or [])]

    async def orderbook(self, pair: str = "btc_jpy") -> Orderbook:
        data = await self._http.public("GET", "/api/order_books", params={"pair": pair})
        return Orderbook.model_validate(data)

    async def rate(
        self,
        order_type: str,
        pair: str,
        *,
        amount: Decimal | str | None = None,
        price: Decimal | str | None = None,
    ) -> Rate:
        params: dict[str, object] = {"order_type": order_type, "pair": pair}
        if amount is not None:
            params["amount"] = str(amount)
        if price is not None:
            params["price"] = str(price)
        data = await self._http.public("GET", "/api/exchange/orders/rate", params=params)
        return Rate.model_validate(data)

    async def standard_rate(self, pair: str) -> Rate:
        data = await self._http.public("GET", f"/api/rate/{pair}")
        return Rate.model_validate(data)

    async def exchange_status(self, pair: str | None = None) -> list[ExchangeStatus]:
        params = {"pair": pair} if pair else None
        data = await self._http.public("GET", "/api/exchange_status", params=params)
        rows = data.get("exchange_status", data) if isinstance(data, dict) else data
        return [ExchangeStatus.model_validate(d) for d in (rows or [])]

    async def pairs(self) -> list[str]:
        """Return currently reported exchange pairs from `/api/exchange_status`."""
        return [row.pair for row in await self.exchange_status()]
