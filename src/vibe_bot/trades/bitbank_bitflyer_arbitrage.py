from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import signal
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

from vibe_bot.bitbank import PrivateClient as BitbankPrivateClient
from vibe_bot.bitbank import PublicClient as BitbankPublicClient
from vibe_bot.bitflyer import PrivateClient as BitflyerPrivateClient
from vibe_bot.bitflyer import PublicClient as BitflyerPublicClient
from vibe_bot.trades.bitbank_bitflyer_web import WebApp

LOGGER = logging.getLogger("vibe_bot.trades.bitbank_bitflyer_arbitrage")


@dataclass(frozen=True)
class BotConfig:
    bitbank_pair: str = "btc_jpy"
    bitflyer_product_code: str = "FX_BTC_JPY"
    threshold_jpy: Decimal = Decimal("1000")
    order_size: Decimal = Decimal("0.001")
    max_position: Decimal = Decimal("0.003")
    maker_update_interval: float = 0.5
    quote_interval: float = 1.0
    tick_size: Decimal = Decimal("1")
    min_order_size: Decimal = Decimal("0.0001")
    dry_run: bool = True
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    ws_port: int = 8766
    log_dir: Path = Path("logs/trades/bitbank_bitflyer_arbitrage")


@dataclass
class Quote:
    bitbank_bid: Decimal | None = None
    bitbank_ask: Decimal | None = None
    bitflyer_bid: Decimal | None = None
    bitflyer_ask: Decimal | None = None
    timestamp: float = 0.0

    @property
    def ready(self) -> bool:
        return all(
            value is not None
            for value in (
                self.bitbank_bid,
                self.bitbank_ask,
                self.bitflyer_bid,
                self.bitflyer_ask,
            )
        )

    @property
    def buy_price(self) -> Decimal | None:
        if self.bitbank_ask is None or self.bitflyer_bid is None:
            return None
        return self.bitbank_ask - self.bitflyer_bid

    @property
    def sell_price(self) -> Decimal | None:
        if self.bitbank_bid is None or self.bitflyer_ask is None:
            return None
        return self.bitbank_bid - self.bitflyer_ask


@dataclass
class MakerOrder:
    action: str
    side: str
    price: Decimal
    amount: Decimal
    trigger_price: Decimal
    expected_hedge_price: Decimal
    order_id: str | None = None
    placed_at: float = field(default_factory=time.time)
    executed_amount: Decimal = Decimal("0")


@dataclass
class BotState:
    quote: Quote = field(default_factory=Quote)
    position: Decimal = Decimal("0")
    realized_pnl_jpy: Decimal = Decimal("0")
    filled_base: Decimal = Decimal("0")
    trade_count: int = 0
    active_maker: MakerOrder | None = None
    last_action: str = "idle"
    last_error: str = ""
    started_at: float = field(default_factory=time.time)


def decimal_to_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (Quote, MakerOrder, BotState)):
        return decimal_to_json(asdict(value))
    if isinstance(value, dict):
        return {k: decimal_to_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decimal_to_json(v) for v in value]
    return value


def utc_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).isoformat()


