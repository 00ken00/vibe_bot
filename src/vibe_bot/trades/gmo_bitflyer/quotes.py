from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from decimal import Decimal

from vibe_bot.bitflyer import PublicWebSocket as BitflyerPublicWebSocket
from vibe_bot.gmo import PublicWebSocket as GmoPublicWebSocket
from vibe_bot.trades.bitbank_bitflyer.quotes import OrderBook
from vibe_bot.trades.gmo_bitflyer.config import BotConfig
from vibe_bot.trades.gmo_bitflyer.logging import TradeLogger
from vibe_bot.trades.gmo_bitflyer.models import BotState
from vibe_bot.trades.gmo_bitflyer.models import Quote

LOGGER = logging.getLogger("vibe_bot.trades.gmo_bitflyer.quotes")


class WebSocketQuoteFeed:
    """Maintains GMO and bitFlyer books and publishes executable VWAP quotes."""

    def __init__(self, config: BotConfig, state: BotState, logger: TradeLogger) -> None:
        self.config = config
        self.state = state
        self.logger = logger
        self._gmo = OrderBook()
        self._bitflyer = OrderBook()
        self._lock = asyncio.Lock()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with (
                    GmoPublicWebSocket() as gmo,
                    BitflyerPublicWebSocket() as bitflyer,
                ):
                    await gmo.subscribe("orderbooks", self.config.gmo_symbol)
                    await bitflyer.subscribe(
                        f"lightning_board_snapshot_{self.config.bitflyer_product_code}"
                    )
                    await bitflyer.subscribe(
                        f"lightning_board_{self.config.bitflyer_product_code}"
                    )
                    self.logger.event("quote_ws_connected")
                    tasks = [
                        asyncio.create_task(self._run_gmo(gmo, stop)),
                        asyncio.create_task(self._run_bitflyer(bitflyer, stop)),
                        asyncio.create_task(stop.wait()),
                    ]
                    try:
                        done, pending = await asyncio.wait(
                            tasks, return_when=asyncio.FIRST_COMPLETED
                        )
                        del pending
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

    async def _run_gmo(self, ws: GmoPublicWebSocket, stop: asyncio.Event) -> None:
        async for msg in ws.messages():
            if stop.is_set():
                return
            if not self._is_gmo_orderbook(msg):
                continue
            async with self._lock:
                self._gmo.replace(
                    bids=self._levels(msg.get("bids")),
                    asks=self._levels(msg.get("asks")),
                )
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
            async with self._lock:
                if "board_snapshot" in channel:
                    self._bitflyer.replace(
                        bids=self._levels(data.get("bids")),
                        asks=self._levels(data.get("asks")),
                    )
                elif "board_" in channel:
                    self._bitflyer.update(
                        bids=self._levels(data.get("bids")),
                        asks=self._levels(data.get("asks")),
                    )
                await self._publish_quote_locked()

    def _is_gmo_orderbook(self, msg: Mapping[str, object]) -> bool:
        channel = str(msg.get("channel") or "")
        symbol = str(msg.get("symbol") or "")
        return channel == "orderbooks" and symbol == self.config.gmo_symbol

    def _levels(self, levels: object) -> list[object]:
        return list(levels) if isinstance(levels, list) else []

    async def _publish_quote_locked(self) -> None:
        amount = self.config.order_size
        quote = Quote(
            gmo_bid=self._gmo.best_bid,
            gmo_ask=self._gmo.best_ask,
            gmo_bid_vwap=self._gmo.vwap("sell", amount),
            gmo_ask_vwap=self._gmo.vwap("buy", amount),
            bitflyer_bid=self._bitflyer.best_bid,
            bitflyer_ask=self._bitflyer.best_ask,
            bitflyer_bid_vwap=self._bitflyer.vwap("sell", amount),
            bitflyer_ask_vwap=self._bitflyer.vwap("buy", amount),
            timestamp=time.time(),
        )
        self.state.quote = quote
        if quote.ready:
            self.state.last_error = ""
