---
name: bitflyer
description: Use the vibe_bot.bitflyer client library to interact with the bitFlyer Lightning exchange (REST + JSON-RPC over WebSocket realtime). Invoke when the user asks to query market data, check account state, place/cancel spot or CFD (FX_BTC_JPY) orders, manage positions, or stream live data from bitFlyer. Always go through the library — never inline httpx or HMAC code.
---

# bitFlyer Lightning client

A Python async client for the bitFlyer Lightning exchange lives at `src/vibe_bot/bitflyer/`. **Do not inline HTTP calls or HMAC signing** — use the client classes. They handle auth, rate limiting, error mapping, reconnects, and realtime auth.

## Quick reference

```python
from vibe_bot.bitflyer import PublicClient, PrivateClient, PublicWebSocket, PrivateWebSocket
```

| Want to... | Use |
|---|---|
| Read tickers, board, executions, markets, board state, health | `PublicClient` |
| Read funding rate (FX), corporate leverage, chats | `PublicClient` |
| Read account balance, collateral, addresses, deposits, withdrawals | `PrivateClient` |
| Place / cancel child orders (LIMIT / MARKET) | `PrivateClient.send_child_order` / `.cancel_child_order` |
| Place / cancel parent orders (IFD / OCO / IFDOCO / STOP / STOP_LIMIT / TRAIL) | `PrivateClient.send_parent_order` / `.cancel_parent_order` |
| List child orders, executions, positions | `PrivateClient` |
| Stream live ticker / executions / board / board_snapshot | `PublicWebSocket` |
| Stream child / parent order events | `PrivateWebSocket` |

## Auth setup

`PrivateClient` and `PrivateWebSocket` both read `BITFLYER_API_KEY` and `BITFLYER_API_SECRET` from the environment by default. Override with constructor args if needed.

**Local & VM convention: `.env` at repo root.** Copy `.env.example` → `.env` and fill in values. `.env` is gitignored and `chmod 600`. Load it at the **entry point** of any script that talks to private endpoints (do not load it from inside the library):

```python
from dotenv import load_dotenv
load_dotenv()  # before importing/using PrivateClient

from vibe_bot.bitflyer import PrivateClient
```

To check that creds work without placing trades:

```python
async with PrivateClient() as p:
    await p.permissions()  # raises AuthError if creds bad
```

## Critical gotchas (different from GMO and bitbank!)

- **Product codes are uppercase**: `BTC_JPY`, `ETH_JPY`, `XRP_JPY`, `ETH_BTC`, …
- **CFD/leverage uses an `FX_` prefix**: the BTC/JPY perp is `FX_BTC_JPY`. Funding rate, positions, and FX-only channels live under that code.
- **Side / type are uppercase**: `"BUY"` / `"SELL"`, `"LIMIT"` / `"MARKET"`, `"GTC"` / `"IOC"` / `"FOK"`. (GMO matches; bitbank does not.)
- **REST signing**: `ACCESS-SIGN = HMAC-SHA256(secret, ACCESS-TIMESTAMP + METHOD + PATH + BODY)` in lowercase hex. PATH includes the `/v1/...` prefix **and** the query string for GETs. The library does this — don't roll your own.
- **REST values are JSON numbers, not strings.** bitFlyer expects `price: 10000000` and `size: 0.001` as numbers; the library serializes `Decimal` losslessly via `format(d, "f")` and emits int/float as appropriate. Pass `Decimal` or strings; the library coerces. Avoid passing floats.
- **No client-supplied order id.** bitFlyer returns a `child_order_acceptance_id` you can use as a temporary handle until the order is created and gets a `child_order_id`. To deduplicate retries the safe pattern is "send once, then on transport error call `child_orders(child_order_acceptance_id=...)`" rather than blind retry.
- **Cancel/cancel-all return empty bodies.** `cancel_child_order` and `cancel_all_child_orders` resolve to `None` on success.
- **Rate limits**: documented in five-minute windows.
  - Per-IP global: 500 / 5 min  *(library default — `RateLimiter()`)*
  - Private API general: 500 / 5 min
  - Order endpoints: 300 / 5 min
  - Small-order safety net: 100 placements / minute for size ≤ 0.1 BTC

  The library defaults to the per-IP cap. For order traffic, plumb a stricter limiter into a separate `HttpClient`:
  ```python
  from vibe_bot.bitflyer import HttpClient, PrivateClient, RateLimiter
  order_http = HttpClient(api_key=..., api_secret=..., rate_limiter=RateLimiter(rate=300, per=300.0))
  orders = PrivateClient(http=order_http)
  ```
