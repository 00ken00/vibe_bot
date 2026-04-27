from __future__ import annotations

from .http import HttpClient
from .models import (
    Board,
    BoardStateInfo,
    CorporateLeverage,
    Execution,
    FundingRate,
    Health,
    Market,
    Ticker,
)


class PublicClient:
    """REST client for bitFlyer public endpoints (no auth required).

    Product codes are uppercase (e.g. `BTC_JPY`, `FX_BTC_JPY`, `ETH_BTC`).
    Spot uses `<base>_<quote>`; CFD/leverage prefixes with `FX_`.
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

    async def markets(self, region: str | None = None) -> list[Market]:
        """List supported product codes. Pass `region="usa"` or `"eu"` for the
        regional product set."""
        path = "/getmarkets"
        if region:
            path = f"{path}/{region.lower()}"
        data = await self._http.public("GET", path)
        return [Market.model_validate(d) for d in (data or [])]

    async def board(self, product_code: str = "BTC_JPY") -> Board:
        data = await self._http.public(
            "GET", "/getboard", params={"product_code": product_code}
        )
        return Board.model_validate(data)

    async def ticker(self, product_code: str = "BTC_JPY") -> Ticker:
        data = await self._http.public(
            "GET", "/getticker", params={"product_code": product_code}
        )
        return Ticker.model_validate(data)

    async def executions(
        self,
        product_code: str = "BTC_JPY",
        *,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
    ) -> list[Execution]:
        params: dict = {"product_code": product_code}
        if count is not None:
            params["count"] = count
        if before is not None:
            params["before"] = before
        if after is not None:
            params["after"] = after
        data = await self._http.public("GET", "/getexecutions", params=params)
        return [Execution.model_validate(d) for d in (data or [])]

    async def board_state(self, product_code: str = "BTC_JPY") -> BoardStateInfo:
        data = await self._http.public(
            "GET", "/getboardstate", params={"product_code": product_code}
        )
        return BoardStateInfo.model_validate(data)

    async def health(self, product_code: str = "BTC_JPY") -> Health:
        data = await self._http.public(
            "GET", "/gethealth", params={"product_code": product_code}
        )
        return Health.model_validate(data)

    async def funding_rate(self, product_code: str = "FX_BTC_JPY") -> FundingRate:
        data = await self._http.public(
            "GET", "/getfundingrate", params={"product_code": product_code}
        )
        return FundingRate.model_validate(data)

    async def corporate_leverage(self) -> CorporateLeverage:
        data = await self._http.public("GET", "/getcorporateleverage")
        return CorporateLeverage.model_validate(data)

    async def chats(
        self, *, from_date: str | None = None, region: str | None = None
    ) -> list[dict]:
        """Recent Lightning chat log. `region` is optional (`"usa"` / `"eu"`).
        Returned as raw dicts (the chat schema is small but bitFlyer-specific)."""
        path = "/getchats"
        if region:
            path = f"{path}/{region.lower()}"
        params: dict = {}
        if from_date:
            params["from_date"] = from_date
        data = await self._http.public("GET", path, params=params or None)
        return list(data or [])
