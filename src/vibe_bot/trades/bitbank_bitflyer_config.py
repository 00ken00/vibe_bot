from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from vibe_bot.trades.bitbank_bitflyer_utils import decimal_arg


@dataclass(frozen=True)
class BotConfig:
    """Runtime configuration for the bitbank/bitFlyer arbitrage bot.

    Holds exchange symbols, strategy thresholds, sizing limits, web server
    ports, logging paths, and whether execution is dry-run or live.
    """

    bitbank_pair: str = "btc_jpy"
    bitflyer_product_code: str = "FX_BTC_JPY"
    threshold_jpy: Decimal = Decimal("1000")
    threshold_offset_jpy: Decimal = Decimal("0")
    order_size: Decimal = Decimal("0.001")
    stage_size: Decimal = Decimal("0.001")
    max_stages: int = 3
    maker_update_interval: float = 0.5
    monitor_update_interval: float = 1.0
    tick_size: Decimal = Decimal("1")
    min_order_size: Decimal = Decimal("0.0001")
    bitflyer_min_order_size: Decimal = Decimal("0.001")
    bitflyer_maintenance_guard_enabled: bool = True
    bitflyer_maintenance_start_jst: str = "03:59:30"
    bitflyer_maintenance_end_jst: str = "04:12:30"
    dry_run: bool = True
    hedge_enabled: bool = True
    web_host: str = "0.0.0.0"
    web_port: int = 8765
    ws_port: int = 8766
    log_dir: Path = Path("logs/trades/bitbank_bitflyer_arbitrage")

    @property
    def max_position(self) -> Decimal:
        return self.stage_size * Decimal(self.max_stages)


def parse_hhmmss(value: str) -> int:
    parts = value.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"invalid JST time: {value}")
    hour = int(parts[0])
    minute = int(parts[1])
    second = int(parts[2]) if len(parts) == 3 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        raise ValueError(f"invalid JST time: {value}")
    return hour * 3600 + minute * 60 + second


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="bitbank maker / bitFlyer taker BTC-JPY arbitrage bot with web monitor."
    )
    parser.add_argument("--threshold-jpy", type=decimal_arg, default=Decimal("1000"))
    parser.add_argument(
        "--threshold-offset-jpy",
        type=decimal_arg,
        default=Decimal("0"),
        help="center spread offset for open/close thresholds",
    )
    parser.add_argument("--order-size", type=decimal_arg, default=Decimal("0.001"))
    parser.add_argument(
        "--stage-size",
        type=decimal_arg,
        default=Decimal("0.001"),
        help="target exposure size per spread ladder stage",
    )
    parser.add_argument(
        "--max-stages",
        type=int,
        default=3,
        help="maximum number of spread ladder stages per side",
    )
    parser.add_argument("--maker-update-interval", type=float, default=0.5)
    parser.add_argument(
        "--monitor-update-interval",
        type=float,
        default=1.0,
        help="seconds between browser websocket snapshot updates",
    )
    parser.add_argument("--tick-size", type=decimal_arg, default=Decimal("1"))
    parser.add_argument("--min-order-size", type=decimal_arg, default=Decimal("0.0001"))
    parser.add_argument(
        "--bitflyer-min-order-size",
        type=decimal_arg,
        default=Decimal("0.001"),
        help="minimum executable bitFlyer hedge order size",
    )
    parser.add_argument(
        "--disable-bitflyer-maintenance-guard",
        action="store_true",
        help="do not pause makers during the daily bitFlyer maintenance guard",
    )
    parser.add_argument(
        "--bitflyer-maintenance-start-jst",
        default="03:59:30",
        help="JST HH:MM[:SS] start time for the bitFlyer maintenance guard",
    )
    parser.add_argument(
        "--bitflyer-maintenance-end-jst",
        default="04:12:30",
        help="JST HH:MM[:SS] end time for the bitFlyer maintenance guard",
    )
    parser.add_argument("--bitbank-pair", default="btc_jpy")
    parser.add_argument("--bitflyer-product-code", default="FX_BTC_JPY")
    parser.add_argument("--web-host", default="0.0.0.0")
    parser.add_argument("--web-port", type=int, default=8765)
    parser.add_argument("--ws-port", type=int, default=8766)
    parser.add_argument(
        "--log-dir", type=Path, default=Path("logs/trades/bitbank_bitflyer_arbitrage")
    )
    parser.add_argument("--live", action="store_true", help="place real orders")
    parser.add_argument(
        "--disable-bitflyer-hedge",
        action="store_true",
        help="in live mode, do not place the bitFlyer hedge market order after a bitbank fill",
    )
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
    if args.bitflyer_min_order_size <= 0:
        raise SystemExit("--bitflyer-min-order-size must be positive")
    if args.order_size < args.min_order_size:
        raise SystemExit("--order-size must be greater than or equal to --min-order-size")
    if args.stage_size < args.min_order_size:
        raise SystemExit("--stage-size must be greater than or equal to --min-order-size")
    if args.maker_update_interval <= 0:
        raise SystemExit("--maker-update-interval must be positive")
    if args.monitor_update_interval <= 0:
        raise SystemExit("--monitor-update-interval must be positive")
    try:
        parse_hhmmss(args.bitflyer_maintenance_start_jst)
        parse_hhmmss(args.bitflyer_maintenance_end_jst)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return BotConfig(
        bitbank_pair=args.bitbank_pair,
        bitflyer_product_code=args.bitflyer_product_code,
        threshold_jpy=args.threshold_jpy,
        threshold_offset_jpy=args.threshold_offset_jpy,
        order_size=args.order_size,
        stage_size=args.stage_size,
        max_stages=args.max_stages,
        maker_update_interval=args.maker_update_interval,
        monitor_update_interval=args.monitor_update_interval,
        tick_size=args.tick_size,
        min_order_size=args.min_order_size,
        bitflyer_min_order_size=args.bitflyer_min_order_size,
        bitflyer_maintenance_guard_enabled=not args.disable_bitflyer_maintenance_guard,
        bitflyer_maintenance_start_jst=args.bitflyer_maintenance_start_jst,
        bitflyer_maintenance_end_jst=args.bitflyer_maintenance_end_jst,
        dry_run=not args.live,
        hedge_enabled=not args.disable_bitflyer_hedge,
        web_host=args.web_host,
        web_port=args.web_port,
        ws_port=args.ws_port,
        log_dir=args.log_dir,
    )
