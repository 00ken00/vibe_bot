from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Callable

from .errors import AuthError
from .http import HttpClient
from .models import (
    Assets,
    DepositList,
    MarginPositions,
    MarginStatusInfo,
    Order,
    OrderList,
    OrderType,
    Side,
    SubscribeToken,
    TradeList,
    Withdrawal,
    WithdrawalAccountList,
    WithdrawalList,
)


def _stringify(value: Any) -> Any:
    """bitbank accepts numbers as strings to avoid float precision issues."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    return value


def _clean(d: dict) -> dict:
    return {k: _stringify(v) for k, v in d.items() if v is not None}


class PrivateClient:
    """REST client for bitbank private endpoints. HMAC-signed.

    Reads `BITBANK_API_KEY` / `BITBANK_API_SECRET` from the environment by
    default; pass `api_key` / `api_secret` explicitly to override. Pair codes
    are lowercase (e.g. `btc_jpy`); sides are lowercase `buy` / `sell`.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        http: HttpClient | None = None,
        private_trace: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        if http is not None:
            self._http = http
            self._owns_http = False
        else:
            key = api_key or os.environ.get("BITBANK_API_KEY")
            secret = api_secret or os.environ.get("BITBANK_API_SECRET")
            if not key or not secret:
                raise AuthError(
                    20003,
                    message="BITBANK_API_KEY / BITBANK_API_SECRET not set",
                )
            self._http = HttpClient(
                api_key=key,
                api_secret=secret,
                private_trace=private_trace,
            )
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

    async def assets(self) -> Assets:
        data = await self._http.private("GET", "/user/assets")
        return Assets.model_validate(data)

    # --- Orders ---

    async def order_info(self, *, pair: str, order_id: int | str) -> Order:
        data = await self._http.private(
            "GET", "/user/spot/order", params={"pair": pair, "order_id": str(order_id)}
        )
        return Order.model_validate(data)

    async def active_orders(
        self,
        *,
        pair: str | None = None,
        count: int | None = None,
        from_id: int | None = None,
        end_id: int | None = None,
        since: int | None = None,
        end: int | None = None,
    ) -> OrderList:
        params = _clean({
            "pair": pair, "count": count, "from_id": from_id, "end_id": end_id,
            "since": since, "end": end,
        })
        data = await self._http.private(
            "GET", "/user/spot/active_orders", params=params or None
        )
        return OrderList.model_validate(data)

    async def place_order(
        self,
        *,
        pair: str,
        side: Side,
        order_type: OrderType,
        amount: Decimal | str | None = None,
        price: Decimal | str | None = None,
        post_only: bool | None = None,
        trigger_price: Decimal | str | None = None,
        position_side: str | None = None,
    ) -> Order:
        body = _clean({
            "pair": pair,
            "side": side,
            "type": order_type,
            "amount": amount,
            "price": price,
            "post_only": post_only,
            "trigger_price": trigger_price,
            "position_side": position_side,
        })
        data = await self._http.private("POST", "/user/spot/order", json_body=body)
        return Order.model_validate(data)

    async def cancel_order(self, *, pair: str, order_id: int | str) -> Order:
        body = {"pair": pair, "order_id": int(order_id)}
        data = await self._http.private("POST", "/user/spot/cancel_order", json_body=body)
        return Order.model_validate(data)

    async def cancel_orders(
        self, *, pair: str, order_ids: list[int | str]
    ) -> OrderList:
        """Up to 30 ids per call."""
        if len(order_ids) > 30:
            raise ValueError("bitbank caps cancel_orders at 30 ids per request")
        body = {"pair": pair, "order_ids": [int(x) for x in order_ids]}
        data = await self._http.private("POST", "/user/spot/cancel_orders", json_body=body)
        return OrderList.model_validate(data)

    async def orders_info(
        self, *, pair: str, order_ids: list[int | str]
    ) -> OrderList:
        body = {"pair": pair, "order_ids": [int(x) for x in order_ids]}
        data = await self._http.private("POST", "/user/spot/orders_info", json_body=body)
        return OrderList.model_validate(data)

    # --- Trade history ---

    async def trade_history(
        self,
        *,
        pair: str | None = None,
        count: int | None = None,
        order_id: int | str | None = None,
        since: int | None = None,
        end: int | None = None,
        order: str | None = None,
    ) -> TradeList:
        params = _clean({
            "pair": pair, "count": count,
            "order_id": str(order_id) if order_id is not None else None,
            "since": since, "end": end, "order": order,
        })
        data = await self._http.private(
            "GET", "/user/spot/trade_history", params=params or None
        )
        return TradeList.model_validate(data)

    # --- Deposits ---

    async def deposit_history(
        self,
        *,
        asset: str | None = None,
        count: int | None = None,
        since: int | None = None,
        end: int | None = None,
    ) -> DepositList:
        params = _clean({"asset": asset, "count": count, "since": since, "end": end})
        data = await self._http.private(
            "GET", "/user/deposit_history", params=params or None
        )
        return DepositList.model_validate(data)

    async def unconfirmed_deposits(self) -> Any:
        return await self._http.private("GET", "/user/unconfirmed_deposits")

    async def deposit_originators(self) -> Any:
        return await self._http.private("GET", "/user/deposit_originators")

    async def confirm_deposits(self, deposits: list[dict]) -> Any:
        return await self._http.private(
            "POST", "/user/confirm_deposits", json_body={"deposits": deposits}
        )

    async def confirm_deposits_all(self, originator_uuid: str) -> Any:
        return await self._http.private(
            "POST",
            "/user/confirm_deposits_all",
            json_body={"originator_uuid": originator_uuid},
        )

    # --- Withdrawals ---

    async def withdrawal_account(self, asset: str) -> WithdrawalAccountList:
        data = await self._http.private(
            "GET", "/user/withdrawal_account", params={"asset": asset}
        )
        return WithdrawalAccountList.model_validate(data)

    async def request_withdrawal(
        self,
        *,
        asset: str,
        uuid: str,
        amount: Decimal | str,
        otp_token: str | None = None,
        sms_token: str | None = None,
    ) -> Withdrawal:
        body = _clean({
            "asset": asset, "uuid": uuid, "amount": amount,
            "otp_token": otp_token, "sms_token": sms_token,
        })
        data = await self._http.private(
            "POST", "/user/request_withdrawal", json_body=body
        )
        return Withdrawal.model_validate(data)

    async def withdrawal_history(
        self,
        *,
        asset: str | None = None,
        count: int | None = None,
        since: int | None = None,
        end: int | None = None,
    ) -> WithdrawalList:
        params = _clean({"asset": asset, "count": count, "since": since, "end": end})
        data = await self._http.private(
            "GET", "/user/withdrawal_history", params=params or None
        )
        return WithdrawalList.model_validate(data)

    # --- Margin ---

    async def margin_status(self) -> MarginStatusInfo:
        data = await self._http.private("GET", "/user/margin/status")
        return MarginStatusInfo.model_validate(data)

    async def margin_positions(self) -> MarginPositions:
        data = await self._http.private("GET", "/user/margin/positions")
        return MarginPositions.model_validate(data)

    # --- Private stream subscribe ---

    async def subscribe(self) -> SubscribeToken:
        """Mint a PubNub channel + token for the private stream.

        bitbank's private real-time channel rides PubNub, not a native WebSocket.
        The returned token lasts ~12 hours; refresh by calling this again.
        """
        data = await self._http.private("GET", "/user/subscribe")
        return SubscribeToken.model_validate(data)
