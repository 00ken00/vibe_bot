from __future__ import annotations

import asyncio
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from vibe_bot.bitbank import PublicClient as BitbankPublicClient
from vibe_bot.bitflyer import PublicClient as BitflyerPublicClient

if TYPE_CHECKING:
    from vibe_bot.trades.bitbank_bitflyer_arbitrage import BotConfig, TradeLogger

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class HistoricalSpreadPoint:
    timestamp: int
    buy_price: Decimal
    sell_price: Decimal
    close_spread: Decimal
    bitbank_close: Decimal
    bitflyer_close: Decimal


class HistoricalSpreadCache:
    """Background cache for candle-based bitbank/bitFlyer spread history.

    Candlesticks do not include bid/ask or order-book depth, so this cache
    estimates the historical BUY/SELL spread from candle close prices:
    bitbank close +/- one tick minus bitFlyer close.
    """

    def __init__(self, config: BotConfig, logger: TradeLogger) -> None:
        self.config = config
        self.logger = logger
        self.points: list[HistoricalSpreadPoint] = []
        self.last_refresh: float | None = None
        self.last_error = ""

    def snapshot(self) -> dict[str, object]:
        return {
            "type": "history",
            "days": self.config.history_days,
            "candle_minutes": self.config.history_candle_minutes,
            "last_refresh": self.last_refresh,
            "last_error": self.last_error,
            "points": [asdict(point) for point in self.points],
        }

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.refresh()
            except Exception as exc:
                self.last_error = str(exc)
                self.logger.event("historical_spread_refresh_failed", error=str(exc))
            wait_until = time.time() + self.config.history_refresh_interval
            while not stop.is_set() and time.time() < wait_until:
                await asyncio.sleep(1.0)

    async def refresh(self) -> None:
        points = await fetch_historical_spreads(self.config)
        self.points = points
        self.last_refresh = time.time()
        self.last_error = ""
        self.logger.event(
            "historical_spread_refreshed",
            points=len(points),
            days=self.config.history_days,
            candle_minutes=self.config.history_candle_minutes,
        )


async def fetch_historical_spreads(config: BotConfig) -> list[HistoricalSpreadPoint]:
    now = datetime.now(tz=JST)
    start = now - timedelta(days=config.history_days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    async with BitbankPublicClient() as bitbank, BitflyerPublicClient() as bitflyer:
        bitbank_task = asyncio.create_task(
            _fetch_bitbank_closes(
                bitbank,
                pair=config.bitbank_pair,
                candle_minutes=config.history_candle_minutes,
                start=start,
                end=now,
            )
        )
        bitflyer_task = asyncio.create_task(
            _fetch_bitflyer_closes(
                bitflyer,
                product_code=config.bitflyer_product_code,
                candle_minutes=config.history_candle_minutes,
                start_ms=start_ms,
            )
        )
        bitbank_closes, bitflyer_closes = await asyncio.gather(
            bitbank_task, bitflyer_task
        )

    points = []
    for timestamp in sorted(set(bitbank_closes) & set(bitflyer_closes)):
        if timestamp < start_ms or timestamp > end_ms:
            continue
        bitbank_close = bitbank_closes[timestamp]
        bitflyer_close = bitflyer_closes[timestamp]
        close_spread = bitbank_close - bitflyer_close
        points.append(
            HistoricalSpreadPoint(
                timestamp=timestamp,
                buy_price=(bitbank_close - config.tick_size) - bitflyer_close,
                sell_price=(bitbank_close + config.tick_size) - bitflyer_close,
                close_spread=close_spread,
                bitbank_close=bitbank_close,
                bitflyer_close=bitflyer_close,
            )
        )
    return points


async def _fetch_bitbank_closes(
    client: BitbankPublicClient,
    *,
    pair: str,
    candle_minutes: int,
    start: datetime,
    end: datetime,
) -> dict[int, Decimal]:
    candle_type = f"{candle_minutes}min"
    closes: dict[int, Decimal] = {}
    current = start.date()
    while current <= end.date():
        candle = await client.candlestick(pair, candle_type, current.strftime("%Y%m%d"))
        for row in candle.ohlcv:
            if len(row) < 6:
                continue
            closes[int(row[5])] = Decimal(row[3])
        current += timedelta(days=1)
    return closes


async def _fetch_bitflyer_closes(
    client: BitflyerPublicClient,
    *,
    product_code: str,
    candle_minutes: int,
    start_ms: int,
) -> dict[int, Decimal]:
    closes: dict[int, Decimal] = {}
    before: int | None = None
    last_oldest: int | None = None
    while True:
        rows = await client.lightchart_ohlc(
            symbol=product_code,
            period="m",
            grouping=candle_minutes,
            before=before,
        )
        if not rows:
            break
        oldest = None
        for row in rows:
            if len(row) < 5:
                continue
            timestamp = int(row[0])
            close = row[4]
            if close is None:
                continue
            closes[timestamp] = Decimal(str(close))
            oldest = timestamp if oldest is None else min(oldest, timestamp)
        if oldest is None or oldest <= start_ms or oldest == last_oldest:
            break
        last_oldest = oldest
        before = oldest - candle_minutes * 60 * 1000
    return closes
