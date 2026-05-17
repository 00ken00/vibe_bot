from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from vibe_bot.bitbank import PublicClient as BitbankPublicClient
from vibe_bot.bitflyer import PublicClient as BitflyerPublicClient
from vibe_bot.coincheck import PublicClient as CoincheckPublicClient
from vibe_bot.gmo import PublicClient as GmoPublicClient

JST = ZoneInfo("Asia/Tokyo")
PairName = str


@dataclass(frozen=True)
class PairPreset:
    left_exchange: str
    left_symbol: str
    right_exchange: str
    right_symbol: str


PAIR_PRESETS: dict[PairName, PairPreset] = {
    "bitbank_bitflyer": PairPreset("bitbank", "btc_jpy", "bitFlyer", "FX_BTC_JPY"),
    "bitbank_coincheck": PairPreset("bitbank", "btc_jpy", "coincheck", "btc_jpy"),
    "bitbank_gmo": PairPreset("bitbank", "btc_jpy", "GMO", "BTC"),
    "coincheck_bitflyer": PairPreset("coincheck", "btc_jpy", "bitFlyer", "FX_BTC_JPY"),
    "coincheck_gmo": PairPreset("coincheck", "btc_jpy", "GMO", "BTC"),
    "gmo_bitflyer": PairPreset("GMO", "BTC_JPY", "bitFlyer", "FX_BTC_JPY"),
}


@dataclass(frozen=True)
class HistoryConfig:
    left_exchange: str
    left_symbol: str
    right_exchange: str
    right_symbol: str
    days: int = 5
    candle_minutes: int = 5

    @classmethod
    def from_pair(
        cls,
        pair: PairName,
        *,
        days: int = 5,
        candle_minutes: int = 5,
    ) -> "HistoryConfig":
        try:
            preset = PAIR_PRESETS[pair]
        except KeyError as exc:
            choices = ", ".join(sorted(PAIR_PRESETS))
            raise ValueError(f"unknown pair {pair!r}; choose one of: {choices}") from exc
        return cls(
            left_exchange=preset.left_exchange,
            left_symbol=preset.left_symbol,
            right_exchange=preset.right_exchange,
            right_symbol=preset.right_symbol,
            days=days,
            candle_minutes=candle_minutes,
        )


@dataclass(frozen=True)
class HistoricalSpreadPoint:
    timestamp: int
    buy_price: Decimal
    sell_price: Decimal
    close_spread: Decimal
    left_close: Decimal
    right_close: Decimal


