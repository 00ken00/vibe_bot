from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from vibe_bot.trades.bitbank_bitflyer.config import parse_hhmmss
from vibe_bot.trades.bitbank_bitflyer.utils import decimal_arg


@dataclass(frozen=True)
class BotConfig:
    """Runtime configuration for the GMO taker / bitFlyer taker bot."""

    gmo_symbol: str = "BTC_JPY"
    bitflyer_product_code: str = "FX_BTC_JPY"
    threshold_jpy: Decimal = Decimal("1000")
    threshold_offset_jpy: Decimal = Decimal("0")
    order_size: Decimal = Decimal("0.001")
    stage_size: Decimal = Decimal("0.001")
    max_stages: int = 3
    tick_size: Decimal = Decimal("1")
    min_order_size: Decimal = Decimal("0.001")
    update_interval: float = 0.5
    entry_cooldown_seconds: float = 5.0
    ema_alpha: Decimal = Decimal("0.08")
    noise_window: int = 60
    min_filter_samples: int = 20
    noise_multiplier: Decimal = Decimal("2.0")
    min_extra_edge_jpy: Decimal = Decimal("0")
    persistence_seconds: float = 2.0
    max_slippage_jpy: Decimal = Decimal("500")
    bitflyer_maintenance_guard_enabled: bool = True
    bitflyer_maintenance_start_jst: str = "03:59:30"
    bitflyer_maintenance_end_jst: str = "04:12:30"
    dry_run: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 8765
    ws_port: int = 8766
    monitor_update_interval: float = 1.0
    log_dir: Path = Path("logs/trades/gmo_bitflyer_arbitrage")

    @property
    def max_position(self) -> Decimal:
        return self.stage_size * Decimal(self.max_stages)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="GMO taker / bitFlyer taker BTC-JPY arbitrage bot."
    )
    parser.add_argument("--gmo-symbol", default="BTC_JPY")
    parser.add_argument("--bitflyer-product-code", default="FX_BTC_JPY")
    parser.add_argument("--threshold-jpy", type=decimal_arg, default=Decimal("1000"))
    parser.add_argument(
        "--threshold-offset-jpy",
        type=decimal_arg,
        default=Decimal("0"),
        help="center spread offset for open/close thresholds",
    )
    parser.add_argument("--order-size", type=decimal_arg, default=Decimal("0.001"))
    parser.add_argument("--stage-size", type=decimal_arg, default=Decimal("0.001"))
    parser.add_argument("--max-stages", type=int, default=3)
    parser.add_argument("--tick-size", type=decimal_arg, default=Decimal("1"))
    parser.add_argument("--min-order-size", type=decimal_arg, default=Decimal("0.001"))
    parser.add_argument("--update-interval", type=float, default=0.5)
    parser.add_argument("--entry-cooldown-seconds", type=float, default=5.0)
    parser.add_argument(
        "--ema-alpha",
        type=decimal_arg,
        default=Decimal("0.08"),
        help="EMA alpha for the long-period spread trend",
    )
    parser.add_argument("--noise-window", type=int, default=60)
    parser.add_argument("--min-filter-samples", type=int, default=20)
    parser.add_argument(
        "--noise-multiplier",
        type=decimal_arg,
        default=Decimal("2.0"),
        help="extra edge required per JPY of short-term residual noise",
    )
    parser.add_argument("--min-extra-edge-jpy", type=decimal_arg, default=Decimal("0"))
    parser.add_argument("--persistence-seconds", type=float, default=2.0)
    parser.add_argument(
        "--max-slippage-jpy",
        type=decimal_arg,
        default=Decimal("500"),
        help="maximum allowed limit offset from executable VWAP on each taker leg",
    )
    parser.add_argument(
        "--disable-bitflyer-maintenance-guard",
        action="store_true",
        help="do not pause trading during the daily bitFlyer maintenance guard",
    )
    parser.add_argument("--bitflyer-maintenance-start-jst", default="03:59:30")
    parser.add_argument("--bitflyer-maintenance-end-jst", default="04:12:30")
    parser.add_argument("--web-host", default="0.0.0.0")
    parser.add_argument("--web-port", type=int, default=8765)
    parser.add_argument("--ws-port", type=int, default=8766)
    parser.add_argument(
        "--monitor-update-interval",
        type=float,
        default=1.0,
        help="seconds between browser websocket snapshot updates",
    )
    parser.add_argument(
        "--log-dir", type=Path, default=Path("logs/trades/gmo_bitflyer_arbitrage")
    )
    parser.add_argument("--live", action="store_true", help="place real orders")
    parser.add_argument("--log-level", default="INFO")
    return parser


