from __future__ import annotations

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

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class HistoryConfig:
    bitbank_pair: str = "btc_jpy"
    bitflyer_product_code: str = "FX_BTC_JPY"
    days: int = 5
    candle_minutes: int = 5
    tick_size: Decimal = Decimal("1")


@dataclass(frozen=True)
class HistoricalSpreadPoint:
    timestamp: int
    buy_price: Decimal
    sell_price: Decimal
    close_spread: Decimal
    bitbank_close: Decimal
    bitflyer_close: Decimal


async def fetch_historical_spreads(
    config: HistoryConfig,
) -> list[HistoricalSpreadPoint]:
    """Fetch candle-based historical spread estimates.

    Candlesticks do not include bid/ask or order-book depth, so this estimates:
    BUY = bitbank close - tick - bitFlyer close
    SELL = bitbank close + tick - bitFlyer close
    """
    now = datetime.now(tz=JST)
    start = now - timedelta(days=config.days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    async with BitbankPublicClient() as bitbank, BitflyerPublicClient() as bitflyer:
        bitbank_task = asyncio.create_task(
            _fetch_bitbank_closes(
                bitbank,
                pair=config.bitbank_pair,
                candle_minutes=config.candle_minutes,
                start=start,
                end=now,
            )
        )
        bitflyer_task = asyncio.create_task(
            _fetch_bitflyer_closes(
                bitflyer,
                product_code=config.bitflyer_product_code,
                candle_minutes=config.candle_minutes,
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


def build_figure(
    points: list[HistoricalSpreadPoint],
    config: HistoryConfig,
) -> go.Figure:
    x = [datetime.fromtimestamp(point.timestamp / 1000, tz=JST) for point in points]
    buy = [float(point.buy_price) for point in points]
    sell = [float(point.sell_price) for point in points]
    close = [float(point.close_spread) for point in points]
    bitbank_close = [float(point.bitbank_close) for point in points]
    bitflyer_close = [float(point.bitflyer_close) for point in points]
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
            y=bitbank_close,
            mode="lines",
            name="bitbank close",
            line={"color": "#0f9f6e", "width": 1.4},
        ),
        row=2,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=bitflyer_close,
            mode="lines",
            name="bitFlyer close",
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
            "bitbank / bitFlyer Historical Spread "
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


async def _run(config: HistoryConfig, output_html: Path | str | None) -> go.Figure:
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
    bitbank_pair: str = "btc_jpy",
    bitflyer_product_code: str = "FX_BTC_JPY",
    days: int = 5,
    candle_minutes: int = 5,
    tick_size: Decimal | str = Decimal("1"),
    output_html: Path | str | None = None,
) -> go.Figure:
    """Fetch historical candles and open a Plotly spread chart.

    IPython usage:
        fig = main(days=5, candle_minutes=5)
        fig = main(days=3, candle_minutes=15, output_html="/tmp/spread.html")
    """
    if days <= 0:
        raise ValueError("days must be positive")
    if candle_minutes not in (1, 5, 15, 30):
        raise ValueError("candle_minutes must be one of 1, 5, 15, 30")
    tick = Decimal(str(tick_size))
    if tick <= 0:
        raise ValueError("tick_size must be positive")
    config = HistoryConfig(
        bitbank_pair=bitbank_pair,
        bitflyer_product_code=bitflyer_product_code,
        days=days,
        candle_minutes=candle_minutes,
        tick_size=tick,
    )
    return asyncio.run(_run(config, output_html))


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
        before = _previous_bitflyer_lightchart_boundary_ms(oldest)
    return closes


def _previous_bitflyer_lightchart_boundary_ms(timestamp_ms: int) -> int:
    timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=JST)
    boundary_hour = 21 if timestamp.hour >= 21 else 9
    boundary = timestamp.replace(
        hour=boundary_hour, minute=0, second=0, microsecond=0
    )
    return int(boundary.timestamp() * 1000)


if __name__ == "__main__":
    main(
        bitbank_pair="btc_jpy",
        bitflyer_product_code="FX_BTC_JPY",
        days=5,
        candle_minutes=5,
        tick_size="1",
        output_html=None,
    )
