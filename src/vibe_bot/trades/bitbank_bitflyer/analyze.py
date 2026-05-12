from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path


DEFAULT_LOG_DIR = Path("logs/trades/bitbank_bitflyer_arbitrage")


@dataclass(frozen=True)
class TradeFill:
    source: Path
    timestamp: datetime
    action: str
    order_id: str
    bitbank_side: str
    bitbank_price: Decimal
    amount: Decimal
    bitflyer_side: str
    bitflyer_expected_price: Decimal | None
    bitflyer_average_price: Decimal | None
    raw_slippage_jpy: Decimal | None
    cashflow_jpy: Decimal
    position: Decimal
    bitbank_position: Decimal
    bitflyer_position: Decimal
    unhedged_position: Decimal
    realized_pnl_jpy: Decimal
    bitbank_fill_pnl_jpy: Decimal
    bitbank_realized_pnl_jpy: Decimal
    bitbank_open_cost_jpy: Decimal
    bitbank_cost_basis_ready: bool
    bitflyer_fill_pnl_jpy: Decimal
    bitflyer_realized_pnl_jpy: Decimal
    bitflyer_open_cost_jpy: Decimal
    bitflyer_cost_basis_ready: bool
    hedge_executed: bool

    @property
    def expected_edge_per_btc(self) -> Decimal | None:
        if self.bitflyer_expected_price is None:
            return None
        if self.action == "BUY":
            return self.bitflyer_expected_price - self.bitbank_price
        return self.bitbank_price - self.bitflyer_expected_price

    @property
    def actual_edge_per_btc(self) -> Decimal | None:
        if self.bitflyer_average_price is None:
            return None
        if self.action == "BUY":
            return self.bitflyer_average_price - self.bitbank_price
        return self.bitbank_price - self.bitflyer_average_price

    @property
    def expected_cashflow_jpy(self) -> Decimal | None:
        edge = self.expected_edge_per_btc
        return None if edge is None else edge * self.amount

    @property
    def actual_cashflow_from_prices_jpy(self) -> Decimal | None:
        edge = self.actual_edge_per_btc
        return None if edge is None else edge * self.amount

    @property
    def hedge_slippage_impact_jpy(self) -> Decimal | None:
        expected = self.expected_cashflow_jpy
        actual = self.actual_cashflow_from_prices_jpy
        if expected is None or actual is None:
            return None
        return actual - expected


@dataclass(frozen=True)
class MakerLifecycle:
    order_id: str
    action: str
    side: str
    price: Decimal
    amount: Decimal
    trigger_price: Decimal
    expected_hedge_price: Decimal
    stage_index: int
    placed_at_iso: datetime | None
    placed_at_monotonic: Decimal | None


@dataclass
class AnalysisResult:
    trades: list[TradeFill]
    makers: dict[str, MakerLifecycle]
    event_counts: Counter[str]
    hedge_deferred: list[dict[str, object]]
    errors: list[dict[str, object]]
    bitbank_margin_interest_samples: list[Decimal]


def main(log_dir: Path | str = DEFAULT_LOG_DIR) -> AnalysisResult:
    """Analyze downloaded bitbank/bitFlyer arbitrage logs.

    IPython usage:
        from vibe_bot.trades.bitbank_bitflyer.analyze import main
        result = main()
        result = main("logs/trades/bitbank_bitflyer_arbitrage")

    The printed report focuses on realized spread, expected spread, hedge
    slippage, partial/unhedged fills, cost-basis reliability, and lifecycle
    counts. The returned object keeps parsed rows for deeper ad hoc analysis.
    """
    log_path = Path(log_dir)
    result = AnalysisResult(
        trades=load_trades(log_path),
        makers={},
        event_counts=Counter(),
        hedge_deferred=[],
        errors=[],
        bitbank_margin_interest_samples=[],
    )
    parse_events(log_path, result)
    print_report(result)
    return result


def load_trades(log_dir: Path) -> list[TradeFill]:
    trades = []
    for path in sorted(log_dir.glob("trades-*.csv")):
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                trades.append(_trade_from_row(path, row))
    return trades


