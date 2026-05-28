---
name: gmo
description: Use the vibe_bot.gmo client library to interact with the GMO Coin exchange (REST + WebSocket). Invoke when the user asks to query market data, check account state, place/cancel orders, manage positions, or stream live data from GMO. Always go through the library — never inline httpx or HMAC code.
---

# GMO Coin client

A Python async client for the GMO Coin exchange lives at `src/vibe_bot/gmo/`. **Do not inline HTTP calls or HMAC signing** — use the client classes. They handle auth, rate limiting, error mapping, reconnects, and token refresh.

## Quick reference

```python
from vibe_bot.gmo import PublicClient, PrivateClient, PublicWebSocket, PrivateWebSocket
```

| Want to... | Use |
|---|---|
| Read tickers, orderbook, klines, symbols | `PublicClient` |
| Read account margin, assets, executions | `PrivateClient` |
| Place / change / cancel orders | `PrivateClient` |
| Manage positions, transfers | `PrivateClient` |
| Stream live ticker / trades / orderbook | `PublicWebSocket` |
| Stream order / execution / position events | `PrivateWebSocket` |

## Auth setup

`PrivateClient` reads `GMO_API_KEY` and `GMO_API_SECRET` from the environment by default. Override with constructor args if needed.

**Local & VM convention: `.env` at repo root.** Copy `.env.example` → `.env` and fill in values. `.env` is gitignored and `chmod 600`. Load it at the **entry point** of any script that talks to private endpoints (do not load it from inside the library):

```python
from dotenv import load_dotenv
load_dotenv()  # before importing/using PrivateClient

from vibe_bot.gmo import PrivateClient
```

On the GCP VM, place `.env` at the project root the same way (copy via `scp` or paste with `chmod 600`); the same `load_dotenv()` call works.

To check that creds work without placing trades:

```python
async with PrivateClient() as p:
    await p.margin()  # raises AuthError if creds bad
```

## Critical gotchas

- **Always use the library's clients.** They sign correctly (`timestamp + METHOD + path + body`, hex HMAC-SHA256, query string excluded). If you find yourself reaching for `hmac` or `httpx`, stop.
- **Pass `client_order_id`** when placing orders that must be idempotent. GMO accepts it on `place_order`; without it, a network retry can place a duplicate order.
- **Numeric values: pass `Decimal` or strings, not floats.** The library serializes `Decimal` losslessly. Floats like `0.1` will round-trip through `str(Decimal(str(x)))` but Decimal is safer.
- **Rate limits**: REST default is 20 req/s (Tier 1, <¥1B/week volume). Bump via `HttpClient(rate_limiter=RateLimiter(rate=30))` only if eligible. WebSocket subscribe is hard-capped at 1/sec/IP — the client throttles automatically; do not loop `subscribe()` calls without expecting a delay.
- **WS access token TTL is ~60 minutes.** `PrivateWebSocket` extends every 30 min automatically.
- **Symbols**: spot uses `BTC`, `ETH`, etc.; margin/leverage uses `BTC_JPY`, `ETH_JPY`, etc. Mixing them will error.
- **Klines `date` param**: `YYYYMMDD` for intraday intervals, `YYYY` for daily-and-up.
- **Errors**: catch `ApiError` (or its subclasses `AuthError`, `RateLimitError`); they carry GMO's `status` code and `messages` list.

## Historical candles

- Accepted `candle_minutes` in `src/vibe_bot/trades/history/history.py`: `1`, `5`, `15`, `30`, `60`.
- Use symbol naming carefully: spot uses symbols like `BTC`; some history presets use `BTC_JPY` for leverage-style markets.

## Common recipes

### Check account state

```python
async with PrivateClient() as p:
    margin = await p.margin()
    assets = await p.assets()
```

### Place a limit buy with idempotency

```python
import uuid
from decimal import Decimal

async with PrivateClient() as p:
    order_id = await p.place_order(
        symbol="BTC_JPY",
        side="BUY",
        execution_type="LIMIT",
        size=Decimal("0.001"),
        price=Decimal("10000000"),
        time_in_force="FAK",
        client_order_id=str(uuid.uuid4()),
    )
```

### Stream ticker

```python
async with PublicWebSocket() as ws:
    await ws.subscribe("ticker", "BTC_JPY")
    async for msg in ws.messages():
        print(msg["last"])
```

### Stream private execution events

```python
from vibe_bot.gmo import PrivateWebSocket, CH_EXECUTION_EVENTS

async with PrivateWebSocket() as ws:
    await ws.subscribe(CH_EXECUTION_EVENTS)
    async for msg in ws.messages():
        ...
```

## When to extend the library

If you find yourself doing the same multi-call dance twice (e.g., "fetch open positions, then close each at market"), add a method on `PrivateClient` rather than repeating it in scripts. Keep the public REST/WS *surface* in the library; keep *strategy* in user code.

## Smoke-testing without keys

`PublicClient` needs no auth. Use it to verify the network and library path work before assuming a private-side issue:

```python
from vibe_bot.gmo import PublicClient
async with PublicClient() as c:
    print(await c.status())
```
