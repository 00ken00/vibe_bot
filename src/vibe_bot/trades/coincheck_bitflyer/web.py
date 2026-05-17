from __future__ import annotations

import asyncio
import html
import logging
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import websockets
from websockets.asyncio.server import ServerConnection

if TYPE_CHECKING:
    from vibe_bot.trades.bitbank_bitflyer.logging import Broadcaster
    from vibe_bot.trades.coincheck_bitflyer.config import BotConfig
    from vibe_bot.trades.coincheck_bitflyer.models import BotState

LOGGER = logging.getLogger("vibe_bot.trades.coincheck_bitflyer.web")


class WebApp:
    def __init__(
        self,
        config: BotConfig,
        state: BotState,
        broadcaster: Broadcaster,
    ) -> None:
        self.config = config
        self.state = state
        self.broadcaster = broadcaster
        self._httpd: ThreadingHTTPServer | None = None

    def start_http(self) -> None:
        html_bytes = self._html().encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in ("/", "/index.html"):
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)

            def log_message(self, format: str, *args: object) -> None:
                LOGGER.debug("web: " + format, *args)

        self._httpd = ThreadingHTTPServer(
            (self.config.web_host, self.config.web_port), Handler
        )
        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()

    def stop_http(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    async def run_ws(self, stop: asyncio.Event) -> None:
        async def handler(ws: ServerConnection) -> None:
            await self.broadcaster.add(ws)
            try:
                await ws.wait_closed()
            finally:
                await self.broadcaster.remove(ws)

        async with websockets.serve(handler, self.config.web_host, self.config.ws_port):
            while not stop.is_set():
                await asyncio.sleep(0.2)

    async def publish_loop(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.broadcaster.publish(self.snapshot())
            await asyncio.sleep(self.config.monitor_update_interval)

    def snapshot(self) -> dict[str, object]:
        quote = self.state.quote
        condition = self.state.last_trade_condition
        uptime = time.time() - self.state.started_at
        return {
            "type": "snapshot",
            "timestamp": time.time(),
            "uptime_sec": round(uptime, 1),
            "dry_run": self.config.dry_run,
            "threshold_jpy": self.config.threshold_jpy,
            "threshold_offset_jpy": self.config.threshold_offset_jpy,
            "position": self.state.position,
            "coincheck_position": self.state.coincheck_position,
            "bitflyer_position": self.state.bitflyer_position,
            "unhedged_position": self.state.unhedged_position,
            "realized_pnl_jpy": self.state.realized_pnl_jpy,
            "filled_base": self.state.filled_base,
            "trade_count": self.state.trade_count,
            "coincheck_order_success_rate": self.state.coincheck_order_success_rate,
            "coincheck_order_metric_count": len(self.state.coincheck_order_metrics),
            "bitflyer_average_slippage_jpy_per_btc": (
                self.state.bitflyer_average_slippage_jpy_per_btc
            ),
            "bitflyer_order_metric_count": len(self.state.bitflyer_order_metrics),
            "last_action": self.state.last_action.value,
            "stage_status": self.state.stage_status,
            "filter": self.state.filter,
            "trade_condition": condition,
            "action_history": [
                {
                    "timestamp": entry.timestamp,
                    "action": entry.action.value,
                    "description": entry.description,
                }
                for entry in reversed(self.state.action_history[-100:])
            ],
            "last_error": self.state.last_error,
            "quote": {
                "coincheck_bid": quote.coincheck_bid,
                "coincheck_ask": quote.coincheck_ask,
                "coincheck_bid_vwap": quote.coincheck_bid_vwap,
                "coincheck_ask_vwap": quote.coincheck_ask_vwap,
                "bitflyer_bid": quote.bitflyer_bid,
                "bitflyer_ask": quote.bitflyer_ask,
                "bitflyer_bid_vwap": quote.bitflyer_bid_vwap,
                "bitflyer_ask_vwap": quote.bitflyer_ask_vwap,
                "buy_price": quote.buy_price,
                "sell_price": quote.sell_price,
                "mid_spread": quote.mid_spread,
                "timestamp": quote.timestamp,
            },
        }

    def _parameters_html(self) -> str:
        rows = []
        for name, value in self._config_display_items():
            rows.append(
                '<div class="param">'
                f"<span>{html.escape(name)}</span>"
                f"<strong>{html.escape(str(value))}</strong>"
                "</div>"
            )
        return "\n".join(rows)

    def _config_display_items(self) -> list[tuple[str, object]]:
        params = list(asdict(self.config).items())
        for name, attr in vars(type(self.config)).items():
            if isinstance(attr, property):
                params.append((name, getattr(self.config, name)))
        return params

    def _html(self) -> str:
        parameters_html = self._parameters_html()
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Coincheck / bitFlyer Arbitrage</title>
<style>
:root {{
  color-scheme: light;
  --bg: #f6f7f9;
  --panel: #ffffff;
  --ink: #1d2430;
  --muted: #667085;
  --line: #d9dee8;
  --buy: #1464d2;
  --sell: #c2410c;
  --trend: #0f9f6e;
  --warn: #b42318;
  --ok: #087443;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--ink);
}}
header {{
  min-height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 18px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}}
h1 {{ font-size: 17px; margin: 0; letter-spacing: 0; }}
.status {{ display: flex; gap: 10px; align-items: center; color: var(--muted); flex-wrap: wrap; }}
.dot {{ width: 9px; height: 9px; border-radius: 999px; background: var(--warn); }}
.dot.ok {{ background: var(--ok); }}
main {{ padding: 16px; display: grid; gap: 14px; }}
.metrics, .gate-grid, .table {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}}
.metric, .gate {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  min-height: 70px;
}}
.label {{ color: var(--muted); font-size: 12px; }}
.value {{
  margin-top: 5px;
  font-size: 20px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.small-value {{
  margin-top: 5px;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
}}
.gate.pass {{ border-color: #75c69a; }}
.gate.block {{ border-color: #f0a98e; }}
.gate.pending {{ border-color: var(--line); }}
.gate .state {{ margin-top: 5px; font-size: 15px; font-weight: 700; }}
.gate.pass .state {{ color: var(--ok); }}
.gate.block .state {{ color: var(--warn); }}
.chart-wrap {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
}}
canvas {{ width: 100%; height: 430px; display: block; }}
.legend {{ display: flex; gap: 18px; color: var(--muted); margin-bottom: 8px; flex-wrap: wrap; }}
.key {{ display: inline-flex; align-items: center; gap: 6px; }}
.swatch {{ width: 20px; height: 3px; border-radius: 2px; }}
.history, .params {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}}
.section-title {{
  color: var(--ink);
  font-weight: 600;
  padding: 11px 12px;
  border-bottom: 1px solid var(--line);
}}
.history-scroll {{ max-height: 360px; overflow-y: auto; }}
table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }}
th {{ color: var(--muted); font-size: 12px; font-weight: 600; }}
td {{ font-size: 13px; overflow-wrap: anywhere; }}
.history-time {{ width: 150px; color: var(--muted); font-variant-numeric: tabular-nums; }}
.history-action {{ width: 170px; font-weight: 600; }}
.empty {{ color: var(--muted); padding: 10px 12px; }}
.error {{ color: var(--warn); min-height: 20px; }}
.param-grid {{
  display: grid;
  grid-template-columns: repeat(3, minmax(180px, 1fr));
  gap: 1px;
  background: var(--line);
}}
.param {{
  min-height: 48px;
  background: var(--panel);
  padding: 8px 10px;
  display: grid;
  gap: 3px;
}}
.param span {{ color: var(--muted); font-size: 12px; }}
.param strong {{ font-size: 14px; font-weight: 600; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }}
@media (max-width: 760px) {{
  header {{ align-items: flex-start; flex-direction: column; }}
  main {{ padding: 10px; }}
  .param-grid {{ grid-template-columns: 1fr; }}
  canvas {{ height: 340px; }}
  .history-time {{ width: 110px; }}
  .history-action {{ width: 130px; }}
}}
</style>
</head>
<body>
<header>
  <h1>Coincheck / bitFlyer Arbitrage</h1>
  <div class="status"><span id="dot" class="dot"></span><span id="conn">Disconnected</span><span id="mode"></span></div>
</header>
<main>
  <section class="metrics">
    <div class="metric"><div class="label">BUY Price</div><div id="buyPrice" class="value">--</div></div>
    <div class="metric"><div class="label">SELL Price</div><div id="sellPrice" class="value">--</div></div>
    <div class="metric"><div class="label">Mid Spread</div><div id="midSpread" class="value">--</div></div>
    <div class="metric"><div class="label">Position BTC</div><div id="position" class="value">--</div></div>
    <div class="metric"><div class="label">Realized PnL JPY</div><div id="pnl" class="value">--</div></div>
    <div class="metric"><div class="label">Filled BTC</div><div id="filled" class="value">--</div></div>
    <div class="metric"><div class="label">Trades</div><div id="trades" class="value">--</div></div>
    <div class="metric"><div class="label">Coincheck Order Success</div><div id="coincheckOrderSuccess" class="value">--</div><div id="coincheckOrderSuccessDetail" class="label">recent 0 / 20</div></div>
    <div class="metric"><div class="label">bitFlyer Avg Slippage/BTC</div><div id="bitflyerSlippage" class="value">--</div><div id="bitflyerSlippageDetail" class="label">recent 0 / 20</div></div>
    <div class="metric"><div class="label">Action</div><div id="action" class="value">--</div></div>
  </section>

  <section class="gate-grid">
    <div id="gateDecision" class="gate pending"><div class="label">Gate Decision</div><div id="gateDecisionState" class="state">--</div><div id="gateDecisionDetail" class="small-value">--</div></div>
    <div id="gateStage" class="gate pending"><div class="label">Stage Trigger</div><div id="gateStageState" class="state">--</div><div id="gateStageDetail" class="small-value">--</div></div>
    <div id="gateTrend" class="gate pending"><div class="label">EMA Trend</div><div id="gateTrendState" class="state">--</div><div id="gateTrendDetail" class="small-value">--</div></div>
    <div id="gateNoise" class="gate pending"><div class="label">Noise Buffer</div><div id="gateNoiseState" class="state">--</div><div id="gateNoiseDetail" class="small-value">--</div></div>
    <div id="gatePersistence" class="gate pending"><div class="label">Persistence</div><div id="gatePersistenceState" class="state">--</div><div id="gatePersistenceDetail" class="small-value">--</div></div>
    <div id="gateSlippage" class="gate pending"><div class="label">Order Types</div><div id="gateSlippageState" class="state">--</div><div id="gateSlippageDetail" class="small-value">--</div></div>
  </section>

  <section class="chart-wrap">
    <div class="legend">
      <span class="key"><span class="swatch" style="background:var(--buy)"></span>BUY price</span>
      <span class="key"><span class="swatch" style="background:var(--sell)"></span>SELL price</span>
      <span class="key"><span class="swatch" style="background:var(--trend)"></span>EMA trend</span>
      <span class="key">threshold: {self.config.threshold_jpy} JPY</span>
      <span class="key">offset: {self.config.threshold_offset_jpy} JPY</span>
    </div>
    <canvas id="chart" width="1400" height="520"></canvas>
  </section>

  <section class="table">
    <div class="metric"><div class="label">Coincheck Top Bid / Ask</div><div id="coincheckTop" class="small-value">--</div></div>
    <div class="metric"><div class="label">Coincheck VWAP Sell / Buy</div><div id="coincheckDepth" class="small-value">--</div></div>
    <div class="metric"><div class="label">bitFlyer Top Bid / Ask</div><div id="bfTop" class="small-value">--</div></div>
    <div class="metric"><div class="label">bitFlyer VWAP Sell / Buy</div><div id="bfDepth" class="small-value">--</div></div>
    <div class="metric"><div class="label">Coincheck / bitFlyer Pos</div><div id="exchangePositions" class="small-value">--</div></div>
    <div class="metric"><div class="label">Unhedged BTC</div><div id="unhedged" class="small-value">--</div></div>
    <div class="metric"><div class="label">Filter Samples</div><div id="filterSamples" class="small-value">--</div></div>
    <div class="metric"><div class="label">Uptime</div><div id="uptime" class="small-value">--</div></div>
  </section>

  <section class="table">
    <div class="metric"><div class="label">Current Stage</div><div id="stageCurrent" class="small-value">--</div></div>
    <div class="metric"><div class="label">Long Open / Close</div><div id="stageLong" class="small-value">--</div></div>
    <div class="metric"><div class="label">Short Open / Close</div><div id="stageShort" class="small-value">--</div></div>
    <div class="metric"><div class="label">Next Open / Close BTC</div><div id="stageAmounts" class="small-value">--</div></div>
  </section>

  <section class="history">
    <div class="section-title">Bot Action History</div>
    <table><thead><tr><th class="history-time">Time</th><th class="history-action">Action</th><th>Description</th></tr></thead></table>
    <div class="history-scroll">
      <table><tbody id="actionHistory"><tr><td colspan="3" class="empty">--</td></tr></tbody></table>
    </div>
  </section>

  <div id="error" class="error"></div>

  <section class="params">
    <div class="section-title">Parameters</div>
    <div class="param-grid">
      {parameters_html}
    </div>
  </section>
</main>
<script>
const wsUrl = `${{window.location.protocol === "https:" ? "wss" : "ws"}}://${{window.location.hostname}}:{self.config.ws_port}`;
const chartWindowMs = 60_000;
const points = [];
let latest = null;
const fmt = new Intl.NumberFormat("ja-JP", {{ maximumFractionDigits: 2 }});
const btcFmt = new Intl.NumberFormat("en-US", {{ minimumFractionDigits: 4, maximumFractionDigits: 8 }});
const el = id => document.getElementById(id);
function num(v) {{ return v == null ? null : Number(v); }}
function finite(v) {{ return Number.isFinite(v); }}
function money(v) {{ const n = num(v); return finite(n) ? fmt.format(n) : "--"; }}
function btc(v) {{ const n = num(v); return finite(n) ? btcFmt.format(n) : "--"; }}
function pct(v) {{ const n = num(v); return finite(n) ? `${{fmt.format(n * 100)}}%` : "--"; }}
function setText(id, value) {{ el(id).textContent = value; }}
function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, c => ({{
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }}[c]));
}}
function connect() {{
  const ws = new WebSocket(wsUrl);
  ws.onopen = () => {{ el("dot").classList.add("ok"); setText("conn", "Connected"); }};
  ws.onclose = () => {{
    el("dot").classList.remove("ok");
    setText("conn", "Disconnected");
    setTimeout(connect, 1000);
  }};
  ws.onmessage = event => {{
    latest = JSON.parse(event.data);
    const q = latest.quote || {{}};
    const f = latest.filter || {{}};
    points.push({{
      t: latest.timestamp * 1000,
      buy: num(q.buy_price),
      sell: num(q.sell_price),
      trend: num(f.trend_spread)
    }});
    const cutoff = latest.timestamp * 1000 - chartWindowMs;
    while (points.length && points[0].t < cutoff) points.shift();
    render();
    draw();
  }};
}}
function render() {{
  const q = latest.quote || {{}};
  const f = latest.filter || {{}};
  setText("mode", latest.dry_run ? "DRY RUN" : "LIVE");
  setText("buyPrice", money(q.buy_price));
  setText("sellPrice", money(q.sell_price));
  setText("midSpread", money(q.mid_spread));
  setText("position", btc(latest.position));
  setText("pnl", money(latest.realized_pnl_jpy));
  setText("filled", btc(latest.filled_base));
  setText("trades", latest.trade_count ?? "--");
  setText("coincheckOrderSuccess", pct(latest.coincheck_order_success_rate));
  setText("coincheckOrderSuccessDetail", `recent ${{latest.coincheck_order_metric_count ?? 0}} / 20`);
  setText("bitflyerSlippage", money(latest.bitflyer_average_slippage_jpy_per_btc));
  setText("bitflyerSlippageDetail", `recent ${{latest.bitflyer_order_metric_count ?? 0}} / 20`);
  setText("action", latest.last_action || "--");
  setText("coincheckTop", `${{money(q.coincheck_bid)}} / ${{money(q.coincheck_ask)}}`);
  setText("coincheckDepth", `${{money(q.coincheck_bid_vwap)}} / ${{money(q.coincheck_ask_vwap)}}`);
  setText("bfTop", `${{money(q.bitflyer_bid)}} / ${{money(q.bitflyer_ask)}}`);
  setText("bfDepth", `${{money(q.bitflyer_bid_vwap)}} / ${{money(q.bitflyer_ask_vwap)}}`);
  setText("exchangePositions", `${{btc(latest.coincheck_position)}} / ${{btc(latest.bitflyer_position)}}`);
  setText("unhedged", btc(latest.unhedged_position));
  setText("filterSamples", `${{f.samples ?? 0}} / {self.config.min_filter_samples}`);
  setText("uptime", `${{Math.round(num(latest.uptime_sec || 0))}}s`);
  renderStage();
  renderGates();
  renderActionHistory();
  setText("error", latest.last_error || "");
}}
function renderStage() {{
  const s = latest.stage_status || {{}};
  setText("stageCurrent", `${{s.current_stage ?? "--"}} / ${{s.max_stages ?? "--"}}`);
  setText("stageLong", `${{money(s.long_open_trigger)}} / ${{money(s.long_close_trigger)}}`);
  setText("stageShort", `${{money(s.short_open_trigger)}} / ${{money(s.short_close_trigger)}}`);
  setText("stageAmounts", `${{btc(s.next_open_amount)}} / ${{btc(s.close_amount)}}`);
}}
function setGate(id, status, detail) {{
  const box = el(id);
  box.classList.remove("pass", "block", "pending");
  box.classList.add(status);
  setText(id + "State", status === "pass" ? "PASS" : status === "block" ? "BLOCK" : "PENDING");
  setText(id + "Detail", detail || "--");
}}
function renderGates() {{
  const c = latest.trade_condition;
  const f = latest.filter || {{}};
  if (!c) {{
    setGate("gateDecision", "pending", "waiting for first condition check");
    setGate("gateStage", "pending", "--");
    setGate("gateTrend", "pending", "--");
    setGate("gateNoise", "pending", "--");
    setGate("gatePersistence", "pending", "--");
    setGate("gateSlippage", "pending", "--");
    return;
  }}
  const target = c.target;
  const reason = c.reason || "--";
  setGate("gateDecision", c.passed ? "pass" : "block", reason);
  setGate("gateStage", target ? "pass" : "block",
    target ? `${{target.action}} stage ${{target.stage_index}} trigger ${{money(target.trigger_price)}}` : "no candidate");
  if (reason === "filter_warming_up") {{
    setGate("gateTrend", "pending", `samples ${{f.samples ?? 0}} / {self.config.min_filter_samples}`);
    setGate("gateNoise", "pending", `samples ${{f.samples ?? 0}} / {self.config.min_filter_samples}`);
    setGate("gatePersistence", "pending", "--");
    setGate("gateSlippage", "pending", "--");
    return;
  }}
  if (target && reason === "trend_disagrees") {{
    setGate("gateTrend", "block", `trend ${{money(f.trend_spread)}} vs trigger ${{money(target.trigger_price)}}`);
    setGate("gateNoise", "pending", "--");
    setGate("gatePersistence", "pending", "--");
    setGate("gateSlippage", "pending", "--");
    return;
  }}
  if (target) {{
    setGate("gateTrend", reason === "trend_disagrees" ? "block" : "pass",
      `trend ${{money(f.trend_spread)}} vs trigger ${{money(target.trigger_price)}}`);
    const edge = target.action === "BUY"
      ? num(target.trigger_price) - num(target.executable_spread)
      : num(target.executable_spread) - num(target.trigger_price);
    setGate("gateNoise", reason === "edge_below_noise_buffer" ? "block" : "pass",
      `edge ${{money(edge)}} / required ${{money(f.required_extra_edge)}} / noise ${{money(f.residual_noise)}}`);
    if (reason === "edge_below_noise_buffer") {{
      setGate("gatePersistence", "pending", "--");
      setGate("gateSlippage", "pending", "--");
      return;
    }}
  }} else {{
    setGate("gateTrend", "pending", `trend ${{money(f.trend_spread)}}`);
    setGate("gateNoise", "pending", `required ${{money(f.required_extra_edge)}}`);
  }}
  if (target) {{
    setGate("gatePersistence", reason === "persistence" ? "block" : "pass",
      reason === "persistence" ? `requires {self.config.persistence_seconds}s` : `requires {self.config.persistence_seconds}s`);
    setGate("gateSlippage", reason === "persistence" ? "pending" : "pass",
      `Coincheck ${{target.coincheck_side}} limit ${{money(target.coincheck_limit_price)}} / bitFlyer ${{target.bitflyer_side}} MARKET IOC`);
  }} else {{
    setGate("gatePersistence", "pending", "--");
    setGate("gateSlippage", "pending", "--");
  }}
}}
function renderActionHistory() {{
  const rows = latest.action_history || [];
  const body = el("actionHistory");
  if (!rows.length) {{
    body.innerHTML = `<tr><td colspan="3" class="empty">--</td></tr>`;
    return;
  }}
  body.innerHTML = rows.slice(0, 100).map(row => {{
    const date = new Date(num(row.timestamp) * 1000);
    const timeText = Number.isFinite(date.getTime()) ? date.toLocaleTimeString() : "--";
    return `<tr><td class="history-time">${{escapeHtml(timeText)}}</td><td class="history-action">${{escapeHtml(row.action || "--")}}</td><td>${{escapeHtml(row.description || "")}}</td></tr>`;
  }}).join("");
}}
function draw() {{
  const canvas = el("chart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, w, h);
  const now = latest ? latest.timestamp * 1000 : Date.now();
  const windowStart = now - chartWindowMs;
  const visible = points.filter(p => p.t >= windowStart && p.t <= now);
  const values = [];
  visible.forEach(p => {{
    if (finite(p.buy)) values.push(p.buy);
    if (finite(p.sell)) values.push(p.sell);
    if (finite(p.trend)) values.push(p.trend);
  }});
  if (values.length < 2) return;
  let min = Math.min(...values), max = Math.max(...values);
  const pad = Math.max(10, (max - min) * 0.12);
  min -= pad; max += pad;
  const left = 62, right = 18, top = 16, bottom = 34;
  const cw = w - left - right, ch = h - top - bottom;
  const x = t => left + (t - windowStart) * cw / chartWindowMs;
  const y = v => top + (max - v) * ch / (max - min || 1);
  ctx.strokeStyle = "#d9dee8";
  ctx.lineWidth = 1;
  ctx.font = "12px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
  ctx.fillStyle = "#667085";
  for (let i = 0; i <= 5; i++) {{
    const yy = top + i * ch / 5;
    const val = max - i * (max - min) / 5;
    ctx.beginPath(); ctx.moveTo(left, yy); ctx.lineTo(w - right, yy); ctx.stroke();
    ctx.fillText(fmt.format(val), 8, yy + 4);
  }}
  function line(key, color, dash = []) {{
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash(dash);
    ctx.beginPath();
    let moved = false;
    visible.forEach(p => {{
      const v = p[key];
      if (!finite(v)) {{ moved = false; return; }}
      if (!moved) {{ ctx.moveTo(x(p.t), y(v)); moved = true; }}
      else ctx.lineTo(x(p.t), y(v));
    }});
    ctx.stroke();
    ctx.setLineDash([]);
  }}
  line("buy", "#1464d2");
  line("sell", "#c2410c");
  line("trend", "#0f9f6e", [7, 5]);
  const zeroY = y(0);
  if (zeroY >= top && zeroY <= top + ch) {{
    ctx.strokeStyle = "#101828";
    ctx.setLineDash([5, 5]);
    ctx.beginPath(); ctx.moveTo(left, zeroY); ctx.lineTo(w - right, zeroY); ctx.stroke();
    ctx.setLineDash([]);
  }}
}}
connect();
window.addEventListener("resize", draw);
</script>
</body>
</html>"""