def parse_events(log_dir: Path, result: AnalysisResult) -> None:
    for path in sorted(log_dir.glob("events-*.jsonl")):
        with path.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    result.event_counts["json_decode_error"] += 1
                    continue
                event_type = str(event.get("event") or "")
                result.event_counts[event_type] += 1
                if event_type == "maker_placed":
                    maker = event.get("maker")
                    if isinstance(maker, dict) and maker.get("order_id") is not None:
                        lifecycle = _maker_from_event(event, maker)
                        result.makers[lifecycle.order_id] = lifecycle
                elif event_type == "bitflyer_hedge_deferred":
                    result.hedge_deferred.append(event)
                elif event_type in {"error", "maker_cancel_failed"}:
                    result.errors.append(event)
                elif event_type == "private_api_trace":
                    _collect_private_api_sample(event, result)


def print_report(result: AnalysisResult) -> None:
    trades = result.trades
    print("=== bitbank/bitFlyer arbitrage log analysis ===")
    print(f"trade rows: {len(trades)}")
    if not trades:
        return

    first = min(t.timestamp for t in trades)
    last = max(t.timestamp for t in trades)
    print(f"time range: {first.isoformat()} -> {last.isoformat()}")
    print(f"event counts: {_top_counts(result.event_counts, 12)}")
    print()

    _print_pnl_summary(trades)
    _print_bitflyer_pnl_sign_diagnostic(trades)
    _print_edge_summary(trades)
    _print_action_summary(trades)
    _print_trigger_summary(result)
    _print_cost_basis_summary(trades)
    _print_fee_funding_summary(result)
    _print_unhedged_summary(result)
    _print_lifecycle_summary(result)
    _print_worst_rows(trades)


def _print_pnl_summary(trades: list[TradeFill]) -> None:
    last = trades[-1]
    total_cashflow = sum((t.cashflow_jpy for t in trades), Decimal("0"))
    final_component_pnl = last.bitbank_realized_pnl_jpy + last.bitflyer_realized_pnl_jpy
    print("PnL")
    print(f"  sum cashflow_jpy: {fmt(total_cashflow)} JPY")
    print(f"  final realized_pnl_jpy column: {fmt(last.realized_pnl_jpy)} JPY")
    print(f"  final bitbank_realized_pnl_jpy: {fmt(last.bitbank_realized_pnl_jpy)} JPY")
    print(f"  final bitflyer_realized_pnl_jpy: {fmt(last.bitflyer_realized_pnl_jpy)} JPY")
    print(f"  final bitbank + bitFlyer component PnL: {fmt(final_component_pnl)} JPY")
    print(f"  final position: {fmt(last.position)} BTC")
    print()


def _print_bitflyer_pnl_sign_diagnostic(trades: list[TradeFill]) -> None:
    """Detect historical rows affected by the old bitFlyer long-close sign bug."""
    position_before = Decimal("0")
    affected_rows = []
    corrected_delta = Decimal("0")
    for trade in trades:
        if (
            trade.bitflyer_side == "SELL"
            and position_before < 0
            and trade.bitflyer_fill_pnl_jpy < 0
        ):
            affected_rows.append(trade)
            corrected_delta += -trade.bitflyer_fill_pnl_jpy * Decimal("2")
        position_before = trade.bitflyer_position

    if not affected_rows:
        return

    last = trades[-1]
    current_component_pnl = last.bitbank_realized_pnl_jpy + last.bitflyer_realized_pnl_jpy
    corrected_component_pnl = current_component_pnl + corrected_delta
    print("bitFlyer PnL sign diagnostic")
    print(
        "  affected rows: "
        f"{len(affected_rows)} bitFlyer SELL rows closing negative bitFlyer position"
    )
    print(f"  estimated correction to component PnL: +{fmt(corrected_delta)} JPY")
    print(
        "  component PnL after correction estimate: "
        f"{fmt(corrected_component_pnl)} JPY"
    )
    print(
        "  cashflow_jpy is not affected; this only affects bitflyer_realized_pnl_jpy"
    )
    print()


