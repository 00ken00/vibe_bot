from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import time
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from vibe_bot.bitbank import PublicWebSocket as BitbankPublicWebSocket
from vibe_bot.bitflyer import PrivateClient as BitflyerPrivateClient
from vibe_bot.trades.bitbank_bitflyer.arbitrage import ArbitrageTrader
from vibe_bot.trades.bitbank_bitflyer.config import BotConfig
from vibe_bot.trades.bitbank_bitflyer.config import build_parser as build_base_parser
from vibe_bot.trades.bitbank_bitflyer.config import config_from_args
from vibe_bot.trades.bitbank_bitflyer.config import parse_hhmmss
from vibe_bot.trades.bitbank_bitflyer.logging import Broadcaster
from vibe_bot.trades.bitbank_bitflyer.logging import TradeLogger
from vibe_bot.trades.bitbank_bitflyer.models import BitbankTransaction
from vibe_bot.trades.bitbank_bitflyer.models import BotState
from vibe_bot.trades.bitbank_bitflyer.models import MakerOrder
from vibe_bot.trades.bitbank_bitflyer.quotes import WebSocketQuoteFeed
from vibe_bot.trades.bitbank_bitflyer.utils import JST
from vibe_bot.trades.bitbank_bitflyer.utils import jst_iso
from vibe_bot.trades.bitbank_bitflyer.web import WebApp

LOGGER = logging.getLogger("vibe_bot.trades.bitbank_bitflyer.bitflyer_only")


