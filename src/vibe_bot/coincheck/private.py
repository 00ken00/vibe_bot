from __future__ import annotations

import os
from decimal import Decimal
from typing import Any, Callable

from .errors import AuthError
from .http import HttpClient
from .models import (
    Address,
    Balance,
    BankAccount,
    Borrow,
    CancelStatus,
    Deposit,
    LeverageBalance,
    Order,
    OrderList,
    OrderType,
    PositionList,
    Side,
    TransactionList,
)


def _stringify(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    return value


def _clean(d: dict) -> dict:
    return {k: _stringify(v) for k, v in d.items() if v is not None}


class PrivateClient:
    """REST client for Coincheck private endpoints. HMAC-signed.

    Reads `COINCHECK_API_KEY` / `COINCHECK_API_SECRET` from the environment by
    default. Pair codes are lowercase (e.g. `btc_jpy`).
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
            key = api_key or os.environ.get("COINCHECK_API_KEY")
            secret = api_secret or os.environ.get("COINCHECK_API_SECRET")
            if not key or not secret:
                raise AuthError(
                    -1,
                    message="COINCHECK_API_KEY / COINCHECK_API_SECRET not set",
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

    async def balance(self) -> Balance:
        data = await self._http.private("GET", "/api/accounts/balance")
        return Balance.model_validate(data)

    async def leverage_balance(self) -> LeverageBalance:
        data = await self._http.private("GET", "/api/accounts/leverage_balance")
        return LeverageBalance.model_validate(data)

    async def active_orders(self, pair: str | None = None) -> OrderList:
        params = {"pair": pair} if pair else None
        data = await self._http.private("GET", "/api/exchange/orders/opens", params=params)
        return OrderList.model_validate(data)

    async def place_order(
        self,
        *,
        pair: str,
        order_type: OrderType,
        rate: Decimal | str | None = None,
        amount: Decimal | str | None = None,
        market_buy_amount: Decimal | str | None = None,
        stop_loss_rate: Decimal | str | None = None,
    ) -> Order:
        body = _clean({
            "pair": pair,
            "order_type": order_type,
            "rate": rate,
            "amount": amount,
            "market_buy_amount": market_buy_amount,
            "stop_loss_rate": stop_loss_rate,
        })
        data = await self._http.private("POST", "/api/exchange/orders", json_body=body)
        return Order.model_validate(data)

    async def cancel_order(self, order_id: int | str) -> CancelStatus:
        data = await self._http.private("DELETE", f"/api/exchange/orders/{order_id}")
        return CancelStatus.model_validate(data)

    async def cancel_status(self, order_id: int | str) -> CancelStatus:
        data = await self._http.private(
            "GET", "/api/exchange/orders/cancel_status", params={"id": str(order_id)}
        )
        return CancelStatus.model_validate(data)

    async def order_status(self, order_id: int | str) -> dict[str, Any]:
        return await self._http.private("GET", f"/api/exchange/orders/{order_id}")

    async def transactions(
        self,
        *,
        limit: int | None = None,
        order: str | None = None,
        starting_after: int | str | None = None,
        ending_before: int | str | None = None,
    ) -> TransactionList:
        params = _clean({
            "limit": limit,
            "order": order,
            "starting_after": starting_after,
            "ending_before": ending_before,
        })
        data = await self._http.private(
            "GET", "/api/exchange/orders/transactions", params=params or None
        )
        return TransactionList.model_validate(data)

    async def transactions_pagination(self, **params: Any) -> dict[str, Any]:
        return await self._http.private(
            "GET", "/api/exchange/orders/transactions_pagination", params=_clean(params) or None
        )

    async def positions(self, **params: Any) -> PositionList:
        data = await self._http.private("GET", "/api/exchange/leverage/positions", params=_clean(params) or None)
        return PositionList.model_validate(data)

    async def bank_accounts(self) -> list[BankAccount]:
        data = await self._http.private("GET", "/api/bank_accounts")
        rows = data.get("data", data) if isinstance(data, dict) else data
        return [BankAccount.model_validate(d) for d in (rows or [])]

    async def create_bank_account(self, **body: Any) -> BankAccount:
        data = await self._http.private("POST", "/api/bank_accounts", json_body=_clean(body))
        return BankAccount.model_validate(data)

    async def delete_bank_account(self, bank_account_id: int | str) -> dict[str, Any]:
        return await self._http.private("DELETE", f"/api/bank_accounts/{bank_account_id}")

    async def deposit_history(self, **params: Any) -> list[Deposit]:
        data = await self._http.private("GET", "/api/deposit_money", params=_clean(params) or None)
        rows = data.get("deposits", data.get("data", data)) if isinstance(data, dict) else data
        return [Deposit.model_validate(d) for d in (rows or [])]

    async def quick_deposit(self, deposit_id: int | str) -> dict[str, Any]:
        return await self._http.private("POST", f"/api/deposit_money/{deposit_id}/fast")

    async def withdraws(self, **params: Any) -> dict[str, Any]:
        return await self._http.private("GET", "/api/withdraws", params=_clean(params) or None)

    async def create_withdrawal(
        self,
        *,
        bank_account_id: int | str,
        amount: Decimal | int | str,
        currency: str = "JPY",
        is_fast: bool | None = None,
    ) -> dict[str, Any]:
        body = _clean({
            "bank_account_id": bank_account_id,
            "amount": amount,
            "currency": currency,
            "is_fast": is_fast,
        })
        return await self._http.private("POST", "/api/withdraws", json_body=body)

    async def cancel_withdrawal(self, withdraw_id: int | str) -> dict[str, Any]:
        return await self._http.private("DELETE", f"/api/withdraws/{withdraw_id}")

    async def send_money(self, **body: Any) -> dict[str, Any]:
        return await self._http.private("POST", "/api/send_money", json_body=_clean(body))

    async def send_money_history(self, **params: Any) -> dict[str, Any]:
        return await self._http.private("GET", "/api/send_money", params=_clean(params) or None)

    async def deposit_address(self, currency: str) -> Address:
        data = await self._http.private("GET", f"/api/deposit_money/{currency}/address")
        return Address.model_validate(data)

    async def lending_borrows(self, **params: Any) -> list[Borrow]:
        data = await self._http.private("GET", "/api/lending/borrows/matches", params=_clean(params) or None)
        rows = data.get("matches", data.get("data", data)) if isinstance(data, dict) else data
        return [Borrow.model_validate(d) for d in (rows or [])]

    async def create_borrow(
        self, *, amount: Decimal | str, currency: str = "BTC"
    ) -> Borrow:
        data = await self._http.private(
            "POST",
            "/api/lending/borrows",
            json_body=_clean({"amount": amount, "currency": currency}),
        )
        return Borrow.model_validate(data)

    async def repay_borrow(self, borrow_id: int | str) -> dict[str, Any]:
        return await self._http.private("POST", f"/api/lending/borrows/{borrow_id}/repay")