def _print_edge_summary(trades: list[TradeFill]) -> None:
    with_expected = [
        t
        for t in trades
        if t.hedge_executed
        and t.bitflyer_expected_price is not None
        and t.bitflyer_expected_price > 0
        and t.expected_cashflow_jpy is not None
    ]
    with_actual = [
        t for t in trades if t.hedge_executed and t.actual_cashflow_from_prices_jpy is not None
    ]
    slippage_rows = [t for t in trades if t.hedge_slippage_impact_jpy is not None]
    expected = sum((t.expected_cashflow_jpy for t in with_expected), Decimal("0"))
    actual = sum((t.actual_cashflow_from_prices_jpy for t in with_actual), Decimal("0"))
    slip = sum((t.hedge_slippage_impact_jpy for t in slippage_rows), Decimal("0"))
    total_amount = sum((t.amount for t in with_actual), Decimal("0"))
    print("Spread and hedge execution")
    print(f"  expected cashflow from maker-time hedge price: {fmt(expected)} JPY")
    print(f"  actual cashflow from bitbank fill + bitFlyer execution: {fmt(actual)} JPY")
    print(f"  hedge execution impact vs expectation: {fmt(slip)} JPY")
    if total_amount:
        print(f"  average actual edge: {fmt(actual / total_amount)} JPY/BTC")
        print(f"  average hedge impact: {fmt(slip / total_amount)} JPY/BTC")
    print(f"  hedge-executed rows with bitFlyer execution price: {len(with_actual)}")
    print(f"  rows without bitFlyer execution price: {len(trades) - len(with_actual)}")
    print()


def _print_action_summary(trades: list[TradeFill]) -> None:
    print("By action")
    for action in ("BUY", "SELL"):
        rows = [t for t in trades if t.action == action]
        if not rows:
            continue
        amount = sum((t.amount for t in rows), Decimal("0"))
        cash = sum((t.cashflow_jpy for t in rows), Decimal("0"))
        actual_edges = [t.actual_edge_per_btc for t in rows if t.actual_edge_per_btc is not None]
        min_edge = min(actual_edges) if actual_edges else None
        max_edge = max(actual_edges) if actual_edges else None
        slip = sum(
            (t.hedge_slippage_impact_jpy for t in rows if t.hedge_slippage_impact_jpy is not None),
            Decimal("0"),
        )
        print(
            f"  {action}: rows={len(rows)}, amount={fmt(amount)} BTC, "
            f"cashflow={fmt(cash)} JPY, hedge_impact={fmt(slip)} JPY, "
            f"actual_edge_min/max={fmt(min_edge)}/{fmt(max_edge)} JPY/BTC"
        )
    print()


def _print_trigger_summary(result: AnalysisResult) -> None:
    rows_by_trigger: dict[tuple[str, Decimal | None], list[TradeFill]] = defaultdict(list)
    for trade in result.trades:
        maker = result.makers.get(trade.order_id)
        rows_by_trigger[(trade.action, maker.trigger_price if maker else None)].append(trade)

    print("By maker trigger")
    for (action, trigger), rows in sorted(
        rows_by_trigger.items(),
        key=lambda item: (item[0][0], item[0][1] is None, item[0][1] or Decimal("0")),
    ):
        amount = sum((t.amount for t in rows), Decimal("0"))
        cash = sum((t.cashflow_jpy for t in rows), Decimal("0"))
        expected = sum(
            (
                t.expected_cashflow_jpy
                for t in rows
                if t.hedge_executed and t.expected_cashflow_jpy is not None
            ),
            Decimal("0"),
        )
        actual_rows = [t for t in rows if t.actual_edge_per_btc is not None]
        actual_amount = sum((t.amount for t in actual_rows), Decimal("0"))
        avg_edge = (
            sum(
                ((t.actual_edge_per_btc or Decimal("0")) * t.amount for t in actual_rows),
                Decimal("0"),
            )
            / actual_amount
            if actual_amount
            else None
        )
        print(
            f"  {action} trigger={fmt(trigger)}: rows={len(rows)}, amount={fmt(amount)} BTC, "
            f"expected={fmt(expected)} JPY, cashflow={fmt(cash)} JPY, "
            f"avg_actual_edge={fmt(avg_edge)} JPY/BTC"
        )
    print()


