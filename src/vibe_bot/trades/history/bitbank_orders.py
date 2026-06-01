from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from dotenv import load_dotenv
from plotly.subplots import make_subplots

from vibe_bot.bitbank import PrivateClient as BitbankPrivateClient
from vibe_bot.bitbank.models import Trade
from vibe_bot.trades.history.coincheck_orders import PricePoint
from vibe_bot.trades.history.coincheck_orders import PriceSource
from vibe_bot.trades.history.coincheck_orders import ProfitPoint
from vibe_bot.trades.history.coincheck_orders import SpotAmountPoint
from vibe_bot.trades.history.coincheck_orders import fetch_price_points
from vibe_bot.trades.history.history import validate_config

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class BitbankOrdersChartData:
    spot_amounts: list[SpotAmountPoint]
    prices: list[PricePoint]
    start: datetime
    end: datetime
    pair: str
    price_source: PriceSource
    candle_minutes: int
    profit_points: list[ProfitPoint] | None = None
    neutral_amount: Decimal | None = None


@dataclass(frozen=True)
class BitbankSpotData:
    spot_amounts: list[SpotAmountPoint]
    trades: list[Trade]


@dataclass
class PositionLot:
    side: int
    amount: Decimal
    price: Decimal | None


DEFAULT_PRICE_SOURCES: tuple[PriceSource, ...] = (
    PriceSource("bitbank", "btc_jpy"),
    PriceSource("coincheck", "btc_jpy"),
    PriceSource("GMO", "BTC"),
    PriceSource("bitFlyer", "FX_BTC_JPY"),
)


async def fetch_chart_data(
    *,
    days: int = 5,
    candle_minutes: int = 5,
    pair: str = "btc_jpy",
    price_sources: tuple[PriceSource, ...] = DEFAULT_PRICE_SOURCES,
    trade_limit: int = 1000,
    max_trade_pages: int | None = None,
    neutral_amount: Decimal | str | None = None,
) -> BitbankOrdersChartData:
    """Fetch recent bitbank spot BTC balance changes and BTC price candles."""
    validate_config(days, candle_minutes)
    if trade_limit <= 0:
        raise ValueError("trade_limit must be positive")
    if max_trade_pages is not None and max_trade_pages <= 0:
        raise ValueError("max_trade_pages must be positive")
    if not price_sources:
        raise ValueError("price_sources must not be empty")

    parsed_neutral_amount = _optional_decimal(neutral_amount)
    end = datetime.now(tz=JST)
    start = end - timedelta(days=days)
    base_asset = pair.split("_", 1)[0].lower()

    spot_task = asyncio.create_task(
        fetch_bitbank_spot_data(
            pair=pair,
            base_asset=base_asset,
            start=start,
            end=end,
            trade_limit=trade_limit,
            max_trade_pages=max_trade_pages,
        )
    )
    price_task = asyncio.create_task(
        fetch_price_points(
            price_sources=price_sources,
            candle_minutes=candle_minutes,
            start=start,
            end=end,
        )
    )
    spot_data, prices = await asyncio.gather(spot_task, price_task)
    if not spot_data.spot_amounts:
        raise RuntimeError("no bitbank spot amount points were returned")
    if not prices:
        raise RuntimeError("no BTC price candles were returned")

    profit_points = (
        build_profit_points(
            trades=spot_data.trades,
            spot_amounts=spot_data.spot_amounts,
            neutral_amount=parsed_neutral_amount,
            start=start,
            end=end,
        )
        if parsed_neutral_amount is not None
        else None
    )

    return BitbankOrdersChartData(
        spot_amounts=spot_data.spot_amounts,
        prices=prices,
        profit_points=profit_points,
        start=start,
        end=end,
        pair=pair,
        price_source=PriceSource(
            prices[0].source_exchange,
            prices[0].source_symbol,
        ),
        candle_minutes=candle_minutes,
        neutral_amount=parsed_neutral_amount,
    )


async def fetch_bitbank_spot_amounts(
    *,
    pair: str,
    base_asset: str,
    start: datetime,
    end: datetime,
    trade_limit: int,
    max_trade_pages: int | None,
) -> list[SpotAmountPoint]:
    spot_data = await fetch_bitbank_spot_data(
        pair=pair,
        base_asset=base_asset,
        start=start,
        end=end,
        trade_limit=trade_limit,
        max_trade_pages=max_trade_pages,
    )
    return spot_data.spot_amounts


async def fetch_bitbank_spot_data(
    *,
    pair: str,
    base_asset: str,
    start: datetime,
    end: datetime,
    trade_limit: int,
    max_trade_pages: int | None,
) -> BitbankSpotData:
    async with BitbankPrivateClient() as client:
        assets = await client.assets()
        trades = await _fetch_recent_trades(
            client,
            pair=pair,
            start=start,
            end=end,
            limit=trade_limit,
            max_pages=max_trade_pages,
        )

    current_amount = _asset_amount(assets.assets, base_asset)
    spot_trades = [
        trade
        for trade in trades
        if trade.pair == pair
        and trade.position_side is None
        and _trade_time(trade) <= end
    ]
    return BitbankSpotData(
        spot_amounts=build_spot_amount_points(
            current_amount=current_amount,
            trades=spot_trades,
            start=start,
            end=end,
        ),
        trades=spot_trades,
    )


