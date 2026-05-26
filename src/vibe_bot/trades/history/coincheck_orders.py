from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from dotenv import load_dotenv
from plotly.subplots import make_subplots

from vibe_bot.coincheck import PrivateClient as CoincheckPrivateClient
from vibe_bot.coincheck.models import Transaction
from vibe_bot.trades.history.history import _fetch_exchange_closes, validate_config

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class PriceSource:
    exchange: str
    symbol: str


@dataclass(frozen=True)
class SpotAmountPoint:
    timestamp: int
    amount: Decimal


@dataclass(frozen=True)
class PricePoint:
    timestamp: int
    close: Decimal
    source_exchange: str
    source_symbol: str


@dataclass(frozen=True)
class CoincheckOrdersChartData:
    spot_amounts: list[SpotAmountPoint]
    prices: list[PricePoint]
    start: datetime
    end: datetime
    pair: str
    price_source: PriceSource
    candle_minutes: int


DEFAULT_PRICE_SOURCES: tuple[PriceSource, ...] = (
    PriceSource("coincheck", "btc_jpy"),
    PriceSource("bitbank", "btc_jpy"),
    PriceSource("GMO", "BTC"),
    PriceSource("bitFlyer", "FX_BTC_JPY"),
)


async def fetch_chart_data(
    *,
    days: int = 5,
    candle_minutes: int = 5,
    pair: str = "btc_jpy",
    price_sources: tuple[PriceSource, ...] = DEFAULT_PRICE_SOURCES,
    transaction_limit: int = 100,
    max_transaction_pages: int = 20,
) -> CoincheckOrdersChartData:
    """Fetch recent Coincheck spot BTC balance changes and BTC price candles."""
    validate_config(days, candle_minutes)
    if transaction_limit <= 0:
        raise ValueError("transaction_limit must be positive")
    if max_transaction_pages <= 0:
        raise ValueError("max_transaction_pages must be positive")
    if not price_sources:
        raise ValueError("price_sources must not be empty")

    end = datetime.now(tz=JST)
    start = end - timedelta(days=days)
    base_asset = pair.split("_", 1)[0].lower()

    spot_task = asyncio.create_task(
        fetch_coincheck_spot_amounts(
            pair=pair,
            base_asset=base_asset,
            start=start,
            end=end,
            transaction_limit=transaction_limit,
            max_transaction_pages=max_transaction_pages,
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
    spot_amounts, prices = await asyncio.gather(spot_task, price_task)
    if not spot_amounts:
        raise RuntimeError("no Coincheck spot amount points were returned")
    if not prices:
        raise RuntimeError("no BTC price candles were returned")

    return CoincheckOrdersChartData(
        spot_amounts=spot_amounts,
        prices=prices,
        start=start,
        end=end,
        pair=pair,
        price_source=PriceSource(
            prices[0].source_exchange,
            prices[0].source_symbol,
        ),
        candle_minutes=candle_minutes,
    )


async def fetch_coincheck_spot_amounts(
    *,
    pair: str,
    base_asset: str,
    start: datetime,
    end: datetime,
    transaction_limit: int,
    max_transaction_pages: int,
) -> list[SpotAmountPoint]:
    async with CoincheckPrivateClient() as client:
        balance = await client.balance()
        transactions = await _fetch_recent_transactions(
            client,
            start=start,
            limit=transaction_limit,
            max_pages=max_transaction_pages,
        )

    current_amount = _balance_amount(balance.balances, base_asset)
    relevant = [
        transaction
        for transaction in transactions
        if transaction.pair == pair and _transaction_time(transaction) <= end
    ]
    return build_spot_amount_points(
        current_amount=current_amount,
        transactions=relevant,
        base_asset=base_asset,
        start=start,
        end=end,
    )


async def fetch_price_points(
    *,
    price_sources: tuple[PriceSource, ...],
    candle_minutes: int,
    start: datetime,
    end: datetime,
) -> list[PricePoint]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    for source in price_sources:
        closes = await _fetch_exchange_closes(
            source.exchange,
            symbol=source.symbol,
            candle_minutes=candle_minutes,
            start=start,
            end=end,
            start_ms=start_ms,
        )
        points = [
            PricePoint(
                timestamp=timestamp,
                close=close,
                source_exchange=source.exchange,
                source_symbol=source.symbol,
            )
            for timestamp, close in sorted(closes.items())
            if start_ms <= timestamp <= end_ms
        ]
        if points:
            return points
    return []


def build_spot_amount_points(
    *,
    current_amount: Decimal,
    transactions: list[Transaction],
    base_asset: str,
    start: datetime,
    end: datetime,
) -> list[SpotAmountPoint]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    ordered = sorted(transactions, key=_transaction_time)
    deltas = [
        (_transaction_time(transaction), _base_asset_delta(transaction, base_asset))
        for transaction in ordered
    ]
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


def build_figure(data: CoincheckOrdersChartData) -> go.Figure:
    price_x = [
        datetime.fromtimestamp(point.timestamp / 1000, tz=JST)
        for point in data.prices
    ]
    price_y = [float(point.close) for point in data.prices]
    amount_x = [
        datetime.fromtimestamp(point.timestamp / 1000, tz=JST)
        for point in data.spot_amounts
    ]
    amount_y = [float(point.amount) for point in data.spot_amounts]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.58, 0.42],
        subplot_titles=("BTC Price", "Coincheck Spot BTC Amount"),
    )
    fig.add_trace(
        go.Scatter(
            x=price_x,
            y=price_y,
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
            y=amount_y,
            mode="lines",
            line_shape="hv",
            name="Coincheck spot BTC",
            line={"color": "#0f9f6e", "width": 1.8},
            hovertemplate="%{y:.8f} BTC<extra></extra>",
        ),
        row=2,
        col=1,
    )
    fig.update_layout(
        title=(
            f"Coincheck Spot BTC Amount and BTC Price "
            f"({data.candle_minutes}min candles, {data.start:%Y-%m-%d} - {data.end:%Y-%m-%d} JST)"
        ),
        hovermode="x unified",
        template="plotly_white",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        margin={"l": 72, "r": 24, "t": 90, "b": 64},
        height=850,
    )
    fig.update_yaxes(title_text="BTC/JPY", tickformat=",", row=1, col=1)
    fig.update_yaxes(title_text="BTC", tickformat=".8f", row=2, col=1)
    fig.update_xaxes(title_text="Time (JST)", row=2, col=1)
    fig.add_annotation(
        text=(
            "Spot amount is reconstructed from current Coincheck balance and "
            "recent Coincheck spot executions."
        ),
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
    transaction_limit: int = 100,
    max_transaction_pages: int = 20,
    output_html: Path | str | None = None,
    show: bool = True,
) -> go.Figure:
    data = await fetch_chart_data(
        days=days,
        candle_minutes=candle_minutes,
        pair=pair,
        price_sources=price_sources,
        transaction_limit=transaction_limit,
        max_transaction_pages=max_transaction_pages,
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
    transaction_limit: int = 100,
    max_transaction_pages: int = 20,
    output_html: Path | str | None = None,
    show: bool = True,
) -> go.Figure:
    """Build the Coincheck spot BTC amount chart with direct function arguments."""
    load_dotenv()
    return asyncio.run(
        run(
            days=days,
            candle_minutes=candle_minutes,
            pair=pair,
            price_sources=price_sources,
            transaction_limit=transaction_limit,
            max_transaction_pages=max_transaction_pages,
            output_html=output_html,
            show=show,
        )
    )


