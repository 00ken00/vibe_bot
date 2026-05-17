# Coincheck Order API Compared With GMO, bitFlyer, and bitbank

Generated on 2026-05-17.

## Summary

Coincheck has the simplest order API among the four exchanges. For arbitrage execution, GMO and bitFlyer expose stronger execution controls. bitbank is also stronger than Coincheck for maker strategies because it supports `post_only` and more conditional order types.

| Exchange | Basic Orders | Time in Force | Post-Only | Stop / Conditional | Advanced Orders |
|---|---|---|---|---|---|
| Coincheck | `buy`, `sell`, `market_buy`, `market_sell` | `good_til_cancelled`, `post_only` | Yes, via `time_in_force=post_only` | `stop_loss_rate` | No OCO/IFD-style API |
| GMO | `MARKET`, `LIMIT`, `STOP` | `FAK`, `FAS`, `FOK`, `SOK` | Yes, via `SOK` | `STOP`, `losscutPrice` | Close order, bulk close, cancel-before style operations |
| bitFlyer | Child: `LIMIT`, `MARKET` | `GTC`, `IOC`, `FOK` | No explicit post-only | Parent: `STOP`, `STOP_LIMIT`, `TRAIL` | `SIMPLE`, `IFD`, `OCO`, `IFDOCO` |
| bitbank | `limit`, `market`, `stop`, `stop_limit`, `take_profit`, `stop_loss`, `losscut` | No general TIF exposed | Yes, `post_only=true` for limit | Yes | No OCO/IFD-style API |

## Coincheck

Official Coincheck order types:

- `buy`
- `sell`
- `market_buy`
- `market_sell`

Order parameters include:

- `pair`
- `order_type`
- `rate`
- `amount`
- `market_buy_amount`
- `stop_loss_rate`
- `time_in_force`

`market_buy` uses JPY amount via `market_buy_amount`.

`market_sell` uses crypto amount via `amount`.

`time_in_force` supports:

- `good_til_cancelled`
- `post_only`

Repo note: [src/vibe_bot/coincheck/private.py](/Users/ken/Projects/vibe_bot/src/vibe_bot/coincheck/private.py) currently exposes `stop_loss_rate`, but does not yet expose `time_in_force` in `PrivateClient.place_order(...)`.

Source: https://coincheck.com/ja/documents/exchange/api

## GMO

GMO order types are controlled by `executionType`:

- `MARKET`
- `LIMIT`
- `STOP`

`timeInForce` supports:

- `FAK`
- `FAS`
- `FOK`
- `SOK`

`SOK` is post-only and applies to limit orders.

Useful order parameters include:

- `symbol`
- `side`
- `executionType`
- `size`
- `price`
- `losscutPrice`
- `timeInForce`
- `clientOrderId`

GMO also exposes close and bulk-close APIs, which are useful for margin/leverage position management.

Source: https://api.coin.z.com/docs/en/

## bitFlyer

bitFlyer separates normal child orders from parent orders.

Child order types:

- `LIMIT`
- `MARKET`

Child order options:

- `product_code`
- `child_order_type`
- `side`
- `price`
- `size`
- `minute_to_expire`
- `time_in_force`

`time_in_force` supports:

- `GTC`
- `IOC`
- `FOK`

Parent order methods:

- `SIMPLE`
- `IFD`
- `OCO`
- `IFDOCO`

Parent condition types:

- `LIMIT`
- `MARKET`
- `STOP`
- `STOP_LIMIT`
- `TRAIL`

bitFlyer has strong IOC/FOK and advanced order support, but no explicit post-only option.

Source: https://lightning.bitflyer.com/docs

## bitbank

bitbank order types:

- `limit`
- `market`
- `stop`
- `stop_limit`
- `take_profit`
- `stop_loss`
- `losscut`

Order parameters include:

- `pair`
- `side`
- `type`
- `amount`
- `price`
- `post_only`
- `trigger_price`
- `position_side`

`post_only=true` is available for limit orders.

bitbank does not expose the same general time-in-force controls as GMO or bitFlyer.

Source: https://github.com/bitbankinc/bitbank-api-docs/blob/master/rest-api.md

## Practical Ranking For Arbitrage Execution Controls

1. GMO
   - Best time-in-force set.
   - Supports post-only via `SOK`.
   - Has `clientOrderId`, useful for retry/idempotency control.

2. bitFlyer
   - Strong `IOC` / `FOK` support.
   - Advanced parent orders: `IFD`, `OCO`, `IFDOCO`.
   - No explicit post-only.

3. bitbank
   - Good for maker quoting because of `post_only`.
   - Good conditional order coverage.
   - Weaker time-in-force controls.

4. Coincheck
   - Supports limit, market, post-only, and stop-loss.
   - Minimal compared with the others.
   - Local client should expose `time_in_force` before relying on Coincheck post-only orders.