def build_spot_amount_points(
    *,
    current_amount: Decimal,
    trades: list[Trade],
    start: datetime,
    end: datetime,
) -> list[SpotAmountPoint]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    ordered = sorted(trades, key=_trade_time)
    deltas = [(_trade_time(trade), _base_asset_delta(trade)) for trade in ordered]
    amount = current_amount - sum(
        (delta for timestamp, delta in deltas if timestamp >= start),
        Decimal("0"),
    )
    points: list[SpotAmountPoint] = [SpotAmountPoint(start_ms, amount)]

    for timestamp, delta in deltas:
        timestamp_ms = int(timestamp.timestamp() * 1000)
        if timestamp_ms < start_ms:
            continue
        if timestamp_ms > end_ms:
            break
        points.append(SpotAmountPoint(timestamp_ms, amount))
        amount += delta
        points.append(SpotAmountPoint(timestamp_ms, amount))

    points.append(SpotAmountPoint(end_ms, amount))
    return points


def build_profit_points(
    *,
    trades: list[Trade],
    spot_amounts: list[SpotAmountPoint],
    neutral_amount: Decimal,
    start: datetime,
    end: datetime,
) -> list[ProfitPoint]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    realized_profit = Decimal("0")
    points: list[ProfitPoint] = [ProfitPoint(start_ms, realized_profit)]
    lots: deque[PositionLot] = deque()

    initial_position = spot_amounts[0].amount - neutral_amount
    if initial_position > 0:
        lots.append(PositionLot(1, initial_position, None))
    elif initial_position < 0:
        lots.append(PositionLot(-1, abs(initial_position), None))

    for trade in sorted(trades, key=_trade_time):
        timestamp_ms = trade.executed_at
        if timestamp_ms < start_ms:
            continue
        if timestamp_ms > end_ms:
            break

        delta = _base_asset_delta(trade)
        if delta == 0:
            continue

        side = 1 if delta > 0 else -1
        remaining = abs(delta)
        points.append(ProfitPoint(timestamp_ms, realized_profit))

        while remaining > 0 and lots and lots[0].side != side:
            lot = lots[0]
            close_amount = min(remaining, lot.amount)
            if lot.price is not None:
                if lot.side > 0:
                    realized_profit += (trade.price - lot.price) * close_amount
                else:
                    realized_profit += (lot.price - trade.price) * close_amount

            lot.amount -= close_amount
            remaining -= close_amount
            if lot.amount == 0:
                lots.popleft()

        if remaining > 0:
            lots.append(PositionLot(side, remaining, trade.price))

        points.append(ProfitPoint(timestamp_ms, realized_profit))

    points.append(ProfitPoint(end_ms, realized_profit))
    return points


def build_figure(data: BitbankOrdersChartData) -> go.Figure:
    price_x = [
        datetime.fromtimestamp(point.timestamp / 1000, tz=JST)
        for point in data.prices
    ]
    amount_x = [
        datetime.fromtimestamp(point.timestamp / 1000, tz=JST)
        for point in data.spot_amounts
    ]
    has_profit = data.profit_points is not None
    rows = 3 if has_profit else 2

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.46, 0.27, 0.27] if has_profit else [0.58, 0.42],
        subplot_titles=(
            ("BTC Price", "bitbank Spot BTC Amount", "Realized Profit")
            if has_profit
            else ("BTC Price", "bitbank Spot BTC Amount")
        ),
    )
    fig.add_trace(
        go.Scatter(
            x=price_x,
            y=[float(point.close) for point in data.prices],
            mode="lines",
            name=f"{data.price_source.exchange} close",
            line={"color": "#1464d2", "width": 1.6},
            hovertemplate="%{y:,.0f} JPY<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=amount_x,
            y=[float(point.amount) for point in data.spot_amounts],
            mode="lines",
            line_shape="hv",
            name="bitbank spot BTC",
            line={"color": "#0f9f6e", "width": 1.8},
            hovertemplate="%{y:.8f} BTC<extra></extra>",
        ),
        row=2,
        col=1,
    )
    if data.profit_points is not None:
        fig.add_trace(
            go.Scatter(
                x=[
                    datetime.fromtimestamp(point.timestamp / 1000, tz=JST)
                    for point in data.profit_points
                ],
                y=[float(point.profit) for point in data.profit_points],
                mode="lines",
                line_shape="hv",
                name="Realized profit",
                line={"color": "#c2410c", "width": 1.8},
                hovertemplate="%{y:,.0f} JPY<extra></extra>",
            ),
            row=3,
            col=1,
        )
        fig.add_hline(
            y=0,
            line={"color": "#98a2b3", "width": 1, "dash": "dot"},
            row=3,
            col=1,
        )

    fig.update_layout(
        title=(
            f"bitbank Spot BTC Amount and BTC Price "
            f"({data.candle_minutes}min candles, {data.start:%Y-%m-%d} - {data.end:%Y-%m-%d} JST)"
        ),
        hovermode="x unified",
        template="plotly_white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        margin={"l": 72, "r": 24, "t": 90, "b": 64},
        height=1050 if has_profit else 850,
    )
    fig.update_yaxes(title_text="BTC/JPY", tickformat=",", row=1, col=1)
    fig.update_yaxes(title_text="BTC", tickformat=".8f", row=2, col=1)
    if has_profit:
        fig.update_yaxes(title_text="JPY", tickformat=",", row=3, col=1)
    fig.update_xaxes(title_text="Time (JST)", row=rows, col=1)
    annotation_text = (
        "Spot amount is reconstructed from current bitbank on-hand balance and "
        "recent bitbank spot executions, including base-asset trading fees."
    )
    if data.neutral_amount is not None:
        annotation_text += (
            f" Profit treats {data.neutral_amount} BTC as neutral and uses "
            "observed executions as entries/exits; pre-window entry prices are unknown."
        )
    fig.add_annotation(
        text=annotation_text,
        xref="paper",
        yref="paper",
        x=0,
        y=-0.18,
        showarrow=False,
        font={"size": 12, "color": "#667085"},
    )
    return fig


