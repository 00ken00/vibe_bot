from __future__ import annotations

import asyncio
import html
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import websockets
from websockets.asyncio.server import ServerConnection

if TYPE_CHECKING:
    from vibe_bot.trades.bitbank_bitflyer_arbitrage import (
        BotConfig,
        BotState,
        Broadcaster,
    )

LOGGER = logging.getLogger("vibe_bot.trades.bitbank_bitflyer_web")


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
        html = self._html().encode()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.path not in ("/", "/index.html"):
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

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
            payload = self.snapshot()
            await self.broadcaster.publish(payload)
            await asyncio.sleep(self.config.monitor_update_interval)

    def snapshot(self) -> dict[str, object]:
        quote = self.state.quote
        active = self.state.active_maker
        uptime = time.time() - self.state.started_at
        return {
            "type": "snapshot",
            "timestamp": time.time(),
            "uptime_sec": round(uptime, 1),
            "dry_run": self.config.dry_run,
            "hedge_enabled": self.config.hedge_enabled,
            "threshold_jpy": self.config.threshold_jpy,
            "threshold_offset_jpy": self.config.threshold_offset_jpy,
            "position": self.state.position,
            "realized_pnl_jpy": self.state.realized_pnl_jpy,
            "filled_base": self.state.filled_base,
            "trade_count": self.state.trade_count,
            "last_action": self.state.last_action.value,
            "last_action_description": self.state.last_action.description,
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
                "bitbank_bid": quote.bitbank_bid,
                "bitbank_ask": quote.bitbank_ask,
                "bitflyer_bid": quote.bitflyer_bid,
                "bitflyer_ask": quote.bitflyer_ask,
                "bitbank_bid_vwap": quote.bitbank_bid_vwap,
                "bitbank_ask_vwap": quote.bitbank_ask_vwap,
                "bitflyer_bid_vwap": quote.bitflyer_bid_vwap,
                "bitflyer_ask_vwap": quote.bitflyer_ask_vwap,
                "buy_price": quote.buy_price,
                "sell_price": quote.sell_price,
                "timestamp": quote.timestamp,
            },
            "active_maker": active,
        }

    def _parameters_html(self) -> str:
        params: list[tuple[str, object]] = [
            ("bitbank_pair", self.config.bitbank_pair),
            ("bitflyer_product_code", self.config.bitflyer_product_code),
            ("threshold_jpy", self.config.threshold_jpy),
            ("threshold_offset_jpy", self.config.threshold_offset_jpy),
            ("order_size", self.config.order_size),
            ("max_position", self.config.max_position),
            ("maker_update_interval", self.config.maker_update_interval),
            ("monitor_update_interval", self.config.monitor_update_interval),
            ("tick_size", self.config.tick_size),
            ("min_order_size", self.config.min_order_size),
            ("dry_run", self.config.dry_run),
            ("hedge_enabled", self.config.hedge_enabled),
            ("web_host", self.config.web_host),
            ("web_port", self.config.web_port),
            ("ws_port", self.config.ws_port),
            ("log_dir", self.config.log_dir),
        ]
        rows = []
        for name, value in params:
            safe_name = html.escape(name)
            safe_value = html.escape(str(value))
            rows.append(
                f'<div class="param"><span>{safe_name}</span><strong>{safe_value}</strong></div>'
            )
        return "\n".join(rows)

    def _html(self) -> str:
        parameters_html = self._parameters_html()
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>bitbank / bitFlyer Arbitrage</title>
<style>
:root {{
  color-scheme: light;
  --bg: #f7f8fa;
  --panel: #ffffff;
  --ink: #1d2430;
  --muted: #667085;
  --line: #d9dee8;
  --buy: #1464d2;
  --sell: #c2410c;
  --maker: #0f9f6e;
  --warn: #b42318;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: var(--bg);
  color: var(--ink);
}}
header {{
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 18px;
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}}
h1 {{ font-size: 17px; margin: 0; letter-spacing: 0; }}
.status {{ display: flex; gap: 10px; align-items: center; color: var(--muted); }}
.dot {{ width: 9px; height: 9px; border-radius: 999px; background: var(--warn); }}
.dot.ok {{ background: var(--maker); }}
main {{ padding: 16px; display: grid; gap: 14px; }}
.metrics {{
  display: grid;
  grid-template-columns: repeat(6, minmax(140px, 1fr));
  gap: 10px;
}}
.metric {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  min-height: 70px;
}}
.label {{ color: var(--muted); font-size: 12px; }}
.value {{ margin-top: 5px; font-size: 20px; font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.tooltip-wrap {{ position: relative; display: block; max-width: 100%; }}
.tooltip {{
  visibility: hidden;
  opacity: 0;
  position: absolute;
  z-index: 5;
  right: 0;
  top: calc(100% + 8px);
  width: min(280px, calc(100vw - 32px));
  max-width: calc(100vw - 32px);
  background: #101828;
  color: #ffffff;
  border-radius: 6px;
  padding: 7px 9px;
  font-size: 12px;
  line-height: 1.35;
  white-space: normal;
  overflow-wrap: anywhere;
  box-shadow: 0 8px 20px rgba(16, 24, 40, 0.16);
  transition: opacity 120ms ease;
}}
.tooltip-wrap:hover .tooltip {{ visibility: visible; opacity: 1; }}
.chart-wrap {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
}}
canvas {{ width: 100%; height: 480px; display: block; }}
.legend {{ display: flex; gap: 18px; color: var(--muted); margin-bottom: 8px; flex-wrap: wrap; }}
.key {{ display: inline-flex; align-items: center; gap: 6px; }}
.swatch {{ width: 20px; height: 3px; border-radius: 2px; }}
.swatch.dashed {{
  height: 0;
  border-top: 3px dashed var(--maker);
  background: transparent;
}}
.table {{
  display: grid;
  grid-template-columns: repeat(4, minmax(170px, 1fr));
  gap: 10px;
}}
.history {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}}
.history-title {{
  color: var(--ink);
  font-weight: 600;
  padding: 11px 12px;
  border-bottom: 1px solid var(--line);
}}
.history-table {{
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}}
.history-scroll {{
  max-height: 456px;
  overflow-y: auto;
}}
.history-table th,
.history-table td {{
  text-align: left;
  padding: 8px 10px;
  border-bottom: 1px solid var(--line);
  vertical-align: top;
}}
.history-table th {{
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}}
.history-table td {{
  font-size: 13px;
  overflow-wrap: anywhere;
}}
.history-time {{ width: 150px; color: var(--muted); font-variant-numeric: tabular-nums; }}
.history-action {{ width: 190px; font-weight: 600; }}
.history-empty {{ color: var(--muted); padding: 10px 12px; }}
.error {{ color: var(--warn); min-height: 20px; }}
.params {{
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 0;
}}
.params-title {{
  color: var(--ink);
  font-weight: 600;
  padding: 11px 12px;
}}
.param-grid {{
  border-top: 1px solid var(--line);
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
@media (max-width: 980px) {{
  .metrics, .table {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  .param-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  canvas {{ height: 390px; }}
}}
@media (max-width: 560px) {{
  header {{ align-items: flex-start; height: auto; gap: 8px; padding: 12px; flex-direction: column; }}
  main {{ padding: 10px; }}
  .metrics, .table {{ grid-template-columns: 1fr; }}
  .param-grid {{ grid-template-columns: 1fr; }}
  canvas {{ height: 340px; }}
}}
</style>
</head>
<body>
<header>
  <h1>bitbank / bitFlyer Arbitrage</h1>
  <div class="status"><span id="dot" class="dot"></span><span id="conn">Disconnected</span><span id="mode"></span></div>
</header>
<main>
  <section class="metrics">
    <div class="metric"><div class="label">BUY Price</div><div id="buyPrice" class="value">--</div></div>
    <div class="metric"><div class="label">SELL Price</div><div id="sellPrice" class="value">--</div></div>
    <div class="metric"><div class="label">Position BTC</div><div id="position" class="value">--</div></div>
    <div class="metric"><div class="label">Realized PnL JPY</div><div id="pnl" class="value">--</div></div>
    <div class="metric"><div class="label">Filled BTC</div><div id="filled" class="value">--</div></div>
    <div class="metric"><div class="label">Action</div><div class="tooltip-wrap"><div id="action" class="value">--</div><div id="actionTooltip" class="tooltip">--</div></div></div>
  </section>
  <section class="chart-wrap">
    <div class="legend">
      <span class="key"><span class="swatch" style="background:var(--buy)"></span>BUY price</span>
      <span class="key"><span class="swatch" style="background:var(--sell)"></span>SELL price</span>
      <span class="key"><span class="swatch dashed"></span>maker effective spread</span>
      <span class="key">threshold: {self.config.threshold_jpy} JPY</span>
      <span class="key">offset: {self.config.threshold_offset_jpy} JPY</span>
    </div>
    <canvas id="chart" width="1400" height="560"></canvas>
  </section>
  <section class="table">
    <div class="metric"><div class="label">bitbank Top Bid / Ask</div><div id="bb" class="value">--</div></div>
    <div class="metric"><div class="label">bitbank Est Sell / Buy</div><div id="bbDepth" class="value">--</div></div>
    <div class="metric"><div class="label">bitFlyer Top Bid / Ask</div><div id="bf" class="value">--</div></div>
    <div class="metric"><div class="label">bitFlyer Est Sell / Buy</div><div id="bfDepth" class="value">--</div></div>
    <div class="metric"><div class="label">Active Maker</div><div id="maker" class="value">--</div></div>
    <div class="metric"><div class="label">Uptime</div><div id="uptime" class="value">--</div></div>
  </section>
  <section class="history">
    <div class="history-title">Bot Action History</div>
    <table class="history-table">
      <thead><tr><th class="history-time">Time</th><th class="history-action">Action</th><th>Description</th></tr></thead>
    </table>
    <div class="history-scroll">
      <table class="history-table">
        <tbody id="actionHistory"><tr><td colspan="3" class="history-empty">--</td></tr></tbody>
      </table>
    </div>
  </section>
  <div id="error" class="error"></div>
  <section class="params">
    <div class="params-title">Parameters</div>
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
function setText(id, value) {{ el(id).textContent = value; }}
function escapeHtml(value) {{
  return String(value).replace(/[&<>"']/g, c => ({{
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }}[c]));
}}
function effectiveMakerSpread(maker, quote) {{
  if (!maker || !quote) return null;
  const price = num(maker.price);
  if (!Number.isFinite(price)) return null;
  if (maker.action === "BUY") {{
    const hedge = num(quote.bitflyer_bid_vwap);
    return Number.isFinite(hedge) ? price - hedge : null;
  }}
  if (maker.action === "SELL") {{
    const hedge = num(quote.bitflyer_ask_vwap);
    return Number.isFinite(hedge) ? price - hedge : null;
  }}
  return null;
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
    const buy = num(q.buy_price);
    const sell = num(q.sell_price);
    const maker = latest.active_maker;
    const makerEffectiveSpread = effectiveMakerSpread(maker, q);
    points.push({{
      t: latest.timestamp * 1000,
      buy,
      sell,
      maker: makerEffectiveSpread,
      makerTrigger: maker ? num(maker.trigger_price) : null,
      makerPrice: maker ? num(maker.price) : null,
      makerAction: maker ? maker.action : null
    }});
    const cutoff = latest.timestamp * 1000 - chartWindowMs;
    while (points.length && points[0].t < cutoff) points.shift();
    renderMetrics();
    draw();
  }};
}}
function renderMetrics() {{
  const q = latest.quote || {{}};
  setText("mode", latest.dry_run ? "DRY RUN" : "LIVE");
  setText("buyPrice", q.buy_price == null ? "--" : fmt.format(num(q.buy_price)));
  setText("sellPrice", q.sell_price == null ? "--" : fmt.format(num(q.sell_price)));
  setText("position", btcFmt.format(num(latest.position || 0)));
  setText("pnl", fmt.format(num(latest.realized_pnl_jpy || 0)));
  setText("filled", btcFmt.format(num(latest.filled_base || 0)));
  setText("action", latest.last_action || "--");
  setText("actionTooltip", latest.last_action_description || "--");
  setText("bb", `${{fmt.format(num(q.bitbank_bid || 0))}} / ${{fmt.format(num(q.bitbank_ask || 0))}}`);
  setText("bbDepth", `${{fmt.format(num(q.bitbank_bid_vwap || 0))}} / ${{fmt.format(num(q.bitbank_ask_vwap || 0))}}`);
  setText("bf", `${{fmt.format(num(q.bitflyer_bid || 0))}} / ${{fmt.format(num(q.bitflyer_ask || 0))}}`);
  setText("bfDepth", `${{fmt.format(num(q.bitflyer_bid_vwap || 0))}} / ${{fmt.format(num(q.bitflyer_ask_vwap || 0))}}`);
  if (latest.active_maker) {{
    const m = latest.active_maker;
    const account = m.position_side ? `margin ${{m.position_side}}` : "spot";
    setText("maker", `${{m.action}} ${{account}} ${{btcFmt.format(num(m.amount))}} @ ${{fmt.format(num(m.price))}}`);
  }} else {{
    setText("maker", "--");
  }}
  setText("uptime", `${{Math.round(num(latest.uptime_sec || 0))}}s`);
  setText("error", latest.last_error || "");
  renderActionHistory();
}}
function renderActionHistory() {{
  const rows = latest.action_history || [];
  const body = el("actionHistory");
  if (!rows.length) {{
    body.innerHTML = `<tr><td colspan="3" class="history-empty">--</td></tr>`;
    return;
  }}
  body.innerHTML = rows.slice(0, 100).map(row => {{
    const date = new Date(num(row.timestamp) * 1000);
    const timeText = Number.isFinite(date.getTime()) ? date.toLocaleTimeString() : "--";
    const action = row.action || "--";
    const description = row.description || "";
    return `<tr><td class="history-time">${{escapeHtml(timeText)}}</td><td class="history-action">${{escapeHtml(action)}}</td><td>${{escapeHtml(description)}}</td></tr>`;
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
    if (Number.isFinite(p.buy)) values.push(p.buy);
    if (Number.isFinite(p.sell)) values.push(p.sell);
    if (Number.isFinite(p.maker)) values.push(p.maker);
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
  for (let i = 0; i <= 6; i++) {{
    const xx = left + i * cw / 6;
    const secondsAgo = 60 - i * 10;
    ctx.fillStyle = "#667085";
    ctx.fillText(secondsAgo === 0 ? "now" : `-${{secondsAgo}}s`, xx - 12, h - 10);
  }}
  function line(key, color, dash = []) {{
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash(dash);
    ctx.beginPath();
    let moved = false;
    visible.forEach(p => {{
      const v = p[key];
      if (!Number.isFinite(v)) {{
        moved = false;
        return;
      }}
      if (!moved) {{ ctx.moveTo(x(p.t), y(v)); moved = true; }}
      else ctx.lineTo(x(p.t), y(v));
    }});
    ctx.stroke();
    ctx.setLineDash([]);
  }}
  line("buy", "#1464d2");
  line("sell", "#c2410c");
  line("maker", "#0f9f6e", [7, 5]);
  const zeroY = y(0);
  if (zeroY >= top && zeroY <= top + ch) {{
    ctx.strokeStyle = "#101828";
    ctx.setLineDash([5, 5]);
    ctx.beginPath(); ctx.moveTo(left, zeroY); ctx.lineTo(w - right, zeroY); ctx.stroke();
    ctx.setLineDash([]);
  }}
  if (latest && latest.active_maker) {{
    const m = latest.active_maker;
    const account = m.position_side ? `margin ${{m.position_side}}` : "spot";
    const effective = effectiveMakerSpread(m, latest.quote || {{}});
    const effectiveText = Number.isFinite(effective) ? fmt.format(effective) : "--";
    ctx.fillStyle = "#0f9f6e";
    ctx.fillText(`maker ${{m.action}} ${{account}} effective spread ${{effectiveText}} / trigger ${{fmt.format(num(m.trigger_price))}} / order @ ${{fmt.format(num(m.price))}}`, left + 8, top + 18);
  }}
}}
connect();
window.addEventListener("resize", draw);
</script>
</body>
</html>"""
