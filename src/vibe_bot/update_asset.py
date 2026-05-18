from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
import shutil
import subprocess
import sys

from dotenv import load_dotenv

from vibe_bot.bitbank import PrivateClient as BitbankPrivateClient
from vibe_bot.bitbank import PublicClient as BitbankPublicClient
from vibe_bot.bitbank.models import Asset as BitbankAsset
from vibe_bot.bitbank.models import MarginPosition
from vibe_bot.bitflyer import PrivateClient as BitflyerPrivateClient
from vibe_bot.coincheck import PrivateClient as CoincheckPrivateClient
from vibe_bot.gmo import PrivateClient as GmoPrivateClient


TRACKED_RATES = ("btc_jpy", "eth_jpy", "xrp_jpy", "sol_jpy")
OUTPUT_COLUMNS = (
    "datetime",
    "btc_rate",
    "eth_rate",
    "xrp_rate",
    "sol_rate",
    "gmo",
    "bitbank",
    "coincheck",
    "bitflyer_cfd",
)


@dataclass(frozen=True)
class AssetSnapshot:
    timestamp: str
    rates: dict[str, Decimal]
    gmo_total_asset: Decimal
    bitbank_total_asset: Decimal
    coincheck_total_asset: Decimal
    bitflyer_cfd_asset: Decimal

    def as_display_row(self) -> dict[str, str]:
        return {
            "datetime": self.timestamp,
            "btc_rate": format_jpy(self.rates["btc_jpy"]),
            "eth_rate": format_jpy(self.rates["eth_jpy"]),
            "xrp_rate": format_jpy(self.rates["xrp_jpy"]),
            "sol_rate": format_jpy(self.rates["sol_jpy"]),
            "gmo": format_jpy(self.gmo_total_asset),
            "bitbank": format_jpy(self.bitbank_total_asset),
            "coincheck": format_jpy(self.coincheck_total_asset),
            "bitflyer_cfd": format_jpy(self.bitflyer_cfd_asset),
        }


async def fetch_asset_snapshot() -> AssetSnapshot:
    async with AsyncExitStack() as stack:
        bitbank_public = await stack.enter_async_context(BitbankPublicClient())
        bitbank_private = await stack.enter_async_context(BitbankPrivateClient())
        gmo_private = await stack.enter_async_context(GmoPrivateClient())
        coincheck_private = await stack.enter_async_context(CoincheckPrivateClient())
        bitflyer_private = await stack.enter_async_context(BitflyerPrivateClient())

        tickers_task = asyncio.gather(
            *(bitbank_public.ticker(pair) for pair in TRACKED_RATES)
        )
        gmo_assets_task = asyncio.create_task(gmo_private.assets())
        bitbank_assets_task = asyncio.create_task(bitbank_private.assets())
        bitbank_positions_task = asyncio.create_task(bitbank_private.margin_positions())
        coincheck_balance_task = asyncio.create_task(coincheck_private.balance())
        bitflyer_collateral_task = asyncio.create_task(bitflyer_private.collateral())

        tickers = await tickers_task
        rates = {
            pair: ticker_mid(ticker.buy, ticker.sell, ticker.last)
            for pair, ticker in zip(TRACKED_RATES, tickers)
        }
        gmo_assets = await gmo_assets_task
        bitbank_assets = await bitbank_assets_task
        bitbank_positions = await bitbank_positions_task
        coincheck_balance = await coincheck_balance_task
        bitflyer_collateral = await bitflyer_collateral_task

        return AssetSnapshot(
            timestamp=datetime.now().replace(microsecond=0).isoformat(sep=" "),
            rates=rates,
            gmo_total_asset=sum(
                (
                    asset.amount
                    if asset.symbol == "JPY"
                    else asset.amount * asset.conversion_rate
                )
                for asset in gmo_assets
                if asset.amount != 0
            ),
            bitbank_total_asset=bitbank_total_asset(
                bitbank_assets.assets,
                bitbank_positions.positions,
                rates,
            ),
            coincheck_total_asset=coincheck_total_asset(
                coincheck_balance.balances,
                rates,
            ),
            bitflyer_cfd_asset=bitflyer_collateral.collateral,
        )


