import csv
import html
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path


BASE = Path("logs/trades/bitbank_bitflyer_arbitrage")
RUN_ID = "20260602-225309-f78b1576"
EVENTS_PATH = BASE / f"events-{RUN_ID}.jsonl"
TRADES_PATH = BASE / f"trades-{RUN_ID}.csv"
OUT_PATH = BASE / f"maker-to-hedge-slippage-{RUN_ID}.html"


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def dec(value: str | None) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def load_maker_created_times() -> dict[str, str]:
    maker_created: dict[str, str] = {}
    with EVENTS_PATH.open() as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "maker_placed":
                continue
            maker = event.get("maker") or {}
            order_id = str(maker.get("order_id") or "")
            if order_id:
                maker_created.setdefault(order_id, event.get("timestamp", ""))
    return maker_created


def build_points(maker_created: dict[str, str]) -> tuple[list[dict[str, object]], int]:
    points: list[dict[str, object]] = []
    missing_created = 0
    with TRADES_PATH.open() as f:
        for row in csv.DictReader(f):
            slippage = dec(row.get("slippage_jpy"))
            bitflyer_execution = parse_dt(row.get("bitflyer_execution_timestamp"))
            order_id = str(row.get("bitbank_order_id") or "")
            created_text = maker_created.get(order_id)
            created = parse_dt(created_text)
            if slippage is None or bitflyer_execution is None:
                continue
            if created is None:
                missing_created += 1
                continue
            elapsed = (bitflyer_execution - created).total_seconds()
            if elapsed < 0:
                continue
            points.append(
                {
                    "x": elapsed,
                    "y": float(slippage),
                    "side": row.get("bitflyer_side") or "unknown",
                    "custom": [
                        row.get("timestamp"),
                        created_text,
                        row.get("bitflyer_execution_timestamp"),
                        order_id,
                        row.get("action"),
                        row.get("bitflyer_side"),
                        row.get("bitbank_amount"),
                        row.get("bitbank_price"),
                        row.get("bitflyer_expected_price"),
                        row.get("bitflyer_average_price"),
                        row.get("cashflow_jpy"),
                        row.get("bitbank_fill_detection_source"),
                    ],
                }
            )
    return points, missing_created


def linear_regression(points: list[dict[str, object]]) -> dict[str, float] | None:
    if len(points) < 2:
        return None
    xs = [float(p["x"]) for p in points]
    ys = [float(p["y"]) for p in points]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    r_squared = 1 - ss_res / ss_tot if ss_tot else 0
    return {"slope": slope, "intercept": intercept, "r_squared": r_squared}


def build_traces(
    points: list[dict[str, object]], regression: dict[str, float] | None
) -> list[dict[str, object]]:
    traces: list[dict[str, object]] = []
    colors = {"SELL": "#2563eb", "BUY": "#dc2626", "unknown": "#667085"}
    for side in ["SELL", "BUY", "unknown"]:
        group = [p for p in points if p["side"] == side]
        if not group:
            continue
        traces.append(
            {
                "type": "scatter",
                "mode": "markers",
                "name": f"bitFlyer {side}",
                "x": [p["x"] for p in group],
                "y": [p["y"] for p in group],
                "customdata": [p["custom"] for p in group],
                "marker": {"size": 9, "opacity": 0.82, "color": colors[side]},
                "hovertemplate": (
                    "Maker to hedge fill: %{x:.3f} s<br>"
                    "Slippage: %{y:,.0f} JPY/BTC<br>"
                    "trade timestamp: %{customdata[0]}<br>"
                    "maker created: %{customdata[1]}<br>"
                    "bitFlyer filled: %{customdata[2]}<br>"
                    "bitbank order: %{customdata[3]}<br>"
                    "action / hedge: %{customdata[4]} / %{customdata[5]}<br>"
                    "amount: %{customdata[6]} BTC<br>"
                    "bitbank price: %{customdata[7]}<br>"
                    "expected bf: %{customdata[8]}<br>"
                    "actual bf: %{customdata[9]}<br>"
                    "cashflow: %{customdata[10]} JPY<br>"
                    "fill source: %{customdata[11]}"
                    "<extra></extra>"
                ),
            }
        )
    if regression and points:
        min_x = min(float(p["x"]) for p in points)
        max_x = max(float(p["x"]) for p in points)
        slope = regression["slope"]
        intercept = regression["intercept"]
        traces.append(
            {
                "type": "scatter",
                "mode": "lines",
                "name": (
                    f"OLS slope {slope:,.1f} JPY/BTC/s, "
                    f"R2 {regression['r_squared']:.3f}"
                ),
                "x": [min_x, max_x],
                "y": [slope * min_x + intercept, slope * max_x + intercept],
                "line": {"color": "#111827", "width": 2},
                "hovertemplate": (
                    "Regression line<br>"
                    f"slope: {slope:,.3f} JPY/BTC/s<br>"
                    f"intercept: {intercept:,.3f} JPY/BTC<br>"
                    f"R2: {regression['r_squared']:.4f}<extra></extra>"
                ),
            }
        )
    return traces


