"""Fetch recent bitbank btc_jpy trades and write a TSV.

The running position columns are signed BTC exposure amounts. They are anchored
to current net BTC exposure from /v1/user/assets and /v1/user/margin/positions,
then calculated backward through recent private trades returned by
/v1/user/spot/trade_history.
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from vibe_bot.bitbank import PrivateClient


PAIR = "btc_jpy"
BASE_ASSET = "btc"
COUNT = 500
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "quote_log" / "transactions"


def signed_delta(side: str, amount: Decimal) -> Decimal:
    if side == "buy":
        return amount
    if side == "sell":
        return -amount
    raise ValueError(f"unexpected side: {side!r}")


def decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


async def fetch_rows() -> tuple[list[dict[str, str]], Decimal]:
    async with PrivateClient() as client:
        trade_list = await client.trade_history(pair=PAIR, count=COUNT, order="desc")
        assets = await client.assets()
        margin_positions = await client.margin_positions()

    base_asset = next(
        (asset for asset in assets.assets if asset.asset == BASE_ASSET),
        None,
    )
    if base_asset is None:
        raise RuntimeError(f"{BASE_ASSET!r} asset was not returned by bitbank")

    current_position = base_asset.onhand_amount
    for position in margin_positions.positions:
        if position.pair != PAIR or position.open_amount is None:
            continue
        if position.position_side == "long":
            current_position += position.open_amount
        elif position.position_side == "short":
            current_position -= position.open_amount
        else:
            raise ValueError(f"unexpected position side: {position.position_side!r}")
    newest_first = sorted(
        trade_list.trades,
        key=lambda trade: (trade.executed_at, trade.trade_id),
        reverse=True,
    )

    rows: list[dict[str, str]] = []
    position_after = current_position
    for trade in newest_first:
        delta = signed_delta(trade.side, trade.amount)
        position_before = position_after - delta
        rows.append(
            {
                "trade_id": str(trade.trade_id),
                "executed_at": str(trade.executed_at),
                "pair": trade.pair,
                "order_id": str(trade.order_id),
                "side": trade.side,
                "position_side": trade.position_side or "",
                "type": trade.type,
                "price": decimal_text(trade.price),
                "amount": decimal_text(trade.amount),
                "maker_taker": trade.maker_taker,
                "fee_amount_base": decimal_text(trade.fee_amount_base),
                "fee_amount_quote": decimal_text(trade.fee_amount_quote),
                "profit_loss": decimal_text(trade.profit_loss),
                "interest": decimal_text(trade.interest),
                "position_before": decimal_text(position_before),
                "position_after": decimal_text(position_after),
            }
        )
        position_after = position_before

    rows.reverse()
    return rows, current_position


async def main() -> None:
    rows, current_position = await fetch_rows()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    output_path = OUT_DIR / f"bitbank_btc_jpy_recent_transactions_{stamp}.tsv"

    fieldnames = [
        "trade_id",
        "executed_at",
        "pair",
        "order_id",
        "side",
        "position_side",
        "type",
        "price",
        "amount",
        "maker_taker",
        "fee_amount_base",
        "fee_amount_quote",
        "profit_loss",
        "interest",
        "position_before",
        "position_after",
    ]
    with output_path.open("w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} rows -> {output_path}")
    print(f"current {BASE_ASSET} net exposure: {decimal_text(current_position)}")


if __name__ == "__main__":
    asyncio.run(main())
