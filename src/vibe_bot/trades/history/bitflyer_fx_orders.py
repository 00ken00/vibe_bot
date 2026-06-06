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

from vibe_bot.bitflyer import PrivateClient as BitflyerPrivateClient
from vibe_bot.bitflyer.models import Position
from vibe_bot.bitflyer.models import PrivateExecution
from vibe_bot.trades.history.coincheck_orders import PricePoint
from vibe_bot.trades.history.coincheck_orders import PriceSource
from vibe_bot.trades.history.coincheck_orders import ProfitPoint
from vibe_bot.trades.history.coincheck_orders import SpotAmountPoint
from vibe_bot.trades.history.coincheck_orders import fetch_price_points
from vibe_bot.trades.history.history import validate_config

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class BitflyerFxOrdersChartData:
    positions: list[SpotAmountPoint]
    prices: list[PricePoint]
    start: datetime
    end: datetime
    product_code: str
    price_source: PriceSource
    candle_minutes: int
    profit_points: list[ProfitPoint] | None = None
    neutral_position: Decimal | None = None
    current_position_funding_fees: Decimal = Decimal("0")
    current_position_swap_points: Decimal = Decimal("0")
    current_position_sfd: Decimal = Decimal("0")
    current_position_commission: Decimal = Decimal("0")
    open_position_details: list[dict[str, object]] | None = None


@dataclass(frozen=True)
class BitflyerFxData:
    positions: list[SpotAmountPoint]
    executions: list[PrivateExecution]
    current_position: Decimal
    current_position_funding_fees: Decimal
    current_position_swap_points: Decimal
    current_position_sfd: Decimal
    current_position_commission: Decimal
    open_position_details: list[dict[str, object]]


@dataclass
class PositionLot:
    side: int
    amount: Decimal
    price: Decimal | None


DEFAULT_PRICE_SOURCES: tuple[PriceSource, ...] = (
    PriceSource("bitFlyer", "FX_BTC_JPY"),
    PriceSource("bitbank", "btc_jpy"),
    PriceSource("coincheck", "btc_jpy"),
    PriceSource("GMO", "BTC"),
)