async def run(
    *,
    days: int = 5,
    candle_minutes: int = 5,
    pair: str = "btc_jpy",
    price_sources: tuple[PriceSource, ...] = DEFAULT_PRICE_SOURCES,
    trade_limit: int = 1000,
    max_trade_pages: int | None = None,
    neutral_amount: Decimal | str | None = None,
    output_html: Path | str | None = None,
    show: bool = True,
) -> go.Figure:
    data = await fetch_chart_data(
        days=days,
        candle_minutes=candle_minutes,
        pair=pair,
        price_sources=price_sources,
        trade_limit=trade_limit,
        max_trade_pages=max_trade_pages,
        neutral_amount=neutral_amount,
    )
    fig = build_figure(data)
    if output_html is not None:
        output_path = Path(output_html)
        fig.write_html(output_path, include_plotlyjs="cdn")
        print(f"wrote: {output_path}")
    if show:
        fig.show(renderer="browser")
    return fig


def main(
    *,
    days: int = 5,
    candle_minutes: int = 5,
    pair: str = "btc_jpy",
    price_sources: tuple[PriceSource, ...] = DEFAULT_PRICE_SOURCES,
    trade_limit: int = 1000,
    max_trade_pages: int | None = None,
    neutral_amount: Decimal | str | None = None,
    output_html: Path | str | None = None,
    show: bool = True,
) -> go.Figure:
    """Build the bitbank spot BTC amount chart with direct function arguments."""
    load_dotenv()
    return asyncio.run(
        run(
            days=days,
            candle_minutes=candle_minutes,
            pair=pair,
            price_sources=price_sources,
            trade_limit=trade_limit,
            max_trade_pages=max_trade_pages,
            neutral_amount=neutral_amount,
            output_html=output_html,
            show=show,
        )
    )


async def _fetch_recent_trades(
    client: BitbankPrivateClient,
    *,
    pair: str,
    start: datetime,
    end: datetime,
    limit: int,
    max_pages: int | None,
) -> list[Trade]:
    rows_by_id: dict[int, Trade] = {}
    start_ms = int(start.timestamp() * 1000)
    page_end_ms = int(end.timestamp() * 1000)
    pages = 0
    while max_pages is None or pages < max_pages:
        payload = await client.trade_history(
            pair=pair,
            count=limit,
            since=start_ms,
            end=page_end_ms,
            order="desc",
        )
        page_trades = payload.trades
        if not page_trades:
            break
        pages += 1

        previous_count = len(rows_by_id)
        for trade in page_trades:
            rows_by_id[trade.trade_id] = trade
        oldest_ms = min(trade.executed_at for trade in page_trades)
        if len(page_trades) < limit or oldest_ms <= start_ms:
            break
        if len(rows_by_id) == previous_count or oldest_ms >= page_end_ms:
            break
        page_end_ms = oldest_ms
    return list(rows_by_id.values())


def _asset_amount(assets: list[object], base_asset: str) -> Decimal:
    for asset in assets:
        if getattr(asset, "asset", "").lower() == base_asset:
            return asset.onhand_amount
    return Decimal("0")


def _base_asset_delta(trade: Trade) -> Decimal:
    amount = trade.amount if trade.side == "buy" else -trade.amount
    return amount - trade.fee_amount_base


def _trade_time(trade: Trade) -> datetime:
    return datetime.fromtimestamp(trade.executed_at / 1000, tz=JST)


def _optional_decimal(value: Decimal | str | None) -> Decimal | None:
    if value is None:
        return None
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if amount < 0:
        raise ValueError("neutral_amount must be non-negative")
    return amount


if __name__ == "__main__":
    main(days=5, candle_minutes=5)