def _print_cost_basis_summary(trades: list[TradeFill]) -> None:
    bitbank_not_ready = [t for t in trades if not t.bitbank_cost_basis_ready]
    bitflyer_not_ready = [t for t in trades if not t.bitflyer_cost_basis_ready]
    print("Cost-basis reliability")
    print(f"  bitbank_cost_basis_ready=False rows: {len(bitbank_not_ready)}")
    print(f"  bitflyer_cost_basis_ready=False rows: {len(bitflyer_not_ready)}")
    if bitbank_not_ready or bitflyer_not_ready:
        first = min((t.timestamp for t in bitbank_not_ready + bitflyer_not_ready))
        last = max((t.timestamp for t in bitbank_not_ready + bitflyer_not_ready))
        print(f"  unreliable cost-basis range: {first.isoformat()} -> {last.isoformat()}")
    print()


def _print_fee_funding_summary(result: AnalysisResult) -> None:
    print("Fees / funding visible in logs")
    if result.bitbank_margin_interest_samples:
        print(
            "  bitbank margin unrealized interest samples: "
            f"count={len(result.bitbank_margin_interest_samples)}, "
            f"max={fmt(max(result.bitbank_margin_interest_samples))} JPY"
        )
    else:
        print("  no bitbank margin interest samples found")
    print("  trade CSV cashflow does not subtract exchange fees, margin interest, or funding-like charges")
    print()


def _print_unhedged_summary(result: AnalysisResult) -> None:
    trades = result.trades
    hedge_false = [t for t in trades if not t.hedge_executed]
    max_abs_unhedged = max((abs(t.unhedged_position) for t in trades), default=Decimal("0"))
    print("Unhedged / deferred hedge")
    print(f"  hedge_executed=False trade rows: {len(hedge_false)}")
    print(f"  bitflyer_hedge_deferred events: {len(result.hedge_deferred)}")
    print(f"  max abs unhedged_position in trade rows: {fmt(max_abs_unhedged)} BTC")
    reasons = Counter(str(e.get("reason")) for e in result.hedge_deferred)
    if reasons:
        print(f"  deferred reasons: {_top_counts(reasons, 8)}")
    print()


def _print_lifecycle_summary(result: AnalysisResult) -> None:
    counts = result.event_counts
    filled = counts.get("maker_filled", 0)
    placed = counts.get("maker_placed", 0)
    canceled = counts.get("maker_canceled", 0)
    done = counts.get("maker_done", 0)
    print("Maker lifecycle")
    print(f"  placed={placed}, filled_events={filled}, canceled={canceled}, maker_done={done}")
    if placed:
        print(f"  fill event rate per placed maker: {Decimal(filled) / Decimal(placed):.4f}")
    print(f"  errors tracked: {len(result.errors)}")
    print()


def _print_worst_rows(trades: list[TradeFill], limit: int = 8) -> None:
    print("Worst trade rows by actual cashflow")
    scored = sorted(trades, key=lambda t: t.cashflow_jpy)
    for t in scored[:limit]:
        print(
            f"  {t.timestamp.isoformat()} {t.action} amount={fmt(t.amount)} "
            f"bb={fmt(t.bitbank_price)} bf_avg={fmt(t.bitflyer_average_price)} "
            f"edge={fmt(t.actual_edge_per_btc)} cashflow={fmt(t.cashflow_jpy)} "
            f"hedge_impact={fmt(t.hedge_slippage_impact_jpy)} order={t.order_id}"
        )
    print()


