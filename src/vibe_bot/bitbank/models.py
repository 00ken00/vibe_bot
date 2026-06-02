from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# bitbank uses lowercase strings for sides and order types.
Side = Literal["buy", "sell"]
OrderType = Literal[
    "limit", "market", "stop", "stop_limit", "take_profit", "stop_loss", "losscut",
]
OrderStatus = Literal[
    "INACTIVE", "UNFILLED", "PARTIALLY_FILLED", "FULLY_FILLED",
    "CANCELED_UNFILLED", "CANCELED_PARTIALLY_FILLED", "REJECTED",
]
PositionSide = Literal["long", "short"]
MakerTaker = Literal["maker", "taker"]
SpotStatus = Literal["NORMAL", "BUSY", "VERY_BUSY", "HALT"]
CandleType = Literal[
    "1min", "5min", "15min", "30min",
    "1hour", "4hour", "8hour", "12hour",
    "1day", "1week", "1month",
]
CircuitMode = Literal[
    "NONE", "CIRCUIT_BREAK", "FULL_RANGE_CIRCUIT_BREAK", "RESUMPTION", "LISTING",
]
DepositStatus = Literal["FOUND", "CONFIRMED", "DONE"]
WithdrawalStatus = Literal[
    "CONFIRMING", "EXAMINING", "SENDING", "DONE", "REJECTED", "CANCELED", "CONFIRM_TIMEOUT",
]
MarginStatus = Literal["NORMAL", "LOSSCUT", "CALL", "DEBT", "SETTLED"]


class _Base(BaseModel):
    """All bitbank response objects share this config."""
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


# --- Public ---


class Ticker(_Base):
    pair: str | None = None
    # bitbank returns null sell/buy on illiquid pairs (no resting order on a side).
    sell: Decimal | None = None
    buy: Decimal | None = None
    high: Decimal | None = None
    low: Decimal | None = None
    open: Decimal | None = None
    last: Decimal | None = None
    vol: Decimal
    timestamp: int


class Depth(_Base):
    asks: list[list[Decimal]]
    bids: list[list[Decimal]]
    asks_over: Decimal | None = None
    bids_under: Decimal | None = None
    asks_under: Decimal | None = None
    bids_over: Decimal | None = None
    ask_market: Decimal | None = None
    bid_market: Decimal | None = None
    timestamp: int
    sequence_id: int | str | None = Field(default=None, alias="sequenceId")


class Transaction(_Base):
    transaction_id: int
    side: Side
    price: Decimal
    amount: Decimal
    executed_at: int


class TransactionList(_Base):
    transactions: list[Transaction] = Field(default_factory=list)


class Candlestick(_Base):
    """One bitbank candle row: [open, high, low, close, volume, unix_ms]."""
    type: CandleType
    ohlcv: list[list[Decimal | int]]
    timestamp: int | None = None


class CircuitBreakInfo(_Base):
    mode: CircuitMode
    estimated_itayose_price: Decimal | None = None
    estimated_itayose_amount: Decimal | None = None
    itayose_upper_price: Decimal | None = None
    itayose_lower_price: Decimal | None = None
    upper_trigger_price: Decimal | None = None
    lower_trigger_price: Decimal | None = None
    fee_type: str
    reopen_timestamp: int | None = None
    timestamp: int


class SpotPairStatus(_Base):
    pair: str
    status: SpotStatus
    min_amount: Decimal


# --- Private: account ---


class Asset(_Base):
    asset: str
    free_amount: Decimal
    amount_precision: int
    onhand_amount: Decimal
    locked_amount: Decimal
    withdrawing_amount: Decimal
    withdrawal_fee: Decimal | dict | None = None
    stop_deposit: bool
    stop_withdrawal: bool
    network_list: list[dict] | None = None
    collateral_ratio: Decimal | None = None


class Assets(_Base):
    assets: list[Asset] = Field(default_factory=list)


# --- Private: orders ---


class Order(_Base):
    order_id: int
    pair: str
    side: Side
    position_side: PositionSide | None = None
    type: OrderType
    start_amount: Decimal
    remaining_amount: Decimal
    executed_amount: Decimal
    price: Decimal | None = None
    post_only: bool | None = None
    user_cancelable: bool | None = None
    average_price: Decimal | None = None
    ordered_at: int
    executed_at: int | None = None
    expire_at: int | None = None
    triggered_at: int | None = None
    trigger_price: Decimal | None = None
    status: OrderStatus
    canceled_at: int | None = None


class OrderList(_Base):
    orders: list[Order] = Field(default_factory=list)


# --- Private: trades ---


class Trade(_Base):
    trade_id: int
    pair: str
    order_id: int
    side: Side
    position_side: PositionSide | None = None
    type: OrderType
    amount: Decimal
    price: Decimal
    maker_taker: MakerTaker
    fee_amount_base: Decimal
    fee_amount_quote: Decimal
    profit_loss: Decimal | None = None
    interest: Decimal | None = None
    executed_at: int


class TradeList(_Base):
    trades: list[Trade] = Field(default_factory=list)


# --- Private: deposits / withdrawals ---


class Deposit(_Base):
    uuid: str
    address: str | None = None
    asset: str
    network: str | None = None
    amount: Decimal
    txid: str | None = None
    status: DepositStatus
    found_at: int | None = None
    confirmed_at: int | None = None


class DepositList(_Base):
    deposits: list[Deposit] = Field(default_factory=list)


class WithdrawalAccount(_Base):
    uuid: str
    label: str
    network: str | None = None
    address: str


class WithdrawalAccountList(_Base):
    accounts: list[WithdrawalAccount] = Field(default_factory=list)


class Withdrawal(_Base):
    uuid: str
    asset: str
    amount: Decimal
    fee: Decimal | None = None
    network: str | None = None
    label: str | None = None
    address: str | None = None
    txid: str | None = None
    status: WithdrawalStatus
    requested_at: int | None = None


class WithdrawalList(_Base):
    withdrawals: list[Withdrawal] = Field(default_factory=list)


# --- Private: margin ---


class MarginAvailableBalance(_Base):
    pair: str
    long: Decimal | None = None
    short: Decimal | None = None


class MarginStatusInfo(_Base):
    status: MarginStatus
    available_balances: list[MarginAvailableBalance] = Field(default_factory=list)


class MarginPosition(_Base):
    pair: str
    position_side: PositionSide
    open_amount: Decimal | None = None
    average_price: Decimal | None = None
    unrealized_fee_amount: Decimal | None = None
    unrealized_interest_amount: Decimal | None = None


class MarginPositions(_Base):
    notice: dict | None = None
    payables: dict | None = None
    positions: list[MarginPosition] = Field(default_factory=list)
    losscut_threshold: dict | None = None


# --- Private: stream subscribe token ---


class SubscribeToken(_Base):
    pubnub_channel: str
    pubnub_token: str
