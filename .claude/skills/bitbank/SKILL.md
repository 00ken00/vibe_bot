---
name: bitbank
description: Use the vibe_bot.bitbank client library to interact with the bitbank exchange (REST + Socket.IO public stream + PubNub private stream). Invoke when the user asks to query market data, check account state, place/cancel spot orders, manage withdrawals, or stream live data from bitbank. Always go through the library — never inline httpx or HMAC code.
---

# bitbank client

A Python async client for the bitbank exchange lives at `src/vibe_bot/bitbank/`. **Do not inline HTTP calls or HMAC signing** — use the client classes. They handle auth, rate limiting, error mapping, reconnects, and PubNub token refresh.

## Quick reference

```python
from vibe_bot.bitbank import PublicClient, PrivateClient, PublicWebSocket, PrivateWebSocket
```

| Want to... | Use |
|---|---|
| Read tickers, depth, transactions, candlesticks, circuit-break info | `PublicClient` |
| Read spot pair rules / status | `PublicClient.spot_pairs()` / `.spot_status()` |
| Read account assets, trade history, deposit/withdrawal history | `PrivateClient` |
| Place / cancel spot orders | `PrivateClient` |
| Margin status & positions | `PrivateClient` |
| Stream live ticker / transactions / depth_diff / depth_whole | `PublicWebSocket` |
| Stream private order / trade / asset / withdrawal events | `PrivateWebSocket` |

## Auth setup

`PrivateClient` reads `BITBANK_API_KEY` and `BITBANK_API_SECRET` from the environment by default. Override with constructor args if needed.

**Local & VM convention: `.env` at repo root.** Copy `.env.example` → `.env` and fill in values. `.env` is gitignored and `chmod 600`. Load it at the **entry point** of any script that talks to private endpoints (do not load it from inside the library):

```python
from dotenv import load_dotenv
load_dotenv()  # before importing/using PrivateClient

from vibe_bot.bitbank import PrivateClient
```

To check that creds work without placing trades:

```python
async with PrivateClient() as p:
    await p.assets()  # raises AuthError if creds bad
```

## Critical gotchas (different from GMO!)

- **Pair codes are lowercase**: `btc_jpy`, `xrp_jpy`, `eth_jpy`. Uppercase will 404.
- **Side is lowercase**: `"buy"` / `"sell"` (GMO uses `"BUY"` / `"SELL"`). Order type also lowercase: `limit`, `market`, `stop`, `stop_limit`, `take_profit`, `stop_loss`, `losscut`.
- **Two REST hosts**:
  - Public market data → `https://public.bitbank.cc/<pair>/<endpoint>` (no `/v1`)
  - Private + the unsigned `/v1/spot/pairs` & `/v1/spot/status` → `https://api.bitbank.cc/v1/...`
  The library routes both correctly; just call `PublicClient.spot_pairs()` etc.
- **Signature includes the query string for GET.** That's the opposite of GMO. The library builds the canonical `path?query` payload — don't try to roll your own.
- **Default auth mode is `time_window`** (uses `ACCESS-REQUEST-TIME` + `ACCESS-TIME-WINDOW`). Pass `HttpClient(auth_mode="nonce")` only if your account is locked to nonce-only auth.
- **Rate limits**: ~10 req/s for QUERY endpoints, ~6 req/s for UPDATE (orders / cancels / withdrawals). The default `RateLimiter(rate=10)` covers the QUERY budget; create a separate limiter (and pass it to a separate `HttpClient`) if you want to gate UPDATE traffic distinctly.
- **Numeric values: pass `Decimal` or strings, not floats.** The library serializes `Decimal` losslessly via `format(d, "f")`.
- **Public stream is Socket.IO 4 (Engine.IO 4), not raw JSON-over-WS.** The library speaks the protocol inline — don't subscribe with raw `websockets.connect()` and expect JSON frames.
- **Private stream is PubNub, not a native WebSocket.** `PrivateWebSocket` long-polls PubNub's v2 subscribe API with the channel + token returned by `/v1/user/subscribe`. Token TTL is ~12h; the client refreshes automatically.
- **Order book sync**: bitbank's `depth_diff` channel only carries ~200 levels around the inside. To keep an accurate book, subscribe to **both** `depth_whole_<pair>` and `depth_diff_<pair>`, buffer diffs by `sequenceId`, and replace + replay on each snapshot. The `sequenceId` is shared across both channels.
- **Errors**: catch `ApiError` (or its subclasses `AuthError`, `RateLimitError`); they carry bitbank's numeric `code` (e.g. `20005` invalid signature, `10009` rate limited).

## Common recipes

### Check account state

```python
async with PrivateClient() as p:
    a = await p.assets()
    for asset in a.assets:
        if asset.onhand_amount > 0:
            print(asset.asset, asset.free_amount, "/", asset.onhand_amount)
```

### Place a post-only limit buy

```python
from decimal import Decimal

async with PrivateClient() as p:
    order = await p.place_order(
        pair="btc_jpy",
        side="buy",
        order_type="limit",
        amount=Decimal("0.0001"),
        price=Decimal("10000000"),
        post_only=True,
    )
    print(order.order_id, order.status)
```

bitbank does not accept a client-supplied order id, so to deduplicate retries the safe pattern is "place once, then immediately call `order_info` if you didn't get a response" rather than blind retry.

### Cancel and bulk-cancel

```python
async with PrivateClient() as p:
    await p.cancel_order(pair="btc_jpy", order_id=12345678)
    # Up to 30 ids per call:
    await p.cancel_orders(pair="btc_jpy", order_ids=[12345678, 12345679])
```

### Stream ticker

```python
async with PublicWebSocket() as ws:
    await ws.subscribe("ticker_btc_jpy")
    async for msg in ws.messages():
        data = msg["message"]["data"]
        print(data["last"])
```

Channel names: `ticker_<pair>`, `transactions_<pair>`, `depth_diff_<pair>`, `depth_whole_<pair>`, `circuit_break_info_<pair>`.

### Stream private execution events

```python
async with PrivateWebSocket() as ws:
    async for msg in ws.messages():
        method = msg.get("method")  # asset_update, spot_order, spot_trade, ...
        params = msg.get("params") or []
        ...
```

## When to extend the library

If you find yourself doing the same multi-call dance twice (e.g., "fetch active orders, then cancel each over a price"), add a method on `PrivateClient` rather than repeating it in scripts. Keep the public REST/stream *surface* in the library; keep *strategy* in user code.

## Smoke-testing without keys

`PublicClient` needs no auth. Use it to verify the network and library path work before assuming a private-side issue:

```python
from vibe_bot.bitbank import PublicClient
async with PublicClient() as c:
    print(await c.ticker("btc_jpy"))
```
