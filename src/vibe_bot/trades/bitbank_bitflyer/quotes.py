from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from vibe_bot.bitbank import PublicWebSocket as BitbankPublicWebSocket
from vibe_bot.bitflyer import PublicWebSocket as BitflyerPublicWebSocket
from vibe_bot.trades.bitbank_bitflyer.config import BotConfig
from vibe_bot.trades.bitbank_bitflyer.logging import TradeLogger
from vibe_bot.trades.bitbank_bitflyer.models import BotState
from vibe_bot.trades.bitbank_bitflyer.models import Quote

LOGGER = logging.getLogger("vibe_bot.trades.bitbank_bitflyer.quotes")

BookLevel = Mapping[str, object] | Sequence[object]


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
            bitbank_buy_maker=(
                self._bitbank.best_ask - self.config.tick_size
                if self._bitbank.best_ask is not None
                else None
            ),
            bitbank_sell_maker=(
                self._bitbank.best_bid + self.config.tick_size
                if self._bitbank.best_bid is not None
                else None
            ),
            bitflyer_bid=self._bitflyer.best_bid,
            bitflyer_ask=self._bitflyer.best_ask,
            bitflyer_bid_vwap=self._bitflyer.vwap("sell", amount),
            bitflyer_ask_vwap=self._bitflyer.vwap("buy", amount),
            timestamp=time.time(),
        )
        self.state.quote = quote
        if quote.ready:
            self.state.last_error = ""