class BitflyerOnlyTrader:
    """Trades only bitFlyer from synthetic bitbank maker fills.

    This is not true arbitrage. It uses the same target-selection logic as the
    bitbank/bitFlyer arbitrage bot, but treats bitbank public transactions as a
    synthetic maker fill signal and only sends the bitFlyer hedge leg.
    """

    def __init__(self, config: BotConfig, state: BotState, logger: TradeLogger) -> None:
        self.config = config
        self.state = state
        self.logger = logger
        self._selector = ArbitrageTrader(config, state, logger)
        self._bf_private: BitflyerPrivateClient | None = None
        self._last_target_check = 0.0
        self._synthetic_order_seq = 0

    async def run(self, stop: asyncio.Event) -> None:
        if not self.config.dry_run:
            self._bf_private = BitflyerPrivateClient(
                private_trace=self._log_private_api_trace
            )
            await self._initialize_bitflyer_position()
        tasks = [
            asyncio.create_task(self._target_loop(stop)),
            asyncio.create_task(self._transaction_loop(stop)),
        ]
        try:
            await stop.wait()
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self._bf_private is not None:
                await self._bf_private.aclose()

    async def _target_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                if not self.state.quote.ready:
                    await asyncio.sleep(self.config.maker_update_interval)
                    continue
                self.state.stage_status = self._selector._stage_status()
                target = self._selector._choose_target()
                if target is None:
                    if self.state.active_maker is not None:
                        self.logger.event(
                            "synthetic_maker_removed",
                            reason="no_target",
                            maker=asdict(self.state.active_maker),
                        )
                    self.state.active_maker = None
                elif not self._same_synthetic_maker(self.state.active_maker, target):
                    self._synthetic_order_seq += 1
                    target.order_id = f"SYNTH-{self._synthetic_order_seq}"
                    target.placed_at = time.time()
                    target.executed_amount = Decimal("0")
                    self.state.active_maker = target
                    self.logger.event(
                        "synthetic_maker_placed",
                        maker=asdict(target),
                        quote={
                            "bitbank_bid": self.state.quote.bitbank_bid,
                            "bitbank_ask": self.state.quote.bitbank_ask,
                            "bitbank_buy_maker": self.state.quote.bitbank_buy_maker,
                            "bitbank_sell_maker": self.state.quote.bitbank_sell_maker,
                            "bitflyer_bid_vwap": self.state.quote.bitflyer_bid_vwap,
                            "bitflyer_ask_vwap": self.state.quote.bitflyer_ask_vwap,
                            "buy_price": self.state.quote.buy_price,
                            "sell_price": self.state.quote.sell_price,
                        },
                        queue_model="front_of_queue",
                        queue_model_note=(
                            "Current simulation ignores queue_ahead_size and assumes "
                            "the synthetic maker is first at its price. A future "
                            "queue-aware model should capture book size at placement "
                            "and require matched_trade_volume > queue_ahead_size."
                        ),
                    )
                await asyncio.sleep(self.config.maker_update_interval)
            except Exception as exc:
                self.state.last_error = f"bitFlyer-only target loop failed: {exc}"
                self.logger.event("error", message=self.state.last_error)
                LOGGER.exception("target loop failed")
                await asyncio.sleep(1.0)

    async def _transaction_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with BitbankPublicWebSocket() as ws:
                    await ws.subscribe(f"transactions_{self.config.bitbank_pair}")
                    self.logger.event("bitbank_transactions_ws_connected")
                    async for msg in ws.messages():
                        if stop.is_set():
                            return
                        for tx in self._transactions_from_message(msg):
                            await self._handle_transaction(tx)
            except Exception as exc:
                if stop.is_set():
                    return
                self.state.last_error = f"bitbank transaction websocket failed: {exc}"
                self.logger.event("error", message=self.state.last_error)
                LOGGER.exception("transaction websocket failed")
                await asyncio.sleep(1.0)

    async def _handle_transaction(self, tx: dict[str, object]) -> None:
        self._publish_latest_bitbank_transaction(tx)
        maker = self.state.active_maker
        if maker is None:
            return
        tx_side = str(tx.get("side") or "").lower()
        tx_price = Decimal(str(tx.get("price")))
        tx_amount = Decimal(str(tx.get("amount")))
        if tx_amount <= 0:
            return
        if not self._matches_synthetic_maker(maker, tx_side, tx_price):
            return

        # Simplified fill model: assume this synthetic maker is first in queue at
        # its price, so each matching public transaction consumes our order first.
        # Future queue-aware optimization should record book size at placement and
        # only fill after matched_trade_volume exceeds queue_ahead_size.
        remaining = maker.amount - maker.executed_amount
        if remaining <= 0:
            return
        fill_amount = min(remaining, tx_amount)
        maker.executed_amount += fill_amount
        self.logger.event(
            "synthetic_maker_filled",
            maker=asdict(maker),
            transaction=tx,
            fill_amount=fill_amount,
            remaining_amount=maker.amount - maker.executed_amount,
        )

        if fill_amount < self.config.bitflyer_min_order_size:
            self.logger.event(
                "bitflyer_only_fill_skipped",
                reason="below_bitflyer_min_order_size",
                fill_amount=fill_amount,
                bitflyer_min_order_size=self.config.bitflyer_min_order_size,
                maker=asdict(maker),
                transaction=tx,
            )
            return

        await self._execute_bitflyer_leg(maker, fill_amount, tx_price)
        if maker.executed_amount >= maker.amount:
            self.logger.event("synthetic_maker_done", maker=asdict(maker))
            self.state.active_maker = None

    async def _execute_bitflyer_leg(
        self, maker: MakerOrder, amount: Decimal, synthetic_bitbank_price: Decimal
    ) -> None:
        bitflyer_side = "SELL" if maker.action == "BUY" else "BUY"
        expected_price = maker.expected_hedge_price
        actual_price: Decimal | None = None
        executed_amount = Decimal("0")
        if not self.config.dry_run and self._in_bitflyer_maintenance_guard():
            self.logger.event(
                "bitflyer_only_order_skipped",
                reason="bitflyer_maintenance_guard",
                side=bitflyer_side,
                amount=amount,
                maker=asdict(maker),
            )
            return
        if self.config.dry_run:
            actual_price = expected_price
            executed_amount = amount
            self.logger.event(
                "bitflyer_only_order_dry_run",
                side=bitflyer_side,
                amount=amount,
                expected_price=expected_price,
                maker=asdict(maker),
            )
        else:
            assert self._bf_private is not None
            self.logger.event(
                "bitflyer_only_order_attempt",
                side=bitflyer_side,
                amount=amount,
                expected_price=expected_price,
                maker=asdict(maker),
            )
            ack = await self._bf_private.send_child_order(
                product_code=self.config.bitflyer_product_code,
                child_order_type="MARKET",
                side=bitflyer_side,
                size=amount,
                time_in_force="IOC",
            )
            actual_price, executed_amount = await self._execution_summary(
                ack.child_order_acceptance_id, fallback=expected_price
            )

        if executed_amount <= 0 or actual_price is None:
            self.logger.event(
                "bitflyer_only_order_unfilled",
                side=bitflyer_side,
                amount=amount,
                maker=asdict(maker),
            )
            return

        bitflyer_fill_pnl = self._apply_bitflyer_pnl(
            bitflyer_side, executed_amount, actual_price
        )
        if bitflyer_side == "SELL":
            self.state.bitflyer_position += executed_amount
            cashflow = (actual_price - synthetic_bitbank_price) * executed_amount
            self.state.position += executed_amount
        else:
            self.state.bitflyer_position -= executed_amount
            cashflow = (synthetic_bitbank_price - actual_price) * executed_amount
            self.state.position -= executed_amount

        self.state.realized_pnl_jpy += cashflow
        self.state.filled_base += executed_amount
        self.state.trade_count += 1
        self.logger.event(
            "bitflyer_only_order_filled",
            side=bitflyer_side,
            requested_amount=amount,
            executed_amount=executed_amount,
            average_price=actual_price,
            synthetic_bitbank_price=synthetic_bitbank_price,
            cashflow_jpy=cashflow,
            bitflyer_fill_pnl_jpy=bitflyer_fill_pnl,
            bitflyer_realized_pnl_jpy=self.state.bitflyer_realized_pnl_jpy,
            bitflyer_open_cost_jpy=self.state.bitflyer_open_cost_jpy,
            bitflyer_cost_basis_ready=self.state.bitflyer_cost_basis_ready,
            position=self.state.position,
            bitflyer_position=self.state.bitflyer_position,
        )
        self.logger.trade(
            timestamp=jst_iso(),
            action=maker.action,
            bitbank_order_id=maker.order_id,
            bitbank_side=f"synthetic_{maker.side}",
            bitbank_price=synthetic_bitbank_price,
            bitbank_amount=executed_amount,
            bitflyer_side=bitflyer_side,
            bitflyer_expected_price=expected_price,
            bitflyer_average_price=actual_price,
            slippage_jpy=(
                expected_price - actual_price
                if bitflyer_side == "SELL"
                else actual_price - expected_price
            ),
            cashflow_jpy=cashflow,
            position=self.state.position,
            bitbank_position=self.state.position,
            bitflyer_position=self.state.bitflyer_position,
            unhedged_position=Decimal("0"),
            realized_pnl_jpy=self.state.realized_pnl_jpy,
            bitbank_fill_pnl_jpy=Decimal("0"),
            bitbank_realized_pnl_jpy=Decimal("0"),
            bitbank_open_cost_jpy=Decimal("0"),
            bitbank_cost_basis_ready=True,
            bitflyer_fill_pnl_jpy=bitflyer_fill_pnl,
            bitflyer_realized_pnl_jpy=self.state.bitflyer_realized_pnl_jpy,
            bitflyer_open_cost_jpy=self.state.bitflyer_open_cost_jpy,
            bitflyer_cost_basis_ready=self.state.bitflyer_cost_basis_ready,
            dry_run=self.config.dry_run,
            hedge_enabled=True,
            hedge_executed=not self.config.dry_run,
        )

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

    def _matches_synthetic_maker(
        self, maker: MakerOrder, tx_side: str, tx_price: Decimal
    ) -> bool:
        if maker.action == "BUY":
            return tx_side == "sell" and tx_price <= maker.price
        return tx_side == "buy" and tx_price >= maker.price

    def _transactions_from_message(self, msg: dict[str, object]) -> list[dict[str, object]]:
        message = msg.get("message") if isinstance(msg, dict) else None
        data = message.get("data") if isinstance(message, dict) else None
        if isinstance(data, dict):
            transactions = data.get("transactions")
            if isinstance(transactions, list):
                return [tx for tx in transactions if isinstance(tx, dict)]
            if all(key in data for key in ("side", "price", "amount")):
                return [data]
        if isinstance(data, list):
            return [tx for tx in data if isinstance(tx, dict)]
        return []

    def _publish_latest_bitbank_transaction(self, tx: dict[str, object]) -> None:
        self.state.latest_bitbank_transaction = BitbankTransaction(
            side=str(tx.get("side") or ""),
            price=Decimal(str(tx.get("price"))),
            amount=Decimal(str(tx.get("amount"))),
            transaction_id=(
                int(tx["transaction_id"]) if tx.get("transaction_id") is not None else None
            ),
            executed_at=int(tx["executed_at"]) if tx.get("executed_at") is not None else None,
            timestamp=time.time(),
        )

    def _same_synthetic_maker(
        self, current: MakerOrder | None, target: MakerOrder
    ) -> bool:
        if current is None:
            return False
        return (
            current.action == target.action
            and current.side == target.side
            and current.price == target.price
            and current.amount == target.amount
        )

    def _log_private_api_trace(self, payload: dict[str, object]) -> None:
        self.logger.event("private_api_trace", **payload)

    async def _initialize_bitflyer_position(self) -> None:
        assert self._bf_private is not None
        positions = await self._bf_private.positions(
            product_code=self.config.bitflyer_product_code
        )
        long_amount = Decimal("0")
        short_amount = Decimal("0")
        commission = Decimal("0")
        swap_point_accumulate = Decimal("0")
        sfd = Decimal("0")
        funding_fees = Decimal("0")
        unrealized_pnl = Decimal("0")
        position_details: list[dict[str, object]] = []
        for position in positions:
            if position.side == "BUY":
                long_amount += position.size
            elif position.side == "SELL":
                short_amount += position.size
            commission += position.commission
            swap_point_accumulate += position.swap_point_accumulate
            sfd += position.sfd or Decimal("0")
            funding_fees += position.funding_fees or Decimal("0")
            unrealized_pnl += position.pnl or Decimal("0")
            position_details.append(
                {
                    "side": position.side,
                    "price": position.price,
                    "size": position.size,
                    "commission": position.commission,
                    "swap_point_accumulate": position.swap_point_accumulate,
                    "sfd": position.sfd,
                    "funding_fees": position.funding_fees,
                    "pnl": position.pnl,
                    "open_date": position.open_date,
                }
            )
        net_position = short_amount - long_amount
        self.state.bitflyer_position = net_position
        self.state.position = net_position
        if net_position != 0:
            self.state.bitflyer_cost_basis_ready = False
        self.logger.event(
            "bitflyer_only_position_initialized",
            bitflyer_position=net_position,
            position=self.state.position,
            long_open_amount=long_amount,
            short_open_amount=short_amount,
            commission=commission,
            swap_point_accumulate=swap_point_accumulate,
            sfd=sfd,
            funding_fees=funding_fees,
            unrealized_pnl=unrealized_pnl,
            positions=position_details,
        )

    def _in_bitflyer_maintenance_guard(self) -> bool:
        if not self.config.bitflyer_maintenance_guard_enabled:
            return False
        now = datetime.now(JST)
        now_seconds = now.hour * 3600 + now.minute * 60 + now.second
        start_seconds = parse_hhmmss(self.config.bitflyer_maintenance_start_jst)
        end_seconds = parse_hhmmss(self.config.bitflyer_maintenance_end_jst)
        if start_seconds <= end_seconds:
            return start_seconds <= now_seconds <= end_seconds
        return now_seconds >= start_seconds or now_seconds <= end_seconds