def ticker_mid(
    bid: Decimal | None,
    ask: Decimal | None,
    last: Decimal | None,
) -> Decimal:
    if bid is not None and ask is not None:
        return (bid + ask) / Decimal("2")
    if last is not None:
        return last
    raise ValueError("ticker has neither bid/ask nor last price")


def bitbank_total_asset(
    assets: list[BitbankAsset],
    positions: list[MarginPosition],
    rates: dict[str, Decimal],
) -> Decimal:
    assets_by_symbol = {asset.asset.lower(): asset for asset in assets}
    total = asset_onhand(assets_by_symbol, "jpy")
    total += asset_onhand(assets_by_symbol, "btc") * rates["btc_jpy"]
    total += asset_onhand(assets_by_symbol, "eth") * rates["eth_jpy"]
    total += sum(position_pnl(position, rates) for position in positions)
    return total


def coincheck_total_asset(
    balances: dict[str, Decimal],
    rates: dict[str, Decimal],
) -> Decimal:
    return balances.get("jpy", Decimal("0")) + (
        balances.get("btc", Decimal("0")) * rates["btc_jpy"]
    )


def asset_onhand(assets_by_symbol: dict[str, BitbankAsset], symbol: str) -> Decimal:
    asset = assets_by_symbol.get(symbol)
    return asset.onhand_amount if asset is not None else Decimal("0")


def position_pnl(position: MarginPosition, rates: dict[str, Decimal]) -> Decimal:
    if position.open_amount is None or position.average_price is None:
        return Decimal("0")
    market_price = rates.get(position.pair)
    if market_price is None:
        return Decimal("0")
    price_diff = (
        market_price - position.average_price
        if position.position_side == "long"
        else position.average_price - market_price
    )
    pnl = price_diff * position.open_amount
    pnl -= position.unrealized_fee_amount or Decimal("0")
    pnl -= position.unrealized_interest_amount or Decimal("0")
    return pnl


def format_jpy(value: Decimal) -> str:
    rounded = value.quantize(Decimal("1"))
    return f"{rounded:,}"


def render_table(row: dict[str, str]) -> str:
    widths = {
        column: max(len(column), len(row[column]))
        for column in OUTPUT_COLUMNS
    }
    header = "  ".join(column.ljust(widths[column]) for column in OUTPUT_COLUMNS)
    values = "  ".join(row[column].rjust(widths[column]) for column in OUTPUT_COLUMNS)
    return f"{header}\n{values}"


def render_clipboard_row(row: dict[str, str]) -> str:
    return "\t".join(row[column] for column in OUTPUT_COLUMNS)


def copy_to_clipboard(text: str) -> bool:
    if shutil.which("pbcopy"):
        return _copy_with_command(["pbcopy"], text)
    if shutil.which("wl-copy"):
        return _copy_with_command(["wl-copy"], text)
    if shutil.which("xclip"):
        return _copy_with_command(["xclip", "-selection", "clipboard"], text)
    if shutil.which("xsel"):
        return _copy_with_command(["xsel", "--clipboard", "--input"], text)
    return False


def _copy_with_command(command: list[str], text: str) -> bool:
    try:
        subprocess.run(
            command,
            input=text,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


async def copy_asset(*, clipboard: bool = True) -> AssetSnapshot:
    snapshot = await fetch_asset_snapshot()
    row = snapshot.as_display_row()
    print(render_table(row))
    if clipboard:
        copied = copy_to_clipboard(render_clipboard_row(row))
        if not copied:
            print("clipboard copy skipped: no supported clipboard command", file=sys.stderr)
    return snapshot


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch exchange asset totals and copy a one-row summary."
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="Print the summary without copying the tab-separated row to clipboard.",
    )
    return parser


async def amain() -> None:
    args = build_parser().parse_args()
    load_dotenv(override=True)
    await copy_asset(clipboard=not args.no_clipboard)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
