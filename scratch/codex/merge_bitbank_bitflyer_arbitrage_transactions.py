"""Merge bitbank maker fills with bitFlyer counter executions.

Input defaults to the newest TSV files produced by:

  scratch/codex/fetch_bitbank_btc_jpy_recent_transactions.py
  scratch/codex/fetch_bitflyer_fx_btc_jpy_recent_transactions.py

The matcher groups bitbank rows by order_id and bitFlyer rows by
child_order_acceptance_id, then FIFO-allocates opposite-side bitFlyer execution
size to each bitbank order. This permits one bitFlyer order to cover multiple
small bitbank fills, or one bitbank fill to be covered by multiple bitFlyer
executions.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
QUOTE_LOG_DIR = REPO_ROOT / "quote_log"
BITBANK_GLOB = "bitbank_btc_jpy_recent_transactions_*.tsv"
BITFLYER_GLOB = "bitflyer_fx_btc_jpy_recent_transactions_*.tsv"

# As of bitFlyer's public size table, BTC-CFD/JPY (API product
# FX_BTC_JPY) minimum order size is 0.001 BTC. bitbank BTC/JPY should be
# verified from /v1/spot/pairs for the account/market at run time if needed;
# keep both configurable because exchange rules can change.
DEFAULT_BITBANK_MIN_SIZE = Decimal("0.0001")
DEFAULT_BITFLYER_MIN_SIZE = Decimal("0.001")


@dataclass
class BitbankOrder:
    order_id: str
    pair: str = ""
    side: str = ""
    trade_ids: list[str] = field(default_factory=list)
    amount: Decimal = Decimal("0")
    notional_jpy: Decimal = Decimal("0")
    fee_amount_base: Decimal = Decimal("0")
    fee_amount_quote: Decimal = Decimal("0")
    first_ts_ms: int | None = None
    last_ts_ms: int | None = None
    maker_takers: set[str] = field(default_factory=set)
    types: set[str] = field(default_factory=set)
    position_sides: set[str] = field(default_factory=set)

    @property
    def vwap_price(self) -> Decimal | None:
        if self.amount == 0:
            return None
        return self.notional_jpy / self.amount


@dataclass
class BitflyerOrder:
    order_key: str
    product_code: str = "FX_BTC_JPY"
    side: str = ""
    execution_ids: list[str] = field(default_factory=list)
    child_order_ids: set[str] = field(default_factory=set)
    child_order_acceptance_id: str = ""
    size: Decimal = Decimal("0")
    remaining_size: Decimal = Decimal("0")
    notional_jpy: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    first_ts_ms: int | None = None
    last_ts_ms: int | None = None

    @property
    def vwap_price(self) -> Decimal | None:
        if self.size == 0:
            return None
        return self.notional_jpy / self.size


def parse_decimal(text: str | None, default: Decimal = Decimal("0")) -> Decimal:
    if text is None or text == "":
        return default
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid decimal {text!r}") from exc


def decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


def parse_bitbank_ts_ms(text: str) -> int:
    return int(text)


def parse_bitflyer_ts_ms(text: str) -> int:
    value = text.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def ts_ms_text(ts_ms: int | None) -> str:
    if ts_ms is None:
        return ""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def latest_file(directory: Path, pattern: str) -> Path:
    paths = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not paths:
        raise FileNotFoundError(f"no files matched {directory / pattern}")
    return paths[-1]


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as input_file:
        return list(csv.DictReader(input_file, delimiter="\t"))


def unique_join(values: Iterable[str]) -> str:
    return ",".join(sorted({value for value in values if value}))


def group_bitbank_orders(rows: Iterable[dict[str, str]]) -> list[BitbankOrder]:
    grouped: dict[str, BitbankOrder] = {}
    for row in rows:
        order_id = row["order_id"]
        order = grouped.setdefault(order_id, BitbankOrder(order_id=order_id))
        side = row["side"]
        if order.side and order.side != side:
            raise ValueError(f"bitbank order {order_id} has mixed sides")
        order.side = side
        order.pair = row.get("pair", order.pair)
        order.trade_ids.append(row["trade_id"])

        amount = parse_decimal(row["amount"])
        price = parse_decimal(row["price"])
        order.amount += amount
        order.notional_jpy += amount * price
        order.fee_amount_base += parse_decimal(row.get("fee_amount_base"))
        order.fee_amount_quote += parse_decimal(row.get("fee_amount_quote"))

        ts_ms = parse_bitbank_ts_ms(row["executed_at"])
        order.first_ts_ms = ts_ms if order.first_ts_ms is None else min(order.first_ts_ms, ts_ms)
        order.last_ts_ms = ts_ms if order.last_ts_ms is None else max(order.last_ts_ms, ts_ms)
        order.maker_takers.add(row.get("maker_taker", ""))
        order.types.add(row.get("type", ""))
        order.position_sides.add(row.get("position_side", ""))

    return sorted(
        grouped.values(),
        key=lambda order: (order.last_ts_ms or -1, order.first_ts_ms or -1, order.order_id),
    )


def group_bitflyer_orders(rows: Iterable[dict[str, str]]) -> list[BitflyerOrder]:
    grouped: dict[str, BitflyerOrder] = {}
    for row in rows:
        order_key = (
            row.get("child_order_acceptance_id")
            or row.get("child_order_id")
            or row["id"]
        )
        order = grouped.setdefault(order_key, BitflyerOrder(order_key=order_key))
        side = row["side"]
        if order.side and order.side != side:
            raise ValueError(f"bitFlyer order {order_key} has mixed sides")
        order.side = side
        order.child_order_acceptance_id = row.get("child_order_acceptance_id", "")
        if row.get("child_order_id"):
            order.child_order_ids.add(row["child_order_id"])
        order.execution_ids.append(row["id"])

        size = parse_decimal(row["size"])
        price = parse_decimal(row["price"])
        order.size += size
        order.remaining_size += size
        order.notional_jpy += size * price
        order.commission += parse_decimal(row.get("commission"))

        ts_ms = parse_bitflyer_ts_ms(row["exec_date"])
        order.first_ts_ms = ts_ms if order.first_ts_ms is None else min(order.first_ts_ms, ts_ms)
        order.last_ts_ms = ts_ms if order.last_ts_ms is None else max(order.last_ts_ms, ts_ms)

    return sorted(
        grouped.values(),
        key=lambda order: (order.first_ts_ms or -1, order.last_ts_ms or -1, order.order_key),
    )


def counter_side(bitbank_side: str) -> str:
    if bitbank_side == "buy":
        return "SELL"
    if bitbank_side == "sell":
        return "BUY"
    raise ValueError(f"unexpected bitbank side: {bitbank_side!r}")


def gross_edge_jpy(bitbank_side: str, bitbank_vwap: Decimal, bitflyer_vwap: Decimal, size: Decimal) -> Decimal:
    if bitbank_side == "buy":
        return (bitflyer_vwap - bitbank_vwap) * size
    if bitbank_side == "sell":
        return (bitbank_vwap - bitflyer_vwap) * size
    raise ValueError(f"unexpected bitbank side: {bitbank_side!r}")


def build_output_rows(
    bitbank_orders: list[BitbankOrder],
    bitflyer_orders: list[BitflyerOrder],
    *,
    bitbank_min_size: Decimal,
    bitflyer_min_size: Decimal,
    max_counter_before_ms: int,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    allocation_id = 1
    bb_alignment_id = 1

    for bitbank_order in bitbank_orders:
        expected_side = counter_side(bitbank_order.side)
        remaining = bitbank_order.amount
        order_rows: list[dict[str, str]] = []

        while remaining > 0:
            bitflyer_order = next(
                (
                    order
                    for order in bitflyer_orders
                    if order.side == expected_side
                    and order.remaining_size > 0
                    and (
                        order.remaining_size < order.size
                        or bitbank_order.first_ts_ms is None
                        or order.first_ts_ms is None
                        or order.first_ts_ms >= bitbank_order.first_ts_ms - max_counter_before_ms
                    )
                ),
                None,
            )
            if bitflyer_order is None:
                status = (
                    "unmatched_residual_below_bitflyer_min"
                    if remaining < bitflyer_min_size
                    else "unmatched_residual"
                )
                order_rows.append(make_row(
                    allocation_id=allocation_id,
                    bb_alignment_id=bb_alignment_id,
                    status=status,
                    bitbank_order=bitbank_order,
                    bitflyer_order=None,
                    expected_side=expected_side,
                    allocated_size=Decimal("0"),
                    residual_size=remaining,
                    bitbank_min_size=bitbank_min_size,
                    bitflyer_min_size=bitflyer_min_size,
                ))
                allocation_id += 1
                break

            allocated_size = min(remaining, bitflyer_order.remaining_size)
            bitflyer_order.remaining_size -= allocated_size
            remaining -= allocated_size

            status = "matched"
            if remaining > 0:
                status = "partial_needs_more_bitflyer"
            elif bitflyer_order.remaining_size > 0:
                status = "matched_bitflyer_order_has_carryover"

            order_rows.append(make_row(
                allocation_id=allocation_id,
                bb_alignment_id=bb_alignment_id,
                status=status,
                bitbank_order=bitbank_order,
                bitflyer_order=bitflyer_order,
                expected_side=expected_side,
                allocated_size=allocated_size,
                residual_size=remaining,
                bitbank_min_size=bitbank_min_size,
                bitflyer_min_size=bitflyer_min_size,
            ))
            allocation_id += 1

        matched_size = bitbank_order.amount - remaining
        for row in order_rows:
            row["bb_matched_btc"] = decimal_text(matched_size)
            row["bb_unmatched_btc"] = decimal_text(remaining)
            if remaining == 0:
                row["bb_match_status"] = "matched"
            elif remaining < bitflyer_min_size:
                row["bb_match_status"] = "residual_below_bitflyer_min"
            else:
                row["bb_match_status"] = "unmatched"
            rows.append(row)
        bb_alignment_id += 1

    return rows


def make_row(
    *,
    allocation_id: int,
    bb_alignment_id: int,
    status: str,
    bitbank_order: BitbankOrder,
    bitflyer_order: BitflyerOrder | None,
    expected_side: str,
    allocated_size: Decimal,
    residual_size: Decimal,
    bitbank_min_size: Decimal,
    bitflyer_min_size: Decimal,
) -> dict[str, str]:
    bitbank_vwap = bitbank_order.vwap_price
    bitflyer_vwap = bitflyer_order.vwap_price if bitflyer_order else None
    delay_ms = (
        None
        if bitflyer_order is None
        or bitbank_order.last_ts_ms is None
        or bitflyer_order.first_ts_ms is None
        else bitflyer_order.first_ts_ms - bitbank_order.last_ts_ms
    )
    gross_edge = (
        gross_edge_jpy(bitbank_order.side, bitbank_vwap, bitflyer_vwap, allocated_size)
        if bitbank_vwap is not None and bitflyer_vwap is not None and allocated_size > 0
        else None
    )
    spread = (
        gross_edge / allocated_size
        if gross_edge is not None and allocated_size > 0
        else None
    )

    return {
        "allocation_id": str(allocation_id),
        "bb_alignment_id": str(bb_alignment_id),
        "allocation_status": status,
        "bb_match_status": "",
        "bb_order_id": bitbank_order.order_id,
        "bb_trade_ids": ",".join(bitbank_order.trade_ids),
        "bb_pair": bitbank_order.pair,
        "bb_side": bitbank_order.side,
        "expected_bf_side": expected_side,
        "bb_first_executed_at_utc": ts_ms_text(bitbank_order.first_ts_ms),
        "bb_last_executed_at_utc": ts_ms_text(bitbank_order.last_ts_ms),
        "bb_amount_btc": decimal_text(bitbank_order.amount),
        "bb_matched_btc": "",
        "bb_unmatched_btc": "",
        "bb_residual_after_allocation_btc": decimal_text(residual_size),
        "bb_vwap_jpy": decimal_text(bitbank_vwap),
        "bb_notional_jpy": decimal_text(bitbank_order.notional_jpy),
        "bb_fee_base_btc": decimal_text(bitbank_order.fee_amount_base),
        "bb_fee_quote_jpy": decimal_text(bitbank_order.fee_amount_quote),
        "bb_maker_taker": unique_join(bitbank_order.maker_takers),
        "bb_order_type": unique_join(bitbank_order.types),
        "bb_position_side": unique_join(bitbank_order.position_sides),
        "bf_order_key": bitflyer_order.order_key if bitflyer_order else "",
        "bf_execution_ids": ",".join(bitflyer_order.execution_ids) if bitflyer_order else "",
        "bf_child_order_ids": unique_join(bitflyer_order.child_order_ids) if bitflyer_order else "",
        "bf_child_order_acceptance_id": bitflyer_order.child_order_acceptance_id if bitflyer_order else "",
        "bf_product_code": bitflyer_order.product_code if bitflyer_order else "",
        "bf_side": bitflyer_order.side if bitflyer_order else "",
        "bf_first_exec_date_utc": ts_ms_text(bitflyer_order.first_ts_ms) if bitflyer_order else "",
        "bf_last_exec_date_utc": ts_ms_text(bitflyer_order.last_ts_ms) if bitflyer_order else "",
        "bf_order_size_btc": decimal_text(bitflyer_order.size) if bitflyer_order else "",
        "bf_order_remaining_after_allocation_btc": decimal_text(bitflyer_order.remaining_size) if bitflyer_order else "",
        "bf_vwap_jpy": decimal_text(bitflyer_vwap),
        "bf_notional_jpy": decimal_text(bitflyer_order.notional_jpy) if bitflyer_order else "",
        "bf_commission_btc": decimal_text(bitflyer_order.commission) if bitflyer_order else "",
        "allocated_btc": decimal_text(allocated_size),
        "counter_delay_ms": "" if delay_ms is None else str(delay_ms),
        "gross_edge_jpy": decimal_text(gross_edge),
        "spread_jpy_per_btc": decimal_text(spread),
        "bitbank_min_size_btc": decimal_text(bitbank_min_size),
        "bitflyer_min_size_btc": decimal_text(bitflyer_min_size),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align bitbank BTC/JPY maker fills with bitFlyer FX_BTC_JPY counter executions.",
    )
    parser.add_argument(
        "--bitbank-tsv",
        type=Path,
        default=None,
        help=f"bitbank TSV input; default: newest quote_log/{BITBANK_GLOB}",
    )
    parser.add_argument(
        "--bitflyer-tsv",
        type=Path,
        default=None,
        help=f"bitFlyer TSV input; default: newest quote_log/{BITFLYER_GLOB}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output TSV path; default: quote_log/merged_bitbank_bitflyer_arbitrage_<timestamp>.tsv",
    )
    parser.add_argument(
        "--bitbank-min-size",
        type=Decimal,
        default=DEFAULT_BITBANK_MIN_SIZE,
        help=f"bitbank minimum BTC order size used for diagnostics; default: {DEFAULT_BITBANK_MIN_SIZE}",
    )
    parser.add_argument(
        "--bitflyer-min-size",
        type=Decimal,
        default=DEFAULT_BITFLYER_MIN_SIZE,
        help=f"bitFlyer minimum BTC order size used for diagnostics; default: {DEFAULT_BITFLYER_MIN_SIZE}",
    )
    parser.add_argument(
        "--max-counter-before-ms",
        type=int,
        default=0,
        help="allow matching a fresh bitFlyer order this many ms before the bitbank fill; default: 0",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bitbank_path = args.bitbank_tsv or latest_file(QUOTE_LOG_DIR, BITBANK_GLOB)
    bitflyer_path = args.bitflyer_tsv or latest_file(QUOTE_LOG_DIR, BITFLYER_GLOB)

    bitbank_orders = group_bitbank_orders(read_tsv(bitbank_path))
    bitflyer_orders = group_bitflyer_orders(read_tsv(bitflyer_path))
    output_rows = build_output_rows(
        bitbank_orders,
        bitflyer_orders,
        bitbank_min_size=args.bitbank_min_size,
        bitflyer_min_size=args.bitflyer_min_size,
        max_counter_before_ms=args.max_counter_before_ms,
    )

    output_path = args.output
    if output_path is None:
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
        output_path = QUOTE_LOG_DIR / f"merged_bitbank_bitflyer_arbitrage_{stamp}.tsv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(output_rows[0].keys()) if output_rows else list(make_row(
        allocation_id=0,
        bb_alignment_id=0,
        status="",
        bitbank_order=BitbankOrder(order_id=""),
        bitflyer_order=None,
        expected_side="",
        allocated_size=Decimal("0"),
        residual_size=Decimal("0"),
        bitbank_min_size=args.bitbank_min_size,
        bitflyer_min_size=args.bitflyer_min_size,
    ).keys())
    with output_path.open("w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(output_rows)

    matched_orders = {
        row["bb_order_id"]
        for row in output_rows
        if row["bb_match_status"] == "matched"
    }
    residual_orders = {
        row["bb_order_id"]
        for row in output_rows
        if row["bb_match_status"] != "matched"
    }
    unused_bitflyer = sum(
        (order.remaining_size for order in bitflyer_orders),
        start=Decimal("0"),
    )

    print(f"bitbank input: {bitbank_path}")
    print(f"bitFlyer input: {bitflyer_path}")
    print(f"wrote {len(output_rows)} alignment rows -> {output_path}")
    print(f"bitbank orders: {len(bitbank_orders)} matched={len(matched_orders)} residual={len(residual_orders)}")
    print(f"unused bitFlyer counter size: {decimal_text(unused_bitflyer)} BTC")


if __name__ == "__main__":
    main()