async def fetch_historical_spreads(
    config: HistoryConfig,
) -> list[HistoricalSpreadPoint]:
    """Fetch candle-based historical spread estimates.

    Candlesticks do not include bid/ask or order-book depth, so BUY and SELL
    both use the candle close spread: left exchange close - right exchange close.
    """
    validate_config(config.days, config.candle_minutes)

    now = datetime.now(tz=JST)
    start = now - timedelta(days=config.days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    left_task = asyncio.create_task(
        _fetch_exchange_closes(
            config.left_exchange,
            symbol=config.left_symbol,
            candle_minutes=config.candle_minutes,
            start=start,
            end=now,
            start_ms=start_ms,
        )
    )
    right_task = asyncio.create_task(
        _fetch_exchange_closes(
            config.right_exchange,
            symbol=config.right_symbol,
            candle_minutes=config.candle_minutes,
            start=start,
            end=now,
            start_ms=start_ms,
        )
    )
    left_closes, right_closes = await asyncio.gather(left_task, right_task)

    points = []
    for timestamp in sorted(set(left_closes) & set(right_closes)):
        if timestamp < start_ms or timestamp > end_ms:
            continue
        left_close = left_closes[timestamp]
        right_close = right_closes[timestamp]
        close_spread = left_close - right_close
        points.append(
            HistoricalSpreadPoint(
                timestamp=timestamp,
                buy_price=close_spread,
                sell_price=close_spread,
                close_spread=close_spread,
                left_close=left_close,
                right_close=right_close,
            )
        )
    return points


def build_figure(
    points: list[HistoricalSpreadPoint],
    config: HistoryConfig,
) -> go.Figure:
    x = [datetime.fromtimestamp(point.timestamp / 1000, tz=JST) for point in points]
    buy = [float(point.buy_price) for point in points]
    sell = [float(point.sell_price) for point in points]
    close = [float(point.close_spread) for point in points]
    left_close = [float(point.left_close) for point in points]
    right_close = [float(point.right_close) for point in points]
    buy_average = sum(buy) / len(buy)
    sell_average = sum(sell) / len(sell)
    buy_average_series = [buy_average] * len(x)
    sell_average_series = [sell_average] * len(x)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.58, 0.42],
        subplot_titles=("Approx BUY / SELL Spread", "Candle Close Prices"),
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=buy,
            mode="lines",
            name="approx BUY price",
            line={"color": "#1464d2", "width": 1.6},
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=sell,
            mode="lines",
            name="approx SELL price",
            line={"color": "#c2410c", "width": 1.6},
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=close,
            mode="lines",
            name="close spread",
            line={"color": "#667085", "width": 1, "dash": "dot"},
            visible="legendonly",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=left_close,
            mode="lines",
            name=f"{config.left_exchange} close",
            line={"color": "#0f9f6e", "width": 1.4},
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=right_close,
            mode="lines",
            name=f"{config.right_exchange} close",
            line={"color": "#7c3aed", "width": 1.4},
        ),
        row=2,
        col=1,
    )
    fig.add_hline(
        y=0,
        line_dash="dash",
        line_color="#101828",
        opacity=0.45,
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=buy_average_series,
            mode="lines",
            name=f"BUY avg {buy_average:,.2f}",
            line={"color": "#1464d2", "width": 1, "dash": "dash"},
            hovertemplate="BUY avg %{y:,.2f}<extra></extra>",
            showlegend=True,
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=sell_average_series,
            mode="lines",
            name=f"SELL avg {sell_average:,.2f}",
            line={"color": "#c2410c", "width": 1, "dash": "dash"},
            hovertemplate="SELL avg %{y:,.2f}<extra></extra>",
            showlegend=True,
        ),
        row=1,
        col=1,
    )
    fig.update_layout(
        title=(
            f"{config.left_exchange} / {config.right_exchange} Historical Spread "
            f"({config.candle_minutes}min candles, {config.days}d)"
        ),
        hovermode="x unified",
        template="plotly_white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        margin={"l": 64, "r": 24, "t": 90, "b": 64},
        height=850,
    )
    fig.update_yaxes(title_text="Spread JPY", row=1, col=1)
    fig.update_yaxes(title_text="Close JPY", row=2, col=1)
    fig.update_xaxes(title_text="Time (JST)", row=2, col=1)
    fig.add_annotation(
        text=(
            "Approximation from candle closes. Historical candles do not include "
            "bid/ask or order-book depth."
        ),
        xref="paper",
        yref="paper",
        x=0,
        y=-0.18,
        showarrow=False,
        font={"size": 12, "color": "#667085"},
    )
    return fig


async def run(config: HistoryConfig, output_html: Path | str | None) -> go.Figure:
    points = await fetch_historical_spreads(config)
    if not points:
        raise RuntimeError("no matching historical candle points were returned")
    fig = build_figure(points, config)
    if output_html is not None:
        output_path = Path(output_html)
        fig.write_html(output_path, include_plotlyjs="cdn")
        print(f"wrote: {output_path}")
    fig.show(renderer="browser")
    return fig


def main(
    pair: PairName = "bitbank_bitflyer",
    *,
    left_exchange: str | None = None,
    left_symbol: str | None = None,
    right_exchange: str | None = None,
    right_symbol: str | None = None,
    days: int = 5,
    candle_minutes: int = 5,
    output_html: Path | str | None = None,
) -> go.Figure:
    """Fetch historical candles and open a Plotly spread chart.

    Use `pair` for a preset, or pass all left/right exchange and symbol fields
    for an ad hoc comparison.
    """
    validate_config(days, candle_minutes)
    if any(v is not None for v in (left_exchange, left_symbol, right_exchange, right_symbol)):
        if None in (left_exchange, left_symbol, right_exchange, right_symbol):
            raise ValueError(
                "left_exchange, left_symbol, right_exchange, and right_symbol "
                "must all be provided for a custom comparison"
            )
        config = HistoryConfig(
            left_exchange=left_exchange,
            left_symbol=left_symbol,
            right_exchange=right_exchange,
            right_symbol=right_symbol,
            days=days,
            candle_minutes=candle_minutes,
        )
    else:
        config = HistoryConfig.from_pair(
            pair,
            days=days,
            candle_minutes=candle_minutes,
        )
    return asyncio.run(run(config, output_html))


