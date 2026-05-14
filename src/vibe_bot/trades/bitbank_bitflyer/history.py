from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import plotly.graph_objects as go

from vibe_bot.trades.history.history import (
    HistoricalSpreadPoint as _HistoricalSpreadPoint,
    HistoryConfig as _GenericHistoryConfig,
    build_figure as _build_figure,
    fetch_historical_spreads as _fetch_historical_spreads,
    main as _main,
)


class HistoricalSpreadPoint(_HistoricalSpreadPoint):
    @property
    def bitbank_close(self) -> Decimal:
        return self.left_close

    @property
    def bitflyer_close(self) -> Decimal:
        return self.right_close


class HistoryConfig(_GenericHistoryConfig):
    def __init__(
        self,
        bitbank_pair: str = "btc_jpy",
        bitflyer_product_code: str = "FX_BTC_JPY",
        days: int = 5,
        candle_minutes: int = 5,
    ) -> None:
        super().__init__(
            left_exchange="bitbank",
            left_symbol=bitbank_pair,
            right_exchange="bitFlyer",
            right_symbol=bitflyer_product_code,
            days=days,
            candle_minutes=candle_minutes,
        )

    @property
    def bitbank_pair(self) -> str:
        return self.left_symbol

    @property
    def bitflyer_product_code(self) -> str:
        return self.right_symbol


async def fetch_historical_spreads(
    config: HistoryConfig,
) -> list[HistoricalSpreadPoint]:
    points = await _fetch_historical_spreads(config)
    return [HistoricalSpreadPoint(**point.__dict__) for point in points]


def build_figure(
    points: list[HistoricalSpreadPoint],
    config: HistoryConfig,
) -> go.Figure:
    return _build_figure(points, config)


def main(
    bitbank_pair: str = "btc_jpy",
    bitflyer_product_code: str = "FX_BTC_JPY",
    days: int = 5,
    candle_minutes: int = 5,
    output_html: Path | str | None = None,
) -> go.Figure:
    return _main(
        left_exchange="bitbank",
        left_symbol=bitbank_pair,
        right_exchange="bitFlyer",
        right_symbol=bitflyer_product_code,
        days=days,
        candle_minutes=candle_minutes,
        output_html=output_html,
    )


__all__ = [
    "HistoricalSpreadPoint",
    "HistoryConfig",
    "build_figure",
    "fetch_historical_spreads",
    "main",
]
