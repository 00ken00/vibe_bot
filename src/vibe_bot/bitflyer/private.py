from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Callable

from .errors import AuthError
from .http import HttpClient
from .models import (
    Address,
    BalanceHistoryEntry,
    BankAccount,
    Balance,
    ChildOrder,
    ChildOrderAck,
    ChildOrderType,
    CoinIn,
    CoinOut,
    Collateral,
    CollateralAccount,
    CollateralHistoryEntry,
    Deposit,
    OrderMethod,
    ParentOrder,
    ParentOrderAck,
    ParentOrderDetail,
    ParentOrderParameter,
    Permission,
    Position,
    PrivateExecution,
    Side,
    TimeInForce,
    TradingCommission,
    Withdrawal,
)


def _decimalize(value: Any) -> Any:
    """bitFlyer accepts numbers (not strings) in JSON bodies — convert Decimal
    losslessly via `format(d, 'f')` so 0.0001 doesn't round-trip as 1e-4."""
    if isinstance(value, Decimal):
        s = format(value, "f")
        return float(s) if "." in s or "e" in s.lower() else int(s)
    if isinstance(value, float):
        d = Decimal(str(value))
        s = format(d, "f")
        return float(s) if "." in s else int(s)
    return value


def _clean(d: dict) -> dict:
    return {k: _decimalize(v) for k, v in d.items() if v is not None}


