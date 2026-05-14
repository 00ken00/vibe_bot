from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import plotly.graph_objects as go

from . import common
from .common import HistoricalSpreadPoint


@dataclass(frozen=True)
class HistoryConfig:
    gmo_symbol: str = "BTC_JPY"
    bitflyer_product_code: str = "FX_BTC_JPY"
    days: int = 5
    candle_minutes: int = 5

    def as_pair_config(self) -> common.PairHistoryConfig:
        return common.PairHistoryConfig(
            left_exchange="GMO",
            left_symbol=self.gmo_symbol,
            right_exchange="bitFlyer",
            right_symbol=self.bitflyer_product_code,
            days=self.days,
            candle_minutes=self.candle_minutes,
        )


async def fetch_historical_spreads(
    config: HistoryConfig,
) -> list[HistoricalSpreadPoint]:
    return await common.fetch_historical_spreads(config.as_pair_config())


def build_figure(
    points: list[HistoricalSpreadPoint],
    config: HistoryConfig,
) -> go.Figure:
    return common.build_figure(points, config.as_pair_config())


async def _run(config: HistoryConfig, output_html: Path | str | None) -> go.Figure:
    points = await fetch_historical_spreads(config)
    if not points:
        raise RuntimeError("no matching historical candle points were returned")
    fig = build_figure(points, config)
    if output_html is not None:
        output_path = Path(output_html)
        fig.write_html(output_path, include_plotlyjs="cdn")
        print(f"wrote: {output_path}")
    fig.show(renderer="browser")
    return fig


def main(
    gmo_symbol: str = "BTC_JPY",
    bitflyer_product_code: str = "FX_BTC_JPY",
    days: int = 5,
    candle_minutes: int = 5,
    output_html: Path | str | None = None,
) -> go.Figure:
    """Fetch historical GMO/bitFlyer candles and open a Plotly spread chart."""
    common.validate_config(days, candle_minutes)
    config = HistoryConfig(
        gmo_symbol=gmo_symbol,
        bitflyer_product_code=bitflyer_product_code,
        days=days,
        candle_minutes=candle_minutes,
    )
    return asyncio.run(_run(config, output_html))


if __name__ == "__main__":
    main(
        gmo_symbol="BTC_JPY",
        bitflyer_product_code="FX_BTC_JPY",
        days=5,
        candle_minutes=5,
        output_html=None,
    )
