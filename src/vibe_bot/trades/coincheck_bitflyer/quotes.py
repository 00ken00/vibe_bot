from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from decimal import Decimal

from vibe_bot.bitflyer import PublicWebSocket as BitflyerPublicWebSocket
from vibe_bot.coincheck import PublicClient as CoincheckPublicClient
from vibe_bot.coincheck import PublicWebSocket as CoincheckPublicWebSocket
from vibe_bot.trades.bitbank_bitflyer.quotes import OrderBook
from vibe_bot.trades.coincheck_bitflyer.config import BotConfig
from vibe_bot.trades.coincheck_bitflyer.logging import TradeLogger
from vibe_bot.trades.coincheck_bitflyer.models import BotState
from vibe_bot.trades.coincheck_bitflyer.models import Quote

LOGGER = logging.getLogger("vibe_bot.trades.coincheck_bitflyer.quotes")


class WebSocketQuoteFeed:
    """Maintains Coincheck and bitFlyer books and publishes executable VWAP quotes."""

    def __init__(self, config: BotConfig, state: BotState, logger: TradeLogger) -> None:
        self.config = config
        self.state = state
        self.logger = logger
        self._coincheck = OrderBook()
        self._bitflyer = OrderBook()
        self._lock = asyncio.Lock()

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with (
                    CoincheckPublicClient() as coincheck_public,
                    CoincheckPublicWebSocket() as coincheck,
                    BitflyerPublicWebSocket() as bitflyer,
                ):
                    await self._initialize_coincheck_book(coincheck_public)
                    await coincheck.subscribe(f"{self.config.coincheck_pair}-orderbook")
                    await bitflyer.subscribe(
                        f"lightning_board_snapshot_{self.config.bitflyer_product_code}"
                    )
                    await bitflyer.subscribe(
                        f"lightning_board_{self.config.bitflyer_product_code}"
                    )
                    self.logger.event("quote_ws_connected")
                    tasks = [
                        asyncio.create_task(self._run_coincheck(coincheck, stop)),
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

    async def _run_coincheck(self, ws: CoincheckPublicWebSocket, stop: asyncio.Event) -> None:
        async for msg in ws.messages():
            if stop.is_set():
                return
            data = self._coincheck_orderbook_data(msg)
            if data is None:
                continue
            async with self._lock:
                self._coincheck.update(
                    bids=self._levels(data.get("bids")),
                    asks=self._levels(data.get("asks")),
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

    async def _initialize_coincheck_book(
        self, client: CoincheckPublicClient
    ) -> None:
        orderbook = await client.orderbook(self.config.coincheck_pair)
        async with self._lock:
            self._coincheck.replace(bids=orderbook.bids, asks=orderbook.asks)

    def _coincheck_orderbook_data(self, msg: object) -> Mapping[str, object] | None:
        if not isinstance(msg, list) or len(msg) < 2:
            return None
        pair, data = msg[0], msg[1]
        if pair != self.config.coincheck_pair or not isinstance(data, Mapping):
            return None
        return data

    def _levels(self, levels: object) -> list[object]:
        return list(levels) if isinstance(levels, list) else []

    async def _publish_quote_locked(self) -> None:
        amount = self.config.order_size
        quote = Quote(
            coincheck_bid=self._coincheck.best_bid,
            coincheck_ask=self._coincheck.best_ask,
            coincheck_bid_vwap=self._coincheck.vwap("sell", amount),
            coincheck_ask_vwap=self._coincheck.vwap("buy", amount),
            bitflyer_bid=self._bitflyer.best_bid,
            bitflyer_ask=self._bitflyer.best_ask,
            bitflyer_bid_vwap=self._bitflyer.vwap("sell", amount),
            bitflyer_ask_vwap=self._bitflyer.vwap("buy", amount),
            timestamp=time.time(),
        )
        self.state.quote = quote
        if quote.ready:
            self.state.last_error = ""