class PrivateClient:
    """REST client for bitFlyer private endpoints. HMAC-signed.

    Reads `BITFLYER_API_KEY` / `BITFLYER_API_SECRET` from the environment by
    default; pass `api_key` / `api_secret` explicitly to override. Product
    codes are uppercase (e.g. `BTC_JPY`); sides are uppercase `BUY` / `SELL`.
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
            key = api_key or os.environ.get("BITFLYER_API_KEY")
            secret = api_secret or os.environ.get("BITFLYER_API_SECRET")
            if not key or not secret:
                raise AuthError(
                    -201,
                    message="BITFLYER_API_KEY / BITFLYER_API_SECRET not set",
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

    async def permissions(self) -> list[str]:
        data = await self._http.private("GET", "/me/getpermissions")
        if isinstance(data, list) and data and isinstance(data[0], str):
            return list(data)
        return [Permission.model_validate({"permission": p}).permission for p in (data or [])]

    async def balance(self) -> list[Balance]:
        data = await self._http.private("GET", "/me/getbalance")
        return [Balance.model_validate(d) for d in (data or [])]

    async def collateral(self) -> Collateral:
        data = await self._http.private("GET", "/me/getcollateral")
        return Collateral.model_validate(data)

    async def collateral_accounts(self) -> list[CollateralAccount]:
        data = await self._http.private("GET", "/me/getcollateralaccounts")
        return [CollateralAccount.model_validate(d) for d in (data or [])]

    async def addresses(self) -> list[Address]:
        data = await self._http.private("GET", "/me/getaddresses")
        return [Address.model_validate(d) for d in (data or [])]

    async def coin_ins(
        self,
        *,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
    ) -> list[CoinIn]:
        params = _clean({"count": count, "before": before, "after": after})
        data = await self._http.private(
            "GET", "/me/getcoinins", params=params or None
        )
        return [CoinIn.model_validate(d) for d in (data or [])]

    async def coin_outs(
        self,
        *,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
    ) -> list[CoinOut]:
        params = _clean({"count": count, "before": before, "after": after})
        data = await self._http.private(
            "GET", "/me/getcoinouts", params=params or None
        )
        return [CoinOut.model_validate(d) for d in (data or [])]

    async def bank_accounts(self) -> list[BankAccount]:
        data = await self._http.private("GET", "/me/getbankaccounts")
        return [BankAccount.model_validate(d) for d in (data or [])]

    async def deposits(
        self,
        *,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
    ) -> list[Deposit]:
        params = _clean({"count": count, "before": before, "after": after})
        data = await self._http.private(
            "GET", "/me/getdeposits", params=params or None
        )
        return [Deposit.model_validate(d) for d in (data or [])]

    async def withdraw(
        self,
        *,
        currency_code: str,
        bank_account_id: int,
        amount: Decimal | int | float,
        code: str | None = None,
    ) -> Withdrawal:
        body = _clean({
            "currency_code": currency_code,
            "bank_account_id": bank_account_id,
            "amount": amount,
            "code": code,
        })
        data = await self._http.private("POST", "/me/withdraw", json_body=body)
        return Withdrawal.model_validate(data)

    async def withdrawals(
        self,
        *,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
        message_id: str | None = None,
    ) -> list[Withdrawal]:
        params = _clean({
            "count": count, "before": before, "after": after,
            "message_id": message_id,
        })
        data = await self._http.private(
            "GET", "/me/getwithdrawals", params=params or None
        )
        return [Withdrawal.model_validate(d) for d in (data or [])]

    # --- Child orders ---

    async def send_child_order(
        self,
        *,
        product_code: str,
        child_order_type: ChildOrderType,
        side: Side,
        size: Decimal | int | float,
        price: Decimal | int | float | None = None,
        minute_to_expire: int | None = None,
        time_in_force: TimeInForce | None = None,
    ) -> ChildOrderAck:
        if child_order_type == "LIMIT" and price is None:
            raise ValueError("LIMIT orders require a price")
        body = _clean({
            "product_code": product_code,
            "child_order_type": child_order_type,
            "side": side,
            "price": price,
            "size": size,
            "minute_to_expire": minute_to_expire,
            "time_in_force": time_in_force,
        })
        data = await self._http.private("POST", "/me/sendchildorder", json_body=body)
        return ChildOrderAck.model_validate(data)

    async def cancel_child_order(
        self,
        *,
        product_code: str,
        child_order_id: str | None = None,
        child_order_acceptance_id: str | None = None,
    ) -> None:
        if not (child_order_id or child_order_acceptance_id):
            raise ValueError(
                "pass either child_order_id or child_order_acceptance_id"
            )
        body = _clean({
            "product_code": product_code,
            "child_order_id": child_order_id,
            "child_order_acceptance_id": child_order_acceptance_id,
        })
        await self._http.private("POST", "/me/cancelchildorder", json_body=body)

    async def cancel_all_child_orders(self, *, product_code: str) -> None:
        await self._http.private(
            "POST", "/me/cancelallchildorders",
            json_body={"product_code": product_code},
        )

    async def child_orders(
        self,
        *,
        product_code: str,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
        child_order_state: str | None = None,
        child_order_id: str | None = None,
        child_order_acceptance_id: str | None = None,
        parent_order_id: str | None = None,
    ) -> list[ChildOrder]:
        params = _clean({
            "product_code": product_code,
            "count": count, "before": before, "after": after,
            "child_order_state": child_order_state,
            "child_order_id": child_order_id,
            "child_order_acceptance_id": child_order_acceptance_id,
            "parent_order_id": parent_order_id,
        })
        data = await self._http.private(
            "GET", "/me/getchildorders", params=params or None
        )
        return [ChildOrder.model_validate(d) for d in (data or [])]

    async def executions(
        self,
        *,
        product_code: str,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
        child_order_id: str | None = None,
        child_order_acceptance_id: str | None = None,
    ) -> list[PrivateExecution]:
        params = _clean({
            "product_code": product_code,
            "count": count, "before": before, "after": after,
            "child_order_id": child_order_id,
            "child_order_acceptance_id": child_order_acceptance_id,
        })
        data = await self._http.private(
            "GET", "/me/getexecutions", params=params or None
        )
        return [PrivateExecution.model_validate(d) for d in (data or [])]

    # --- Parent (special) orders ---

    async def send_parent_order(
        self,
        *,
        order_method: OrderMethod,
        parameters: list[ParentOrderParameter | dict[str, Any]],
        minute_to_expire: int | None = None,
        time_in_force: TimeInForce | None = None,
    ) -> ParentOrderAck:
        params_payload: list[dict[str, Any]] = []
        for p in parameters:
            if isinstance(p, ParentOrderParameter):
                model: ParentOrderParameter = p
                d: dict[str, Any] = model.model_dump(exclude_none=True)
            else:
                d = dict(p)
            params_payload.append(_clean(d))
        body = _clean({
            "order_method": order_method,
            "minute_to_expire": minute_to_expire,
            "time_in_force": time_in_force,
        })
        body["parameters"] = params_payload
        data = await self._http.private("POST", "/me/sendparentorder", json_body=body)
        return ParentOrderAck.model_validate(data)

    async def cancel_parent_order(
        self,
        *,
        product_code: str,
        parent_order_id: str | None = None,
        parent_order_acceptance_id: str | None = None,
    ) -> None:
        if not (parent_order_id or parent_order_acceptance_id):
            raise ValueError(
                "pass either parent_order_id or parent_order_acceptance_id"
            )
        body = _clean({
            "product_code": product_code,
            "parent_order_id": parent_order_id,
            "parent_order_acceptance_id": parent_order_acceptance_id,
        })
        await self._http.private("POST", "/me/cancelparentorder", json_body=body)

    async def parent_orders(
        self,
        *,
        product_code: str,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
        parent_order_state: str | None = None,
    ) -> list[ParentOrder]:
        params = _clean({
            "product_code": product_code,
            "count": count, "before": before, "after": after,
            "parent_order_state": parent_order_state,
        })
        data = await self._http.private(
            "GET", "/me/getparentorders", params=params or None
        )
        return [ParentOrder.model_validate(d) for d in (data or [])]

    async def parent_order(
        self,
        *,
        parent_order_id: str | None = None,
        parent_order_acceptance_id: str | None = None,
    ) -> ParentOrderDetail:
        if not (parent_order_id or parent_order_acceptance_id):
            raise ValueError(
                "pass either parent_order_id or parent_order_acceptance_id"
            )
        params = _clean({
            "parent_order_id": parent_order_id,
            "parent_order_acceptance_id": parent_order_acceptance_id,
        })
        data = await self._http.private(
            "GET", "/me/getparentorder", params=params or None
        )
        return ParentOrderDetail.model_validate(data)

    # --- Positions / history / commission ---

    async def positions(self, *, product_code: str = "FX_BTC_JPY") -> list[Position]:
        data = await self._http.private(
            "GET", "/me/getpositions", params={"product_code": product_code}
        )
        return [Position.model_validate(d) for d in (data or [])]

    async def balance_history(
        self,
        *,
        currency_code: str,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
    ) -> list[BalanceHistoryEntry]:
        params = _clean({
            "currency_code": currency_code,
            "count": count, "before": before, "after": after,
        })
        data = await self._http.private(
            "GET", "/me/getbalancehistory", params=params or None
        )
        return [BalanceHistoryEntry.model_validate(d) for d in (data or [])]

    async def collateral_history(
        self,
        *,
        count: int | None = None,
        before: int | None = None,
        after: int | None = None,
    ) -> list[CollateralHistoryEntry]:
        params = _clean({"count": count, "before": before, "after": after})
        data = await self._http.private(
            "GET", "/me/getcollateralhistory", params=params or None
        )
        return [CollateralHistoryEntry.model_validate(d) for d in (data or [])]

    async def trading_commission(
        self, *, product_code: str = "BTC_JPY"
    ) -> TradingCommission:
        data = await self._http.private(
            "GET", "/me/gettradingcommission",
            params={"product_code": product_code},
        )
        return TradingCommission.model_validate(data)
