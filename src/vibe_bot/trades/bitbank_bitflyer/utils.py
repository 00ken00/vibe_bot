from __future__ import annotations

import argparse
import secrets
from dataclasses import asdict, is_dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
import time


Jsonable = None | bool | int | float | str | list[object] | dict[str, object]
JST = ZoneInfo("Asia/Tokyo")


def decimal_to_json(value: object) -> Jsonable:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return decimal_to_json(asdict(value))
    if isinstance(value, dict):
        return {str(k): decimal_to_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decimal_to_json(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def decimal_to_json_dict(value: dict[str, object]) -> dict[str, object]:
    converted = decimal_to_json(value)
    if not isinstance(converted, dict):
        raise TypeError("expected JSON object")
    return converted


def jst_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), JST).isoformat()


def local_date_stamp() -> str:
    return datetime.now(JST).strftime("%Y%m%d")


def local_run_id() -> str:
    timestamp = datetime.now(JST).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{secrets.token_hex(4)}"


def quantize_down(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_DOWN) * tick


def quantize_up(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_UP) * tick


def decimal_arg(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not result.is_finite():
        raise argparse.ArgumentTypeError("must be finite")
    return result
