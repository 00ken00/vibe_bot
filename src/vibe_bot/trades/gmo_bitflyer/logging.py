from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from vibe_bot.trades.bitbank_bitflyer.logging import event_summary
from vibe_bot.trades.bitbank_bitflyer.utils import decimal_to_json
from vibe_bot.trades.bitbank_bitflyer.utils import decimal_to_json_dict
from vibe_bot.trades.bitbank_bitflyer.utils import jst_iso
from vibe_bot.trades.bitbank_bitflyer.utils import local_date_stamp


class TradeLogger:
    """JSONL event log plus CSV trade log for the GMO / bitFlyer bot."""

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stamp = local_date_stamp()
        self.events_path = self.log_dir / f"events-{stamp}.jsonl"
        self.trades_path = self.log_dir / f"trades-{stamp}.csv"
        self.fieldnames = [
            "timestamp",
            "action",
            "stage_index",
            "amount",
            "trigger_price",
            "executable_spread",
            "trend_spread",
            "required_extra_edge",
            "gmo_side",
            "gmo_expected_price",
            "gmo_average_price",
            "gmo_order_id",
            "bitflyer_side",
            "bitflyer_expected_price",
            "bitflyer_average_price",
            "bitflyer_acceptance_id",
            "cashflow_jpy",
            "position",
            "gmo_position",
            "bitflyer_position",
            "unhedged_position",
            "realized_pnl_jpy",
            "dry_run",
        ]
        if self.trades_path.exists() and self.trades_path.stat().st_size > 0:
            with self.trades_path.open(newline="") as f:
                existing_header = next(csv.reader(f), [])
            if existing_header != self.fieldnames:
                suffix = time.strftime("%H%M%S")
                self.trades_path = self.log_dir / f"trades-{stamp}-{suffix}.csv"
        self._csv_file = self.trades_path.open("a", newline="")
        self._csv = csv.DictWriter(self._csv_file, fieldnames=self.fieldnames)
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


__all__ = ["TradeLogger", "event_summary"]