def build_summary(
    points: list[dict[str, object]],
    missing_created: int,
    regression: dict[str, float] | None,
) -> dict[str, object]:
    if not points:
        return {"count": 0, "missing_created": missing_created}
    xs = sorted(float(p["x"]) for p in points)
    ys = sorted(float(p["y"]) for p in points)
    summary: dict[str, object] = {
        "count": len(points),
        "missing_created": missing_created,
        "elapsed_min": min(xs),
        "elapsed_median": xs[len(xs) // 2],
        "elapsed_max": max(xs),
        "slippage_min": min(ys),
        "slippage_median": ys[len(ys) // 2],
        "slippage_max": max(ys),
    }
    if regression:
        summary["slope_jpy_per_btc_per_sec"] = regression["slope"]
        summary["intercept_jpy_per_btc"] = regression["intercept"]
        summary["r_squared"] = regression["r_squared"]
    return summary


def write_html(traces: list[dict[str, object]], summary: dict[str, object]) -> None:
    layout = {
        "title": {
            "text": (
                "bitbank Maker Creation to bitFlyer Hedge Fill vs Slippage"
                f"<br><sup>{RUN_ID}</sup>"
            )
        },
        "template": "plotly_white",
        "xaxis": {
            "title": {
                "text": (
                    "Time from bitbank maker order created to bitFlyer hedge "
                    "filled (seconds)"
                )
            }
        },
        "yaxis": {"title": {"text": "slippage_jpy (JPY/BTC; positive = adverse)"}},
        "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02},
        "margin": {"l": 85, "r": 28, "t": 95, "b": 86},
        "shapes": [
            {
                "type": "line",
                "xref": "paper",
                "x0": 0,
                "x1": 1,
                "yref": "y",
                "y0": 0,
                "y1": 0,
                "line": {"color": "#98a2b3", "dash": "dot", "width": 1},
            }
        ],
        "annotations": [
            {
                "xref": "paper",
                "yref": "paper",
                "x": 0,
                "y": -0.18,
                "showarrow": False,
                "align": "left",
                "font": {"size": 12, "color": "#667085"},
                "text": (
                    "Positive slippage is adverse. X uses maker_placed timestamp "
                    "to bitflyer_execution_timestamp. Rows without logged "
                    "slippage are excluded."
                ),
            }
        ],
    }
    summary_text = " | ".join(
        f"{key}: {value:.3f}" if isinstance(value, float) else f"{key}: {value}"
        for key, value in summary.items()
    )
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Maker to Hedge Slippage {html.escape(RUN_ID)}</title>
  <script charset="utf-8" src="https://cdn.plot.ly/plotly-3.5.0.min.js" integrity="sha256-fHbNLP+GlIXN+efbQec78UkemUz3NJp7UmfGxC1tNxs=" crossorigin="anonymous"></script>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    #chart {{ width: 100vw; height: calc(100vh - 34px); }}
    #summary {{ height: 34px; padding: 8px 14px; box-sizing: border-box; color: #475467; font-size: 12px; border-top: 1px solid #eaecf0; }}
  </style>
</head>
<body>
  <div id="chart"></div>
  <div id="summary">{html.escape(summary_text)}</div>
  <script>
    const data = {json.dumps(traces)};
    const layout = {json.dumps(layout)};
    Plotly.newPlot("chart", data, layout, {{responsive: true}});
  </script>
</body>
</html>
"""
    OUT_PATH.write_text(page)


def main() -> None:
    points, missing_created = build_points(load_maker_created_times())
    regression = linear_regression(points)
    summary = build_summary(points, missing_created, regression)
    write_html(build_traces(points, regression), summary)
    print(OUT_PATH)
    print(summary)


if __name__ == "__main__":
    main()
