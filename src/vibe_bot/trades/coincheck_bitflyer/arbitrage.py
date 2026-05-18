from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections import deque
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from dotenv import load_dotenv

from vibe_bot.bitflyer import PrivateClient as BitflyerPrivateClient
from vibe_bot.coincheck import PrivateClient as CoincheckPrivateClient
from vibe_bot.trades.bitbank_bitflyer.config import parse_hhmmss
from vibe_bot.trades.bitbank_bitflyer.logging import Broadcaster
from vibe_bot.trades.bitbank_bitflyer.utils import JST
from vibe_bot.trades.bitbank_bitflyer.utils import jst_iso
from vibe_bot.trades.bitbank_bitflyer.utils import quantize_down
from vibe_bot.trades.bitbank_bitflyer.utils import quantize_up
from vibe_bot.trades.coincheck_bitflyer.config import BotConfig
from vibe_bot.trades.coincheck_bitflyer.config import build_parser
from vibe_bot.trades.coincheck_bitflyer.config import config_from_args
from vibe_bot.trades.coincheck_bitflyer.logging import TradeLogger
from vibe_bot.trades.coincheck_bitflyer.logging import event_summary
from vibe_bot.trades.coincheck_bitflyer.models import BotAction
from vibe_bot.trades.coincheck_bitflyer.models import BotState
from vibe_bot.trades.coincheck_bitflyer.models import FilterSnapshot
from vibe_bot.trades.coincheck_bitflyer.models import StageStatus
from vibe_bot.trades.coincheck_bitflyer.models import TradeCondition
from vibe_bot.trades.coincheck_bitflyer.models import TradeTarget
from vibe_bot.trades.coincheck_bitflyer.quotes import WebSocketQuoteFeed
from vibe_bot.trades.coincheck_bitflyer.web import WebApp

LOGGER = logging.getLogger("vibe_bot.trades.coincheck_bitflyer.arbitrage")


def _jst_time_seconds() -> int:
    now = datetime.now(JST)
    return now.hour * 3600 + now.minute * 60 + now.second


class SpreadFilter:
    """Tracks spread trend and short-term residual noise."""

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self.trend: Decimal | None = None
        self.residuals: deque[Decimal] = deque(maxlen=config.gate_noise_window)

    def update(self, raw_spread: Decimal) -> FilterSnapshot:
        if self.trend is None:
            self.trend = raw_spread
        else:
            alpha = self.config.gate_ema_alpha
            self.trend = alpha * raw_spread + (Decimal("1") - alpha) * self.trend
        residual = raw_spread - self.trend
        self.residuals.append(residual)
        noise = self._rms_noise()
        required = None
        if noise is not None:
            required = max(
                self.config.gate_min_extra_edge_jpy,
                noise * self.config.gate_noise_multiplier,
            )
        return FilterSnapshot(
            samples=len(self.residuals),
            trend_spread=self.trend,
            residual_noise=noise,
            required_extra_edge=required,
        )

    def _rms_noise(self) -> Decimal | None:
        if len(self.residuals) < 2:
            return None
        mean_square = sum((x * x for x in self.residuals), Decimal("0")) / Decimal(
            len(self.residuals)
        )
        return mean_square.sqrt()


