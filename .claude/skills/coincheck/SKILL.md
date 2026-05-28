---
name: coincheck
description: Use the vibe_bot.coincheck client library to interact with the Coincheck exchange (REST + WebSocket). Invoke when the user asks to query market data, check account state, place/cancel spot orders, manage deposits/withdrawals, or stream live Coincheck data. Always go through the library -- never inline httpx, websockets, or HMAC code.
---

# Coincheck client

A Python async client for Coincheck lives at `src/vibe_bot/coincheck/`. **Do not inline HTTP calls or HMAC signing**. Use the client classes so auth, rate limiting, error mapping, and reconnect behavior stay centralized.

## Quick reference

```python
from vibe_bot.coincheck import PublicClient, PrivateClient, PublicWebSocket, PrivateWebSocket
```

| Want to... | Use |
|---|---|
| Read ticker, trades, orderbook, rates, pairs | `PublicClient` |
| Read balances, orders, transactions, deposits, withdrawals | `PrivateClient` |
| Place / cancel spot orders | `PrivateClient` |
| Stream public trades / order book deltas | `PublicWebSocket` |
| Stream order / execution events | `PrivateWebSocket` |

## Auth setup

`PrivateClient` and `PrivateWebSocket` read `COINCHECK_API_KEY` and `COINCHECK_API_SECRET` from the environment by default. Override with constructor args if needed.

**Local & VM convention: `.env` at repo root.** Copy `.env.example` -> `.env` and fill in values. `.env` is gitignored and should be `chmod 600`. Load it at the entry point of scripts that talk to private endpoints:

```python
from dotenv import load_dotenv
load_dotenv()

from vibe_bot.coincheck import PrivateClient
```

To check credentials without placing orders:

```python
async with PrivateClient() as p:
    await p.balance()
```

## Critical gotchas

- **Always use the library's clients.** Coincheck signs `ACCESS-NONCE + full request URL + raw body` with HMAC-SHA256. Private WebSocket connects to `wss://stream.coincheck.com` but signs the fixed URI `wss://stream.coincheck.com/private`.
- **Numeric values: pass `Decimal` or strings.** The library serializes `Decimal` losslessly.
- **Pair codes are lowercase**, e.g. `btc_jpy`.
- **Order types are Coincheck-specific strings**: `buy`, `sell`, `market_buy`, `market_sell`.
- **Private WebSocket channels must be enabled** on the API key. Standard channels are `order-events` and `execution-events`.
- **Errors**: catch `ApiError` or its subclasses `AuthError` and `RateLimitError`.

## Historical candles

- Accepted `candle_minutes` in `src/vibe_bot/trades/history/history.py`: `1`, `5`, `15`, `60`, `240`, `720`, `1440`.
- Coincheck rejects `30`-minute candles.
- Coincheck's chart endpoint caps responses at about 301 rows, regardless of larger `limit` values. Practical max coverage by interval:
  - `1`: about 0.2 days
  - `5`: about 1.0 days
  - `15`: about 3.1 days
  - `60`: about 12.5 days
  - `240`: about 50 days
  - `720`: about 150 days
  - `1440`: about 300 days
- For longer windows in comparison charts, do not silently substitute another exchange for Coincheck unless the user explicitly wants a proxy source; changing the exchange changes the meaning of the spread.

## Common recipes

### Public ticker

```python
async with PublicClient() as c:
    ticker = await c.ticker("btc_jpy")
    print(ticker.last)
```

### Place a limit buy

```python
from decimal import Decimal

async with PrivateClient() as p:
    order = await p.place_order(
        pair="btc_jpy",
        order_type="buy",
        rate=Decimal("10000000"),
        amount=Decimal("0.001"),
    )
```

### Cancel an order

```python
async with PrivateClient() as p:
    await p.cancel_order(123456789)
```

### Stream public trades

```python
async with PublicWebSocket() as ws:
    await ws.subscribe("btc_jpy-trades")
    async for msg in ws.messages():
        print(msg)
```

### Stream private events

```python
from vibe_bot.coincheck import PrivateWebSocket, CH_ORDER_EVENTS, CH_EXECUTION_EVENTS

async with PrivateWebSocket() as ws:
    await ws.subscribe(CH_ORDER_EVENTS, CH_EXECUTION_EVENTS)
    async for msg in ws.messages():
        print(msg)
```

## When to extend the library

If a Coincheck workflow is repeated in scripts, add a method to `PublicClient` or `PrivateClient` instead of duplicating request details. Keep exchange transport and auth in `src/vibe_bot/coincheck/`; keep strategy code outside the exchange client.
