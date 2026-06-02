from __future__ import annotations

import csv
import json
from pathlib import Path

from vibe_bot.trades.bitbank_bitflyer.logging import event_summary
from vibe_bot.trades.bitbank_bitflyer.utils import decimal_to_json
from vibe_bot.trades.bitbank_bitflyer.utils import decimal_to_json_dict
from vibe_bot.trades.bitbank_bitflyer.utils import jst_iso
from vibe_bot.trades.bitbank_bitflyer.utils import local_run_id


class TradeLogger:
    """JSONL event log plus CSV trade log for the Coincheck / bitFlyer bot."""

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = local_run_id()
        self.events_path = self.log_dir / f"events-{self.run_id}.jsonl"
        self.trades_path = self.log_dir / f"trades-{self.run_id}.csv"
        self.events_path.touch(exist_ok=False)
        self.fieldnames = [
            "run_id",
            "timestamp",
            "action",
            "stage_index",
            "amount",
            "trigger_price",
            "executable_spread",
            "trend_spread",
            "required_extra_edge",
            "coincheck_side",
            "coincheck_expected_price",
            "coincheck_average_price",
            "coincheck_order_id",
            "bitflyer_side",
            "bitflyer_expected_price",
            "bitflyer_average_price",
            "bitflyer_acceptance_id",
            "cashflow_jpy",
            "position",
            "coincheck_position",
            "bitflyer_position",
            "unhedged_position",
            "realized_pnl_jpy",
            "dry_run",
            "hedge_enabled",
            "hedge_executed",
        ]
        self._csv_file = self.trades_path.open("x", newline="")
        self._csv = csv.DictWriter(self._csv_file, fieldnames=self.fieldnames)
        self._csv.writeheader()
        self._csv_file.flush()

    def close(self) -> None:
        self._csv_file.close()

    def event(self, event_type: str, **payload: object) -> None:
        row = {"run_id": self.run_id, "timestamp": jst_iso(), "event": event_type, **payload}
        with self.events_path.open("a") as f:
            f.write(json.dumps(decimal_to_json(row), separators=(",", ":")) + "\n")

    def trade(self, **payload: object) -> None:
        self._csv.writerow(decimal_to_json_dict({"run_id": self.run_id, **payload}))
        self._csv_file.flush()


__all__ = ["TradeLogger", "event_summary"]
