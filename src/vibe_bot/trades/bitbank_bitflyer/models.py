from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from vibe_bot.bitbank.models import PositionSide as BitbankPositionSide
from vibe_bot.bitbank.models import Side as BitbankSide


class BotAction(Enum):
    """Typed strategy status exposed to logs and the web monitor."""

    IDLE = "idle"  # No trade target is active.
    WAITING_FOR_QUOTES = "waiting_for_quotes"  # Waiting for order-book data.
    MAINTAIN_BUY = "maintain_buy"  # Keeping the current BUY-action maker quote.
    MAINTAIN_SELL = "maintain_sell"  # Keeping the current SELL-action maker quote.
    QUOTE_BUY_DRY_RUN = "quote_buy_dry_run"  # Dry-run selected a BUY maker quote.
    QUOTE_SELL_DRY_RUN = "quote_sell_dry_run"  # Dry-run selected a SELL maker quote.
    PLACED_BUY = "placed_buy"  # Live mode placed a BUY-action maker order.
    PLACED_SELL = "placed_sell"  # Live mode placed a SELL-action maker order.
    CANCELING_MAKER = "canceling_maker"  # Canceling the active maker order.
    CANCELED_MAKER = "canceled_maker"  # Canceled or removed the active maker order.
    CANCEL_FAILED = "cancel_failed"  # Failed to cancel the active maker order.
    MAKER_FILLED = "maker_filled"  # Detected a fill on the active maker order.

    @classmethod
    def maintain(cls, action: str) -> "BotAction":
        return cls.MAINTAIN_BUY if action == "BUY" else cls.MAINTAIN_SELL

    @classmethod
    def dry_run_quote(cls, action: str) -> "BotAction":
        return cls.QUOTE_BUY_DRY_RUN if action == "BUY" else cls.QUOTE_SELL_DRY_RUN

    @classmethod
    def placed(cls, action: str) -> "BotAction":
        return cls.PLACED_BUY if action == "BUY" else cls.PLACED_SELL


@dataclass
class Quote:
    """Current order-book prices used to calculate arbitrage spreads.

    bitbank uses top-of-book aggressive maker prices. bitFlyer uses VWAP
    estimates because the hedge leg is a taker order; the strategy VWAP is
    computed over ``order_size * hedge_vwap_multiplier`` so the expected hedge
    price stays conservative when the book thins out, while the ``_base``
    fields keep the plain ``order_size`` VWAP for slippage diagnostics.
    """

    bitbank_bid: Decimal | None = None
    bitbank_ask: Decimal | None = None
    bitbank_buy_maker: Decimal | None = None
    bitbank_sell_maker: Decimal | None = None
    bitflyer_bid: Decimal | None = None
    bitflyer_ask: Decimal | None = None
    bitflyer_bid_vwap: Decimal | None = None
    bitflyer_ask_vwap: Decimal | None = None
    bitflyer_bid_vwap_base: Decimal | None = None
    bitflyer_ask_vwap_base: Decimal | None = None
    timestamp: float = 0.0

    @property
    def ready(self) -> bool:
        return all(
            value is not None
            for value in (
                self.bitbank_bid,
                self.bitbank_ask,
                self.bitbank_buy_maker,
                self.bitbank_sell_maker,
                self.bitflyer_bid,
                self.bitflyer_ask,
                self.bitflyer_bid_vwap,
                self.bitflyer_ask_vwap,
            )
        )

    @property
    def bitflyer_mid(self) -> Decimal | None:
        if self.bitflyer_bid is None or self.bitflyer_ask is None:
            return None
        return (self.bitflyer_bid + self.bitflyer_ask) / 2

    @property
    def buy_price(self) -> Decimal | None:
        if self.bitbank_buy_maker is None or self.bitflyer_bid_vwap is None:
            return None
        return self.bitbank_buy_maker - self.bitflyer_bid_vwap

    @property
    def sell_price(self) -> Decimal | None:
        if self.bitbank_sell_maker is None or self.bitflyer_ask_vwap is None:
            return None
        return self.bitbank_sell_maker - self.bitflyer_ask_vwap


@dataclass
class BitbankTransaction:
    side: str
    price: Decimal
    amount: Decimal
    transaction_id: int | None = None
    executed_at: int | None = None
    timestamp: float = 0.0


@dataclass
class MakerOrder:
    """The single active or desired maker quote on bitbank.

    Records the arbitrage action it represents, bitbank order side/price/size,
    the trigger spread used to choose it, and the expected bitFlyer hedge price.
    """

    action: str
    side: BitbankSide
    position_side: BitbankPositionSide | None
    price: Decimal
    amount: Decimal
    trigger_price: Decimal
    expected_hedge_price: Decimal
    stage_index: int
    order_id: str | None = None
    placed_at: float = field(default_factory=time.time)
    executed_amount: Decimal = Decimal("0")
    expected_hedge_price_base: Decimal | None = None


@dataclass
class StageStatus:
    position: Decimal = Decimal("0")
    current_stage: int = 0
    next_stage: int | None = 1
    stage_size: Decimal = Decimal("0")
    max_stages: int = 0
    max_position: Decimal = Decimal("0")
    long_open_trigger: Decimal | None = None
    long_close_trigger: Decimal | None = None
    short_open_trigger: Decimal | None = None
    short_close_trigger: Decimal | None = None
    next_open_amount: Decimal | None = None
    close_amount: Decimal | None = None


@dataclass
class ActionHistoryEntry:
    timestamp: float
    action: BotAction
    description: str


@dataclass
class BotState:
    """Mutable runtime state shared by the strategy loop and web monitor.

    Tracks latest quotes, aggregate position, realized PnL, fill counters, the
    active maker order, and the most recent status/error for observability.
    """

    quote: Quote = field(default_factory=Quote)
    latest_bitbank_transaction: BitbankTransaction | None = None
    position: Decimal = Decimal("0")
    bitbank_position: Decimal = Decimal("0")
    bitflyer_position: Decimal = Decimal("0")
    realized_pnl_jpy: Decimal = Decimal("0")
    bitbank_realized_pnl_jpy: Decimal = Decimal("0")
    bitbank_open_cost_jpy: Decimal = Decimal("0")
    bitbank_cost_basis_ready: bool = True
    bitflyer_realized_pnl_jpy: Decimal = Decimal("0")
    bitflyer_open_cost_jpy: Decimal = Decimal("0")
    bitflyer_cost_basis_ready: bool = True
    filled_base: Decimal = Decimal("0")
    trade_count: int = 0
    active_maker: MakerOrder | None = None
    stage_status: StageStatus = field(default_factory=StageStatus)
    last_action: BotAction = BotAction.IDLE
    action_history: list[ActionHistoryEntry] = field(default_factory=list)
    last_error: str = ""
    started_at: float = field(default_factory=time.time)

    @property
    def unhedged_position(self) -> Decimal:
        return self.bitbank_position - self.bitflyer_position

    def set_action(self, action: BotAction, description: str | None = None) -> None:
        action_description = description or ""
        if (
            action == self.last_action
            and self.action_history
            and self.action_history[-1].description == action_description
        ):
            return
        self.last_action = action
        self.action_history.append(
            ActionHistoryEntry(
                timestamp=time.time(),
                action=action,
                description=action_description,
            )
        )
        if len(self.action_history) > 100:
            del self.action_history[:-100]
