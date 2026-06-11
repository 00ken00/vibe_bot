from __future__ import annotations

import csv
import json
from decimal import Decimal
from pathlib import Path
from typing import IO

from websockets.asyncio.server import ServerConnection

from vibe_bot.trades.bitbank_bitflyer.utils import decimal_to_json
from vibe_bot.trades.bitbank_bitflyer.utils import decimal_to_json_dict
from vibe_bot.trades.bitbank_bitflyer.utils import jst_iso
from vibe_bot.trades.bitbank_bitflyer.utils import local_run_id


def event_summary(event_type: str, **payload: object) -> str:
    details = ", ".join(
        f"{key}={summary_value(value)}" for key, value in payload.items()
    )
    return f"{event_type}: {details}" if details else event_type


def summary_value(value: object) -> str:
    converted = decimal_to_json(value)
    if isinstance(converted, dict):
        fields = []
        for key in (
            "action",
            "side",
            "position_side",
            "order_id",
            "price",
            "amount",
            "trigger_price",
            "stage_index",
            "executed_amount",
            "status",
        ):
            if key in converted and converted[key] is not None:
                fields.append(f"{key}={converted[key]}")
        return "{" + ", ".join(fields) + "}" if fields else "{}"
    if isinstance(converted, list):
        return f"[{len(converted)} items]"
    return str(converted)


class TradeLogger:
    """Persists strategy events and trade/fill records for analysis.

    Events are written as JSONL for operational debugging. Trade records are
    written as CSV with fill prices, hedge prices, slippage, position, and PnL.
    """

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = local_run_id()
        self.events_path = self.log_dir / f"events-{self.run_id}.jsonl"
        self.events_path.touch(exist_ok=False)
        fieldnames = [
            "run_id",
            "timestamp",
            "action",
            "bitbank_order_id",
            "bitbank_side",
            "bitbank_price",
            "bitbank_amount",
            "bitflyer_side",
            "bitflyer_expected_price",
            "bitflyer_expected_price_base",
            "bitflyer_average_price",
            "bitflyer_child_order_acceptance_id",
            "bitbank_fill_detection_source",
            "bitbank_execution_timestamp",
            "bitbank_fill_notice_timestamp",
            "bitbank_execution_to_notice_ms",
            "bitflyer_hedge_request_timestamp",
            "bitflyer_hedge_acceptance_timestamp",
            "bitflyer_execution_timestamp",
            "bitflyer_hedge_confirmation_timestamp",
            "notice_to_hedge_request_ms",
            "hedge_request_to_acceptance_ms",
            "hedge_request_to_execution_ms",
            "hedge_execution_to_confirmation_ms",
            "notice_to_hedge_execution_ms",
            "slippage_jpy",
            "cashflow_jpy",
            "position",
            "bitbank_position",
            "bitflyer_position",
            "unhedged_position",
            "realized_pnl_jpy",
            "bitbank_fill_pnl_jpy",
            "bitbank_realized_pnl_jpy",
            "bitbank_open_cost_jpy",
            "bitbank_cost_basis_ready",
            "bitflyer_fill_pnl_jpy",
            "bitflyer_realized_pnl_jpy",
            "bitflyer_open_cost_jpy",
            "bitflyer_cost_basis_ready",
            "dry_run",
            "hedge_enabled",
            "hedge_executed",
        ]
        self.trades_path = self.log_dir / f"trades-{self.run_id}.csv"
        self._csv_file = self.trades_path.open("x", newline="")
        self._csv = csv.DictWriter(self._csv_file, fieldnames=fieldnames)
        self._csv.writeheader()
        self._csv_file.flush()
        self.quotes_path = self.log_dir / f"quotes-{self.run_id}.csv"
        self._quotes_file: IO[str] | None = None

    def close(self) -> None:
        self._csv_file.close()
        if self._quotes_file is not None:
            self._quotes_file.close()

    def event(self, event_type: str, **payload: object) -> None:
        row = {"run_id": self.run_id, "timestamp": jst_iso(), "event": event_type, **payload}
        with self.events_path.open("a") as f:
            f.write(json.dumps(decimal_to_json(row), separators=(",", ":")) + "\n")

    def trade(self, **payload: object) -> None:
        self._csv.writerow(decimal_to_json_dict({"run_id": self.run_id, **payload}))
        self._csv_file.flush()

    def quote(
        self,
        *,
        timestamp: float,
        exchange: str,
        best_bid: Decimal | None,
        best_ask: Decimal | None,
        bid_vwap: Decimal | None,
        ask_vwap: Decimal | None,
    ) -> None:
        if self._quotes_file is None:
            self._quotes_file = self.quotes_path.open("x", newline="")
            self._quotes_file.write(
                "timestamp,exchange,best_bid,best_ask,bid_vwap,ask_vwap\n"
            )
        self._quotes_file.write(
            f"{timestamp:.3f},{exchange},{_quote_price(best_bid)},"
            f"{_quote_price(best_ask)},{_quote_vwap(bid_vwap)},{_quote_vwap(ask_vwap)}\n"
        )
        self._quotes_file.flush()


def _quote_price(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _quote_vwap(value: Decimal | None) -> str:
    if value is None:
        return ""
    return f"{value:.3f}"


class Broadcaster:
    """Fan-out helper for pushing realtime snapshots to web clients.

    The web app registers websocket clients here, and the publish loop sends the
    latest serialized bot state to each connected browser.
    """

    def __init__(self) -> None:
        self._clients: set[ServerConnection] = set()

    async def add(self, ws: ServerConnection) -> None:
        self._clients.add(ws)

    async def remove(self, ws: ServerConnection) -> None:
        self._clients.discard(ws)

    async def publish(self, payload: dict[str, object]) -> None:
        if not self._clients:
            return
        message = json.dumps(decimal_to_json(payload), separators=(",", ":"))
        stale = []
        for ws in list(self._clients):
            try:
                await ws.send(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self._clients.discard(ws)
