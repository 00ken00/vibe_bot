from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
from dotenv import load_dotenv

from vibe_bot.bitbank import PrivateClient as BitbankPrivateClient
from vibe_bot.bitbank.models import Trade as BitbankTrade
from vibe_bot.bitflyer import PrivateClient as BitflyerPrivateClient
from vibe_bot.bitflyer.models import PrivateExecution as BitflyerExecution

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_LOG_DIR = Path("logs/trades/bitbank_bitflyer_arbitrage")


@dataclass
class HedgeAttempt:
    bitbank_order_id: str
    bitbank_fill_time: datetime
    bitbank_notice_time: datetime
    bitflyer_side: str
    amount: Decimal
    expected_price: Decimal
    acceptance_id: str | None = None
    executions: dict[int, BitflyerExecution] = field(default_factory=dict)


@dataclass(frozen=True)
class SlippagePoint:
    bitbank_order_id: str
    acceptance_id: str
    bitbank_fill_time: datetime
    bitflyer_fill_time: datetime
    bitflyer_side: str
    amount: Decimal
    expected_price: Decimal
    actual_price: Decimal

    @property
    def elapsed_seconds(self) -> float:
        return (self.bitflyer_fill_time - self.bitbank_fill_time).total_seconds()

    @property
    def slippage_per_btc(self) -> Decimal:
        if self.bitflyer_side == "SELL":
            return self.expected_price - self.actual_price
        return self.actual_price - self.expected_price


async def fetch_points(
    events_path: Path | str,
    *,
    bitbank_pair: str = "btc_jpy",
    bitflyer_product_code: str = "FX_BTC_JPY",
) -> list[SlippagePoint]:
    path = Path(events_path)
    events = _load_events(path)
    maker_events = [event for event in events if event.get("event") == "maker_filled"]
    if not maker_events:
        raise RuntimeError(f"no maker_filled events found in {path}")

    start = _event_time(maker_events[0]) - timedelta(minutes=5)
    end = _event_time(maker_events[-1]) + timedelta(minutes=5)
    async with BitbankPrivateClient() as client:
        bitbank_trades = await _fetch_bitbank_trades(
            client,
            pair=bitbank_pair,
            start=start,
            end=end,
        )

    attempts = _parse_attempts(events, bitbank_trades)
    missing = [attempt for attempt in attempts if attempt.acceptance_id and not attempt.executions]
    if missing:
        async with BitflyerPrivateClient() as client:
            for attempt in missing:
                executions = await client.executions(
                    product_code=bitflyer_product_code,
                    child_order_acceptance_id=attempt.acceptance_id,
                )
                attempt.executions.update((execution.id, execution) for execution in executions)

    return [_point_from_attempt(attempt) for attempt in attempts if attempt.executions]


