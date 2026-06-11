from __future__ import annotations

import csv
import gzip
import json
import logging
import re
import shutil
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import IO

from websockets.asyncio.server import ServerConnection

from vibe_bot.trades.bitbank_bitflyer.utils import JST
from vibe_bot.trades.bitbank_bitflyer.utils import decimal_to_json
from vibe_bot.trades.bitbank_bitflyer.utils import decimal_to_json_dict
from vibe_bot.trades.bitbank_bitflyer.utils import jst_iso
from vibe_bot.trades.bitbank_bitflyer.utils import local_date_stamp
from vibe_bot.trades.bitbank_bitflyer.utils import local_run_id

LOGGER = logging.getLogger("vibe_bot.trades.bitbank_bitflyer.logging")

QUOTES_RETENTION_DAYS = 7

_QUOTES_DATE_RE = re.compile(r"-(\d{8})\.csv(?:\.gz)?$")


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
        self.quotes_path: Path | None = None
        self._quotes_file: IO[str] | None = None
        self._quotes_day: str | None = None
        self._archive_lock = threading.Lock()
        # Compress quote logs left unfinished by previous runs and prune old ones.
        self._archive_quotes_async()

    def close(self) -> None:
        self._csv_file.close()
        if self._quotes_file is not None:
            self._quotes_file.close()
            self._quotes_file = None
            self._quotes_day = None
            self.quotes_path = None
            self._archive_quotes()

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
        vwap_size: Decimal | None,
        bid_vwap: Decimal | None,
        ask_vwap: Decimal | None,
        base_size: Decimal,
        bid_vwap_base: Decimal | None,
        ask_vwap_base: Decimal | None,
    ) -> None:
        day = local_date_stamp()
        if self._quotes_file is None or day != self._quotes_day:
            if self._quotes_file is not None:
                self._quotes_file.close()
            self._quotes_day = day
            self.quotes_path = self.log_dir / f"quotes-{self.run_id}-{day}.csv"
            self._quotes_file = self.quotes_path.open("x", newline="")
            self._quotes_file.write(
                "timestamp,exchange,best_bid,best_ask,vwap_size,bid_vwap,ask_vwap,"
                "base_size,bid_vwap_base,ask_vwap_base\n"
            )
            self._archive_quotes_async()
        self._quotes_file.write(
            f"{timestamp:.3f},{exchange},{_quote_price(best_bid)},"
            f"{_quote_price(best_ask)},{_quote_price(vwap_size)},"
            f"{_quote_vwap(bid_vwap)},{_quote_vwap(ask_vwap)},"
            f"{_quote_price(base_size)},{_quote_vwap(bid_vwap_base)},"
            f"{_quote_vwap(ask_vwap_base)}\n"
        )
        self._quotes_file.flush()

    def _archive_quotes_async(self) -> None:
        threading.Thread(target=self._archive_quotes, daemon=True).start()

    def _archive_quotes(self) -> None:
        """Gzip finished daily quote logs and drop those past retention."""
        with self._archive_lock:
            self._archive_quotes_locked()

    def _archive_quotes_locked(self) -> None:
        cutoff = (
            datetime.now(JST) - timedelta(days=QUOTES_RETENTION_DAYS)
        ).strftime("%Y%m%d")
        current = self.quotes_path
        for path in sorted(self.log_dir.glob("quotes-*.csv*")):
            match = _QUOTES_DATE_RE.search(path.name)
            if match is None or path == current:
                continue
            try:
                if match.group(1) < cutoff:
                    path.unlink(missing_ok=True)
                elif path.suffix == ".csv":
                    with path.open("rb") as src:
                        with gzip.open(f"{path}.gz", "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    path.unlink()
            except Exception:
                LOGGER.exception("failed to archive quote log %s", path)


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
