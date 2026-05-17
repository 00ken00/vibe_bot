from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from .http import HttpClient
from .models import Candlestick, ExchangeStatus, Orderbook, Rate, Ticker, Trade, TradeList


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

    async def trade_page(
        self,
        pair: str = "btc_jpy",
        *,
        limit: int | None = None,
        order: str | None = None,
        starting_after: int | None = None,
        ending_before: int | None = None,
    ) -> TradeList:
        params: dict[str, object] = {"pair": pair}
        if limit is not None:
            params["limit"] = limit
        if order is not None:
            params["order"] = order
        if starting_after is not None:
            params["starting_after"] = starting_after
        if ending_before is not None:
            params["ending_before"] = ending_before
        data = await self._http.public("GET", "/api/trades", params=params)
        if isinstance(data, dict):
            return TradeList.model_validate(data)
        return TradeList(data=[Trade.model_validate(d) for d in (data or [])])

    async def trades(
        self,
        pair: str = "btc_jpy",
        *,
        limit: int | None = None,
        order: str | None = None,
        starting_after: int | None = None,
        ending_before: int | None = None,
    ) -> list[Trade]:
        page = await self.trade_page(
            pair,
            limit=limit,
            order=order,
            starting_after=starting_after,
            ending_before=ending_before,
        )
        return page.data

    async def candlesticks(
        self,
        pair: str,
        *,
        candle_minutes: int,
        start: datetime,
        end: datetime,
        limit: int = 300,
    ) -> list[Candlestick]:
        """Fetch Coincheck chart candles."""
        if candle_minutes <= 0:
            raise ValueError("candle_minutes must be positive")
        if start >= end:
            raise ValueError("start must be before end")

        start_utc = _as_utc(start)
        end_utc = _as_utc(end)
        rows = await self._http.public(
            "GET",
            "/api/charts/candle_rates",
            params={
                "limit": limit,
                "market": "coincheck",
                "pair": pair,
                "unit": candle_minutes * 60,
                "v2": "true",
            },
        )

        candles = []
        for row in rows or []:
            if len(row) < 6:
                continue
            open_time = int(row[0]) * 1000
            timestamp = datetime.fromtimestamp(open_time / 1000, tz=timezone.utc)
            if timestamp < start_utc or timestamp >= end_utc:
                continue
            candles.append(
                Candlestick(
                    open_time=open_time,
                    open=Decimal(str(row[1])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[3])),
                    close=Decimal(str(row[4])),
                    volume=Decimal(str(row[5])),
                )
            )
        return sorted(candles, key=lambda candle: candle.open_time)

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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