def _collect_private_api_sample(event: dict[str, object], result: AnalysisResult) -> None:
    if event.get("exchange") != "bitbank":
        return
    url = str(event.get("url") or "")
    if "/user/margin/positions" not in url:
        return
    raw_response = event.get("raw_response")
    if not isinstance(raw_response, str):
        return
    try:
        payload = json.loads(raw_response)
    except json.JSONDecodeError:
        return
    positions = payload.get("data", {}).get("positions", [])
    if not isinstance(positions, list):
        return
    total_interest = Decimal("0")
    for position in positions:
        if not isinstance(position, dict) or position.get("pair") != "btc_jpy":
            continue
        total_interest += dec(position.get("unrealized_interest_amount")) or Decimal("0")
    result.bitbank_margin_interest_samples.append(total_interest)


def _trade_from_row(path: Path, row: dict[str, str]) -> TradeFill:
    return TradeFill(
        source=path,
        timestamp=datetime.fromisoformat(row["timestamp"]),
        action=row["action"],
        order_id=row["bitbank_order_id"],
        bitbank_side=row["bitbank_side"],
        bitbank_price=dec(row["bitbank_price"]) or Decimal("0"),
        amount=dec(row["bitbank_amount"]) or Decimal("0"),
        bitflyer_side=row["bitflyer_side"],
        bitflyer_expected_price=dec(row["bitflyer_expected_price"]),
        bitflyer_average_price=dec(row["bitflyer_average_price"]),
        raw_slippage_jpy=dec(row["slippage_jpy"]),
        cashflow_jpy=dec(row["cashflow_jpy"]) or Decimal("0"),
        position=dec(row["position"]) or Decimal("0"),
        bitbank_position=dec(row["bitbank_position"]) or Decimal("0"),
        bitflyer_position=dec(row["bitflyer_position"]) or Decimal("0"),
        unhedged_position=dec(row["unhedged_position"]) or Decimal("0"),
        realized_pnl_jpy=dec(row["realized_pnl_jpy"]) or Decimal("0"),
        bitbank_fill_pnl_jpy=dec(row["bitbank_fill_pnl_jpy"]) or Decimal("0"),
        bitbank_realized_pnl_jpy=dec(row["bitbank_realized_pnl_jpy"]) or Decimal("0"),
        bitbank_open_cost_jpy=dec(row["bitbank_open_cost_jpy"]) or Decimal("0"),
        bitbank_cost_basis_ready=parse_bool(row["bitbank_cost_basis_ready"]),
        bitflyer_fill_pnl_jpy=dec(row["bitflyer_fill_pnl_jpy"]) or Decimal("0"),
        bitflyer_realized_pnl_jpy=dec(row["bitflyer_realized_pnl_jpy"]) or Decimal("0"),
        bitflyer_open_cost_jpy=dec(row["bitflyer_open_cost_jpy"]) or Decimal("0"),
        bitflyer_cost_basis_ready=parse_bool(row["bitflyer_cost_basis_ready"]),
        hedge_executed=parse_bool(row["hedge_executed"]),
    )


def _maker_from_event(event: dict[str, object], maker: dict[str, object]) -> MakerLifecycle:
    return MakerLifecycle(
        order_id=str(maker["order_id"]),
        action=str(maker.get("action") or ""),
        side=str(maker.get("side") or ""),
        price=dec(maker.get("price")) or Decimal("0"),
        amount=dec(maker.get("amount")) or Decimal("0"),
        trigger_price=dec(maker.get("trigger_price")) or Decimal("0"),
        expected_hedge_price=dec(maker.get("expected_hedge_price")) or Decimal("0"),
        stage_index=int(maker.get("stage_index") or 0),
        placed_at_iso=(
            datetime.fromisoformat(str(event["timestamp"]))
            if event.get("timestamp")
            else None
        ),
        placed_at_monotonic=dec(maker.get("placed_at")),
    )


def dec(value: object) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def fmt(value: object) -> str:
    if value is None:
        return "--"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def _top_counts(counter: Counter[str], limit: int) -> str:
    return ", ".join(f"{key}={value}" for key, value in counter.most_common(limit))


if __name__ == "__main__":
    main()