async def fetch_chart_data(
    *,
    days: int = 5,
    candle_minutes: int = 5,
    product_code: str = "FX_BTC_JPY",
    price_sources: tuple[PriceSource, ...] = DEFAULT_PRICE_SOURCES,
    execution_limit: int = 500,
    max_execution_pages: int | None = None,
    neutral_position: Decimal | str | None = Decimal("0"),
) -> BitflyerFxOrdersChartData:
    """Fetch recent bitFlyer FX position changes and BTC price candles."""
    validate_config(days, candle_minutes)
    if execution_limit <= 0:
        raise ValueError("execution_limit must be positive")
    if max_execution_pages is not None and max_execution_pages <= 0:
        raise ValueError("max_execution_pages must be positive")
    if not price_sources:
        raise ValueError("price_sources must not be empty")

    parsed_neutral_position = _optional_decimal(neutral_position)
    end = datetime.now(tz=JST)
    start = end - timedelta(days=days)

    fx_task = asyncio.create_task(
        fetch_bitflyer_fx_data(
            product_code=product_code,
            start=start,
            end=end,
            execution_limit=execution_limit,
            max_execution_pages=max_execution_pages,
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
    fx_data, prices = await asyncio.gather(fx_task, price_task)
    if not fx_data.positions:
        raise RuntimeError("no bitFlyer FX position points were returned")
    if not prices:
        raise RuntimeError("no BTC price candles were returned")

    profit_points = (
        build_profit_points(
            executions=fx_data.executions,
            positions=fx_data.positions,
            neutral_position=parsed_neutral_position,
            start=start,
            end=end,
        )
        if parsed_neutral_position is not None
        else None
    )

    return BitflyerFxOrdersChartData(
        positions=fx_data.positions,
        prices=prices,
        profit_points=profit_points,
        start=start,
        end=end,
        product_code=product_code,
        price_source=PriceSource(
            prices[0].source_exchange,
            prices[0].source_symbol,
        ),
        candle_minutes=candle_minutes,
        neutral_position=parsed_neutral_position,
        current_position_funding_fees=fx_data.current_position_funding_fees,
        current_position_swap_points=fx_data.current_position_swap_points,
        current_position_sfd=fx_data.current_position_sfd,
        current_position_commission=fx_data.current_position_commission,
        open_position_details=fx_data.open_position_details,
    )


async def fetch_bitflyer_fx_data(
    *,
    product_code: str,
    start: datetime,
    end: datetime,
    execution_limit: int,
    max_execution_pages: int | None,
) -> BitflyerFxData:
    async with BitflyerPrivateClient() as client:
        positions = await client.positions(product_code=product_code)
        executions = await _fetch_recent_executions(
            client,
            product_code=product_code,
            start=start,
            limit=execution_limit,
            max_pages=max_execution_pages,
        )

    current_position = _current_position(positions)
    fee_summary = _position_fee_summary(positions)
    relevant = [
        execution
        for execution in executions
        if _execution_time(execution) <= end
    ]
    return BitflyerFxData(
        positions=build_position_points(
            current_position=current_position,
            executions=relevant,
            start=start,
            end=end,
        ),
        executions=relevant,
        current_position=current_position,
        current_position_funding_fees=fee_summary["funding_fees"],
        current_position_swap_points=fee_summary["swap_point_accumulate"],
        current_position_sfd=fee_summary["sfd"],
        current_position_commission=fee_summary["commission"],
        open_position_details=fee_summary["positions"],
    )


def build_position_points(
    *,
    current_position: Decimal,
    executions: list[PrivateExecution],
    start: datetime,
    end: datetime,
) -> list[SpotAmountPoint]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    ordered = sorted(executions, key=_execution_sort_key)
    deltas = [
        (_execution_time(execution), _position_delta(execution))
        for execution in ordered
    ]
    position = current_position - sum(
        (delta for timestamp, delta in deltas if timestamp >= start),
        Decimal("0"),
    )
    points: list[SpotAmountPoint] = [SpotAmountPoint(start_ms, position)]

    for timestamp, delta in deltas:
        timestamp_ms = int(timestamp.timestamp() * 1000)
        if timestamp_ms < start_ms:
            continue
        if timestamp_ms > end_ms:
            break
        points.append(SpotAmountPoint(timestamp_ms, position))
        position += delta
        points.append(SpotAmountPoint(timestamp_ms, position))

    points.append(SpotAmountPoint(end_ms, position))
    return points


def build_profit_points(
    *,
    executions: list[PrivateExecution],
    positions: list[SpotAmountPoint],
    neutral_position: Decimal,
    start: datetime,
    end: datetime,
) -> list[ProfitPoint]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    realized_profit = Decimal("0")
    points: list[ProfitPoint] = [ProfitPoint(start_ms, realized_profit)]
    lots: deque[PositionLot] = deque()

    initial_position = positions[0].amount - neutral_position
    if initial_position > 0:
        lots.append(PositionLot(1, initial_position, None))
    elif initial_position < 0:
        lots.append(PositionLot(-1, abs(initial_position), None))

    for execution in sorted(executions, key=_execution_sort_key):
        timestamp = _execution_time(execution)
        timestamp_ms = int(timestamp.timestamp() * 1000)
        if timestamp_ms < start_ms:
            continue
        if timestamp_ms > end_ms:
            break

        delta = _position_delta(execution)
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
                    realized_profit += (execution.price - lot.price) * close_amount
                else:
                    realized_profit += (lot.price - execution.price) * close_amount

            lot.amount -= close_amount
            remaining -= close_amount
            if lot.amount == 0:
                lots.popleft()

        if remaining > 0:
            lots.append(PositionLot(side, remaining, execution.price))

        points.append(ProfitPoint(timestamp_ms, realized_profit))

    points.append(ProfitPoint(end_ms, realized_profit))
    return points


def build_figure(data: BitflyerFxOrdersChartData) -> go.Figure:
    price_x = [
        datetime.fromtimestamp(point.timestamp / 1000, tz=JST)
        for point in data.prices
    ]
    position_x = [
        datetime.fromtimestamp(point.timestamp / 1000, tz=JST)
        for point in data.positions
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
            ("BTC Price", "bitFlyer FX Net BTC Position", "Realized PnL")
            if has_profit
            else ("BTC Price", "bitFlyer FX Net BTC Position")
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
            x=position_x,
            y=[float(point.amount) for point in data.positions],
            mode="lines",
            line_shape="hv",
            name="bitFlyer FX net BTC",
            line={"color": "#0f9f6e", "width": 1.8},
            hovertemplate="%{y:.8f} BTC<extra></extra>",
        ),
        row=2,
        col=1,
    )
    fig.add_hline(
        y=0,
        line={"color": "#98a2b3", "width": 1, "dash": "dot"},
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
                name="Realized PnL",
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
            f"bitFlyer FX Net BTC Position and BTC Price "
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
        "Net position is reconstructed from current bitFlyer FX open positions "
        "and recent private executions. Positive is long, negative is short."
    )
    if data.neutral_position is not None:
        annotation_text += (
            f" Realized PnL treats {data.neutral_position} BTC as neutral and uses "
            "observed executions as entries/exits; pre-window entry prices are unknown. "
            "Execution commission is not subtracted."
        )
    annotation_text += (
        f" Current open-position funding_fees={data.current_position_funding_fees} JPY, "
        f"swap_point_accumulate={data.current_position_swap_points} JPY, "
        f"sfd={data.current_position_sfd} JPY, "
        f"commission={data.current_position_commission} JPY."
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
    product_code: str = "FX_BTC_JPY",
    price_sources: tuple[PriceSource, ...] = DEFAULT_PRICE_SOURCES,
    execution_limit: int = 500,
    max_execution_pages: int | None = None,
    neutral_position: Decimal | str | None = Decimal("0"),
    output_html: Path | str | None = None,
    show: bool = True,
) -> go.Figure:
    data = await fetch_chart_data(
        days=days,
        candle_minutes=candle_minutes,
        product_code=product_code,
        price_sources=price_sources,
        execution_limit=execution_limit,
        max_execution_pages=max_execution_pages,
        neutral_position=neutral_position,
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
    product_code: str = "FX_BTC_JPY",
    price_sources: tuple[PriceSource, ...] = DEFAULT_PRICE_SOURCES,
    execution_limit: int = 500,
    max_execution_pages: int | None = None,
    neutral_position: Decimal | str | None = Decimal("0"),
    output_html: Path | str | None = None,
    show: bool = True,
) -> go.Figure:
    """Build the bitFlyer FX net BTC position chart with direct function arguments."""
    load_dotenv()
    return asyncio.run(
        run(
            days=days,
            candle_minutes=candle_minutes,
            product_code=product_code,
            price_sources=price_sources,
            execution_limit=execution_limit,
            max_execution_pages=max_execution_pages,
            neutral_position=neutral_position,
            output_html=output_html,
            show=show,
        )
    )


async def _fetch_recent_executions(
    client: BitflyerPrivateClient,
    *,
    product_code: str,
    start: datetime,
    limit: int,
    max_pages: int | None,
) -> list[PrivateExecution]:
    rows_by_id: dict[int, PrivateExecution] = {}
    pages = 0
    before: int | None = None
    while max_pages is None or pages < max_pages:
        executions = await client.executions(
            product_code=product_code,
            count=limit,
            before=before,
        )
        if not executions:
            break
        pages += 1

        previous_count = len(rows_by_id)
        for execution in executions:
            rows_by_id[execution.id] = execution

        oldest = min(executions, key=_execution_time)
        oldest_id = min(execution.id for execution in executions)
        if len(executions) < limit or _execution_time(oldest) <= start:
            break
        if len(rows_by_id) == previous_count or oldest_id == before:
            break
        before = oldest_id
    return list(rows_by_id.values())


def _current_position(positions: list[Position]) -> Decimal:
    return sum(
        (_signed_size(position.side, position.size) for position in positions),
        Decimal("0"),
    )


def _position_fee_summary(positions: list[Position]) -> dict[str, object]:
    details: list[dict[str, object]] = []
    commission = Decimal("0")
    swap_point_accumulate = Decimal("0")
    sfd = Decimal("0")
    funding_fees = Decimal("0")
    for position in positions:
        commission += position.commission
        swap_point_accumulate += position.swap_point_accumulate
        sfd += position.sfd or Decimal("0")
        funding_fees += position.funding_fees or Decimal("0")
        details.append(
            {
                "side": position.side,
                "price": position.price,
                "size": position.size,
                "commission": position.commission,
                "swap_point_accumulate": position.swap_point_accumulate,
                "sfd": position.sfd,
                "funding_fees": position.funding_fees,
                "pnl": position.pnl,
                "open_date": position.open_date,
            }
        )
    return {
        "commission": commission,
        "swap_point_accumulate": swap_point_accumulate,
        "sfd": sfd,
        "funding_fees": funding_fees,
        "positions": details,
    }


def _position_delta(execution: PrivateExecution) -> Decimal:
    return _signed_size(execution.side, execution.size)


def _signed_size(side: str, size: Decimal) -> Decimal:
    if side == "BUY":
        return size
    if side == "SELL":
        return -size
    raise ValueError(f"unexpected bitFlyer side: {side!r}")


def _execution_time(execution: PrivateExecution) -> datetime:
    return datetime.fromisoformat(
        execution.exec_date.replace("Z", "+00:00")
    ).astimezone(JST)


def _execution_sort_key(execution: PrivateExecution) -> tuple[datetime, int]:
    return (_execution_time(execution), execution.id)


def _optional_decimal(value: Decimal | str | None) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


if __name__ == "__main__":
    main(days=5, candle_minutes=5)
