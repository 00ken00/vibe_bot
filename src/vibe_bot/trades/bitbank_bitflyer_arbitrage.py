from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import signal
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from websockets.asyncio.server import ServerConnection

from vibe_bot.bitbank import PrivateClient as BitbankPrivateClient
from vibe_bot.bitbank import PublicWebSocket as BitbankPublicWebSocket
from vibe_bot.bitbank.models import Side as BitbankSide
from vibe_bot.bitflyer import PrivateClient as BitflyerPrivateClient
from vibe_bot.bitflyer import PublicWebSocket as BitflyerPublicWebSocket
from vibe_bot.trades.bitbank_bitflyer_web import WebApp

LOGGER = logging.getLogger("vibe_bot.trades.bitbank_bitflyer_arbitrage")


class BotAction(Enum):
    """Typed strategy status exposed to logs and the web monitor."""

    IDLE = ("idle", "No trade target is active.")
    WAITING_FOR_QUOTES = (
        "waiting_for_quotes",
        "Waiting for enough order-book data from both exchanges.",
    )
    MAINTAIN_BUY = (
        "maintain_buy",
        "Keeping the current BUY-action bitbank maker quote.",
    )
    MAINTAIN_SELL = (
        "maintain_sell",
        "Keeping the current SELL-action bitbank maker quote.",
    )
    QUOTE_BUY_DRY_RUN = (
        "quote_buy_dry_run",
        "Dry-run selected a BUY-action maker quote; no order was placed.",
    )
    QUOTE_SELL_DRY_RUN = (
        "quote_sell_dry_run",
        "Dry-run selected a SELL-action maker quote; no order was placed.",
    )
    PLACED_BUY = (
        "placed_buy",
        "Live mode placed a real BUY-action bitbank maker order.",
    )
    PLACED_SELL = (
        "placed_sell",
        "Live mode placed a real SELL-action bitbank maker order.",
    )

    def __init__(self, value: str, description: str) -> None:
        self._value_ = value
        self.description = description

    @classmethod
    def maintain(cls, action: str) -> "BotAction":
        return cls.MAINTAIN_BUY if action == "BUY" else cls.MAINTAIN_SELL

    @classmethod
    def dry_run_quote(cls, action: str) -> "BotAction":
        return cls.QUOTE_BUY_DRY_RUN if action == "BUY" else cls.QUOTE_SELL_DRY_RUN

    @classmethod
    def placed(cls, action: str) -> "BotAction":
        return cls.PLACED_BUY if action == "BUY" else cls.PLACED_SELL


@dataclass(frozen=True)
class BotConfig:
    """Runtime configuration for the bitbank/bitFlyer arbitrage bot.

    Holds exchange symbols, strategy thresholds, sizing limits, web server
    ports, logging paths, and whether execution is dry-run or live.
    """

    bitbank_pair: str = "btc_jpy"
    bitflyer_product_code: str = "FX_BTC_JPY"
    threshold_jpy: Decimal = Decimal("1000")
    threshold_offset_jpy: Decimal = Decimal("0")
    order_size: Decimal = Decimal("0.001")
    max_position: Decimal = Decimal("0.003")
    maker_update_interval: float = 0.5
    monitor_update_interval: float = 1.0
    tick_size: Decimal = Decimal("1")
    min_order_size: Decimal = Decimal("0.0001")
    dry_run: bool = True
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    ws_port: int = 8766
    log_dir: Path = Path("logs/trades/bitbank_bitflyer_arbitrage")


@dataclass
class Quote:
    """Current order-book prices used to calculate arbitrage spreads.

    Top-of-book fields are kept for display and maker quote placement. VWAP
    fields estimate the average price needed to fully execute ``order_size``.
    BUY/SELL arbitrage prices are calculated from those VWAP fields.
    """

    bitbank_bid: Decimal | None = None
    bitbank_ask: Decimal | None = None
    bitflyer_bid: Decimal | None = None
    bitflyer_ask: Decimal | None = None
    bitbank_bid_vwap: Decimal | None = None
    bitbank_ask_vwap: Decimal | None = None
    bitflyer_bid_vwap: Decimal | None = None
    bitflyer_ask_vwap: Decimal | None = None
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
                self.bitbank_bid_vwap,
                self.bitbank_ask_vwap,
                self.bitflyer_bid_vwap,
                self.bitflyer_ask_vwap,
            )
        )

    @property
    def buy_price(self) -> Decimal | None:
        if self.bitbank_ask_vwap is None or self.bitflyer_bid_vwap is None:
            return None
        return self.bitbank_ask_vwap - self.bitflyer_bid_vwap

    @property
    def sell_price(self) -> Decimal | None:
        if self.bitbank_bid_vwap is None or self.bitflyer_ask_vwap is None:
            return None
        return self.bitbank_bid_vwap - self.bitflyer_ask_vwap