def build_figure(points: list[SlippagePoint]) -> go.Figure:
    if not points:
        raise RuntimeError("no completed bitFlyer hedge executions were found")

    fig = go.Figure()
    for side, color in (("SELL", "#2563eb"), ("BUY", "#dc2626")):
        side_points = [point for point in points if point.bitflyer_side == side]
        if not side_points:
            continue
        fig.add_trace(
            go.Scatter(
                x=[point.elapsed_seconds for point in side_points],
                y=[float(point.slippage_per_btc) for point in side_points],
                mode="markers",
                name=f"bitFlyer {side}",
                marker={"color": color, "size": 9, "opacity": 0.8},
                customdata=[
                    [
                        point.bitbank_fill_time.astimezone(JST).isoformat(),
                        point.bitflyer_fill_time.astimezone(JST).isoformat(),
                        point.bitbank_order_id,
                        point.acceptance_id,
                        str(point.amount),
                        str(point.expected_price),
                        str(point.actual_price),
                    ]
                    for point in side_points
                ],
                hovertemplate=(
                    "Elapsed: %{x:.3f} s<br>"
                    "Slippage: %{y:,.0f} JPY/BTC<br>"
                    "bitbank fill: %{customdata[0]}<br>"
                    "bitFlyer fill: %{customdata[1]}<br>"
                    "bitbank order: %{customdata[2]}<br>"
                    "bitFlyer acceptance: %{customdata[3]}<br>"
                    "Amount: %{customdata[4]} BTC<br>"
                    "Expected: %{customdata[5]} JPY/BTC<br>"
                    "Actual: %{customdata[6]} JPY/BTC"
                    "<extra></extra>"
                ),
            )
        )
    fig.add_hline(y=0, line={"color": "#98a2b3", "width": 1, "dash": "dot"})
    fig.update_layout(
        title="bitbank Fill to bitFlyer Hedge Completion vs bitFlyer Slippage",
        template="plotly_white",
        xaxis_title="Elapsed time from bitbank actual fill to bitFlyer actual hedge fill (seconds)",
        yaxis_title="bitFlyer adverse slippage (JPY/BTC)",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02},
        margin={"l": 80, "r": 24, "t": 90, "b": 72},
    )
    fig.add_annotation(
        text=(
            "Positive slippage is adverse. Negative slippage is favorable. "
            "For multi-execution hedges, elapsed time uses the last bitFlyer execution "
            "and slippage uses the size-weighted average execution price."
        ),
        xref="paper",
        yref="paper",
        x=0,
        y=-0.2,
        showarrow=False,
        font={"size": 12, "color": "#667085"},
    )
    return fig


async def run(
    events_path: Path | str | None = None,
    *,
    output_html: Path | str | None = None,
    show: bool = False,
) -> go.Figure:
    path = Path(events_path) if events_path else latest_events_path()
    points = await fetch_points(path)
    fig = build_figure(points)
    output_path = Path(output_html) if output_html else path.with_name(f"slippage-latency-{path.stem.removeprefix('events-')}.html")
    fig.write_html(output_path, include_plotlyjs="cdn")
    print(f"wrote: {output_path}")
    print(f"points: {len(points)}")
    if show:
        fig.show(renderer="browser")
    return fig


def latest_events_path(log_dir: Path | str = DEFAULT_LOG_DIR) -> Path:
    paths = sorted(Path(log_dir).glob("events-*.jsonl"))
    if not paths:
        raise RuntimeError(f"no events logs found in {log_dir}")
    return paths[-1]


def main(
    events_path: Path | str | None = None,
    *,
    output_html: Path | str | None = None,
    show: bool = False,
) -> go.Figure:
    load_dotenv(".env")
    return asyncio.run(run(events_path, output_html=output_html, show=show))


async def _fetch_bitbank_trades(
    client: BitbankPrivateClient,
    *,
    pair: str,
    start: datetime,
    end: datetime,
    limit: int = 1000,
) -> list[BitbankTrade]:
    rows_by_id: dict[int, BitbankTrade] = {}
    start_ms = int(start.timestamp() * 1000)
    page_end_ms = int(end.timestamp() * 1000)
    while True:
        payload = await client.trade_history(
            pair=pair,
            count=limit,
            since=start_ms,
            end=page_end_ms,
            order="desc",
        )
        trades = payload.trades
        if not trades:
            break
        for trade in trades:
            rows_by_id[trade.trade_id] = trade
        oldest_ms = min(trade.executed_at for trade in trades)
        if len(trades) < limit or oldest_ms <= start_ms:
            break
        page_end_ms = oldest_ms
    return list(rows_by_id.values())