def cli() -> go.Figure:
    parser = argparse.ArgumentParser(
        description="Plot historical candle-close spreads between two exchanges."
    )
    parser.add_argument(
        "--pair",
        default="bitbank_bitflyer",
        choices=sorted(PAIR_PRESETS),
        help="Preset comparison pair.",
    )
    parser.add_argument("--left-exchange", help="Custom left exchange name.")
    parser.add_argument("--left-symbol", help="Custom left exchange symbol.")
    parser.add_argument("--right-exchange", help="Custom right exchange name.")
    parser.add_argument("--right-symbol", help="Custom right exchange symbol.")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--candle-minutes", type=int, default=5)
    parser.add_argument("--output-html")
    args = parser.parse_args()
    return main(
        pair=args.pair,
        left_exchange=args.left_exchange,
        left_symbol=args.left_symbol,
        right_exchange=args.right_exchange,
        right_symbol=args.right_symbol,
        days=args.days,
        candle_minutes=args.candle_minutes,
        output_html=args.output_html,
    )


def validate_config(days: int, candle_minutes: int) -> None:
    if days <= 0:
        raise ValueError("days must be positive")
    if candle_minutes not in (1, 5, 15, 30):
        raise ValueError("candle_minutes must be one of 1, 5, 15, 30")


async def _fetch_exchange_closes(
    exchange: str,
    *,
    symbol: str,
    candle_minutes: int,
    start: datetime,
    end: datetime,
    start_ms: int,
) -> dict[int, Decimal]:
    if exchange == "bitbank":
        async with BitbankPublicClient() as client:
            return await fetch_bitbank_closes(
                client,
                pair=symbol,
                candle_minutes=candle_minutes,
                start=start,
                end=end,
            )
    if exchange == "bitFlyer":
        async with BitflyerPublicClient() as client:
            return await fetch_bitflyer_closes(
                client,
                product_code=symbol,
                candle_minutes=candle_minutes,
                start_ms=start_ms,
            )
    if exchange == "GMO":
        async with GmoPublicClient() as client:
            return await fetch_gmo_closes(
                client,
                symbol=symbol,
                candle_minutes=candle_minutes,
                start=start,
                end=end,
            )
    if exchange == "coincheck":
        async with CoincheckPublicClient() as client:
            return await fetch_coincheck_closes(
                client,
                pair=symbol,
                candle_minutes=candle_minutes,
                start=start,
                end=end,
            )
    raise ValueError(f"unsupported exchange: {exchange}")


async def fetch_bitbank_closes(
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


async def fetch_gmo_closes(
    client: GmoPublicClient,
    *,
    symbol: str,
    candle_minutes: int,
    start: datetime,
    end: datetime,
) -> dict[int, Decimal]:
    interval = f"{candle_minutes}min"
    closes: dict[int, Decimal] = {}
    current = start.date()
    while current <= end.date():
        rows = await client.klines(symbol, interval, current.strftime("%Y%m%d"))
        for row in rows:
            closes[_parse_gmo_open_time_ms(row.open_time)] = row.close
        current += timedelta(days=1)
    return closes


async def fetch_coincheck_closes(
    client: CoincheckPublicClient,
    *,
    pair: str,
    candle_minutes: int,
    start: datetime,
    end: datetime,
) -> dict[int, Decimal]:
    candles = await client.candlesticks(
        pair,
        candle_minutes=candle_minutes,
        start=start,
        end=end,
    )
    return {candle.open_time: candle.close for candle in candles}


async def fetch_bitflyer_closes(
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
        before = _previous_bitflyer_lightchart_boundary_ms(oldest)
    return closes


def _parse_gmo_open_time_ms(value: str) -> int:
    if value.isdigit():
        return int(value)
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def _previous_bitflyer_lightchart_boundary_ms(timestamp_ms: int) -> int:
    timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=JST)
    boundary_hour = 21 if timestamp.hour >= 21 else 9
    boundary = timestamp.replace(
        hour=boundary_hour, minute=0, second=0, microsecond=0
    )
    return int(boundary.timestamp() * 1000)


def __main__():

    left_exchange, left_symbol = "bitbank", "btc_jpy"
    left_exchange, left_symbol = "GMO", "BTC_JPY"
    right_exchange, right_symbol = "GMO", "BTC_JPY"
    right_exchange, right_symbol = "bitFlyer", "FX_BTC_JPY"
    right_exchange, right_symbol = "coincheck", "btc_jpy"
    main(
        left_exchange=left_exchange,
        left_symbol=left_symbol,
        right_exchange=right_exchange,
        right_symbol=right_symbol,
        days=5,
        candle_minutes=5,
    )