@dataclass
class MakerOrder:
    """The single active or desired maker quote on bitbank.

    Records the arbitrage action it represents, bitbank order side/price/size,
    the trigger spread used to choose it, and the expected bitFlyer hedge price.
    """

    action: str
    side: BitbankSide
    price: Decimal
    amount: Decimal
    trigger_price: Decimal
    expected_hedge_price: Decimal
    order_id: str | None = None
    placed_at: float = field(default_factory=time.time)
    executed_amount: Decimal = Decimal("0")


@dataclass
class BotState:
    """Mutable runtime state shared by the strategy loop and web monitor.

    Tracks latest quotes, aggregate position, realized PnL, fill counters, the
    active maker order, and the most recent status/error for observability.
    """

    quote: Quote = field(default_factory=Quote)
    position: Decimal = Decimal("0")
    realized_pnl_jpy: Decimal = Decimal("0")
    filled_base: Decimal = Decimal("0")
    trade_count: int = 0
    active_maker: MakerOrder | None = None
    last_action: BotAction = BotAction.IDLE
    last_error: str = ""
    started_at: float = field(default_factory=time.time)


Jsonable = None | bool | int | float | str | list[object] | dict[str, object]
BookLevel = Mapping[str, object] | Sequence[object]


def decimal_to_json(value: object) -> Jsonable:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BotAction):
        return value.value
    if isinstance(value, (Quote, MakerOrder, BotState)):
        return decimal_to_json(asdict(value))
    if isinstance(value, dict):
        return {str(k): decimal_to_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decimal_to_json(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def decimal_to_json_dict(value: dict[str, object]) -> dict[str, object]:
    converted = decimal_to_json(value)
    if not isinstance(converted, dict):
        raise TypeError("expected JSON object")
    return converted


def utc_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).isoformat()


def quantize_down(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def quantize_up(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_UP) * tick


class TradeLogger:
    """Persists strategy events and trade/fill records for analysis.

    Events are written as JSONL for operational debugging. Trade records are
    written as CSV with fill prices, hedge prices, slippage, position, and PnL.
    """

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

    def event(self, event_type: str, **payload: object) -> None:
        row = {"timestamp": utc_iso(), "event": event_type, **payload}
        with self.events_path.open("a") as f:
            f.write(json.dumps(decimal_to_json(row), separators=(",", ":")) + "\n")

    def trade(self, **payload: object) -> None:
        self._csv.writerow(decimal_to_json_dict(payload))
        self._csv_file.flush()


class Broadcaster:
    """Fan-out helper for pushing realtime snapshots to web clients.

    The web app registers websocket clients here, and the publish loop sends the
    latest serialized bot state to each connected browser.
    """

    def __init__(self) -> None:
        self._clients: set[ServerConnection] = set()

    async def add(self, ws: ServerConnection) -> None:
        self._clients.add(ws)

    async def remove(self, ws: ServerConnection) -> None:
        self._clients.discard(ws)

    async def publish(self, payload: dict[str, object]) -> None:
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


class OrderBook:
    """In-memory order book that can estimate full-size execution prices.

    The book stores price levels from websocket snapshots and diffs. ``vwap``
    walks enough levels to fill the requested amount and returns ``None`` when
    visible depth is insufficient.
    """

    def __init__(self) -> None:
        self.bids: dict[Decimal, Decimal] = {}
        self.asks: dict[Decimal, Decimal] = {}

    def replace(self, *, bids: Iterable[BookLevel], asks: Iterable[BookLevel]) -> None:
        self.bids = self._levels_to_dict(bids)
        self.asks = self._levels_to_dict(asks)

    def update(self, *, bids: Iterable[BookLevel], asks: Iterable[BookLevel]) -> None:
        self._apply_levels(self.bids, bids)
        self._apply_levels(self.asks, asks)

    @property
    def best_bid(self) -> Decimal | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return min(self.asks) if self.asks else None

    def vwap(self, side: str, amount: Decimal) -> Decimal | None:
        if amount <= 0:
            return None
        book = self.asks if side == "buy" else self.bids
        reverse = side == "sell"
        remaining = amount
        notional = Decimal("0")
        for price in sorted(book, reverse=reverse):
            size = book[price]
            if size <= 0:
                continue
            take = min(size, remaining)
            notional += price * take
            remaining -= take
            if remaining <= 0:
                return notional / amount
        return None

    def _levels_to_dict(self, levels: Iterable[BookLevel]) -> dict[Decimal, Decimal]:
        result: dict[Decimal, Decimal] = {}
        for price, size in self._iter_levels(levels):
            if size > 0:
                result[price] = size
        return result

    def _apply_levels(
        self, book: dict[Decimal, Decimal], levels: Iterable[BookLevel]
    ) -> None:
        for price, size in self._iter_levels(levels):
            if size <= 0:
                book.pop(price, None)
            else:
                book[price] = size

    def _iter_levels(
        self, levels: Iterable[BookLevel]
    ) -> Iterable[tuple[Decimal, Decimal]]:
        for level in levels or []:
            if isinstance(level, Mapping):
                price = level.get("price")
                size = level.get("size")
                if size is None:
                    size = level.get("amount")
            else:
                price, size = level[0], level[1]
            yield Decimal(str(price)), Decimal(str(size))


class WebSocketQuoteFeed:
    """Maintains exchange order books from public websocket streams.

    Subscribes to bitbank depth and bitFlyer board channels, updates local order
    books from snapshots/diffs, and publishes a ``Quote`` whose executable
    prices are based on the configured order size instead of the best level.
    """

    def __init__(self, config: BotConfig, state: BotState, logger: TradeLogger) -> None:
        self.config = config
        self.state = state
        self.logger = logger
        self._bitbank = OrderBook()
        self._bitflyer = OrderBook()
        self._lock = asyncio.Lock()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with (
                    BitbankPublicWebSocket() as bitbank,
                    BitflyerPublicWebSocket() as bitflyer,
                ):
                    await bitbank.subscribe(f"depth_whole_{self.config.bitbank_pair}")
                    await bitbank.subscribe(f"depth_diff_{self.config.bitbank_pair}")
                    await bitflyer.subscribe(
                        f"lightning_board_snapshot_{self.config.bitflyer_product_code}"
                    )
                    await bitflyer.subscribe(
                        f"lightning_board_{self.config.bitflyer_product_code}"
                    )
                    self.logger.event("quote_ws_connected")
                    tasks = [
                        asyncio.create_task(self._run_bitbank(bitbank, stop)),
                        asyncio.create_task(self._run_bitflyer(bitflyer, stop)),
                        asyncio.create_task(stop.wait()),
                    ]
                    try:
                        done, pending = await asyncio.wait(
                            tasks, return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in done:
                            task.result()
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as exc:
                if stop.is_set():
                    return
                self.state.last_error = f"quote websocket failed: {exc}"
                self.logger.event("error", message=self.state.last_error)
                LOGGER.exception("quote websocket failed")
                await asyncio.sleep(1.0)

    async def _run_bitbank(
        self, ws: BitbankPublicWebSocket, stop: asyncio.Event
    ) -> None:
        async for msg in ws.messages():
            if stop.is_set():
                return
            room = str(msg.get("room_name") or "")
            message = msg.get("message") if isinstance(msg, dict) else None
            data = message.get("data") if isinstance(message, dict) else None
            if not isinstance(data, dict):
                continue
            bids = data.get("bids") or []
            asks = data.get("asks") or []
            async with self._lock:
                if room.startswith("depth_whole_"):
                    self._bitbank.replace(bids=bids, asks=asks)
                elif room.startswith("depth_diff_"):
                    self._bitbank.update(bids=bids, asks=asks)
                await self._publish_quote_locked()

    async def _run_bitflyer(
        self, ws: BitflyerPublicWebSocket, stop: asyncio.Event
    ) -> None:
        async for msg in ws.messages():
            if stop.is_set():
                return
            channel = str(msg.get("channel") or "")
            data = msg.get("message")
            if not isinstance(data, dict):
                continue
            bids = data.get("bids") or []
            asks = data.get("asks") or []
            async with self._lock:
                if "board_snapshot" in channel:
                    self._bitflyer.replace(bids=bids, asks=asks)
                elif "board_" in channel:
                    self._bitflyer.update(bids=bids, asks=asks)
                await self._publish_quote_locked()

    async def _publish_quote_locked(self) -> None:
        amount = self.config.order_size
        quote = Quote(
            bitbank_bid=self._bitbank.best_bid,
            bitbank_ask=self._bitbank.best_ask,
            bitflyer_bid=self._bitflyer.best_bid,
            bitflyer_ask=self._bitflyer.best_ask,
            bitbank_bid_vwap=self._bitbank.vwap("sell", amount),
            bitbank_ask_vwap=self._bitbank.vwap("buy", amount),
            bitflyer_bid_vwap=self._bitflyer.vwap("sell", amount),
            bitflyer_ask_vwap=self._bitflyer.vwap("buy", amount),
            timestamp=time.time(),
        )
        self.state.quote = quote
        if quote.ready:
            self.state.last_error = ""


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
            self.state.last_action = BotAction.WAITING_FOR_QUOTES
            return
        await self._refresh_active_maker()
        target = self._choose_target()
        if target is None:
            self.state.last_action = BotAction.IDLE
            await self._cancel_active_maker("no_target")
            return
        if self._same_maker(self.state.active_maker, target):
            self.state.last_action = BotAction.maintain(target.action)
            return
        await self._replace_maker(target)

    def _choose_target(self) -> MakerOrder | None:
        quote = self.state.quote
        assert quote.ready
        buy_price = quote.buy_price
        sell_price = quote.sell_price
        assert buy_price is not None and sell_price is not None
        threshold = self.config.threshold_jpy
        offset = self.config.threshold_offset_jpy
        buy_open_trigger = offset - threshold
        sell_open_trigger = offset + threshold
        position = self.state.position

        if position > 0:
            if sell_price > offset:
                return self._build_target("SELL", offset)
            return None
        if position < 0:
            if buy_price < offset:
                return self._build_target("BUY", offset)
            return None

        buy_edge = buy_open_trigger - buy_price
        sell_edge = sell_price - sell_open_trigger
        if buy_edge <= 0 and sell_edge <= 0:
            return None
        if sell_edge > buy_edge:
            return self._build_target("SELL", sell_open_trigger)
        return self._build_target("BUY", buy_open_trigger)

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
        assert quote.bitflyer_bid_vwap is not None
        assert quote.bitflyer_ask_vwap is not None
        if action == "BUY":
            passive = quote.bitbank_bid + self.config.tick_size
            profitable = quote.bitflyer_bid_vwap + trigger
            price = quantize_down(min(passive, profitable), self.config.tick_size)
            expected_hedge = quote.bitflyer_bid_vwap
            side = "buy"
        else:
            passive = quote.bitbank_ask - self.config.tick_size
            profitable = quote.bitflyer_ask_vwap + trigger
            price = quantize_up(max(passive, profitable), self.config.tick_size)
            expected_hedge = quote.bitflyer_ask_vwap
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
            self.state.last_action = BotAction.dry_run_quote(target.action)
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
        self.state.last_action = BotAction.placed(target.action)
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
    parser.add_argument(
        "--threshold-offset-jpy",
        type=decimal_arg,
        default=Decimal("0"),
        help="center spread offset for open/close thresholds",
    )
    parser.add_argument("--order-size", type=decimal_arg, default=Decimal("0.001"))
    parser.add_argument("--max-position", type=decimal_arg, default=Decimal("0.003"))
    parser.add_argument("--maker-update-interval", type=float, default=0.5)
    parser.add_argument(
        "--monitor-update-interval",
        type=float,
        default=1.0,
        help="seconds between browser websocket snapshot updates",
    )
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
    if args.monitor_update_interval <= 0:
        raise SystemExit("--monitor-update-interval must be positive")
    return BotConfig(
        bitbank_pair=args.bitbank_pair,
        bitflyer_product_code=args.bitflyer_product_code,
        threshold_jpy=args.threshold_jpy,
        threshold_offset_jpy=args.threshold_offset_jpy,
        order_size=args.order_size,
        max_position=args.max_position,
        maker_update_interval=args.maker_update_interval,
        monitor_update_interval=args.monitor_update_interval,
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
