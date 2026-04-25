from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Side = Literal["BUY", "SELL"]
ExecutionType = Literal["MARKET", "LIMIT", "STOP"]
TimeInForce = Literal["FAK", "FAS", "FOK", "SOK"]
OrderStatus = Literal[
    "WAITING", "ORDERED", "MODIFYING", "CANCELLING", "CANCELED",
    "EXECUTED", "EXPIRED",
]
SettleType = Literal["OPEN", "CLOSE", "LOSS_CUT"]
ServiceStatus = Literal["MAINTENANCE", "PREOPEN", "OPEN"]
KlineInterval = Literal[
    "1min", "5min", "10min", "15min", "30min",
    "1hour", "4hour", "8hour", "12hour",
    "1day", "1week", "1month",
]


class _Base(BaseModel):
    """All GMO response objects share this config."""
    model_config = ConfigDict(extra="ignore", frozen=True)


class Status(_Base):
    status: ServiceStatus


class Ticker(_Base):
    symbol: str
    ask: Decimal
    bid: Decimal
    high: Decimal
    last: Decimal
    low: Decimal
    volume: Decimal
    timestamp: str


class OrderbookLevel(_Base):
    price: Decimal
    size: Decimal


class Orderbook(_Base):
    symbol: str
    asks: list[OrderbookLevel]
    bids: list[OrderbookLevel]
    timestamp: str | None = None


class Trade(_Base):
    price: Decimal
    side: Side
    size: Decimal
    timestamp: str


class Pagination(_Base):
    current_page: int = Field(alias="currentPage")
    count: int


class TradeList(_Base):
    pagination: Pagination
    items: list[Trade] = Field(default_factory=list, alias="list")


class Kline(_Base):
    open_time: str = Field(alias="openTime")
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class SymbolRule(_Base):
    symbol: str
    min_order_size: Decimal = Field(alias="minOrderSize")
    max_order_size: Decimal = Field(alias="maxOrderSize")
    size_step: Decimal = Field(alias="sizeStep")
    tick_size: Decimal = Field(alias="tickSize")
    taker_fee: Decimal = Field(alias="takerFee")
    maker_fee: Decimal = Field(alias="makerFee")


# --- Private ---


class Margin(_Base):
    actual_profit_loss: Decimal = Field(alias="actualProfitLoss")
    available_amount: Decimal = Field(alias="availableAmount")
    margin: Decimal
    margin_call_status: str = Field(alias="marginCallStatus")
    margin_maintenance_rate: Decimal = Field(alias="marginMaintenanceRate")
    profit_loss: Decimal = Field(alias="profitLoss")


class Asset(_Base):
    amount: Decimal
    available: Decimal
    conversion_rate: Decimal = Field(alias="conversionRate")
    symbol: str


class TradingVolume(_Base):
    trading_volume: Decimal = Field(alias="tradingVolume")
    point: Decimal | None = None


class Order(_Base):
    root_order_id: int = Field(alias="rootOrderId")
    order_id: int = Field(alias="orderId")
    symbol: str
    side: Side
    order_type: str = Field(alias="orderType")
    execution_type: ExecutionType = Field(alias="executionType")
    settle_type: SettleType = Field(alias="settleType")
    size: Decimal
    executed_size: Decimal = Field(alias="executedSize")
    price: Decimal | None = None
    losscut_price: Decimal | None = Field(default=None, alias="losscutPrice")
    status: OrderStatus
    time_in_force: TimeInForce | None = Field(default=None, alias="timeInForce")
    timestamp: str


class OrderList(_Base):
    pagination: Pagination | None = None
    items: list[Order] = Field(default_factory=list, alias="list")


class Execution(_Base):
    execution_id: int = Field(alias="executionId")
    order_id: int = Field(alias="orderId")
    position_id: int | None = Field(default=None, alias="positionId")
    symbol: str
    side: Side
    settle_type: SettleType = Field(alias="settleType")
    size: Decimal
    price: Decimal
    loss_gain: Decimal = Field(alias="lossGain")
    fee: Decimal
    timestamp: str


class ExecutionList(_Base):
    pagination: Pagination | None = None
    items: list[Execution] = Field(default_factory=list, alias="list")


class Position(_Base):
    position_id: int = Field(alias="positionId")
    symbol: str
    side: Side
    size: Decimal
    orderd_size: Decimal = Field(alias="orderdSize")
    price: Decimal
    loss_gain: Decimal = Field(alias="lossGain")
    leverage: Decimal
    losscut_price: Decimal = Field(alias="losscutPrice")
    timestamp: str


class PositionList(_Base):
    pagination: Pagination | None = None
    items: list[Position] = Field(default_factory=list, alias="list")


class PositionSummary(_Base):
    average_position_rate: Decimal = Field(alias="averagePositionRate")
    position_loss_gain: Decimal = Field(alias="positionLossGain")
    side: Side
    sum_order_quantity: Decimal = Field(alias="sumOrderQuantity")
    sum_position_quantity: Decimal = Field(alias="sumPositionQuantity")
    symbol: str


class PositionSummaryList(_Base):
    items: list[PositionSummary] = Field(default_factory=list, alias="list")


class WsAuthToken(_Base):
    """Returned by POST /v1/ws-auth as a raw string in `data`. Wrapper for ergonomics."""
    token: str