async def run_bot(config: BotConfig) -> None:
    state = BotState()
    logger = TradeLogger(config.log_dir)
    broadcaster = Broadcaster()
    web = WebApp(config, state, broadcaster)
    quote_feed = WebSocketQuoteFeed(config, state, logger)
    trader = BitflyerOnlyTrader(config, state, logger)
    stop = asyncio.Event()

    def request_stop() -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_stop)
        except NotImplementedError:
            pass

    web.start_http()
    logger.event("bitflyer_only_bot_started", config=asdict(config))
    print(f"web app: http://{config.web_host}:{config.web_port}/")
    print("mode: DRY RUN" if config.dry_run else "mode: LIVE BITFLYER ONLY")
    print(f"log dir: {config.log_dir}")

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
        await asyncio.gather(*tasks, return_exceptions=True)
        web.stop_http()
        logger.event("bitflyer_only_bot_stopped")
        logger.close()


def build_parser() -> argparse.ArgumentParser:
    parser = build_base_parser()
    parser.description = (
        "bitFlyer-only experimental strategy driven by synthetic bitbank maker fills."
    )
    parser.set_defaults(log_dir=Path("logs/trades/bitbank_bitflyer_only"))
    return parser


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.disable_bitflyer_hedge:
        raise SystemExit("--disable-bitflyer-hedge is not valid for bitFlyer-only mode")
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = replace(config_from_args(args), hedge_enabled=True)
    asyncio.run(run_bot(config))


if __name__ == "__main__":
    main()
