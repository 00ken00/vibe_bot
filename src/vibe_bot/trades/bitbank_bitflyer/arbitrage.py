from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Iterable
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from dotenv import load_dotenv

from vibe_bot.bitbank import PrivateClient as BitbankPrivateClient
from vibe_bot.bitbank.models import PositionSide as BitbankPositionSide
from vibe_bot.bitflyer import PrivateClient as BitflyerPrivateClient
from vibe_bot.trades.bitbank_bitflyer.config import BotConfig
from vibe_bot.trades.bitbank_bitflyer.config import build_parser
from vibe_bot.trades.bitbank_bitflyer.config import config_from_args
from vibe_bot.trades.bitbank_bitflyer.config import parse_hhmmss
from vibe_bot.trades.bitbank_bitflyer.logging import Broadcaster
from vibe_bot.trades.bitbank_bitflyer.logging import TradeLogger
from vibe_bot.trades.bitbank_bitflyer.logging import event_summary
from vibe_bot.trades.bitbank_bitflyer.models import BotAction
from vibe_bot.trades.bitbank_bitflyer.models import BotState
from vibe_bot.trades.bitbank_bitflyer.models import MakerOrder
from vibe_bot.trades.bitbank_bitflyer.models import StageStatus
from vibe_bot.trades.bitbank_bitflyer.quotes import WebSocketQuoteFeed
from vibe_bot.trades.bitbank_bitflyer.utils import JST
from vibe_bot.trades.bitbank_bitflyer.utils import jst_iso
from vibe_bot.trades.bitbank_bitflyer.utils import quantize_down
from vibe_bot.trades.bitbank_bitflyer.utils import quantize_up
from vibe_bot.trades.bitbank_bitflyer.web import WebApp

LOGGER = logging.getLogger("vibe_bot.trades.bitbank_bitflyer.arbitrage")


def _jst_time_seconds() -> int:
    now = datetime.now(JST)
    return now.hour * 3600 + now.minute * 60 + now.second