class ArbitrageTrader:
    """Runs the Coincheck taker / bitFlyer taker decision loop."""

    def __init__(self, config: BotConfig, state: BotState, logger: TradeLogger) -> None:
        self.config = config
        self.state = state
        self.logger = logger
        self.filter = SpreadFilter(config)
        self._coincheck_private: CoincheckPrivateClient | None = None
        self._bf_private: BitflyerPrivateClient | None = None
        self._candidate_key: tuple[object, ...] | None = None
        self._candidate_since: float | None = None
        self._last_trade_at = 0.0
        self._shutdown_started = False

    async def run(self, stop: asyncio.Event) -> None:
        try:
            if not self.config.dry_run:
                try:
                    self._coincheck_private = CoincheckPrivateClient()
                    self._bf_private = BitflyerPrivateClient()
                    await self._initialize_live_position()
                except Exception as exc:
                    self.state.last_error = f"trader initialization failed: {exc}"
                    self.state.last_trade_condition = TradeCondition(
                        False,
                        "trader_initialization_failed",
                        details={"error": str(exc)},
                    )
                    self.state.set_action(
                        BotAction.BLOCKED,
                        event_summary(
                            "trader_initialization_failed",
                            error=str(exc),
                        ),
                    )
                    self.logger.event("error", message=self.state.last_error)
                    LOGGER.exception("trader initialization failed")
                    while not stop.is_set():
                        await asyncio.sleep(self.config.update_interval)
                    return
            self.state.stage_status = self._stage_status()
            while not stop.is_set():
                try:
                    await self._tick()
                    self.state.last_error = ""
                except Exception as exc:
                    self.state.last_error = f"trader tick failed: {exc}"
                    self.logger.event("error", message=self.state.last_error)
                    LOGGER.exception("trader tick failed")
                await asyncio.sleep(self.config.update_interval)
        finally:
            await self.shutdown("shutdown")

    async def shutdown(self, reason: str) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self.logger.event("trader_shutdown_started", reason=reason)
        if self._coincheck_private is not None:
            await self._coincheck_private.aclose()
            self._coincheck_private = None
        if self._bf_private is not None:
            await self._bf_private.aclose()
            self._bf_private = None
        self.logger.event("trader_shutdown_finished", reason=reason)

    async def _tick(self) -> None:
        quote = self.state.quote
        if not quote.ready:
            self.state.last_trade_condition = TradeCondition(
                False,
                "waiting_for_quotes",
                details=self._quote_ready_details(),
            )
            self.state.set_action(BotAction.WAITING_FOR_QUOTES, "quote.ready=false")
            return
        raw_spread = quote.mid_spread
        if raw_spread is None:
            self.state.last_trade_condition = TradeCondition(
                False,
                "waiting_for_quotes",
                details={"mid_spread": None},
            )
            self.state.set_action(BotAction.WAITING_FOR_QUOTES, "mid_spread=None")
            return
        self.state.filter = self.filter.update(raw_spread)
        self.state.stage_status = self._stage_status()
        condition = self._check_trade_condition()
        self.state.last_trade_condition = condition
        if not condition.passed:
            action = (
                BotAction.WAITING_FOR_FILTER
                if condition.reason in {"filter_warming_up", "persistence"}
                else BotAction.BLOCKED
            )
            self.state.set_action(
                action,
                event_summary(condition.reason, **condition.details),
            )
            self.logger.event(
                "trade_condition_blocked",
                reason=condition.reason,
                details=condition.details,
            )
            return
        assert condition.target is not None
        await self._execute_target(condition.target)

    def _quote_ready_details(self) -> dict[str, bool]:
        quote = self.state.quote
        return {
            "coincheck_bid": quote.coincheck_bid is not None,
            "coincheck_ask": quote.coincheck_ask is not None,
            "coincheck_bid_vwap": quote.coincheck_bid_vwap is not None,
            "coincheck_ask_vwap": quote.coincheck_ask_vwap is not None,
            "bitflyer_bid": quote.bitflyer_bid is not None,
            "bitflyer_ask": quote.bitflyer_ask is not None,
            "bitflyer_bid_vwap": quote.bitflyer_bid_vwap is not None,
            "bitflyer_ask_vwap": quote.bitflyer_ask_vwap is not None,
        }

    def _check_trade_condition(self) -> TradeCondition:
        if self._in_bitflyer_maintenance_guard():
            return TradeCondition(False, "bitflyer_maintenance_guard")
        if time.time() - self._last_trade_at < self.config.gate_entry_cooldown_seconds:
            return TradeCondition(
                False,
                "entry_cooldown",
                details={"last_trade_at": self._last_trade_at},
            )

        target = self._choose_stage_target()
        if target is None:
            self._reset_persistence()
            return TradeCondition(False, "stage_trigger_not_crossed")

        snapshot = self.state.filter
        if (
            snapshot.trend_spread is None
            or snapshot.required_extra_edge is None
            or snapshot.samples < self.config.gate_min_filter_samples
        ):
            self._reset_persistence()
            return TradeCondition(
                False,
                "filter_warming_up",
                target=target,
                details={
                    "samples": snapshot.samples,
                    "gate_min_filter_samples": self.config.gate_min_filter_samples,
                },
            )

        # Trade gate 1: the EMA trend must agree with the stage direction.
        if not self._trend_agrees(target, snapshot.trend_spread):
            self._reset_persistence()
            return TradeCondition(
                False,
                "trend_disagrees",
                target=target,
                details={
                    "action": target.action,
                    "trend_spread": snapshot.trend_spread,
                    "trigger_price": target.trigger_price,
                },
            )

        # Trade gate 2: current executable edge must clear the noise buffer.
        edge = self._target_edge(target)
        if edge < snapshot.required_extra_edge:
            self._reset_persistence()
            return TradeCondition(
                False,
                "edge_below_noise_buffer",
                target=target,
                details={
                    "action": target.action,
                    "edge": edge,
                    "required_extra_edge": snapshot.required_extra_edge,
                    "residual_noise": snapshot.residual_noise,
                },
            )

        target.trend_spread = snapshot.trend_spread
        target.required_extra_edge = snapshot.required_extra_edge
        # Trade gate 3: the same target must persist for the configured time.
        if not self._persistence_passed(target):
            return TradeCondition(
                False,
                "persistence",
                target=target,
                details={
                    "action": target.action,
                    "required_seconds": self.config.gate_persistence_seconds,
                    "candidate_since": self._candidate_since,
                },
            )

        return TradeCondition(True, "passed", target=target)

    def _trend_agrees(self, target: TradeTarget, trend_spread: Decimal) -> bool:
        if target.action == "BUY":
            return trend_spread <= target.trigger_price
        return trend_spread >= target.trigger_price

    def _target_edge(self, target: TradeTarget) -> Decimal:
        if target.action == "BUY":
            return target.trigger_price - target.executable_spread
        return target.executable_spread - target.trigger_price

    def _choose_stage_target(self) -> TradeTarget | None:
        quote = self.state.quote
        assert quote.ready
        buy_price = quote.buy_price
        sell_price = quote.sell_price
        assert buy_price is not None and sell_price is not None
        stage = self.state.stage_status
        position = self.state.position

        if position > 0:
            assert stage.long_close_trigger is not None
            if sell_price > stage.long_close_trigger:
                assert stage.close_amount is not None
                return self._build_target(
                    "SELL", stage.long_close_trigger, stage.close_amount, stage.current_stage
                )
            if (
                stage.next_stage is not None
                and stage.long_open_trigger is not None
                and buy_price < stage.long_open_trigger
            ):
                assert stage.next_open_amount is not None
                return self._build_target(
                    "BUY", stage.long_open_trigger, stage.next_open_amount, stage.next_stage
                )
            return None

        if position < 0:
            assert stage.short_close_trigger is not None
            if buy_price < stage.short_close_trigger:
                assert stage.close_amount is not None
                return self._build_target(
                    "BUY", stage.short_close_trigger, stage.close_amount, stage.current_stage
                )
            if (
                stage.next_stage is not None
                and stage.short_open_trigger is not None
                and sell_price > stage.short_open_trigger
            ):
                assert stage.next_open_amount is not None
                return self._build_target(
                    "SELL",
                    stage.short_open_trigger,
                    stage.next_open_amount,
                    stage.next_stage,
                )
            return None

        buy_open_trigger = self.config.gate_threshold_offset_jpy - self.config.gate_threshold_jpy
        sell_open_trigger = self.config.gate_threshold_offset_jpy + self.config.gate_threshold_jpy
        buy_edge = buy_open_trigger - buy_price
        sell_edge = sell_price - sell_open_trigger
        if buy_edge <= 0 and sell_edge <= 0:
            return None
        assert stage.next_open_amount is not None
        if sell_edge > buy_edge:
            return self._build_target("SELL", sell_open_trigger, stage.next_open_amount, 1)
        return self._build_target("BUY", buy_open_trigger, stage.next_open_amount, 1)

    def _build_target(
        self, action: str, trigger: Decimal, amount: Decimal, stage_index: int
    ) -> TradeTarget | None:
        if amount < self.config.min_order_size:
            return None
        quote = self.state.quote
        assert quote.ready
        assert quote.coincheck_bid_vwap is not None
        assert quote.coincheck_ask_vwap is not None
        assert quote.bitflyer_bid_vwap is not None
        assert quote.bitflyer_ask_vwap is not None
        if action == "BUY":
            executable_spread = quote.buy_price
            assert executable_spread is not None
            coincheck_expected = quote.coincheck_ask_vwap
            bitflyer_expected = quote.bitflyer_bid_vwap
            coincheck_limit = quantize_up(
                coincheck_expected + self.config.gate_max_slippage_jpy, self.config.tick_size
            )
            coincheck_side = "buy"
            bitflyer_side = "SELL"
        else:
            executable_spread = quote.sell_price
            assert executable_spread is not None
            coincheck_expected = quote.coincheck_bid_vwap
            bitflyer_expected = quote.bitflyer_ask_vwap
            coincheck_limit = quantize_down(
                coincheck_expected - self.config.gate_max_slippage_jpy, self.config.tick_size
            )
            coincheck_side = "sell"
            bitflyer_side = "BUY"
        if coincheck_limit <= 0:
            return None
        return TradeTarget(
            action=action,
            amount=amount,
            trigger_price=trigger,
            executable_spread=executable_spread,
            trend_spread=Decimal("0"),
            required_extra_edge=Decimal("0"),
            stage_index=stage_index,
            coincheck_side=coincheck_side,
            bitflyer_side=bitflyer_side,
            coincheck_expected_price=coincheck_expected,
            bitflyer_expected_price=bitflyer_expected,
            coincheck_limit_price=coincheck_limit,
        )

    def _persistence_passed(self, target: TradeTarget) -> bool:
        key = (
            target.action,
            target.stage_index,
            target.trigger_price,
            target.amount,
        )
        now = time.time()
        if key != self._candidate_key:
            self._candidate_key = key
            self._candidate_since = now
        if self.config.gate_persistence_seconds == 0:
            return True
        assert self._candidate_since is not None
        return now - self._candidate_since >= self.config.gate_persistence_seconds

    def _reset_persistence(self) -> None:
        self._candidate_key = None
        self._candidate_since = None

    def _stage_status(self) -> StageStatus:
        position = self.state.position
        abs_position = abs(position)
        current_stage = self._ceil_stage(abs_position) if abs_position > 0 else 0
        next_stage = self._next_stage(abs_position)
        can_open_next = next_stage <= self.config.max_stages
        next_open_amount = (
            self._open_stage_amount(abs_position, next_stage) if can_open_next else None
        )
        close_amount = (
            self._close_stage_amount(abs_position, current_stage)
            if abs_position > 0
            else None
        )
        threshold = self.config.gate_threshold_jpy
        offset = self.config.gate_threshold_offset_jpy
        long_open_trigger = None
        short_open_trigger = None
        if can_open_next:
            if position >= 0:
                long_open_trigger = offset - Decimal(next_stage) * threshold
            if position <= 0:
                short_open_trigger = offset + Decimal(next_stage) * threshold
        return StageStatus(
            position=position,
            current_stage=current_stage,
            next_stage=next_stage if can_open_next else None,
            stage_size=self.config.stage_size,
            max_stages=self.config.max_stages,
            max_position=self.config.max_position,
            long_open_trigger=long_open_trigger,
            long_close_trigger=(
                offset - Decimal(current_stage - 1) * threshold
                if position > 0
                else None
            ),
            short_open_trigger=short_open_trigger,
            short_close_trigger=(
                offset + Decimal(current_stage - 1) * threshold
                if position < 0
                else None
            ),
            next_open_amount=next_open_amount,
            close_amount=close_amount,
        )

    def _ceil_stage(self, abs_position: Decimal) -> int:
        stage = (abs_position / self.config.stage_size).to_integral_value(
            rounding=ROUND_UP
        )
        return max(1, int(stage))

    def _next_stage(self, abs_position: Decimal) -> int:
        completed = (abs_position / self.config.stage_size).to_integral_value(
            rounding=ROUND_DOWN
        )
        return int(completed) + 1

    def _open_stage_amount(self, abs_position: Decimal, stage_index: int) -> Decimal:
        target_position = min(
            self.config.stage_size * Decimal(stage_index),
            self.config.max_position,
        )
        return min(self.config.order_size, target_position - abs_position)

    def _close_stage_amount(self, abs_position: Decimal, stage_index: int) -> Decimal:
        lower_stage_position = self.config.stage_size * Decimal(stage_index - 1)
        return min(self.config.order_size, abs_position - lower_stage_position)

    def _in_bitflyer_maintenance_guard(self) -> bool:
        if not self.config.gate_bitflyer_maintenance_guard_enabled:
            return False
        now_seconds = _jst_time_seconds()
        start_seconds = parse_hhmmss(self.config.gate_bitflyer_maintenance_start_jst)
        end_seconds = parse_hhmmss(self.config.gate_bitflyer_maintenance_end_jst)
        if start_seconds <= end_seconds:
            return start_seconds <= now_seconds < end_seconds
        return now_seconds >= start_seconds or now_seconds < end_seconds

    async def _initialize_live_position(self) -> None:
        assert self._coincheck_private is not None
        assert self._bf_private is not None
        coincheck_position, coincheck_components = await self._coincheck_strategy_position()
        bitflyer_position, bitflyer_components = await self._bitflyer_strategy_position()
        mismatch = abs(coincheck_position - bitflyer_position)
        if mismatch >= self.config.min_order_size:
            raise RuntimeError(
                "Coincheck and bitFlyer positions disagree: "
                f"coincheck={coincheck_position}, bitflyer={bitflyer_position}, mismatch={mismatch}"
            )
        self.state.coincheck_position = coincheck_position
        self.state.bitflyer_position = bitflyer_position
        self.state.position = coincheck_position
        self.logger.event(
            "position_initialized",
            coincheck=coincheck_components,
            bitflyer=bitflyer_components,
            position=self.state.position,
            unhedged_position=self.state.unhedged_position,
        )

    async def _coincheck_strategy_position(self) -> tuple[Decimal, dict[str, object]]:
        assert self._coincheck_private is not None
        base_asset = self.config.coincheck_pair.split("_", 1)[0].lower()
        balance = await self._coincheck_private.balance()
        spot_amount = Decimal("0")
        for asset, amount in balance.balances.items():
            if asset.lower() == base_asset:
                spot_amount = amount
                break
        strategy_position = spot_amount - self.config.coincheck_neutral_spot_amount
        return strategy_position, {
            "pair": self.config.coincheck_pair,
            "base_asset": base_asset,
            "spot_amount": spot_amount,
            "neutral_spot_amount": self.config.coincheck_neutral_spot_amount,
            "strategy_position": strategy_position,
        }

    async def _bitflyer_strategy_position(self) -> tuple[Decimal, dict[str, object]]:
        assert self._bf_private is not None
        positions = await self._bf_private.positions(
            product_code=self.config.bitflyer_product_code
        )
        long_amount = Decimal("0")
        short_amount = Decimal("0")
        for position in positions:
            if position.side == "BUY":
                long_amount += position.size
            elif position.side == "SELL":
                short_amount += position.size
        return short_amount - long_amount, {
            "product_code": self.config.bitflyer_product_code,
            "long_open_amount": long_amount,
            "short_open_amount": short_amount,
        }

    async def _execute_target(self, target: TradeTarget) -> None:
        self._reset_persistence()
        self._last_trade_at = time.time()
        self.logger.event("trade_attempt", target=asdict(target), dry_run=self.config.dry_run)
        if self.config.dry_run:
            self.state.record_coincheck_order_metric(
                attempted_size=target.amount,
                filled_size=target.amount,
                order_id="DRY-RUN",
            )
            self.state.record_bitflyer_order_metric(
                expected_price=target.bitflyer_expected_price,
                average_price=target.bitflyer_expected_price,
                filled_size=target.amount,
                slippage_jpy_per_btc=Decimal("0"),
                acceptance_id="DRY-RUN",
            )
            self._record_trade(
                target=target,
                amount=target.amount,
                coincheck_average_price=target.coincheck_expected_price,
                bitflyer_average_price=target.bitflyer_expected_price,
                coincheck_order_id="DRY-RUN",
                bitflyer_acceptance_id="DRY-RUN",
            )
            self.state.set_action(
                BotAction.TRADE_DRY_RUN,
                event_summary("trade_dry_run", target=asdict(target)),
            )
            return

        assert self._coincheck_private is not None
        assert self._bf_private is not None
        coincheck_price, coincheck_amount, coincheck_order_ids = await self._execute_coincheck(target)
        if coincheck_amount <= 0:
            self.state.set_action(BotAction.TRADE_FAILED, "coincheck_unfilled")
            self.logger.event("coincheck_unfilled", target=asdict(target))
            return
        bf_ack = await self._bf_private.send_child_order(
            product_code=self.config.bitflyer_product_code,
            child_order_type="MARKET",
            side=target.bitflyer_side,
            size=coincheck_amount,
            time_in_force="IOC",
        )
        bitflyer_price, bitflyer_amount = await self._bitflyer_execution_summary(
            bf_ack.child_order_acceptance_id,
            fallback=target.bitflyer_expected_price,
        )
        self.state.record_bitflyer_order_metric(
            expected_price=target.bitflyer_expected_price,
            average_price=bitflyer_price if bitflyer_amount > 0 else None,
            filled_size=bitflyer_amount,
            slippage_jpy_per_btc=self._bitflyer_slippage_jpy_per_btc(
                target.bitflyer_side,
                target.bitflyer_expected_price,
                bitflyer_price,
            )
            if bitflyer_amount > 0
            else Decimal("0"),
            acceptance_id=bf_ack.child_order_acceptance_id,
        )
        hedge_amount = min(coincheck_amount, bitflyer_amount)
        if hedge_amount <= 0:
            self._apply_coincheck_position(target.action, coincheck_amount)
            self.state.set_action(BotAction.TRADE_FAILED, "bitflyer_unfilled")
            self.logger.event(
                "bitflyer_unfilled",
                target=asdict(target),
                coincheck_amount=coincheck_amount,
                coincheck_average_price=coincheck_price,
            )
            return
        if bitflyer_amount != coincheck_amount:
            self.logger.event(
                "partial_hedge",
                requested_amount=coincheck_amount,
                bitflyer_amount=bitflyer_amount,
                recorded_amount=hedge_amount,
            )
        self._record_trade(
            target=target,
            amount=hedge_amount,
            coincheck_average_price=coincheck_price,
            bitflyer_average_price=bitflyer_price,
            coincheck_order_id=",".join(str(x) for x in coincheck_order_ids),
            bitflyer_acceptance_id=bf_ack.child_order_acceptance_id,
        )
        if coincheck_amount > hedge_amount:
            self._apply_coincheck_position(target.action, coincheck_amount - hedge_amount)
            self.logger.event(
                "unhedged_coincheck_remainder",
                amount=coincheck_amount - hedge_amount,
                unhedged_position=self.state.unhedged_position,
            )
        self.state.set_action(
            BotAction.TRADE_PLACED,
            event_summary("trade_placed", target=asdict(target), amount=hedge_amount),
        )

    async def _execute_coincheck(
        self, target: TradeTarget
    ) -> tuple[Decimal, Decimal, list[int]]:
        assert self._coincheck_private is not None
        order_ids: list[int] = []
        order_id: int | None = None
        try:
            order = await self._coincheck_private.place_order(
                pair=self.config.coincheck_pair,
                order_type=target.coincheck_side,
                rate=target.coincheck_limit_price,
                amount=target.amount,
            )
            order_id = order.id
        except Exception:
            self.state.record_coincheck_order_metric(
                attempted_size=target.amount,
                filled_size=Decimal("0"),
            )
            raise
        order_ids.append(order_id)
        price, amount = await self._coincheck_execution_summary(
            order_id,
            side=target.coincheck_side,
            fallback=target.coincheck_expected_price,
        )
        if amount < target.amount:
            with suppress(Exception):
                await self._coincheck_private.cancel_order(order_id)
        self.state.record_coincheck_order_metric(
            attempted_size=target.amount,
            filled_size=amount,
            order_id=order_id,
        )
        if amount <= 0:
            return target.coincheck_expected_price, Decimal("0"), order_ids
        return price, amount, order_ids

    async def _coincheck_execution_summary(
        self, order_id: int, *, side: str, fallback: Decimal
    ) -> tuple[Decimal, Decimal]:
        assert self._coincheck_private is not None
        base_asset = self.config.coincheck_pair.split("_", 1)[0].lower()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            transactions = await self._coincheck_private.transactions(
                limit=100,
                order="desc",
            )
            matching = [
                transaction
                for transaction in transactions.transactions
                if transaction.order_id == order_id and transaction.side == side
            ]
            total_size = sum(
                (
                    abs((transaction.funds or {}).get(base_asset, Decimal("0")))
                    for transaction in matching
                ),
                Decimal("0"),
            )
            if total_size > 0:
                total_notional = sum(
                    (
                        (transaction.rate or fallback)
                        * abs((transaction.funds or {}).get(base_asset, Decimal("0")))
                        for transaction in matching
                    ),
                    Decimal("0"),
                )
                return total_notional / total_size, total_size
            await asyncio.sleep(0.25)
        return fallback, Decimal("0")

    async def _bitflyer_execution_summary(
        self, acceptance_id: str, fallback: Decimal
    ) -> tuple[Decimal, Decimal]:
        assert self._bf_private is not None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            executions = await self._bf_private.executions(
                product_code=self.config.bitflyer_product_code,
                child_order_acceptance_id=acceptance_id,
            )
            if executions:
                total_size = sum((e.size for e in executions), Decimal("0"))
                if total_size > 0:
                    total_notional = sum((e.price * e.size for e in executions), Decimal("0"))
                    return total_notional / total_size, total_size
            await asyncio.sleep(0.25)
        return fallback, Decimal("0")

    def _bitflyer_slippage_jpy_per_btc(
        self, side: str, expected_price: Decimal, average_price: Decimal
    ) -> Decimal:
        if side == "BUY":
            return average_price - expected_price
        return expected_price - average_price

    def _record_trade(
        self,
        *,
        target: TradeTarget,
        amount: Decimal,
        coincheck_average_price: Decimal,
        bitflyer_average_price: Decimal,
        coincheck_order_id: str,
        bitflyer_acceptance_id: str,
    ) -> None:
        self._apply_coincheck_position(target.action, amount)
        self._apply_bitflyer_position(target.bitflyer_side, amount)
        cashflow = (
            (bitflyer_average_price - coincheck_average_price) * amount
            if target.action == "BUY"
            else (coincheck_average_price - bitflyer_average_price) * amount
        )
        self.state.realized_pnl_jpy += cashflow
        self.state.filled_base += amount
        self.state.trade_count += 1
        self.state.position = self.state.coincheck_position
        self.logger.trade(
            timestamp=jst_iso(),
            action=target.action,
            stage_index=target.stage_index,
            amount=amount,
            trigger_price=target.trigger_price,
            executable_spread=target.executable_spread,
            trend_spread=target.trend_spread,
            required_extra_edge=target.required_extra_edge,
            coincheck_side=target.coincheck_side,
            coincheck_expected_price=target.coincheck_expected_price,
            coincheck_average_price=coincheck_average_price,
            coincheck_order_id=coincheck_order_id,
            bitflyer_side=target.bitflyer_side,
            bitflyer_expected_price=target.bitflyer_expected_price,
            bitflyer_average_price=bitflyer_average_price,
            bitflyer_acceptance_id=bitflyer_acceptance_id,
            cashflow_jpy=cashflow,
            position=self.state.position,
            coincheck_position=self.state.coincheck_position,
            bitflyer_position=self.state.bitflyer_position,
            unhedged_position=self.state.unhedged_position,
            realized_pnl_jpy=self.state.realized_pnl_jpy,
            dry_run=self.config.dry_run,
        )

    def _apply_coincheck_position(self, action: str, amount: Decimal) -> None:
        if action == "BUY":
            self.state.coincheck_position += amount
        else:
            self.state.coincheck_position -= amount
        self.state.position = self.state.coincheck_position

    def _apply_bitflyer_position(self, side: str, amount: Decimal) -> None:
        if side == "SELL":
            self.state.bitflyer_position += amount
        else:
            self.state.bitflyer_position -= amount


async def run_bot(config: BotConfig) -> None:
    state = BotState()
    logger = TradeLogger(config.log_dir)
    broadcaster = Broadcaster()
    stop = asyncio.Event()
    web = WebApp(config, state, broadcaster)
    quote_feed = WebSocketQuoteFeed(config, state, logger)
    trader = ArbitrageTrader(config, state, logger)

    def request_stop() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    logger.event("bot_started", config=asdict(config))
    web.start_http()
    print(f"web app: http://{config.web_host}:{config.web_port}/")
    print("mode: DRY RUN" if config.dry_run else "mode: LIVE")
    print(f"logs: {config.log_dir}")
    tasks = [
        asyncio.create_task(quote_feed.run(stop)),
        asyncio.create_task(trader.run(stop)),
        asyncio.create_task(web.run_ws(stop)),
        asyncio.create_task(web.publish_loop(stop)),
    ]
    try:
        await stop.wait()
    finally:
        stop.set()
        await trader.shutdown("process_shutdown")
        await asyncio.gather(*tasks, return_exceptions=True)
        web.stop_http()
        logger.event("bot_stopped")
        logger.close()


def main(argv: Iterable[str] | None = None) -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = config_from_args(args)
    asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