def quantize_down(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def quantize_up(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_UP) * tick


class TradeLogger:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d")
        self.events_path = self.log_dir / f"events-{stamp}.jsonl"
        self.trades_path = self.log_dir / f"trades-{stamp}.csv"
        self._csv_file = self.trades_path.open("a", newline="")
        self._csv = csv.DictWriter(
            self._csv_file,
            fieldnames=[
                "timestamp",
                "action",
                "bitbank_order_id",
                "bitbank_side",
                "bitbank_price",
                "bitbank_amount",
                "bitflyer_side",
                "bitflyer_expected_price",
                "bitflyer_average_price",
                "slippage_jpy",
                "cashflow_jpy",
                "position",
                "realized_pnl_jpy",
                "dry_run",
            ],
        )
        if self.trades_path.stat().st_size == 0:
            self._csv.writeheader()
            self._csv_file.flush()

    def close(self) -> None:
        self._csv_file.close()

    def event(self, event_type: str, **payload: Any) -> None:
        row = {"timestamp": utc_iso(), "event": event_type, **payload}
        with self.events_path.open("a") as f:
            f.write(json.dumps(decimal_to_json(row), separators=(",", ":")) + "\n")

    def trade(self, **payload: Any) -> None:
        self._csv.writerow(decimal_to_json(payload))
        self._csv_file.flush()


class Broadcaster:
    def __init__(self) -> None:
        self._clients: set[Any] = set()

    async def add(self, ws: Any) -> None:
        self._clients.add(ws)

    async def remove(self, ws: Any) -> None:
        self._clients.discard(ws)

    async def publish(self, payload: dict[str, Any]) -> None:
        if not self._clients:
            return
        message = json.dumps(decimal_to_json(payload), separators=(",", ":"))
        stale = []
        for ws in list(self._clients):
            try:
                await ws.send(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._clients.discard(ws)


class PricePoller:
    def __init__(self, config: BotConfig, state: BotState, logger: TradeLogger) -> None:
        self.config = config
        self.state = state
        self.logger = logger

    async def run(self, stop: asyncio.Event) -> None:
        async with BitbankPublicClient() as bitbank, BitflyerPublicClient() as bitflyer:
            while not stop.is_set():
                try:
                    bb_task = asyncio.create_task(bitbank.ticker(self.config.bitbank_pair))
                    bf_task = asyncio.create_task(
                        bitflyer.ticker(self.config.bitflyer_product_code)
                    )
                    bb, bf = await asyncio.gather(bb_task, bf_task)
                    self.state.quote = Quote(
                        bitbank_bid=bb.buy,
                        bitbank_ask=bb.sell,
                        bitflyer_bid=bf.best_bid,
                        bitflyer_ask=bf.best_ask,
                        timestamp=time.time(),
                    )
                    self.state.last_error = ""
                except Exception as exc:
                    self.state.last_error = f"price poll failed: {exc}"
                    self.logger.event("error", message=self.state.last_error)
                    LOGGER.exception("price poll failed")
                await asyncio.sleep(self.config.quote_interval)


class ArbitrageTrader:
    def __init__(self, config: BotConfig, state: BotState, logger: TradeLogger) -> None:
        self.config = config
        self.state = state
        self.logger = logger
        self._bb_private: BitbankPrivateClient | None = None
        self._bf_private: BitflyerPrivateClient | None = None

    async def run(self, stop: asyncio.Event) -> None:
        if not self.config.dry_run:
            self._bb_private = BitbankPrivateClient()
            self._bf_private = BitflyerPrivateClient()
        try:
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
            await self._cancel_active_maker("shutdown")
            if self._bb_private is not None:
                await self._bb_private.aclose()
            if self._bf_private is not None:
                await self._bf_private.aclose()

    async def _tick(self) -> None:
        quote = self.state.quote
        if not quote.ready:
            self.state.last_action = "waiting_for_quotes"
            return
        await self._refresh_active_maker()
        target = self._choose_target()
        if target is None:
            self.state.last_action = "idle"
            await self._cancel_active_maker("no_target")
            return
        if self._same_maker(self.state.active_maker, target):
            self.state.last_action = f"maintain_{target.action.lower()}"
            return
        await self._replace_maker(target)

    def _choose_target(self) -> MakerOrder | None:
        quote = self.state.quote
        assert quote.ready
        buy_price = quote.buy_price
        sell_price = quote.sell_price
        assert buy_price is not None and sell_price is not None
        threshold = self.config.threshold_jpy
        position = self.state.position

        if position > 0:
            if sell_price > Decimal("0"):
                return self._build_target("SELL", Decimal("0"))
            return None
        if position < 0:
            if buy_price < Decimal("0"):
                return self._build_target("BUY", Decimal("0"))
            return None

        buy_edge = -threshold - buy_price
        sell_edge = sell_price - threshold
        if buy_edge <= 0 and sell_edge <= 0:
            return None
        if sell_edge > buy_edge:
            return self._build_target("SELL", threshold)
        return self._build_target("BUY", -threshold)

    def _target_amount(self, action: str) -> Decimal:
        position = self.state.position
        if action == "BUY":
            capacity = self.config.max_position - position
            if position < 0:
                capacity = min(abs(position), self.config.order_size)
            return min(self.config.order_size, capacity)
        capacity = self.config.max_position + position
        if position > 0:
            capacity = min(position, self.config.order_size)
        return min(self.config.order_size, capacity)

    def _build_target(self, action: str, trigger: Decimal) -> MakerOrder | None:
        quote = self.state.quote
        assert quote.ready
        amount = self._target_amount(action)
        if amount < self.config.min_order_size:
            return None
        assert quote.bitbank_bid is not None
        assert quote.bitbank_ask is not None
        assert quote.bitflyer_bid is not None
        assert quote.bitflyer_ask is not None
        if action == "BUY":
            passive = quote.bitbank_bid + self.config.tick_size
            profitable = quote.bitflyer_bid + trigger
            price = quantize_down(min(passive, profitable), self.config.tick_size)
            expected_hedge = quote.bitflyer_bid
            side = "buy"
        else:
            passive = quote.bitbank_ask - self.config.tick_size
            profitable = quote.bitflyer_ask + trigger
            price = quantize_up(max(passive, profitable), self.config.tick_size)
            expected_hedge = quote.bitflyer_ask
            side = "sell"
        if price <= 0:
            return None
        return MakerOrder(
            action=action,
            side=side,
            price=price,
            amount=amount,
            trigger_price=trigger,
            expected_hedge_price=expected_hedge,
        )

    def _same_maker(self, current: MakerOrder | None, target: MakerOrder) -> bool:
        if current is None:
            return False
        return (
            current.action == target.action
            and current.side == target.side
            and current.price == target.price
            and current.amount == target.amount
        )

    async def _replace_maker(self, target: MakerOrder) -> None:
        await self._cancel_active_maker("replace")
        if self.config.dry_run:
            target.order_id = "DRY-RUN"
            self.state.active_maker = target
            self.state.last_action = f"quote_{target.action.lower()}_dry_run"
            self.logger.event("maker_quote", dry_run=True, maker=asdict(target))
            return
        assert self._bb_private is not None
        order = await self._bb_private.place_order(
            pair=self.config.bitbank_pair,
            side=target.side,
            order_type="limit",
            amount=target.amount,
            price=target.price,
            post_only=True,
        )
        target.order_id = str(order.order_id)
        target.executed_amount = order.executed_amount
        self.state.active_maker = target
        self.state.last_action = f"placed_{target.action.lower()}"
        self.logger.event("maker_placed", maker=asdict(target))

    async def _cancel_active_maker(self, reason: str) -> None:
        maker = self.state.active_maker
        if maker is None:
            return
        self.state.active_maker = None
        if self.config.dry_run or maker.order_id in (None, "DRY-RUN"):
            self.logger.event("maker_removed", reason=reason, dry_run=True, maker=asdict(maker))
            return
        assert self._bb_private is not None
        try:
            await self._bb_private.cancel_order(
                pair=self.config.bitbank_pair, order_id=maker.order_id
            )
            self.logger.event("maker_canceled", reason=reason, maker=asdict(maker))
        except Exception as exc:
            self.logger.event(
                "maker_cancel_failed", reason=reason, error=str(exc), maker=asdict(maker)
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
            await self._hedge_fill(maker, delta, order.average_price or maker.price)
        if order.status in ("FULLY_FILLED", "CANCELED_UNFILLED", "CANCELED_PARTIALLY_FILLED", "REJECTED"):
            self.state.active_maker = None
            self.logger.event("maker_done", status=order.status, maker=asdict(maker))

    async def _hedge_fill(
        self, maker: MakerOrder, amount: Decimal, bitbank_fill_price: Decimal
    ) -> None:
        bitflyer_side = "SELL" if maker.action == "BUY" else "BUY"
        actual_hedge_price = maker.expected_hedge_price
        if not self.config.dry_run:
            assert self._bf_private is not None
            ack = await self._bf_private.send_child_order(
                product_code=self.config.bitflyer_product_code,
                child_order_type="MARKET",
                side=bitflyer_side,
                size=amount,
                time_in_force="IOC",
            )
            actual_hedge_price = await self._execution_average(
                ack.child_order_acceptance_id, fallback=maker.expected_hedge_price
            )

        if maker.action == "BUY":
            cashflow = (actual_hedge_price - bitbank_fill_price) * amount
            self.state.position += amount
            slippage = maker.expected_hedge_price - actual_hedge_price
        else:
            cashflow = (bitbank_fill_price - actual_hedge_price) * amount
            self.state.position -= amount
            slippage = actual_hedge_price - maker.expected_hedge_price

        self.state.realized_pnl_jpy += cashflow
        self.state.filled_base += amount
        self.state.trade_count += 1
        self.logger.trade(
            timestamp=utc_iso(),
            action=maker.action,
            bitbank_order_id=maker.order_id,
            bitbank_side=maker.side,
            bitbank_price=bitbank_fill_price,
            bitbank_amount=amount,
            bitflyer_side=bitflyer_side,
            bitflyer_expected_price=maker.expected_hedge_price,
            bitflyer_average_price=actual_hedge_price,
            slippage_jpy=slippage,
            cashflow_jpy=cashflow,
            position=self.state.position,
            realized_pnl_jpy=self.state.realized_pnl_jpy,
            dry_run=self.config.dry_run,
        )

    async def _execution_average(
        self, acceptance_id: str, fallback: Decimal
    ) -> Decimal:
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
                    return total_notional / total_size
            await asyncio.sleep(0.25)
        return fallback


async def run_bot(config: BotConfig) -> None:
    state = BotState()
    logger = TradeLogger(config.log_dir)
    broadcaster = Broadcaster()
    stop = asyncio.Event()
    web = WebApp(config, state, broadcaster)
    price_poller = PricePoller(config, state, logger)
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
        asyncio.create_task(price_poller.run(stop)),
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
        logger.event("bot_stopped")
        logger.close()


def decimal_arg(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not result.is_finite():
        raise argparse.ArgumentTypeError("must be finite")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="bitbank maker / bitFlyer taker BTC-JPY arbitrage bot with web monitor."
    )
    parser.add_argument("--threshold-jpy", type=decimal_arg, default=Decimal("1000"))
    parser.add_argument("--order-size", type=decimal_arg, default=Decimal("0.001"))
    parser.add_argument("--max-position", type=decimal_arg, default=Decimal("0.003"))
    parser.add_argument("--maker-update-interval", type=float, default=0.5)
    parser.add_argument("--quote-interval", type=float, default=1.0)
    parser.add_argument("--tick-size", type=decimal_arg, default=Decimal("1"))
    parser.add_argument("--min-order-size", type=decimal_arg, default=Decimal("0.0001"))
    parser.add_argument("--bitbank-pair", default="btc_jpy")
    parser.add_argument("--bitflyer-product-code", default="FX_BTC_JPY")
    parser.add_argument("--web-host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8765)
    parser.add_argument("--ws-port", type=int, default=8766)
    parser.add_argument("--log-dir", type=Path, default=Path("logs/trades/bitbank_bitflyer_arbitrage"))
    parser.add_argument("--live", action="store_true", help="place real orders")
    parser.add_argument("--log-level", default="INFO")
    return parser


def config_from_args(args: argparse.Namespace) -> BotConfig:
    if args.threshold_jpy <= 0:
        raise SystemExit("--threshold-jpy must be positive")
    if args.order_size <= 0:
        raise SystemExit("--order-size must be positive")
    if args.max_position <= 0:
        raise SystemExit("--max-position must be positive")
    if args.maker_update_interval <= 0:
        raise SystemExit("--maker-update-interval must be positive")
    return BotConfig(
        bitbank_pair=args.bitbank_pair,
        bitflyer_product_code=args.bitflyer_product_code,
        threshold_jpy=args.threshold_jpy,
        order_size=args.order_size,
        max_position=args.max_position,
        maker_update_interval=args.maker_update_interval,
        quote_interval=args.quote_interval,
        tick_size=args.tick_size,
        min_order_size=args.min_order_size,
        dry_run=not args.live,
        web_host=args.web_host,
        web_port=args.web_port,
        ws_port=args.ws_port,
        log_dir=args.log_dir,
    )


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