- **2FA on withdrawals**: pass `code=...` to `withdraw()`. Without it, expect `AuthError(status=-505)` or `-700`.
- **Realtime is JSON-RPC 2.0 over a single WebSocket** (`wss://ws.lightstream.bitflyer.com/json-rpc`). There's also a Socket.IO endpoint, but this client only speaks JSON-RPC; the surface is simpler and behaviorally identical.
- **Private channels need an `auth` call right after connect** with `(api_key, timestamp_ms, nonce, signature=HMAC(secret, timestamp+nonce))`. `PrivateWebSocket` does this for you and re-auths on reconnect.
- **Realtime delivery is best-effort.** Disconnections lose history (no replay). For order-book accuracy, subscribe to **both** `lightning_board_snapshot_<pair>` and `lightning_board_<pair>` and rebuild from each new snapshot.
- **Errors**: catch `ApiError` (or its subclasses `AuthError`, `RateLimitError`); they carry bitFlyer's negative `status` (e.g. `-205` invalid signature, `-132` rate limited, `-505` 2FA). `TransportError` wraps httpx errors and prefixes the class (e.g. `ReadTimeout: ...`) so the cause is visible.
- **Some endpoints are slow** (`/v1/me/getparentorders` without a `parent_order_state` filter, `/v1/me/getbalancehistory` over wide ranges). The default `HttpClient(timeout=10.0)` may bite — pass a larger `timeout=` or always send a state/page filter on those calls.

## Historical candles

- Accepted `candle_minutes` in `src/vibe_bot/trades/history/history.py`: `1`, `5`, `15`, `30`, `60`.
- For `60` minutes, the helper fetches 30-minute data and aggregates closes to hourly buckets.

## Common recipes

### Check account state

```python
async with PrivateClient() as p:
    bal = await p.balance()  # spot: per-currency
    for b in bal:
        if b.amount > 0:
            print(b.currency_code, b.available, "/", b.amount)

    col = await p.collateral()  # margin/CFD
    print("collateral:", col.collateral, "keep_rate:", col.keep_rate)
```

### Place a limit buy

```python
from decimal import Decimal

async with PrivateClient() as p:
    ack = await p.send_child_order(
        product_code="BTC_JPY",
        child_order_type="LIMIT",
        side="BUY",
        size=Decimal("0.001"),
        price=Decimal("10000000"),
        time_in_force="GTC",
    )
    print(ack.child_order_acceptance_id)
```

### Cancel by acceptance id

```python
async with PrivateClient() as p:
    await p.cancel_child_order(
        product_code="BTC_JPY",
        child_order_acceptance_id="JRF20260101-...",
    )
    # Or kill everything for a pair:
    await p.cancel_all_child_orders(product_code="BTC_JPY")
```

### Place a stop on FX

```python
from decimal import Decimal
from vibe_bot.bitflyer.models import ParentOrderParameter

async with PrivateClient() as p:
    ack = await p.send_parent_order(
        order_method="SIMPLE",
        parameters=[
            ParentOrderParameter(
                product_code="FX_BTC_JPY",
                condition_type="STOP",
                side="SELL",
                size=Decimal("0.01"),
                trigger_price=Decimal("11500000"),
            )
        ],
    )
```

### Stream ticker

```python
async with PublicWebSocket() as ws:
    await ws.subscribe("lightning_ticker_BTC_JPY")
    async for msg in ws.messages():
        # msg = {"channel": "lightning_ticker_BTC_JPY", "message": {...}}
        print(msg["message"]["ltp"])
```

Public channel names: `lightning_ticker_<pc>`, `lightning_executions_<pc>`, `lightning_board_snapshot_<pc>`, `lightning_board_<pc>`.

### Stream child-order events

```python
async with PrivateWebSocket() as ws:
    await ws.subscribe("child_order_events")
    async for msg in ws.messages():
        for event in msg["message"]:  # batched
            print(event["event_type"], event.get("child_order_acceptance_id"))
```

Private channels: `child_order_events`, `parent_order_events`.

## When to extend the library

If you find yourself doing the same multi-call dance twice (e.g., "fetch open child orders, then cancel each over a price"), add a method on `PrivateClient` rather than repeating it in scripts. Keep the public REST/stream *surface* in the library; keep *strategy* in user code.

## Smoke-testing without keys

`PublicClient` needs no auth. Use it to verify the network and library path work before assuming a private-side issue:

```python
from vibe_bot.bitflyer import PublicClient
async with PublicClient() as c:
    print(await c.health("BTC_JPY"))
```
