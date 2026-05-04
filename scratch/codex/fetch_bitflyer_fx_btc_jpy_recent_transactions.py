"""Fetch recent bitFlyer FX_BTC_JPY executions and write a TSV.

The running position columns are signed BTC amounts:
positive = long, negative = short. They are anchored to the current open
position from /v1/me/getpositions, then calculated backward through the recent
private executions returned by /v1/me/getexecutions.
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from vibe_bot.bitflyer import PrivateClient


PRODUCT_CODE = "FX_BTC_JPY"
COUNT = 500
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "quote_log" / "transactions"


def signed_delta(side: str, size: Decimal) -> Decimal:
    if side == "BUY":
        return size
    if side == "SELL":
        return -size
    raise ValueError(f"unexpected side: {side!r}")


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


async def fetch_rows() -> tuple[list[dict[str, str]], Decimal]:
    async with PrivateClient() as client:
        executions = await client.executions(product_code=PRODUCT_CODE, count=COUNT)
        positions = await client.positions(product_code=PRODUCT_CODE)

    current_position = sum(
        (signed_delta(position.side, position.size) for position in positions),
        start=Decimal("0"),
    )

    newest_first = sorted(
        executions,
        key=lambda execution: (execution.exec_date, execution.id),
        reverse=True,
    )

    rows: list[dict[str, str]] = []
    position_after = current_position
    for execution in newest_first:
        delta = signed_delta(execution.side, execution.size)
        position_before = position_after - delta
        rows.append(
            {
                "id": str(execution.id),
                "exec_date": execution.exec_date,
                "side": execution.side,
                "price": decimal_text(execution.price),
                "size": decimal_text(execution.size),
                "commission": decimal_text(execution.commission),
                "child_order_id": execution.child_order_id,
                "child_order_acceptance_id": execution.child_order_acceptance_id,
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
    output_path = OUT_DIR / f"bitflyer_fx_btc_jpy_recent_transactions_{stamp}.tsv"

    fieldnames = [
        "id",
        "exec_date",
        "side",
        "price",
        "size",
        "commission",
        "child_order_id",
        "child_order_acceptance_id",
        "position_before",
        "position_after",
    ]
    with output_path.open("w", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} rows -> {output_path}")
    print(f"current open net position: {decimal_text(current_position)} BTC")


if __name__ == "__main__":
    asyncio.run(main())
