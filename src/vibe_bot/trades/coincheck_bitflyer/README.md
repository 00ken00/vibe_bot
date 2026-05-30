# Coincheck / bitFlyer Arbitrage

Standalone arbitrage bot copied from the `gmo_bitflyer` structure and adapted
for Coincheck spot plus bitFlyer hedge execution.

The bot:

- Maintains Coincheck and bitFlyer order books.
- Computes executable VWAP spreads for the configured `--order-size`.
- Treats Coincheck spot inventory relative to `--coincheck-neutral-spot-amount`.
- Places a slippage-capped Coincheck limit order first.
- Hedges the filled Coincheck amount on bitFlyer using a market IOC order.
- Cancels any unfilled Coincheck limit remainder after the execution poll.

## Run

Dry run:

```bash
python3 -m vibe_bot.trades.coincheck_bitflyer.arbitrage
```

Live:

```bash
python3 -m vibe_bot.trades.coincheck_bitflyer.arbitrage --live
```

To place live Coincheck orders without sending bitFlyer hedge orders:

```bash
python3 -m vibe_bot.trades.coincheck_bitflyer.arbitrage \
  --live \
  --disable-bitflyer-hedge
```

In this mode, the bot tracks the resulting unhedged Coincheck position but
does not block new Coincheck trades or attempt bitFlyer repair orders.

The web monitor defaults to:

```text
http://127.0.0.1:8765/
```

Override ports if another bot is already using them:

```bash
python3 -m vibe_bot.trades.coincheck_bitflyer.arbitrage \
  --web-port 8785 \
  --ws-port 8786
```

## Credentials

Live mode requires:

```text
COINCHECK_API_KEY
COINCHECK_API_SECRET
BITFLYER_API_KEY
BITFLYER_API_SECRET
```

The entry point loads `.env` from the repo root.

## Spread Definitions

`BUY` action:

```text
buy_price = coincheck_ask_vwap - bitflyer_bid_vwap
```

This buys Coincheck and sells bitFlyer.

`SELL` action:

```text
sell_price = coincheck_bid_vwap - bitflyer_ask_vwap
```

This sells Coincheck and buys bitFlyer.

The trend filter uses:

```text
mid_spread = Coincheck mid price - bitFlyer mid price
```

## Neutral Coincheck Spot Amount

Coincheck is spot-only, but the bot can treat a configured spot balance as
strategy-neutral:

```text
strategy_position = Coincheck spot BTC balance - coincheck_neutral_spot_amount
```

Example:

```bash
python3 -m vibe_bot.trades.coincheck_bitflyer.arbitrage \
  --coincheck-neutral-spot-amount 1.2
```

If the actual Coincheck BTC balance is `1.0`, the bot treats the Coincheck leg
as `-0.2 BTC` relative to the neutral inventory.

Short-opening stages are limited so they cannot sell the actual Coincheck spot
balance below zero. For example, with a neutral amount of `0.03 BTC`, a stage
size of `0.01 BTC`, and `5` maximum stages, the available strategy stages are:

```text
-3, -2, -1, 0, 1, 2, 3, 4, 5
```

At stage `-3`, the bot can still buy to close the short strategy position, but
it does not expose a trigger to open another short stage.

## Execution Notes

Coincheck does not expose the same FAK limit order behavior as GMO. The bot
therefore places an aggressive limit order, polls recent Coincheck transactions
for fills, and cancels the remaining order if the requested size was not fully
filled.

bitFlyer hedging uses:

```text
MARKET IOC
```

The bot records Coincheck order success rate and bitFlyer average hedge
slippage over the most recent 20 order attempts, and shows both metrics in the
web app.
