from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# bitFlyer uses uppercase strings for sides, order types, and product codes.
Side = Literal["BUY", "SELL"]
ChildOrderType = Literal["LIMIT", "MARKET"]
TimeInForce = Literal["GTC", "IOC", "FOK"]
ChildOrderState = Literal[
    "ACTIVE", "COMPLETED", "CANCELED", "EXPIRED", "REJECTED",
]
ParentOrderType = Literal["LIMIT", "MARKET", "STOP", "STOP_LIMIT", "TRAIL"]
OrderMethod = Literal["SIMPLE", "IFD", "OCO", "IFDOCO"]
ConditionType = Literal["LIMIT", "MARKET", "STOP", "STOP_LIMIT", "TRAIL"]
HealthState = Literal[
    "NORMAL", "BUSY", "VERY BUSY", "SUPER BUSY", "NO ORDER", "STOP",
]
BoardState = Literal[
    "RUNNING", "CLOSED", "STARTING", "PREOPEN", "CIRCUIT BREAK",
    "AWAITING SQ", "MATURED",
]
EventType = Literal[
    "ORDER", "ORDER_FAILED", "CANCEL", "CANCEL_FAILED",
    "EXECUTION", "EXPIRE", "TRIGGER", "COMPLETE",
]


class _Base(BaseModel):
    """All bitFlyer response objects share this config."""
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


# --- Public ---


class Market(_Base):
    product_code: str
    market_type: str | None = None
    alias: str | None = None


class BoardLevel(_Base):
    price: Decimal
    size: Decimal


class Board(_Base):
    mid_price: Decimal | None = None
    bids: list[BoardLevel] = Field(default_factory=list)
    asks: list[BoardLevel] = Field(default_factory=list)


class Ticker(_Base):
    product_code: str
    state: str | None = None
    timestamp: str
    tick_id: int | None = None
    best_bid: Decimal | None = None
    best_ask: Decimal | None = None
    best_bid_size: Decimal | None = None
    best_ask_size: Decimal | None = None
    total_bid_depth: Decimal | None = None
    total_ask_depth: Decimal | None = None
    market_bid_size: Decimal | None = None
    market_ask_size: Decimal | None = None
    ltp: Decimal | None = None
    volume: Decimal | None = None
    volume_by_product: Decimal | None = None


class Execution(_Base):
    id: int
    side: Side
    price: Decimal
    size: Decimal
    exec_date: str
    buy_child_order_acceptance_id: str | None = None
    sell_child_order_acceptance_id: str | None = None


class BoardStateInfo(_Base):
    health: HealthState | str | None = None
    state: BoardState | str
    data: dict | None = None


class Health(_Base):
    status: HealthState | str


class FundingRate(_Base):
    current_funding_rate: Decimal
    next_funding_rate_settledate: str | None = None


class CorporateLeverage(_Base):
    current_max: Decimal | None = None
    current_startdate: str | None = None
    next_max: Decimal | None = None
    next_startdate: str | None = None


# --- Private: account ---


class Permission(_Base):
    permission: str


class Balance(_Base):
    currency_code: str
    amount: Decimal
    available: Decimal


class Collateral(_Base):
    collateral: Decimal
    open_position_pnl: Decimal
    require_collateral: Decimal
    keep_rate: Decimal
    margin_call_amount: Decimal | None = None
    margin_call_due_date: str | None = None


class CollateralAccount(_Base):
    currency_code: str
    amount: Decimal


class Address(_Base):
    type: str
    currency_code: str
    address: str


class CoinIn(_Base):
    id: int
    order_id: str | None = None
    currency_code: str
    amount: Decimal
    address: str | None = None
    tx_hash: str | None = None
    status: str
    event_date: str


class CoinOut(_Base):
    id: int
    order_id: str | None = None
    currency_code: str
    amount: Decimal
    address: str | None = None
    tx_hash: str | None = None
    fee: Decimal | None = None
    additional_fee: Decimal | None = None
    status: str
    event_date: str


class BankAccount(_Base):
    id: int
    is_verified: bool
    bank_name: str | None = None
    branch_name: str | None = None
    account_type: str | None = None
    account_number: str | None = None
    account_name: str | None = None


class Deposit(_Base):
    id: int
    order_id: str | None = None
    currency_code: str
    amount: Decimal
    status: str
    event_date: str


class Withdrawal(_Base):
    id: int | None = None
    order_id: str | None = None
    currency_code: str | None = None
    amount: Decimal | None = None
    status: str | None = None
    event_date: str | None = None
    message_id: str | None = None


# --- Private: orders ---


class ChildOrderAck(_Base):
    """Response from POST /v1/me/sendchildorder."""
    child_order_acceptance_id: str


class ParentOrderAck(_Base):
    """Response from POST /v1/me/sendparentorder."""
    parent_order_acceptance_id: str


class ChildOrder(_Base):
    id: int
    child_order_id: str
    product_code: str
    side: Side
    child_order_type: ChildOrderType
    price: Decimal | None = None
    average_price: Decimal | None = None
    size: Decimal
    child_order_state: ChildOrderState
    expire_date: str
    child_order_date: str
    child_order_acceptance_id: str
    outstanding_size: Decimal
    cancel_size: Decimal
    executed_size: Decimal
    total_commission: Decimal
    time_in_force: TimeInForce | None = None


class PrivateExecution(_Base):
    id: int
    child_order_id: str
    side: Side
    price: Decimal
    size: Decimal
    commission: Decimal
    exec_date: str
    child_order_acceptance_id: str


class Position(_Base):
    product_code: str
    side: Side
    price: Decimal
    size: Decimal
    commission: Decimal
    swap_point_accumulate: Decimal
    require_collateral: Decimal
    open_date: str
    leverage: Decimal | None = None
    pnl: Decimal | None = None
    sfd: Decimal | None = None
    funding_fees: Decimal | None = None


class ParentOrderParameter(_Base):
    product_code: str
    condition_type: ConditionType
    side: Side
    size: Decimal
    price: Decimal | None = None
    trigger_price: Decimal | None = None
    offset: Decimal | None = None


class ParentOrder(_Base):
    id: int
    parent_order_id: str
    product_code: str
    side: Side
    parent_order_type: ParentOrderType
    price: Decimal | None = None
    average_price: Decimal | None = None
    size: Decimal
    parent_order_state: ChildOrderState
    expire_date: str
    parent_order_date: str
    parent_order_acceptance_id: str
    outstanding_size: Decimal
    cancel_size: Decimal
    executed_size: Decimal
    total_commission: Decimal


class ParentOrderDetail(_Base):
    id: int
    parent_order_id: str
    order_method: OrderMethod
    expire_date: str
    time_in_force: TimeInForce | None = None
    parameters: list[ParentOrderParameter] = Field(default_factory=list)
    parent_order_acceptance_id: str


class BalanceHistoryEntry(_Base):
    id: int
    trade_date: str
    event_date: str
    product_code: str | None = None
    currency_code: str
    trade_type: str
    price: Decimal | None = None
    amount: Decimal
    quantity: Decimal | None = None
    commission: Decimal | None = None
    balance: Decimal
    order_id: str | None = None


class CollateralHistoryEntry(_Base):
    id: int
    currency_code: str
    change: Decimal
    amount: Decimal
    reason_code: str | None = None
    date: str


class TradingCommission(_Base):
    commission_rate: Decimal
