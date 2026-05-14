from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import plotly.graph_objects as go

from . import common


@dataclass(frozen=True)
class HistoricalSpreadPoint(common.HistoricalSpreadPoint):
    @property
    def bitbank_close(self) -> Decimal:
        return self.left_close

    @property
    def bitflyer_close(self) -> Decimal:
        return self.right_close


@dataclass(frozen=True)
class HistoryConfig:
    bitbank_pair: str = "btc_jpy"
    bitflyer_product_code: str = "FX_BTC_JPY"
    days: int = 5
    candle_minutes: int = 5

    def as_pair_config(self) -> common.PairHistoryConfig:
        return common.PairHistoryConfig(
            left_exchange="bitbank",
            left_symbol=self.bitbank_pair,
            right_exchange="bitFlyer",
            right_symbol=self.bitflyer_product_code,
            days=self.days,
            candle_minutes=self.candle_minutes,
        )


async def fetch_historical_spreads(
    config: HistoryConfig,
) -> list[HistoricalSpreadPoint]:
    points = await common.fetch_historical_spreads(config.as_pair_config())
    return [HistoricalSpreadPoint(**point.__dict__) for point in points]


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
    bitbank_pair: str = "btc_jpy",
    bitflyer_product_code: str = "FX_BTC_JPY",
    days: int = 5,
    candle_minutes: int = 5,
    output_html: Path | str | None = None,
) -> go.Figure:
    """Fetch historical candles and open a Plotly spread chart.

    IPython usage:
        fig = main(days=5, candle_minutes=5)
        fig = main(days=3, candle_minutes=15, output_html="/tmp/spread.html")
    """
    common.validate_config(days, candle_minutes)
    config = HistoryConfig(
        bitbank_pair=bitbank_pair,
        bitflyer_product_code=bitflyer_product_code,
        days=days,
        candle_minutes=candle_minutes,
    )
    return asyncio.run(_run(config, output_html))


if __name__ == "__main__":
    main(
        bitbank_pair="btc_jpy",
        bitflyer_product_code="FX_BTC_JPY",
        days=5,
        candle_minutes=5,
        output_html=None,
    )
