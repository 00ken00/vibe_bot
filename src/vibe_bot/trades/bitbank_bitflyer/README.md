# bitbank / bitFlyer Trades

This folder contains the bitbank / bitFlyer arbitrage bot and the bitFlyer-only
experimental strategy.

Trade logic diagrams are in [flowchart.md](flowchart.md).

## bitbank / bitFlyer Arbitrage

Run it with:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.arbitrage
```

Dry-run is the default mode. It streams public order books over websocket,
estimates the bitFlyer hedge VWAP for the configured order size, computes
BUY/SELL spreads from bitbank aggressive maker prices, chooses the bitbank maker
quote it would maintain, updates the web monitor, and writes logs. It does not
place orders.

Run dry-run:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.arbitrage \
  --threshold-jpy 1000 \
  --threshold-offset-jpy 0 \
  --order-size 0.001 \
  --stage-size 0.001 \
  --max-stages 3 \
  --maker-update-interval 0.5 \
  --monitor-update-interval 1.0 \
  --disable-bitflyer-hedge
```

Open the monitor locally:

```text
http://127.0.0.1:8765/
```

On a remote server, use the server hostname or IP address instead.

Override parameters by changing the CLI values:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.arbitrage \
  --threshold-jpy 1500 \
  --threshold-offset-jpy 200 \
  --order-size 0.0005 \
  --stage-size 0.001 \
  --max-stages 3
```

Live trading is explicit and will place real bitbank maker orders and bitFlyer
hedge orders:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.arbitrage --live
```

To place live bitbank maker orders without sending the bitFlyer hedge order:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.arbitrage --live --disable-bitflyer-hedge
```

Show all options:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.arbitrage --help
```

## bitFlyer-Only Experimental Strategy

The bitFlyer-only strategy uses the same spread and stage logic as the arbitrage
bot, but it does not place bitbank maker orders. Instead, it watches bitbank
public transactions and treats matching trades as synthetic bitbank maker fills,
then places only the bitFlyer leg.

Run dry-run:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.bitflyer_only \
  --threshold-jpy 2000 \
  --threshold-offset-jpy 0 \
  --order-size 0.01 \
  --stage-size 0.01 \
  --max-stages 5
```

Run live bitFlyer-only:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.bitflyer_only --live
```

Open the monitor locally:

```text
http://127.0.0.1:8765/
```

If the arbitrage bot is already using the default web ports, choose different
ports:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.bitflyer_only \
  --web-port 8775 \
  --ws-port 8776
```

Logs are written to:

```text
logs/trades/bitbank_bitflyer_only
```

This is not true arbitrage. The script assumes the synthetic bitbank maker is
first in queue at its price. If a bitbank transaction is smaller than the
synthetic maker amount, the script treats it as a partial fill and only sends a
bitFlyer order when the fill amount is at least `--bitflyer-min-order-size`.

Show all options:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.bitflyer_only --help
```
