"""Fetch recent FX_BTC_JPY private executions and write a TSV with running position.

position_before / position_after are net signed positions (positive = long, negative
= short) measured in BTC. The tail row is anchored to the current open position from
/v1/me/getpositions; older rows are derived by walking backward.
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from vibe_bot.bitflyer import PrivateClient

PRODUCT = "FX_BTC_JPY"
COUNT = 500  # bitFlyer max per call
OUT_DIR = Path(__file__).resolve().parents[2] / "quote_log"


def _signed_delta(side: str, size: Decimal) -> Decimal:
    return size if side == "BUY" else -size


async def main() -> None:
    async with PrivateClient() as p:
        execs = await p.executions(product_code=PRODUCT, count=COUNT)
        positions = await p.positions(product_code=PRODUCT)

    current_net = sum(
        (_signed_delta(pos.side, pos.size) for pos in positions),
        start=Decimal("0"),
    )

    # API returns newest first. Walk newest -> oldest, anchoring "after" of the
    # most recent execution to the current net position.
    execs_sorted = sorted(execs, key=lambda e: (e.exec_date, e.id), reverse=True)
    rows: list[dict[str, str]] = []
    after = current_net
    for e in execs_sorted:
        delta = _signed_delta(e.side, e.size)
        before = after - delta
        rows.append({
            "id": str(e.id),
            "exec_date": e.exec_date,
            "side": e.side,
            "price": format(e.price, "f"),
            "size": format(e.size, "f"),
            "commission": format(e.commission, "f"),
            "child_order_id": e.child_order_id,
            "child_order_acceptance_id": e.child_order_acceptance_id,
            "position_before": format(before, "f"),
            "position_after": format(after, "f"),
        })
        after = before

    # Write oldest-first so the file reads chronologically.
    rows.reverse()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"bitflyer_fx_btc_jpy_my_executions_{stamp}.tsv"
    fieldnames = [
        "id", "exec_date", "side", "price", "size", "commission",
        "child_order_id", "child_order_acceptance_id",
        "position_before", "position_after",
    ]
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} rows -> {out}")
    print(f"current open net position: {format(current_net, 'f')} BTC")


if __name__ == "__main__":
    asyncio.run(main())
