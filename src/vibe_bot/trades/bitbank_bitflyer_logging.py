from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from websockets.asyncio.server import ServerConnection

from vibe_bot.trades.bitbank_bitflyer_utils import decimal_to_json
from vibe_bot.trades.bitbank_bitflyer_utils import decimal_to_json_dict
from vibe_bot.trades.bitbank_bitflyer_utils import jst_iso
from vibe_bot.trades.bitbank_bitflyer_utils import local_date_stamp


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
        stamp = local_date_stamp()
        self.events_path = self.log_dir / f"events-{stamp}.jsonl"
        fieldnames = [
            "timestamp",
            "action",
            "bitbank_order_id",
            "bitbank_side",
            "bitbank_price",
            "bitbank_amount",
            "bitflyer_side",
            "bitflyer_expected_price",
            "bitflyer_average_price",
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
        self.trades_path = self.log_dir / f"trades-{stamp}.csv"
        if self.trades_path.exists() and self.trades_path.stat().st_size > 0:
            with self.trades_path.open(newline="") as f:
                existing_header = next(csv.reader(f), [])
            if existing_header != fieldnames:
                suffix = time.strftime("%H%M%S")
                self.trades_path = self.log_dir / f"trades-{stamp}-{suffix}.csv"
        self._csv_file = self.trades_path.open("a", newline="")
        self._csv = csv.DictWriter(self._csv_file, fieldnames=fieldnames)
        if self.trades_path.stat().st_size == 0:
            self._csv.writeheader()
            self._csv_file.flush()

    def close(self) -> None:
        self._csv_file.close()

    def event(self, event_type: str, **payload: object) -> None:
        row = {"timestamp": jst_iso(), "event": event_type, **payload}
        with self.events_path.open("a") as f:
            f.write(json.dumps(decimal_to_json(row), separators=(",", ":")) + "\n")

    def trade(self, **payload: object) -> None:
        self._csv.writerow(decimal_to_json_dict(payload))
        self._csv_file.flush()


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