def _parse_attempts(
    events: list[dict[str, object]],
    bitbank_trades: list[BitbankTrade],
) -> list[HedgeAttempt]:
    by_order: dict[str, list[BitbankTrade]] = defaultdict(list)
    for trade in bitbank_trades:
        by_order[str(trade.order_id)].append(trade)
    for trades in by_order.values():
        trades.sort(key=lambda trade: trade.executed_at)

    cumulative_by_order: dict[str, Decimal] = defaultdict(Decimal)
    attempts: list[HedgeAttempt] = []
    attempts_by_acceptance_id: dict[str, HedgeAttempt] = {}
    last_fill: tuple[str, datetime, datetime, Decimal] | None = None
    current_attempt: HedgeAttempt | None = None

    for event in events:
        event_type = event.get("event")
        if event_type == "maker_filled":
            order_id = str(event["bitbank_order_id"])
            cumulative_by_order[order_id] += _decimal(event["fill_amount"])
            native_time = _bitbank_fill_time(
                by_order.get(order_id, []),
                cumulative_by_order[order_id],
            )
            maker = event.get("maker")
            if native_time is not None and isinstance(maker, dict):
                last_fill = (
                    order_id,
                    native_time,
                    _event_time(event),
                    _decimal(maker["expected_hedge_price"]),
                )
        elif event_type == "bitflyer_hedge_attempt" and last_fill is not None:
            order_id, native_time, notice_time, expected_price = last_fill
            current_attempt = HedgeAttempt(
                bitbank_order_id=order_id,
                bitbank_fill_time=native_time,
                bitbank_notice_time=notice_time,
                bitflyer_side=str(event["side"]),
                amount=_decimal(event["amount"]),
                expected_price=expected_price,
            )
            attempts.append(current_attempt)
        elif event_type == "private_api_trace" and event.get("exchange") == "bitflyer":
            raw = _raw_response(event)
            if event.get("path") == "/v1/me/sendchildorder" and current_attempt is not None:
                if isinstance(raw, dict) and raw.get("child_order_acceptance_id"):
                    acceptance_id = str(raw["child_order_acceptance_id"])
                    current_attempt.acceptance_id = acceptance_id
                    attempts_by_acceptance_id[acceptance_id] = current_attempt
            elif event.get("path") == "/v1/me/getexecutions" and isinstance(raw, list):
                for row in raw:
                    if not isinstance(row, dict):
                        continue
                    acceptance_id = str(row.get("child_order_acceptance_id") or "")
                    attempt = attempts_by_acceptance_id.get(acceptance_id)
                    if attempt is not None:
                        execution = BitflyerExecution.model_validate(row)
                        attempt.executions[execution.id] = execution
    return attempts


def _bitbank_fill_time(
    trades: list[BitbankTrade],
    cumulative_amount: Decimal,
) -> datetime | None:
    running = Decimal("0")
    for trade in trades:
        running += trade.amount
        if running >= cumulative_amount:
            return datetime.fromtimestamp(trade.executed_at / 1000, tz=timezone.utc)
    return None


def _point_from_attempt(attempt: HedgeAttempt) -> SlippagePoint:
    executions = list(attempt.executions.values())
    amount = sum((execution.size for execution in executions), Decimal("0"))
    actual_price = sum(
        (execution.price * execution.size for execution in executions),
        Decimal("0"),
    ) / amount
    return SlippagePoint(
        bitbank_order_id=attempt.bitbank_order_id,
        acceptance_id=attempt.acceptance_id or "",
        bitbank_fill_time=attempt.bitbank_fill_time,
        bitflyer_fill_time=max(_bitflyer_execution_time(execution) for execution in executions),
        bitflyer_side=attempt.bitflyer_side,
        amount=amount,
        expected_price=attempt.expected_price,
        actual_price=actual_price,
    )


def _load_events(path: Path) -> list[dict[str, object]]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _raw_response(event: dict[str, object]) -> object:
    raw = event.get("raw_response")
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _event_time(event: dict[str, object]) -> datetime:
    return datetime.fromisoformat(str(event["timestamp"]))


def _bitflyer_execution_time(execution: BitflyerExecution) -> datetime:
    parsed = datetime.fromisoformat(execution.exec_date)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def cli() -> go.Figure:
    parser = argparse.ArgumentParser(
        description="Plot bitbank fill-to-bitFlyer hedge latency against bitFlyer slippage."
    )
    parser.add_argument("--events", help="Events JSONL path. Defaults to the latest log.")
    parser.add_argument("--output-html", help="Output HTML path.")
    parser.add_argument("--show", action="store_true", help="Open the chart in a browser.")
    args = parser.parse_args()
    return main(args.events, output_html=args.output_html, show=args.show)


if __name__ == "__main__":
    cli()
