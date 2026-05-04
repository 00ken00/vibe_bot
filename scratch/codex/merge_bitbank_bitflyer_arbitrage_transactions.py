"""Merge bitbank maker fills with bitFlyer counter executions.

Input defaults to the newest TSV files produced by:

  scratch/codex/fetch_bitbank_btc_jpy_recent_transactions.py
  scratch/codex/fetch_bitflyer_fx_btc_jpy_recent_transactions.py

The matcher groups raw transactions into exchange-side orders, then builds
closed merged orders. Each matched output row consumes bitbank and bitFlyer
quantity in equal BTC size, so the net BTC position sum after that merged row is
zero. Prices are VWAPs over the allocated transactions.
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
TRANSACTIONS_DIR = REPO_ROOT / "quote_log" / "transactions"
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
    remaining_amount: Decimal = Decimal("0")
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


@dataclass
class MergedOrder:
    merge_id: int
    bitbank_side: str
    bitflyer_side: str
    bitbank_orders: list[str] = field(default_factory=list)
    bitbank_trade_ids: list[str] = field(default_factory=list)
    bitflyer_order_keys: list[str] = field(default_factory=list)
    bitflyer_execution_ids: list[str] = field(default_factory=list)
    bitflyer_child_order_ids: set[str] = field(default_factory=set)
    bitflyer_child_order_acceptance_ids: set[str] = field(default_factory=set)
    bitbank_amount: Decimal = Decimal("0")
    bitflyer_size: Decimal = Decimal("0")
    bitbank_notional_jpy: Decimal = Decimal("0")
    bitflyer_notional_jpy: Decimal = Decimal("0")
    bitbank_fee_base: Decimal = Decimal("0")
    bitbank_fee_quote: Decimal = Decimal("0")
    bitflyer_commission: Decimal = Decimal("0")
    first_bitbank_ts_ms: int | None = None
    last_bitbank_ts_ms: int | None = None
    first_bitflyer_ts_ms: int | None = None
    last_bitflyer_ts_ms: int | None = None
    bitbank_maker_takers: set[str] = field(default_factory=set)
    bitbank_order_types: set[str] = field(default_factory=set)
    bitbank_position_sides: set[str] = field(default_factory=set)
    bitbank_pair: str = ""
    bitflyer_product_code: str = ""

    @property
    def bitbank_vwap(self) -> Decimal | None:
        if self.bitbank_amount == 0:
            return None
        return self.bitbank_notional_jpy / self.bitbank_amount

    @property
    def bitflyer_vwap(self) -> Decimal | None:
        if self.bitflyer_size == 0:
            return None
        return self.bitflyer_notional_jpy / self.bitflyer_size

    @property
    def net_position_sum(self) -> Decimal:
        return signed_bitbank_delta(self.bitbank_side, self.bitbank_amount) + signed_bitflyer_delta(
            self.bitflyer_side,
            self.bitflyer_size,
        )


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
    if value == 0:
        return "0"
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


def append_unique(values: list[str], new_values: Iterable[str]) -> None:
    seen = set(values)
    for value in new_values:
        if value and value not in seen:
            values.append(value)
            seen.add(value)


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
        order.remaining_amount += amount
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


def signed_bitbank_delta(side: str, amount: Decimal) -> Decimal:
    if side == "buy":
        return amount
    if side == "sell":
        return -amount
    if not side and amount == 0:
        return Decimal("0")
    raise ValueError(f"unexpected bitbank side: {side!r}")


def signed_bitflyer_delta(side: str, size: Decimal) -> Decimal:
    if side == "BUY":
        return size
    if side == "SELL":
        return -size
    if not side and size == 0:
        return Decimal("0")
    raise ValueError(f"unexpected bitFlyer side: {side!r}")


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
    merge_id = 1
    bitbank_index = 0

    while bitbank_index < len(bitbank_orders):
        bitbank_order = bitbank_orders[bitbank_index]
        if bitbank_order.remaining_amount <= 0:
            bitbank_index += 1
            continue

        expected_side = counter_side(bitbank_order.side)
        bitflyer_order = find_next_bitflyer_order(
            bitflyer_orders,
            expected_side=expected_side,
            min_first_ts_ms=(
                None
                if bitbank_order.first_ts_ms is None
                else bitbank_order.first_ts_ms - max_counter_before_ms
            ),
        )
        group = MergedOrder(
            merge_id=merge_id,
            bitbank_side=bitbank_order.side,
            bitflyer_side=expected_side,
        )

        if bitflyer_order is None:
            add_bitbank_portion(group, bitbank_order, bitbank_order.remaining_amount)
            bitbank_order.remaining_amount = Decimal("0")
            rows.append(make_merged_row(
                group,
                status=unmatched_status(group.bitbank_amount, bitflyer_min_size),
                bitbank_min_size=bitbank_min_size,
                bitflyer_min_size=bitflyer_min_size,
            ))
            merge_id += 1
            bitbank_index += 1
            continue

        while bitflyer_order.remaining_size > 0:
            if bitbank_index >= len(bitbank_orders):
                break

            bitbank_order = bitbank_orders[bitbank_index]
            if bitbank_order.remaining_amount <= 0:
                bitbank_index += 1
                continue
            if bitbank_order.side != group.bitbank_side:
                break

            allocated_size = min(bitbank_order.remaining_amount, bitflyer_order.remaining_size)
            add_bitbank_portion(group, bitbank_order, allocated_size)
            add_bitflyer_portion(group, bitflyer_order, allocated_size)
            bitbank_order.remaining_amount -= allocated_size
            bitflyer_order.remaining_size -= allocated_size

            if bitbank_order.remaining_amount <= 0:
                bitbank_index += 1

        status = "matched_zero_position" if group.net_position_sum == 0 else "unmatched_nonzero_position"
        rows.append(make_merged_row(
            group,
            status=status,
            bitbank_min_size=bitbank_min_size,
            bitflyer_min_size=bitflyer_min_size,
        ))
        merge_id += 1

    return rows


def find_next_bitflyer_order(
    bitflyer_orders: list[BitflyerOrder],
    *,
    expected_side: str,
    min_first_ts_ms: int | None,
) -> BitflyerOrder | None:
    for order in bitflyer_orders:
        if order.side != expected_side or order.remaining_size <= 0:
            continue
        if order.remaining_size < order.size:
            return order
        if min_first_ts_ms is None or order.first_ts_ms is None or order.first_ts_ms >= min_first_ts_ms:
            return order
    return None


def add_bitbank_portion(group: MergedOrder, order: BitbankOrder, amount: Decimal) -> None:
    if amount <= 0:
        return
    ratio = amount / order.amount
    append_unique(group.bitbank_orders, [order.order_id])
    append_unique(group.bitbank_trade_ids, order.trade_ids)
    group.bitbank_pair = order.pair
    group.bitbank_amount += amount
    group.bitbank_notional_jpy += (order.vwap_price or Decimal("0")) * amount
    group.bitbank_fee_base += order.fee_amount_base * ratio
    group.bitbank_fee_quote += order.fee_amount_quote * ratio
    group.first_bitbank_ts_ms = (
        order.first_ts_ms
        if group.first_bitbank_ts_ms is None
        else min(group.first_bitbank_ts_ms, order.first_ts_ms or group.first_bitbank_ts_ms)
    )
    group.last_bitbank_ts_ms = (
        order.last_ts_ms
        if group.last_bitbank_ts_ms is None
        else max(group.last_bitbank_ts_ms, order.last_ts_ms or group.last_bitbank_ts_ms)
    )
    group.bitbank_maker_takers.update(order.maker_takers)
    group.bitbank_order_types.update(order.types)
    group.bitbank_position_sides.update(order.position_sides)


def add_bitflyer_portion(group: MergedOrder, order: BitflyerOrder, size: Decimal) -> None:
    if size <= 0:
        return
    ratio = size / order.size
    append_unique(group.bitflyer_order_keys, [order.order_key])
    append_unique(group.bitflyer_execution_ids, order.execution_ids)
    group.bitflyer_child_order_ids.update(order.child_order_ids)
    if order.child_order_acceptance_id:
        group.bitflyer_child_order_acceptance_ids.add(order.child_order_acceptance_id)
    group.bitflyer_product_code = order.product_code
    group.bitflyer_size += size
    group.bitflyer_notional_jpy += (order.vwap_price or Decimal("0")) * size
    group.bitflyer_commission += order.commission * ratio
    group.first_bitflyer_ts_ms = (
        order.first_ts_ms
        if group.first_bitflyer_ts_ms is None
        else min(group.first_bitflyer_ts_ms, order.first_ts_ms or group.first_bitflyer_ts_ms)
    )
    group.last_bitflyer_ts_ms = (
        order.last_ts_ms
        if group.last_bitflyer_ts_ms is None
        else max(group.last_bitflyer_ts_ms, order.last_ts_ms or group.last_bitflyer_ts_ms)
    )


def unmatched_status(amount: Decimal, bitflyer_min_size: Decimal) -> str:
    if amount < bitflyer_min_size:
        return "unmatched_bitbank_residual_below_bitflyer_min"
    return "unmatched_bitbank_residual"


def make_merged_row(
    group: MergedOrder,
    *,
    status: str,
    bitbank_min_size: Decimal,
    bitflyer_min_size: Decimal,
) -> dict[str, str]:
    bitbank_vwap = group.bitbank_vwap
    bitflyer_vwap = group.bitflyer_vwap
    delay_ms = (
        None
        if group.last_bitbank_ts_ms is None
        or group.first_bitflyer_ts_ms is None
        else group.first_bitflyer_ts_ms - group.last_bitbank_ts_ms
    )
    gross_edge = (
        gross_edge_jpy(group.bitbank_side, bitbank_vwap, bitflyer_vwap, group.bitbank_amount)
        if bitbank_vwap is not None
        and bitflyer_vwap is not None
        and group.bitbank_amount > 0
        and group.net_position_sum == 0
        else None
    )
    spread = (
        gross_edge / group.bitbank_amount
        if gross_edge is not None and group.bitbank_amount > 0
        else None
    )

    return {
        "merge_id": str(group.merge_id),
        "merge_status": status,
        "position_sum_btc": decimal_text(group.net_position_sum),
        "bb_order_ids": ",".join(group.bitbank_orders),
        "bb_trade_ids": ",".join(group.bitbank_trade_ids),
        "bb_pair": group.bitbank_pair,
        "bb_side": group.bitbank_side,
        "bf_side": group.bitflyer_side,
        "bb_first_executed_at_utc": ts_ms_text(group.first_bitbank_ts_ms),
        "bb_last_executed_at_utc": ts_ms_text(group.last_bitbank_ts_ms),
        "bb_amount_btc": decimal_text(group.bitbank_amount),
        "bb_vwap_jpy": decimal_text(bitbank_vwap),
        "bb_notional_jpy": decimal_text(group.bitbank_notional_jpy),
        "bb_fee_base_btc": decimal_text(group.bitbank_fee_base),
        "bb_fee_quote_jpy": decimal_text(group.bitbank_fee_quote),
        "bb_maker_taker": unique_join(group.bitbank_maker_takers),
        "bb_order_type": unique_join(group.bitbank_order_types),
        "bb_position_side": unique_join(group.bitbank_position_sides),
        "bf_order_keys": ",".join(group.bitflyer_order_keys),
        "bf_execution_ids": ",".join(group.bitflyer_execution_ids),
        "bf_child_order_ids": unique_join(group.bitflyer_child_order_ids),
        "bf_child_order_acceptance_ids": unique_join(group.bitflyer_child_order_acceptance_ids),
        "bf_product_code": group.bitflyer_product_code,
        "bf_first_exec_date_utc": ts_ms_text(group.first_bitflyer_ts_ms),
        "bf_last_exec_date_utc": ts_ms_text(group.last_bitflyer_ts_ms),
        "bf_size_btc": decimal_text(group.bitflyer_size),
        "bf_vwap_jpy": decimal_text(bitflyer_vwap),
        "bf_notional_jpy": decimal_text(group.bitflyer_notional_jpy),
        "bf_commission_btc": decimal_text(group.bitflyer_commission),
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
        help=f"bitbank TSV input; default: newest quote_log/transactions/{BITBANK_GLOB}",
    )
    parser.add_argument(
        "--bitflyer-tsv",
        type=Path,
        default=None,
        help=f"bitFlyer TSV input; default: newest quote_log/transactions/{BITFLYER_GLOB}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="output TSV path; default: quote_log/transactions/merged_bitbank_bitflyer_arbitrage_<timestamp>.tsv",
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
    bitbank_path = args.bitbank_tsv or latest_file(TRANSACTIONS_DIR, BITBANK_GLOB)
    bitflyer_path = args.bitflyer_tsv or latest_file(TRANSACTIONS_DIR, BITFLYER_GLOB)

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
        output_path = TRANSACTIONS_DIR / f"merged_bitbank_bitflyer_arbitrage_{stamp}.tsv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(output_rows[0].keys()) if output_rows else list(make_merged_row(
        MergedOrder(
            merge_id=0,
            bitbank_side="",
            bitflyer_side="",
        ),
        status="",
        bitbank_min_size=args.bitbank_min_size,
        bitflyer_min_size=args.bitflyer_min_size,
    ).keys())
    with output_path.open("w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(output_rows)

    matched_groups = [row for row in output_rows if row["merge_status"] == "matched_zero_position"]
    residual_groups = [row for row in output_rows if row["merge_status"] != "matched_zero_position"]
    unused_bitflyer = sum(
        (order.remaining_size for order in bitflyer_orders),
        start=Decimal("0"),
    )

    print(f"bitbank input: {bitbank_path}")
    print(f"bitFlyer input: {bitflyer_path}")
    print(f"wrote {len(output_rows)} merged order rows -> {output_path}")
    print(f"merged orders: matched_zero_position={len(matched_groups)} residual={len(residual_groups)}")
    print(f"unused bitFlyer counter size: {decimal_text(unused_bitflyer)} BTC")


if __name__ == "__main__":
    main()