def config_from_args(args: argparse.Namespace) -> BotConfig:
    if args.threshold_jpy <= 0:
        raise SystemExit("--threshold-jpy must be positive")
    if args.order_size <= 0:
        raise SystemExit("--order-size must be positive")
    if args.stage_size <= 0:
        raise SystemExit("--stage-size must be positive")
    if args.max_stages <= 0:
        raise SystemExit("--max-stages must be positive")
    if args.min_order_size <= 0:
        raise SystemExit("--min-order-size must be positive")
    if args.order_size < args.min_order_size:
        raise SystemExit("--order-size must be greater than or equal to --min-order-size")
    if args.stage_size < args.min_order_size:
        raise SystemExit("--stage-size must be greater than or equal to --min-order-size")
    if args.update_interval <= 0:
        raise SystemExit("--update-interval must be positive")
    if args.entry_cooldown_seconds < 0:
        raise SystemExit("--entry-cooldown-seconds must be non-negative")
    if not (Decimal("0") < args.ema_alpha <= Decimal("1")):
        raise SystemExit("--ema-alpha must be > 0 and <= 1")
    if args.noise_window < 2:
        raise SystemExit("--noise-window must be at least 2")
    if args.min_filter_samples < 1:
        raise SystemExit("--min-filter-samples must be positive")
    if args.noise_multiplier < 0:
        raise SystemExit("--noise-multiplier must be non-negative")
    if args.min_extra_edge_jpy < 0:
        raise SystemExit("--min-extra-edge-jpy must be non-negative")
    if args.persistence_seconds < 0:
        raise SystemExit("--persistence-seconds must be non-negative")
    if args.max_slippage_jpy < 0:
        raise SystemExit("--max-slippage-jpy must be non-negative")
    if args.monitor_update_interval <= 0:
        raise SystemExit("--monitor-update-interval must be positive")
    try:
        parse_hhmmss(args.bitflyer_maintenance_start_jst)
        parse_hhmmss(args.bitflyer_maintenance_end_jst)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return BotConfig(
        gmo_symbol=args.gmo_symbol,
        bitflyer_product_code=args.bitflyer_product_code,
        threshold_jpy=args.threshold_jpy,
        threshold_offset_jpy=args.threshold_offset_jpy,
        order_size=args.order_size,
        stage_size=args.stage_size,
        max_stages=args.max_stages,
        tick_size=args.tick_size,
        min_order_size=args.min_order_size,
        update_interval=args.update_interval,
        entry_cooldown_seconds=args.entry_cooldown_seconds,
        ema_alpha=args.ema_alpha,
        noise_window=args.noise_window,
        min_filter_samples=args.min_filter_samples,
        noise_multiplier=args.noise_multiplier,
        min_extra_edge_jpy=args.min_extra_edge_jpy,
        persistence_seconds=args.persistence_seconds,
        max_slippage_jpy=args.max_slippage_jpy,
        bitflyer_maintenance_guard_enabled=not args.disable_bitflyer_maintenance_guard,
        bitflyer_maintenance_start_jst=args.bitflyer_maintenance_start_jst,
        bitflyer_maintenance_end_jst=args.bitflyer_maintenance_end_jst,
        dry_run=not args.live,
        web_host=args.web_host,
        web_port=args.web_port,
        ws_port=args.ws_port,
        monitor_update_interval=args.monitor_update_interval,
        log_dir=args.log_dir,
    )
