from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Side = Literal["buy", "sell"]
OrderType = Literal["buy", "sell", "market_buy", "market_sell"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


class Ticker(_Base):
    last: Decimal
    bid: Decimal
    ask: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal
    timestamp: int


class Trade(_Base):
    id: int
    amount: Decimal
    rate: Decimal
    pair: str
    order_type: Side | None = None
    created_at: str


class Orderbook(_Base):
    asks: list[list[Decimal]]
    bids: list[list[Decimal]]


class ExchangeAvailability(_Base):
    order: bool | None = None
    market_order: bool | None = None
    cancel: bool | None = None


class ExchangeStatus(_Base):
    pair: str
    state: str | None = None
    status: str | None = None
    timestamp: int | None = None
    availability: ExchangeAvailability | None = None


class Rate(_Base):
    rate: Decimal
    price: Decimal | None = None
    amount: Decimal | None = None


class Balance(_Base):
    balances: dict[str, Decimal] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _collect_balances(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {
            "balances": {
                k: v for k, v in data.items()
                if k not in {"success", "error", "message"}
            }
        }


class LeverageBalance(_Base):
    margin: dict[str, Decimal] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _collect_margin(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {
            "margin": {
                k: v for k, v in data.items()
                if k not in {"success", "error", "message"}
            }
        }


class Order(_Base):
    id: int
    order_type: str | None = None
    rate: Decimal | None = None
    pair: str | None = None
    pending_amount: Decimal | None = None
    pending_market_buy_amount: Decimal | None = None
    stop_loss_rate: Decimal | None = None
    created_at: str | None = None


class OrderList(_Base):
    orders: list[Order] = Field(default_factory=list)


class CancelStatus(_Base):
    id: int
    cancel: bool | None = None
    created_at: str | None = None


class Transaction(_Base):
    id: int
    order_id: int | None = None
    created_at: str
    funds: dict[str, Decimal] | None = None
    pair: str | None = None
    rate: Decimal | None = None
    fee_currency: str | None = None
    fee: Decimal | None = None
    liquidity: str | None = None
    side: Side | None = None


class TransactionList(_Base):
    transactions: list[Transaction] = Field(default_factory=list)


class Position(_Base):
    id: int
    pair: str
    status: str
    created_at: str
    closed_at: str | None = None
    open_rate: Decimal | None = None
    closed_rate: Decimal | None = None
    amount: Decimal
    all_amount: Decimal | None = None
    side: Side
    pl: Decimal | None = None
    new_order: Order | None = None
    close_orders: list[Order] | None = None


class PositionList(_Base):
    data: list[Position] = Field(default_factory=list)


class BankAccount(_Base):
    id: int
    bank_name: str | None = None
    branch_name: str | None = None
    bank_account_type: str | None = None
    number: str | None = None
    name: str | None = None


class Address(_Base):
    address: str | None = None
    currency: str | None = None


class Deposit(_Base):
    id: int | None = None
    amount: Decimal
    currency: str
    address: str | None = None
    status: str | None = None
    confirmed_at: str | None = None
    created_at: str | None = None


class Borrow(_Base):
    id: int
    amount: Decimal
    currency: str
    interest_rate: Decimal | None = None
    deadline: str | None = None
    created_at: str | None = None
