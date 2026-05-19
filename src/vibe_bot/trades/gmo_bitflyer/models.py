from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum


class BotAction(Enum):
    IDLE = "idle"
    WAITING_FOR_QUOTES = "waiting_for_quotes"
    WAITING_FOR_FILTER = "waiting_for_filter"
    BLOCKED = "blocked"
    TRADE_DRY_RUN = "trade_dry_run"
    TRADE_PLACED = "trade_placed"
    TRADE_FAILED = "trade_failed"


@dataclass
class Quote:
    gmo_bid: Decimal | None = None
    gmo_ask: Decimal | None = None
    gmo_bid_vwap: Decimal | None = None
    gmo_ask_vwap: Decimal | None = None
    bitflyer_bid: Decimal | None = None
    bitflyer_ask: Decimal | None = None
    bitflyer_bid_vwap: Decimal | None = None
    bitflyer_ask_vwap: Decimal | None = None
    timestamp: float = 0.0

    @property
    def ready(self) -> bool:
        return all(
            value is not None
            for value in (
                self.gmo_bid,
                self.gmo_ask,
                self.gmo_bid_vwap,
                self.gmo_ask_vwap,
                self.bitflyer_bid,
                self.bitflyer_ask,
                self.bitflyer_bid_vwap,
                self.bitflyer_ask_vwap,
            )
        )

    @property
    def buy_price(self) -> Decimal | None:
        """Spread for buying GMO and selling bitFlyer."""
        if self.gmo_ask_vwap is None or self.bitflyer_bid_vwap is None:
            return None
        return self.gmo_ask_vwap - self.bitflyer_bid_vwap

    @property
    def sell_price(self) -> Decimal | None:
        """Spread for selling GMO and buying bitFlyer."""
        if self.gmo_bid_vwap is None or self.bitflyer_ask_vwap is None:
            return None
        return self.gmo_bid_vwap - self.bitflyer_ask_vwap

    @property
    def mid_spread(self) -> Decimal | None:
        if self.gmo_bid is None or self.gmo_ask is None:
            return None
        if self.bitflyer_bid is None or self.bitflyer_ask is None:
            return None
        return ((self.gmo_bid + self.gmo_ask) / 2) - (
            (self.bitflyer_bid + self.bitflyer_ask) / 2
        )


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
class FilterSnapshot:
    samples: int = 0
    trend_spread: Decimal | None = None
    residual_noise: Decimal | None = None
    required_extra_edge: Decimal | None = None


@dataclass
class TradeTarget:
    action: str
    amount: Decimal
    trigger_price: Decimal
    executable_spread: Decimal
    trend_spread: Decimal
    required_extra_edge: Decimal
    stage_index: int
    gmo_side: str
    bitflyer_side: str
    gmo_expected_price: Decimal
    bitflyer_expected_price: Decimal
    gmo_limit_price: Decimal


@dataclass
class TradeCondition:
    passed: bool
    reason: str
    target: TradeTarget | None = None
    details: dict[str, object] = field(default_factory=dict)


@dataclass
class ActionHistoryEntry:
    timestamp: float
    action: BotAction
    description: str


@dataclass
class GmoOrderMetric:
    attempted_size: Decimal
    filled_size: Decimal
    order_id: int | str | None = None


@dataclass
class BitflyerOrderMetric:
    expected_price: Decimal
    average_price: Decimal | None
    filled_size: Decimal
    slippage_jpy_per_btc: Decimal | None
    acceptance_id: str | None = None


@dataclass
class BotState:
    quote: Quote = field(default_factory=Quote)
    filter: FilterSnapshot = field(default_factory=FilterSnapshot)
    stage_status: StageStatus = field(default_factory=StageStatus)
    position: Decimal = Decimal("0")
    gmo_position: Decimal = Decimal("0")
    bitflyer_position: Decimal = Decimal("0")
    realized_pnl_jpy: Decimal = Decimal("0")
    filled_base: Decimal = Decimal("0")
    trade_count: int = 0
    last_trade_condition: TradeCondition | None = None
    last_action: BotAction = BotAction.IDLE
    action_history: list[ActionHistoryEntry] = field(default_factory=list)
    gmo_order_metrics: list[GmoOrderMetric] = field(default_factory=list)
    bitflyer_order_metrics: list[BitflyerOrderMetric] = field(default_factory=list)
    last_error: str = ""
    started_at: float = field(default_factory=time.time)

    @property
    def unhedged_position(self) -> Decimal:
        return self.gmo_position - self.bitflyer_position

    def set_action(self, action: BotAction, description: str = "") -> None:
        if (
            action == self.last_action
            and self.action_history
            and self.action_history[-1].description == description
        ):
            return
        self.last_action = action
        self.action_history.append(
            ActionHistoryEntry(time.time(), action=action, description=description)
        )
        if len(self.action_history) > 100:
            del self.action_history[:-100]

    @property
    def gmo_order_success_rate(self) -> Decimal | None:
        attempted = sum(
            (entry.attempted_size for entry in self.gmo_order_metrics),
            Decimal("0"),
        )
        if attempted <= 0:
            return None
        filled = sum(
            (entry.filled_size for entry in self.gmo_order_metrics),
            Decimal("0"),
        )
        return filled / attempted

    @property
    def bitflyer_average_slippage_jpy_per_btc(self) -> Decimal | None:
        filled = sum(
            (
                entry.filled_size
                for entry in self.bitflyer_order_metrics
                if entry.slippage_jpy_per_btc is not None
            ),
            Decimal("0"),
        )
        if filled <= 0:
            return None
        total_slippage = sum(
            (
                entry.slippage_jpy_per_btc * entry.filled_size
                for entry in self.bitflyer_order_metrics
                if entry.slippage_jpy_per_btc is not None
            ),
            Decimal("0"),
        )
        return total_slippage / filled

    def record_gmo_order_metric(
        self,
        *,
        attempted_size: Decimal,
        filled_size: Decimal,
        order_id: int | str | None = None,
    ) -> None:
        self.gmo_order_metrics.append(
            GmoOrderMetric(
                attempted_size=attempted_size,
                filled_size=filled_size,
                order_id=order_id,
            )
        )
        del self.gmo_order_metrics[:-20]

    def record_bitflyer_order_metric(
        self,
        *,
        expected_price: Decimal,
        average_price: Decimal | None,
        filled_size: Decimal,
        slippage_jpy_per_btc: Decimal | None,
        acceptance_id: str | None = None,
    ) -> None:
        self.bitflyer_order_metrics.append(
            BitflyerOrderMetric(
                expected_price=expected_price,
                average_price=average_price,
                filled_size=filled_size,
                slippage_jpy_per_btc=slippage_jpy_per_btc,
                acceptance_id=acceptance_id,
            )
        )
        del self.bitflyer_order_metrics[:-20]