class ArbitrageTrader:
    """Runs the arbitrage decision loop and optional live execution.

    Chooses whether the single bitbank maker should represent a BUY or SELL
    action, replaces stale maker quotes, and in live mode hedges bitbank fills
    with bitFlyer market orders. In dry-run mode it only simulates the maker
    quote selection and logs what would be maintained.
    """

    def __init__(self, config: BotConfig, state: BotState, logger: TradeLogger) -> None:
        self.config = config
        self.state = state
        self.logger = logger
        self._bb_private: BitbankPrivateClient | None = None
        self._bf_private: BitflyerPrivateClient | None = None
        self._shutdown_started = False

    async def run(self, stop: asyncio.Event) -> None:
        if not self.config.dry_run:
            self._bb_private = BitbankPrivateClient(
                private_trace=self._log_private_api_trace
            )
            self._bf_private = BitflyerPrivateClient(
                private_trace=self._log_private_api_trace
            )
        try:
            if not self.config.dry_run:
                try:
                    await self._initialize_live_position()
                except Exception as exc:
                    self.state.last_error = f"position initialization failed: {exc}"
                    self.logger.event("error", message=self.state.last_error)
                    stop.set()
                    raise
            self.state.stage_status = self._stage_status()
            while not stop.is_set():
                try:
                    await self._tick()
                    self.state.last_error = ""
                except Exception as exc:
                    self.state.last_error = f"trader tick failed: {exc}"
                    self.logger.event("error", message=self.state.last_error)
                    LOGGER.exception("trader tick failed")
                await asyncio.sleep(self.config.maker_update_interval)
        finally:
            await self.shutdown("shutdown")

    async def shutdown(self, reason: str) -> None:
        if self._shutdown_started:
            return
        self._shutdown_started = True
        self.logger.event("trader_shutdown_started", reason=reason)
        try:
            await self._cancel_active_maker(reason)
        except Exception as exc:
            self.state.last_error = f"shutdown maker cancel failed: {exc}"
            self.logger.event("trader_shutdown_cancel_failed", reason=reason, error=str(exc))
            LOGGER.exception("shutdown maker cancel failed")
        finally:
            if self._bb_private is not None:
                await self._bb_private.aclose()
                self._bb_private = None
            if self._bf_private is not None:
                await self._bf_private.aclose()
                self._bf_private = None
            self.logger.event("trader_shutdown_finished", reason=reason)

    async def _tick(self) -> None:
        quote = self.state.quote
        if not quote.ready:
            self.state.set_action(BotAction.WAITING_FOR_QUOTES, "quote.ready=false")
            return
        self.state.stage_status = self._stage_status()
        await self._refresh_active_maker()
        if self._in_bitflyer_maintenance_guard():
            self.state.set_action(
                BotAction.IDLE,
                event_summary(
                    "bitflyer_maintenance_guard",
                    start_jst=self.config.bitflyer_maintenance_start_jst,
                    end_jst=self.config.bitflyer_maintenance_end_jst,
                ),
            )
            await self._cancel_active_maker("bitflyer_maintenance_guard")
            return
        if (
            not self.config.dry_run
            and self.config.hedge_enabled
            and abs(self.state.unhedged_position) >= self.config.bitflyer_min_order_size
        ):
            await self._repair_unhedged_position()
            if abs(self.state.unhedged_position) >= self.config.bitflyer_min_order_size:
                self.state.set_action(
                    BotAction.IDLE,
                    event_summary(
                        "unhedged_position_blocks_maker",
                        unhedged_position=self.state.unhedged_position,
                    ),
                )
                await self._cancel_active_maker("unhedged_position")
                return
        target = self._choose_target()
        if target is None:
            self.state.set_action(BotAction.IDLE, "target=None")
            await self._cancel_active_maker("no_target")
            return
        if self._same_maker(self.state.active_maker, target):
            self.state.set_action(
                BotAction.maintain(target.action),
                event_summary("maker_maintained", maker=asdict(target)),
            )
            return
        await self._replace_maker(target)

    def _in_bitflyer_maintenance_guard(self) -> bool:
        if not self.config.bitflyer_maintenance_guard_enabled:
            return False
        now_seconds = _jst_time_seconds()
        start_seconds = parse_hhmmss(self.config.bitflyer_maintenance_start_jst)
        end_seconds = parse_hhmmss(self.config.bitflyer_maintenance_end_jst)
        if start_seconds <= end_seconds:
            return start_seconds <= now_seconds < end_seconds
        return now_seconds >= start_seconds or now_seconds < end_seconds

    def _choose_target(self) -> MakerOrder | None:
        quote = self.state.quote
        assert quote.ready
        buy_price = quote.buy_price
        sell_price = quote.sell_price
        assert buy_price is not None and sell_price is not None
        threshold = self.config.threshold_jpy
        offset = self.config.threshold_offset_jpy
        position = self.state.position
        stage_status = self.state.stage_status

        if position > 0:
            current_stage = stage_status.current_stage
            close_trigger = stage_status.long_close_trigger
            assert close_trigger is not None
            if sell_price > close_trigger:
                amount = stage_status.close_amount
                assert amount is not None
                return self._build_target("SELL", close_trigger, amount, current_stage)
            next_stage = stage_status.next_stage
            open_trigger = stage_status.long_open_trigger
            if next_stage is not None and open_trigger is not None and buy_price < open_trigger:
                amount = stage_status.next_open_amount
                assert amount is not None
                return self._build_target("BUY", open_trigger, amount, next_stage)
            return None
        if position < 0:
            current_stage = stage_status.current_stage
            close_trigger = stage_status.short_close_trigger
            assert close_trigger is not None
            if buy_price < close_trigger:
                amount = stage_status.close_amount
                assert amount is not None
                return self._build_target("BUY", close_trigger, amount, current_stage)
            next_stage = stage_status.next_stage
            open_trigger = stage_status.short_open_trigger
            if next_stage is not None and open_trigger is not None and sell_price > open_trigger:
                amount = stage_status.next_open_amount
                assert amount is not None
                return self._build_target("SELL", open_trigger, amount, next_stage)
            return None

        buy_open_trigger = offset - threshold
        sell_open_trigger = offset + threshold
        buy_edge = buy_open_trigger - buy_price
        sell_edge = sell_price - sell_open_trigger
        amount = stage_status.next_open_amount
        assert amount is not None
        if buy_edge <= 0 and sell_edge <= 0:
            return None
        if sell_edge > buy_edge:
            return self._build_target("SELL", sell_open_trigger, amount, 1)
        return self._build_target("BUY", buy_open_trigger, amount, 1)

    def _stage_status(self) -> StageStatus:
        position = self.state.position
        abs_position = abs(position)
        threshold = self.config.threshold_jpy
        offset = self.config.threshold_offset_jpy
        stage_size = self.config.stage_size
        max_position = self.config.max_position

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
            stage_size=stage_size,
            max_stages=self.config.max_stages,
            max_position=max_position,
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

    def _build_target(
        self,
        action: str,
        trigger: Decimal,
        amount: Decimal,
        stage_index: int,
    ) -> MakerOrder | None:
        quote = self.state.quote
        assert quote.ready
        if amount < self.config.min_order_size:
            return None
        assert quote.bitbank_bid is not None
        assert quote.bitbank_ask is not None
        assert quote.bitflyer_bid is not None
        assert quote.bitflyer_ask is not None
        assert quote.bitflyer_bid_vwap is not None
        assert quote.bitflyer_ask_vwap is not None
        position_side: BitbankPositionSide | None = None
        if action == "BUY":
            passive = quote.bitbank_ask - self.config.tick_size
            profitable = quote.bitflyer_bid_vwap + trigger
            price = quantize_down(min(passive, profitable), self.config.tick_size)
            expected_hedge = quote.bitflyer_bid_vwap
            side = "buy"
            if self.state.position < 0:
                position_side = "short"
        else:
            passive = quote.bitbank_bid + self.config.tick_size
            profitable = quote.bitflyer_ask_vwap + trigger
            price = quantize_up(max(passive, profitable), self.config.tick_size)
            expected_hedge = quote.bitflyer_ask_vwap
            side = "sell"
            if self.state.position <= 0:
                position_side = "short"
        if action == "BUY" and price >= quote.bitbank_ask:
            return None
        if action == "SELL" and price <= quote.bitbank_bid:
            return None
        if price <= 0:
            return None
        return MakerOrder(
            action=action,
            side=side,
            position_side=position_side,
            price=price,
            amount=amount,
            trigger_price=trigger,
            expected_hedge_price=expected_hedge,
            stage_index=stage_index,
        )

    def _same_maker(self, current: MakerOrder | None, target: MakerOrder) -> bool:
        if current is None:
            return False
        current_remaining = current.amount - current.executed_amount
        return (
            current.action == target.action
            and current.side == target.side
            and current.price == target.price
            and current_remaining == target.amount
        )

    def _log_private_api_trace(self, payload: dict[str, object]) -> None:
        self.logger.event("private_api_trace", **payload)

    async def _initialize_live_position(self) -> None:
        assert self._bb_private is not None
        assert self._bf_private is not None
        bitbank_position, bitbank_components = await self._bitbank_strategy_position()
        bitflyer_position, bitflyer_components = await self._bitflyer_strategy_position()
        mismatch = abs(bitbank_position - bitflyer_position)
        tolerance = self.config.bitflyer_min_order_size
        payload = {
            "bitbank_position": bitbank_position,
            "bitflyer_position": bitflyer_position,
            "mismatch": mismatch,
            "tolerance": tolerance,
            "hedge_enabled": self.config.hedge_enabled,
            "bitbank": bitbank_components,
            "bitflyer": bitflyer_components,
        }
        if self.config.hedge_enabled and mismatch > tolerance:
            self.logger.event("position_initialization_mismatch", **payload)
            raise RuntimeError(
                "bitbank and bitFlyer positions disagree: "
                f"bitbank={bitbank_position}, bitflyer={bitflyer_position}, "
                f"mismatch={mismatch}, tolerance={tolerance}"
            )
        self.state.bitbank_position = bitbank_position
        self.state.bitflyer_position = bitflyer_position
        self.state.position = self.state.bitbank_position
        if bitflyer_position == 0:
            self.state.bitflyer_cost_basis_ready = True
            self.state.bitflyer_open_cost_jpy = Decimal("0")
        else:
            self.state.bitflyer_cost_basis_ready = False
            self.state.bitflyer_open_cost_jpy = Decimal("0")
            self.logger.event(
                "bitflyer_pnl_cost_basis_unavailable",
                reason="bot_started_with_nonzero_bitflyer_position",
                position=bitflyer_position,
            )
        if bitbank_position == 0:
            self.state.bitbank_cost_basis_ready = True
            self.state.bitbank_open_cost_jpy = Decimal("0")
        else:
            self.state.bitbank_cost_basis_ready = False
            self.state.bitbank_open_cost_jpy = Decimal("0")
            self.logger.event(
                "bitbank_pnl_cost_basis_unavailable",
                reason="bot_started_with_nonzero_bitbank_position",
                position=bitbank_position,
            )
        if not self.config.hedge_enabled and mismatch > tolerance:
            self.logger.event("position_initialization_mismatch_ignored", **payload)
        self.logger.event(
            "position_initialized",
            **payload,
            position=self.state.position,
            unhedged_position=self.state.unhedged_position,
        )

    async def _bitbank_strategy_position(
        self,
    ) -> tuple[Decimal, dict[str, Decimal | str]]:
        assert self._bb_private is not None
        base_asset = self.config.bitbank_pair.split("_", 1)[0].lower()
        assets = await self._bb_private.assets()
        spot_amount = Decimal("0")
        for asset in assets.assets:
            if asset.asset.lower() == base_asset:
                spot_amount = asset.onhand_amount
                break

        margin = await self._bb_private.margin_positions()
        margin_long = Decimal("0")
        margin_short = Decimal("0")
        for position in margin.positions:
            if position.pair != self.config.bitbank_pair:
                continue
            open_amount = position.open_amount or Decimal("0")
            if position.position_side == "long":
                margin_long += open_amount
            elif position.position_side == "short":
                margin_short += open_amount

        net_position = spot_amount + margin_long - margin_short
        return net_position, {
            "pair": self.config.bitbank_pair,
            "base_asset": base_asset,
            "spot_onhand_amount": spot_amount,
            "margin_long_open_amount": margin_long,
            "margin_short_open_amount": margin_short,
        }

    async def _bitflyer_strategy_position(
        self,
    ) -> tuple[Decimal, dict[str, Decimal | str]]:
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
        net_position = short_amount - long_amount
        return net_position, {
            "product_code": self.config.bitflyer_product_code,
            "long_open_amount": long_amount,
            "short_open_amount": short_amount,
        }

    async def _replace_maker(self, target: MakerOrder) -> None:
        await self._cancel_active_maker("replace")
        if self.config.dry_run:
            target.order_id = "DRY-RUN"
            self.state.active_maker = target
            self.state.set_action(
                BotAction.dry_run_quote(target.action),
                event_summary("maker_quote", dry_run=True, maker=asdict(target)),
            )
            self.logger.event("maker_quote", dry_run=True, maker=asdict(target))
            return
        assert self._bb_private is not None
        self.logger.event(
            "maker_place_attempt",
            pair=self.config.bitbank_pair,
            order_type="limit",
            post_only=True,
            maker=asdict(target),
            quote={
                "bitbank_bid": self.state.quote.bitbank_bid,
                "bitbank_ask": self.state.quote.bitbank_ask,
                "bitbank_buy_maker": self.state.quote.bitbank_buy_maker,
                "bitbank_sell_maker": self.state.quote.bitbank_sell_maker,
                "bitflyer_bid": self.state.quote.bitflyer_bid,
                "bitflyer_ask": self.state.quote.bitflyer_ask,
                "bitflyer_bid_vwap": self.state.quote.bitflyer_bid_vwap,
                "bitflyer_ask_vwap": self.state.quote.bitflyer_ask_vwap,
                "buy_price": self.state.quote.buy_price,
                "sell_price": self.state.quote.sell_price,
            },
        )
        order = await self._bb_private.place_order(
            pair=self.config.bitbank_pair,
            side=target.side,
            order_type="limit",
            amount=target.amount,
            price=target.price,
            post_only=True,
            position_side=target.position_side,
        )
        target.order_id = str(order.order_id)
        target.executed_amount = order.executed_amount
        self.state.active_maker = target
        self.state.set_action(
            BotAction.placed(target.action),
            event_summary("maker_placed", maker=asdict(target)),
        )
        self.logger.event("maker_placed", maker=asdict(target))

    async def _cancel_active_maker(self, reason: str) -> None:
        maker = self.state.active_maker
        if maker is None:
            return
        self.state.active_maker = None
        self.state.set_action(
            BotAction.CANCELING_MAKER,
            event_summary(
                "maker_cancel_attempt",
                reason=reason,
                order_id=maker.order_id,
                maker=asdict(maker),
            ),
        )
        if self.config.dry_run or maker.order_id in (None, "DRY-RUN"):
            self.logger.event("maker_removed", reason=reason, dry_run=True, maker=asdict(maker))
            self.state.set_action(
                BotAction.CANCELED_MAKER,
                event_summary(
                    "maker_removed", reason=reason, dry_run=True, maker=asdict(maker)
                ),
            )
            return
        assert self._bb_private is not None
        try:
            await self._bb_private.cancel_order(
                pair=self.config.bitbank_pair, order_id=maker.order_id
            )
            self.logger.event("maker_canceled", reason=reason, maker=asdict(maker))
            self.state.set_action(
                BotAction.CANCELED_MAKER,
                event_summary("maker_canceled", reason=reason, maker=asdict(maker)),
            )
        except Exception as exc:
            self.logger.event(
                "maker_cancel_failed", reason=reason, error=str(exc), maker=asdict(maker)
            )
            self.state.set_action(
                BotAction.CANCEL_FAILED,
                event_summary(
                    "maker_cancel_failed",
                    reason=reason,
                    error=str(exc),
                    maker=asdict(maker),
                ),
            )
            raise

    async def _refresh_active_maker(self) -> None:
        maker = self.state.active_maker
        if maker is None or self.config.dry_run or maker.order_id in (None, "DRY-RUN"):
            return
        assert self._bb_private is not None
        order = await self._bb_private.order_info(
            pair=self.config.bitbank_pair, order_id=maker.order_id
        )
        delta = order.executed_amount - maker.executed_amount
        maker.executed_amount = order.executed_amount
        if delta > 0:
            fill_price = order.average_price or maker.price
            fill_event = {
                "maker": asdict(maker),
                "bitbank_order_id": maker.order_id,
                "fill_amount": delta,
                "cumulative_executed_amount": maker.executed_amount,
                "fill_price": fill_price,
                "order_status": order.status,
            }
            self.state.set_action(
                BotAction.MAKER_FILLED,
                event_summary("maker_filled", **fill_event),
            )
            self.logger.event(
                "maker_filled",
                **fill_event,
            )
            await self._hedge_fill(maker, delta, fill_price)
        if order.status in ("FULLY_FILLED", "CANCELED_UNFILLED", "CANCELED_PARTIALLY_FILLED", "REJECTED"):
            self.state.active_maker = None
            self.logger.event("maker_done", status=order.status, maker=asdict(maker))

    def _apply_bitbank_pnl(
        self, maker: MakerOrder, amount: Decimal, bitbank_fill_price: Decimal
    ) -> Decimal:
        previous_position = self.state.position
        if previous_position == 0:
            self.state.bitbank_cost_basis_ready = True
            self.state.bitbank_open_cost_jpy = Decimal("0")

        if not self.state.bitbank_cost_basis_ready:
            closes_unknown_position = (
                maker.action == "SELL"
                and previous_position > 0
                and amount >= previous_position
            ) or (
                maker.action == "BUY"
                and previous_position < 0
                and amount >= abs(previous_position)
            )
            self.logger.event(
                "bitbank_pnl_skipped",
                reason="cost_basis_unavailable",
                action=maker.action,
                previous_position=previous_position,
                fill_amount=amount,
                fill_price=bitbank_fill_price,
            )
            if closes_unknown_position:
                leftover = amount - abs(previous_position)
                self.state.bitbank_cost_basis_ready = True
                self.state.bitbank_open_cost_jpy = bitbank_fill_price * leftover
            return Decimal("0")

        realized = Decimal("0")
        remaining_open_cost = self.state.bitbank_open_cost_jpy

        if maker.action == "BUY":
            if previous_position < 0:
                close_amount = min(amount, abs(previous_position))
                average_entry = remaining_open_cost / abs(previous_position)
                realized = (average_entry - bitbank_fill_price) * close_amount
                remaining_open_cost -= average_entry * close_amount
                leftover = amount - close_amount
                if leftover > 0:
                    remaining_open_cost = bitbank_fill_price * leftover
            else:
                remaining_open_cost += bitbank_fill_price * amount
        else:
            if previous_position > 0:
                close_amount = min(amount, previous_position)
                average_entry = remaining_open_cost / previous_position
                realized = (bitbank_fill_price - average_entry) * close_amount
                remaining_open_cost -= average_entry * close_amount
                leftover = amount - close_amount
                if leftover > 0:
                    remaining_open_cost = bitbank_fill_price * leftover
            else:
                remaining_open_cost += bitbank_fill_price * amount

        next_position = (
            previous_position + amount
            if maker.action == "BUY"
            else previous_position - amount
        )
        if next_position == 0:
            remaining_open_cost = Decimal("0")

        self.state.bitbank_open_cost_jpy = remaining_open_cost
        self.state.bitbank_realized_pnl_jpy += realized
        return realized

    def _apply_bitflyer_pnl(
        self, side: str, amount: Decimal, average_price: Decimal
    ) -> Decimal:
        previous_position = self.state.bitflyer_position
        if previous_position == 0:
            self.state.bitflyer_cost_basis_ready = True
            self.state.bitflyer_open_cost_jpy = Decimal("0")

        signed_amount = amount if side == "SELL" else -amount
        if not self.state.bitflyer_cost_basis_ready:
            closes_unknown_position = (
                side == "BUY"
                and previous_position > 0
                and amount >= previous_position
            ) or (
                side == "SELL"
                and previous_position < 0
                and amount >= abs(previous_position)
            )
            self.logger.event(
                "bitflyer_pnl_skipped",
                reason="cost_basis_unavailable",
                side=side,
                previous_position=previous_position,
                fill_amount=amount,
                fill_price=average_price,
            )
            if closes_unknown_position:
                leftover = amount - abs(previous_position)
                self.state.bitflyer_cost_basis_ready = True
                self.state.bitflyer_open_cost_jpy = average_price * leftover
            return Decimal("0")

        realized = Decimal("0")
        remaining_open_cost = self.state.bitflyer_open_cost_jpy

        if side == "SELL":
            if previous_position < 0:
                close_amount = min(amount, abs(previous_position))
                average_entry = remaining_open_cost / abs(previous_position)
                realized = (average_price - average_entry) * close_amount
                remaining_open_cost -= average_entry * close_amount
                leftover = amount - close_amount
                if leftover > 0:
                    remaining_open_cost = average_price * leftover
            else:
                remaining_open_cost += average_price * amount
        else:
            if previous_position > 0:
                close_amount = min(amount, previous_position)
                average_entry = remaining_open_cost / previous_position
                realized = (average_entry - average_price) * close_amount
                remaining_open_cost -= average_entry * close_amount
                leftover = amount - close_amount
                if leftover > 0:
                    remaining_open_cost = average_price * leftover
            else:
                remaining_open_cost += average_price * amount

        next_position = previous_position + signed_amount
        if next_position == 0:
            remaining_open_cost = Decimal("0")

        self.state.bitflyer_open_cost_jpy = remaining_open_cost
        self.state.bitflyer_realized_pnl_jpy += realized
        return realized

    async def _hedge_fill(
        self, maker: MakerOrder, amount: Decimal, bitbank_fill_price: Decimal
    ) -> None:
        bitbank_fill_pnl = self._apply_bitbank_pnl(maker, amount, bitbank_fill_price)
        if maker.action == "BUY":
            self.state.bitbank_position += amount
        else:
            self.state.bitbank_position -= amount
        self.state.position = self.state.bitbank_position
        self.logger.event(
            "bitbank_position_updated",
            maker=asdict(maker),
            fill_amount=amount,
            bitbank_position=self.state.bitbank_position,
            bitflyer_position=self.state.bitflyer_position,
            unhedged_position=self.state.unhedged_position,
        )

        hedge_amount = abs(self.state.unhedged_position)
        bitflyer_side = (
            "SELL"
            if self.state.unhedged_position > 0
            else "BUY"
            if self.state.unhedged_position < 0
            else None
        )
        expected_hedge_price = (
            maker.expected_hedge_price if hedge_amount > 0 else Decimal("0")
        )
        actual_hedge_price: Decimal | None = None
        hedge_executed_amount = Decimal("0")
        hedge_executed = False
        bitflyer_fill_pnl = Decimal("0")
        if (
            not self.config.dry_run
            and self.config.hedge_enabled
            and hedge_amount >= self.config.bitflyer_min_order_size
            and not self._in_bitflyer_maintenance_guard()
        ):
            assert self._bf_private is not None
            assert bitflyer_side is not None
            self.logger.event(
                "bitflyer_hedge_attempt",
                side=bitflyer_side,
                amount=hedge_amount,
                bitbank_position=self.state.bitbank_position,
                bitflyer_position=self.state.bitflyer_position,
                unhedged_position=self.state.unhedged_position,
            )
            ack = await self._bf_private.send_child_order(
                product_code=self.config.bitflyer_product_code,
                child_order_type="MARKET",
                side=bitflyer_side,
                size=hedge_amount,
                time_in_force="IOC",
            )
            actual_hedge_price, hedge_executed_amount = await self._execution_summary(
                ack.child_order_acceptance_id, fallback=expected_hedge_price
            )
            if hedge_executed_amount > 0:
                bitflyer_fill_pnl = self._apply_bitflyer_pnl(
                    bitflyer_side, hedge_executed_amount, actual_hedge_price
                )
                if bitflyer_side == "SELL":
                    self.state.bitflyer_position += hedge_executed_amount
                else:
                    self.state.bitflyer_position -= hedge_executed_amount
                hedge_executed = True
                self.logger.event(
                    "bitflyer_position_updated",
                    side=bitflyer_side,
                    executed_amount=hedge_executed_amount,
                    average_price=actual_hedge_price,
                    bitbank_position=self.state.bitbank_position,
                    bitflyer_position=self.state.bitflyer_position,
                    unhedged_position=self.state.unhedged_position,
                )
            else:
                self.logger.event(
                    "bitflyer_hedge_unfilled",
                    side=bitflyer_side,
                    amount=hedge_amount,
                    bitbank_position=self.state.bitbank_position,
                    bitflyer_position=self.state.bitflyer_position,
                    unhedged_position=self.state.unhedged_position,
                )
        elif self.config.hedge_enabled and hedge_amount > 0:
            self.logger.event(
                "bitflyer_hedge_deferred",
                reason=(
                    "dry_run"
                    if self.config.dry_run
                    else "bitflyer_maintenance_guard"
                    if self._in_bitflyer_maintenance_guard()
                    else "below_bitflyer_min_order_size"
                ),
                side=bitflyer_side,
                amount=hedge_amount,
                bitflyer_min_order_size=self.config.bitflyer_min_order_size,
                bitbank_position=self.state.bitbank_position,
                bitflyer_position=self.state.bitflyer_position,
                unhedged_position=self.state.unhedged_position,
            )
        elif not self.config.dry_run:
            self.logger.event(
                "bitflyer_hedge_skipped",
                reason="hedge_disabled",
                maker=asdict(maker),
                bitflyer_side=bitflyer_side,
                amount=hedge_amount,
                expected_hedge_price=expected_hedge_price,
            )

        if (
            hedge_executed
            and actual_hedge_price is not None
            and hedge_executed_amount == amount
        ):
            if bitflyer_side == "SELL":
                cashflow = (actual_hedge_price - bitbank_fill_price) * hedge_executed_amount
                slippage = expected_hedge_price - actual_hedge_price
            else:
                cashflow = (bitbank_fill_price - actual_hedge_price) * hedge_executed_amount
                slippage = actual_hedge_price - expected_hedge_price
        else:
            cashflow = Decimal("0")
            slippage = None
            if hedge_executed and actual_hedge_price is not None:
                self.logger.event(
                    "combined_pnl_skipped",
                    reason="hedge_size_differs_from_current_bitbank_fill",
                    bitbank_fill_amount=amount,
                    hedge_executed_amount=hedge_executed_amount,
                    bitbank_fill_price=bitbank_fill_price,
                    hedge_average_price=actual_hedge_price,
                )

        self.state.realized_pnl_jpy += cashflow
        self.state.filled_base += amount
        self.state.trade_count += 1
        self.logger.trade(
            timestamp=jst_iso(),
            action=maker.action,
            bitbank_order_id=maker.order_id,
            bitbank_side=maker.side,
            bitbank_price=bitbank_fill_price,
            bitbank_amount=amount,
            bitflyer_side=bitflyer_side,
            bitflyer_expected_price=expected_hedge_price,
            bitflyer_average_price=actual_hedge_price,
            slippage_jpy=slippage,
            cashflow_jpy=cashflow,
            position=self.state.position,
            bitbank_position=self.state.bitbank_position,
            bitflyer_position=self.state.bitflyer_position,
            unhedged_position=self.state.unhedged_position,
            realized_pnl_jpy=self.state.realized_pnl_jpy,
            bitbank_fill_pnl_jpy=bitbank_fill_pnl,
            bitbank_realized_pnl_jpy=self.state.bitbank_realized_pnl_jpy,
            bitbank_open_cost_jpy=self.state.bitbank_open_cost_jpy,
            bitbank_cost_basis_ready=self.state.bitbank_cost_basis_ready,
            bitflyer_fill_pnl_jpy=bitflyer_fill_pnl,
            bitflyer_realized_pnl_jpy=self.state.bitflyer_realized_pnl_jpy,
            bitflyer_open_cost_jpy=self.state.bitflyer_open_cost_jpy,
            bitflyer_cost_basis_ready=self.state.bitflyer_cost_basis_ready,
            dry_run=self.config.dry_run,
            hedge_enabled=self.config.hedge_enabled,
            hedge_executed=hedge_executed,
        )

    async def _repair_unhedged_position(self) -> None:
        quote = self.state.quote
        assert quote.ready
        hedge_amount = abs(self.state.unhedged_position)
        if hedge_amount < self.config.bitflyer_min_order_size:
            return
        if self.state.unhedged_position > 0:
            side = "SELL"
            expected_price = quote.bitflyer_bid_vwap
        else:
            side = "BUY"
            expected_price = quote.bitflyer_ask_vwap
        assert expected_price is not None
        self.logger.event(
            "bitflyer_hedge_repair_attempt",
            side=side,
            amount=hedge_amount,
            expected_price=expected_price,
            bitbank_position=self.state.bitbank_position,
            bitflyer_position=self.state.bitflyer_position,
            unhedged_position=self.state.unhedged_position,
        )
        assert self._bf_private is not None
        ack = await self._bf_private.send_child_order(
            product_code=self.config.bitflyer_product_code,
            child_order_type="MARKET",
            side=side,
            size=hedge_amount,
            time_in_force="IOC",
        )
        average_price, executed_amount = await self._execution_summary(
            ack.child_order_acceptance_id, fallback=expected_price
        )
        if executed_amount == 0:
            self.logger.event(
                "bitflyer_hedge_repair_unfilled",
                side=side,
                amount=hedge_amount,
                bitbank_position=self.state.bitbank_position,
                bitflyer_position=self.state.bitflyer_position,
                unhedged_position=self.state.unhedged_position,
            )
            return
        bitflyer_fill_pnl = self._apply_bitflyer_pnl(
            side, executed_amount, average_price
        )
        if side == "SELL":
            self.state.bitflyer_position += executed_amount
        else:
            self.state.bitflyer_position -= executed_amount
        self.logger.event(
            "bitflyer_hedge_repair_executed",
            side=side,
            requested_amount=hedge_amount,
            executed_amount=executed_amount,
            average_price=average_price,
            bitflyer_fill_pnl_jpy=bitflyer_fill_pnl,
            bitflyer_realized_pnl_jpy=self.state.bitflyer_realized_pnl_jpy,
            bitbank_position=self.state.bitbank_position,
            bitflyer_position=self.state.bitflyer_position,
            unhedged_position=self.state.unhedged_position,
        )

    async def _execution_summary(
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

    web.start_http()
    logger.event("bot_started", config=asdict(config))
    print(f"web app: http://{config.web_host}:{config.web_port}/")
    print("mode: DRY RUN" if config.dry_run else "mode: LIVE")

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
