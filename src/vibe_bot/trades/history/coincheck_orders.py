from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
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
class ProfitPoint:
    timestamp: int
    profit: Decimal


@dataclass(frozen=True)
class CoincheckOrdersChartData:
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
class CoincheckSpotData:
    spot_amounts: list[SpotAmountPoint]
    transactions: list[Transaction]


@dataclass
class PositionLot:
    side: int
    amount: Decimal
    price: Decimal | None


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
    max_transaction_pages: int | None = None,
    neutral_amount: Decimal | str | None = None,
) -> CoincheckOrdersChartData:
    """Fetch recent Coincheck spot BTC balance changes and BTC price candles."""
    validate_config(days, candle_minutes)
    if transaction_limit <= 0:
        raise ValueError("transaction_limit must be positive")
    if max_transaction_pages is not None and max_transaction_pages <= 0:
        raise ValueError("max_transaction_pages must be positive")
    if not price_sources:
        raise ValueError("price_sources must not be empty")

    parsed_neutral_amount = _optional_decimal(neutral_amount)
    end = datetime.now(tz=JST)
    start = end - timedelta(days=days)
    base_asset = pair.split("_", 1)[0].lower()

    spot_task = asyncio.create_task(
        fetch_coincheck_spot_data(
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
    spot_data, prices = await asyncio.gather(spot_task, price_task)
    if not spot_data.spot_amounts:
        raise RuntimeError("no Coincheck spot amount points were returned")
    if not prices:
        raise RuntimeError("no BTC price candles were returned")

    profit_points = (
        build_profit_points(
            transactions=spot_data.transactions,
            spot_amounts=spot_data.spot_amounts,
            neutral_amount=parsed_neutral_amount,
            base_asset=base_asset,
            start=start,
            end=end,
        )
        if parsed_neutral_amount is not None
        else None
    )

    return CoincheckOrdersChartData(
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


async def fetch_coincheck_spot_amounts(
    *,
    pair: str,
    base_asset: str,
    start: datetime,
    end: datetime,
    transaction_limit: int,
    max_transaction_pages: int | None,
) -> list[SpotAmountPoint]:
    spot_data = await fetch_coincheck_spot_data(
        pair=pair,
        base_asset=base_asset,
        start=start,
        end=end,
        transaction_limit=transaction_limit,
        max_transaction_pages=max_transaction_pages,
    )
    return spot_data.spot_amounts


async def fetch_coincheck_spot_data(
    *,
    pair: str,
    base_asset: str,
    start: datetime,
    end: datetime,
    transaction_limit: int,
    max_transaction_pages: int | None,
) -> CoincheckSpotData:
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
    return CoincheckSpotData(
        spot_amounts=build_spot_amount_points(
            current_amount=current_amount,
            transactions=relevant,
            base_asset=base_asset,
            start=start,
            end=end,
        ),
        transactions=relevant,
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
    candle_ms = candle_minutes * 60 * 1000
    best_partial: list[PricePoint] = []
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
        if not points:
            continue
        if (
            points[0].timestamp <= start_ms + candle_ms
            and points[-1].timestamp >= end_ms - candle_ms
        ):
            return points
        if not best_partial or _covered_duration_ms(points) > _covered_duration_ms(
            best_partial
        ):
            best_partial = points
    return best_partial


def _covered_duration_ms(points: list[PricePoint]) -> int:
    if not points:
        return 0
    return points[-1].timestamp - points[0].timestamp


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


def build_profit_points(
    *,
    transactions: list[Transaction],
    spot_amounts: list[SpotAmountPoint],
    neutral_amount: Decimal,
    base_asset: str,
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

    for transaction in sorted(transactions, key=_transaction_time):
        timestamp = _transaction_time(transaction)
        timestamp_ms = int(timestamp.timestamp() * 1000)
        if timestamp_ms < start_ms:
            continue
        if timestamp_ms > end_ms:
            break

        delta = _base_asset_delta(transaction, base_asset)
        if delta == 0:
            continue

        price = transaction.rate
        side = 1 if delta > 0 else -1
        remaining = abs(delta)
        points.append(ProfitPoint(timestamp_ms, realized_profit))

        while remaining > 0 and lots and lots[0].side != side:
            lot = lots[0]
            close_amount = min(remaining, lot.amount)
            if lot.price is not None and price is not None:
                if lot.side > 0:
                    realized_profit += (price - lot.price) * close_amount
                else:
                    realized_profit += (lot.price - price) * close_amount

            lot.amount -= close_amount
            remaining -= close_amount
            if lot.amount == 0:
                lots.popleft()

        if remaining > 0:
            lots.append(PositionLot(side, remaining, price))

        points.append(ProfitPoint(timestamp_ms, realized_profit))

    points.append(ProfitPoint(end_ms, realized_profit))
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
    has_profit = data.profit_points is not None
    rows = 3 if has_profit else 2

    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.46, 0.27, 0.27] if has_profit else [0.58, 0.42],
        subplot_titles=(
            ("BTC Price", "Coincheck Spot BTC Amount", "Realized Profit")
            if has_profit
            else ("BTC Price", "Coincheck Spot BTC Amount")
        ),
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
    if data.profit_points is not None:
        profit_x = [
            datetime.fromtimestamp(point.timestamp / 1000, tz=JST)
            for point in data.profit_points
        ]
        profit_y = [float(point.profit) for point in data.profit_points]
        fig.add_trace(
            go.Scatter(
                x=profit_x,
                y=profit_y,
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
            f"Coincheck Spot BTC Amount and BTC Price "
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
        "Spot amount is reconstructed from current Coincheck balance and "
        "recent Coincheck spot executions."
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
    transaction_limit: int = 100,
    max_transaction_pages: int | None = None,
    neutral_amount: Decimal | str | None = None,
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
    transaction_limit: int = 100,
    max_transaction_pages: int | None = None,
    neutral_amount: Decimal | str | None = None,
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
            neutral_amount=neutral_amount,
            output_html=output_html,
            show=show,
        )
    )


async def _fetch_recent_transactions(
    client: CoincheckPrivateClient,
    *,
    start: datetime,
    limit: int,
    max_pages: int | None,
) -> list[Transaction]:
    rows_by_id: dict[int, Transaction] = {}
    starting_after: int | None = None
    pages = 0
    while max_pages is None or pages < max_pages:
        payload = await client.transactions_pagination(
            limit=limit,
            order="desc",
            starting_after=starting_after,
        )
        page_transactions = _pagination_transactions(payload)
        if not page_transactions:
            break
        pages += 1

        for transaction in page_transactions:
            rows_by_id[transaction.id] = transaction
        oldest = min(page_transactions, key=_transaction_time)
        if _transaction_time(oldest) < start:
            break

        next_starting_after = min(transaction.id for transaction in page_transactions)
        if starting_after == next_starting_after:
            break
        starting_after = next_starting_after
    return list(rows_by_id.values())


def _pagination_transactions(payload: Any) -> list[Transaction]:
    rows = payload.get("data") if isinstance(payload, dict) else payload
    return [Transaction.model_validate(row) for row in (rows or [])]


def _balance_amount(balances: dict[str, Decimal], base_asset: str) -> Decimal:
    total = Decimal("0")
    for asset, amount in balances.items():
        if asset.lower() == base_asset:
            total += amount
        elif asset.lower() == f"{base_asset}_reserved":
            total += amount
    return total


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


def _optional_decimal(value: Decimal | str | None) -> Decimal | None:
    if value is None:
        return None
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    if amount < 0:
        raise ValueError("neutral_amount must be non-negative")
    return amount


if __name__ == "__main__":
    main(days=10, candle_minutes=5, neutral_amount="0.05")
