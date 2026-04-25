from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from .errors import AuthError
from .http import HttpClient
from .models import (
    Asset,
    Execution,
    ExecutionList,
    ExecutionType,
    Margin,
    Order,
    OrderList,
    PositionList,
    PositionSummaryList,
    Side,
    TimeInForce,
    TradingVolume,
)


def _stringify(value: Any) -> Any:
    """GMO accepts numbers as strings to avoid float precision issues."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    return value


def _clean(d: dict) -> dict:
    return {k: _stringify(v) for k, v in d.items() if v is not None}


class PrivateClient:
    """REST client for GMO Coin private endpoints. HMAC-signed."""

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        http: HttpClient | None = None,
    ) -> None:
        if http is not None:
            self._http = http
            self._owns_http = False
        else:
            key = api_key or os.environ.get("GMO_API_KEY")
            secret = api_secret or os.environ.get("GMO_API_SECRET")
            if not key or not secret:
                raise AuthError(
                    -1,
                    [{"message_code": "LOCAL", "message_string": "GMO_API_KEY / GMO_API_SECRET not set"}],
                )
            self._http = HttpClient(api_key=key, api_secret=secret)
            self._owns_http = True

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "PrivateClient":
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        del exc_type, exc, tb
        await self.aclose()

    # --- Account ---

    async def margin(self) -> Margin:
        data = await self._http.private("GET", "/v1/account/margin")
        return Margin.model_validate(data)

    async def assets(self) -> list[Asset]:
        data = await self._http.private("GET", "/v1/account/assets")
        return [Asset.model_validate(d) for d in data]

    async def trading_volume(self) -> TradingVolume:
        data = await self._http.private("GET", "/v1/account/tradingVolume")
        return TradingVolume.model_validate(data)

    async def fiat_deposit_history(self, **params: Any) -> Any:
        return await self._http.private(
            "GET", "/v1/account/fiatDepositHistory", params=_clean(params)
        )

    async def fiat_withdrawal_history(self, **params: Any) -> Any:
        return await self._http.private(
            "GET", "/v1/account/fiatWithdrawalHistory", params=_clean(params)
        )

    async def deposit_history(self, **params: Any) -> Any:
        return await self._http.private(
            "GET", "/v1/account/depositHistory", params=_clean(params)
        )

    async def withdrawal_history(self, **params: Any) -> Any:
        return await self._http.private(
            "GET", "/v1/account/withdrawalHistory", params=_clean(params)
        )

    # --- Orders ---

    async def active_orders(
        self,
        symbol: str,
        *,
        page: int | None = None,
        count: int | None = None,
    ) -> OrderList:
        params = _clean({"symbol": symbol, "page": page, "count": count})
        data = await self._http.private("GET", "/v1/activeOrders", params=params)
        return OrderList.model_validate(data)

    async def order_info(self, order_id: int | str) -> Order:
        data = await self._http.private("GET", "/v1/orders", params={"orderId": str(order_id)})
        items = data.get("list", []) if isinstance(data, dict) else data
        return Order.model_validate(items[0])

    async def place_order(
        self,
        *,
        symbol: str,
        side: Side,
        execution_type: ExecutionType,
        size: Decimal | str,
        price: Decimal | str | None = None,
        losscut_price: Decimal | str | None = None,
        time_in_force: TimeInForce | None = None,
        client_order_id: str | None = None,
    ) -> int:
        """Returns the new order id (GMO returns it as the bare `data` field)."""
        body = _clean(
            {
                "symbol": symbol,
                "side": side,
                "executionType": execution_type,
                "size": size,
                "price": price,
                "losscutPrice": losscut_price,
                "timeInForce": time_in_force,
                "clientOrderId": client_order_id,
            }
        )
        data = await self._http.private("POST", "/v1/order", json_body=body)
        return int(data)

    async def change_order(
        self,
        *,
        order_id: int | str,
        price: Decimal | str,
        losscut_price: Decimal | str | None = None,
    ) -> None:
        body = _clean(
            {
                "orderId": str(order_id),
                "price": price,
                "losscutPrice": losscut_price,
            }
        )
        await self._http.private("POST", "/v1/changeOrder", json_body=body)

    async def cancel_order(self, order_id: int | str) -> None:
        await self._http.private(
            "POST", "/v1/cancelOrder", json_body={"orderId": str(order_id)}
        )

    async def cancel_orders(self, order_ids: list[int | str]) -> Any:
        body = {"orderIds": [str(x) for x in order_ids]}
        return await self._http.private("POST", "/v1/cancelOrders", json_body=body)

    async def cancel_bulk_order(
        self,
        *,
        symbols: list[str],
        side: Side | None = None,
        settle_type: str | None = None,
        desc: bool | None = None,
    ) -> Any:
        body = _clean(
            {
                "symbols": symbols,
                "side": side,
                "settleType": settle_type,
                "desc": desc,
            }
        )
        return await self._http.private("POST", "/v1/cancelBulkOrder", json_body=body)

    async def close_order(
        self,
        *,
        symbol: str,
        side: Side,
        execution_type: ExecutionType,
        settle_position: list[dict],
        price: Decimal | str | None = None,
        time_in_force: TimeInForce | None = None,
    ) -> int:
        body = _clean(
            {
                "symbol": symbol,
                "side": side,
                "executionType": execution_type,
                "price": price,
                "timeInForce": time_in_force,
                "settlePosition": settle_position,
            }
        )
        data = await self._http.private("POST", "/v1/closeOrder", json_body=body)
        return int(data)

    async def close_bulk_order(
        self,
        *,
        symbol: str,
        side: Side,
        execution_type: ExecutionType,
        size: Decimal | str,
        price: Decimal | str | None = None,
        time_in_force: TimeInForce | None = None,
    ) -> int:
        body = _clean(
            {
                "symbol": symbol,
                "side": side,
                "executionType": execution_type,
                "size": size,
                "price": price,
                "timeInForce": time_in_force,
            }
        )
        data = await self._http.private("POST", "/v1/closeBulkOrder", json_body=body)
        return int(data)

    async def change_losscut_price(
        self, *, position_id: int | str, losscut_price: Decimal | str
    ) -> None:
        body = _clean({"positionId": str(position_id), "losscutPrice": losscut_price})
        await self._http.private("POST", "/v1/changeLosscutPrice", json_body=body)

    # --- Executions ---

    async def executions(
        self,
        *,
        order_id: int | str | None = None,
        execution_id: int | str | None = None,
    ) -> list[Execution]:
        params = _clean(
            {
                "orderId": str(order_id) if order_id is not None else None,
                "executionId": str(execution_id) if execution_id is not None else None,
            }
        )
        data = await self._http.private("GET", "/v1/executions", params=params)
        items = data.get("list", []) if isinstance(data, dict) else (data or [])
        return [Execution.model_validate(d) for d in items]

    async def latest_executions(
        self,
        symbol: str,
        *,
        page: int | None = None,
        count: int | None = None,
    ) -> ExecutionList:
        params = _clean({"symbol": symbol, "page": page, "count": count})
        data = await self._http.private("GET", "/v1/latestExecutions", params=params)
        return ExecutionList.model_validate(data)

    # --- Positions ---

    async def open_positions(
        self,
        symbol: str,
        *,
        page: int | None = None,
        count: int | None = None,
    ) -> PositionList:
        params = _clean({"symbol": symbol, "page": page, "count": count})
        data = await self._http.private("GET", "/v1/openPositions", params=params)
        return PositionList.model_validate(data)

    async def position_summary(self, symbol: str | None = None) -> PositionSummaryList:
        params = {"symbol": symbol} if symbol else None
        data = await self._http.private("GET", "/v1/positionSummary", params=params)
        return PositionSummaryList.model_validate(data)

    async def positions(
        self,
        symbol: str | None = None,
        *,
        page: int | None = None,
        count: int | None = None,
    ) -> PositionList:
        """Closed-position history."""
        params = _clean({"symbol": symbol, "page": page, "count": count})
        data = await self._http.private("GET", "/v1/positions", params=params or None)
        return PositionList.model_validate(data)

    # --- Transfers ---

    async def transfer(
        self,
        *,
        amount: Decimal | str,
        transfer_type: str,
    ) -> Any:
        """`transfer_type`: 'DEPOSIT' (spot→margin) or 'WITHDRAWAL' (margin→spot)."""
        body = _clean({"amount": amount, "transferType": transfer_type})
        return await self._http.private("POST", "/v1/account/transfer", json_body=body)

    # --- WS auth ---

    async def create_ws_token(self) -> str:
        data = await self._http.private("POST", "/v1/ws-auth")
        return str(data)

    async def extend_ws_token(self, token: str) -> None:
        # GMO quirk: ws-auth PUT/DELETE send a JSON body but sign with empty body.
        await self._http.private(
            "PUT", "/v1/ws-auth", json_body={"token": token}, sign_body_override=""
        )

    async def delete_ws_token(self, token: str) -> None:
        await self._http.private(
            "DELETE", "/v1/ws-auth", json_body={"token": token}, sign_body_override=""
        )
