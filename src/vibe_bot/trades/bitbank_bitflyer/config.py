from __future__ import annotations

import argparse
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from vibe_bot.trades.bitbank_bitflyer.utils import decimal_arg


@dataclass(frozen=True)
class BotConfig:
    """Runtime configuration for the bitbank/bitFlyer arbitrage bot.

    Holds exchange symbols, strategy thresholds, sizing limits, web server
    ports, logging paths, and whether execution is dry-run or live.
    """

    bitbank_pair: str = "btc_jpy"
    bitflyer_product_code: str = "FX_BTC_JPY"
    bitbank_neutral_spot_amount: Decimal = Decimal("0")
    bitflyer_neutral_position_amount: Decimal = Decimal("0")
    threshold_jpy: Decimal = Decimal("1000")
    threshold_offset_jpy: Decimal = Decimal("0")
    hedge_vwap_multiplier: Decimal = Decimal("3")
    hedge_slippage_buffer_jpy: Decimal = Decimal("500")
    momentum_guard_enabled: bool = True
    momentum_guard_window_seconds: float = 2.0
    momentum_guard_threshold_jpy: Decimal = Decimal("3000")
    momentum_guard_cooldown_seconds: float = 2.0
    order_size: Decimal = Decimal("0.001")
    stage_size: Decimal = Decimal("0.001")
    max_stages: int = 3
    maker_placement_interval: float = 0.5
    monitor_update_interval: float = 1.0
    tick_size: Decimal = Decimal("1")
    min_order_size: Decimal = Decimal("0.0001")
    bitflyer_min_order_size: Decimal = Decimal("0.001")
    bitflyer_maintenance_guard_enabled: bool = True
    bitflyer_maintenance_start_jst: str = "03:59:30"
    bitflyer_maintenance_end_jst: str = "05:00:00"
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
    parser.add_argument(
        "--hedge-vwap-multiplier",
        type=decimal_arg,
        default=Decimal("3"),
        help=(
            "bitFlyer hedge VWAP depth as a multiple of --order-size; "
            "deeper VWAP makes the expected hedge price conservative during thin books"
        ),
    )
    parser.add_argument(
        "--hedge-slippage-buffer-jpy",
        type=decimal_arg,
        default=Decimal("500"),
        help=(
            "extra JPY/BTC edge required on top of the expected hedge VWAP to cover "
            "book movement between bitbank fill and bitFlyer hedge execution"
        ),
    )
    parser.add_argument(
        "--disable-momentum-guard",
        action="store_true",
        help="do not pull makers while the bitFlyer mid bursts toward them",
    )
    parser.add_argument(
        "--momentum-guard-window-seconds",
        type=float,
        default=2.0,
        help="lookback window for measuring bitFlyer mid movement",
    )
    parser.add_argument(
        "--momentum-guard-threshold-jpy",
        type=decimal_arg,
        default=Decimal("3000"),
        help=(
            "adverse bitFlyer mid move over the window that pulls the maker; "
            "falling mid blocks BUY makers, rising mid blocks SELL makers"
        ),
    )
    parser.add_argument(
        "--momentum-guard-cooldown-seconds",
        type=float,
        default=2.0,
        help="keep the side's maker pulled this long after the last adverse move",
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
    parser.add_argument(
        "--maker-placement-interval",
        "--maker-update-interval",
        dest="maker_placement_interval",
        type=float,
        default=0.5,
        help=(
            "minimum seconds between bitbank maker placements/replacements; "
            "--maker-update-interval is accepted as a deprecated alias"
        ),
    )
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
        default="05:00:00",
        help="JST HH:MM[:SS] end time for the bitFlyer maintenance guard",
    )
    parser.add_argument("--bitbank-pair", default="btc_jpy")
    parser.add_argument("--bitflyer-product-code", default="FX_BTC_JPY")
    parser.add_argument(
        "--bitbank-neutral-spot-amount",
        type=decimal_arg,
        default=Decimal("0"),
        help=(
            "bitbank spot BTC amount treated as strategy-neutral; "
            "strategy position = spot balance - this amount + net margin position"
        ),
    )
    parser.add_argument(
        "--bitflyer-neutral-position-amount",
        type=decimal_arg,
        default=Decimal("0"),
        help=(
            "signed bitFlyer position amount treated as strategy-neutral; "
            "negative means short, positive means long"
        ),
    )
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
    if args.bitbank_neutral_spot_amount < 0:
        raise SystemExit("--bitbank-neutral-spot-amount must be non-negative")
    if args.order_size <= 0:
        raise SystemExit("--order-size must be positive")
    if args.hedge_vwap_multiplier < 1:
        raise SystemExit("--hedge-vwap-multiplier must be at least 1")
    if args.hedge_slippage_buffer_jpy < 0:
        raise SystemExit("--hedge-slippage-buffer-jpy must be non-negative")
    if args.momentum_guard_window_seconds <= 0:
        raise SystemExit("--momentum-guard-window-seconds must be positive")
    if args.momentum_guard_threshold_jpy <= 0:
        raise SystemExit("--momentum-guard-threshold-jpy must be positive")
    if args.momentum_guard_cooldown_seconds < 0:
        raise SystemExit("--momentum-guard-cooldown-seconds must be non-negative")
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
    if args.maker_placement_interval <= 0:
        raise SystemExit("--maker-placement-interval must be positive")
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
        bitbank_neutral_spot_amount=args.bitbank_neutral_spot_amount,
        bitflyer_neutral_position_amount=args.bitflyer_neutral_position_amount,
        threshold_jpy=args.threshold_jpy,
        threshold_offset_jpy=args.threshold_offset_jpy,
        hedge_vwap_multiplier=args.hedge_vwap_multiplier,
        hedge_slippage_buffer_jpy=args.hedge_slippage_buffer_jpy,
        momentum_guard_enabled=not args.disable_momentum_guard,
        momentum_guard_window_seconds=args.momentum_guard_window_seconds,
        momentum_guard_threshold_jpy=args.momentum_guard_threshold_jpy,
        momentum_guard_cooldown_seconds=args.momentum_guard_cooldown_seconds,
        order_size=args.order_size,
        stage_size=args.stage_size,
        max_stages=args.max_stages,
        maker_placement_interval=args.maker_placement_interval,
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