async def _fetch_recent_transactions(
    client: CoincheckPrivateClient,
    *,
    start: datetime,
    limit: int,
    max_pages: int,
) -> list[Transaction]:
    rows: list[Transaction] = []
    ending_before: int | None = None
    for _ in range(max_pages):
        page = await client.transactions(
            limit=limit,
            order="desc",
            ending_before=ending_before,
        )
        if not page.transactions:
            break
        rows.extend(page.transactions)
        oldest = min(page.transactions, key=_transaction_time)
        if _transaction_time(oldest) < start:
            break
        next_ending_before = min(transaction.id for transaction in page.transactions)
        if ending_before == next_ending_before:
            break
        ending_before = next_ending_before
    return rows


def _balance_amount(balances: dict[str, Decimal], base_asset: str) -> Decimal:
    for asset, amount in balances.items():
        if asset.lower() == base_asset:
            return amount
    return Decimal("0")


def _base_asset_delta(transaction: Transaction, base_asset: str) -> Decimal:
    funds = transaction.funds or {}
    for asset, amount in funds.items():
        if asset.lower() == base_asset:
            return amount
    if transaction.rate is not None:
        quote_amount = funds.get("jpy")
        if quote_amount is not None:
            try:
                amount = abs(quote_amount) / transaction.rate
            except (InvalidOperation, ZeroDivisionError):
                amount = Decimal("0")
            return amount if transaction.side == "buy" else -amount
    return Decimal("0")


def _transaction_time(transaction: Transaction) -> datetime:
    timestamp = transaction.created_at.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(JST)


if __name__ == "__main__":
    main(days=3, candle_minutes=5)
