# vibe-bot

Async exchange clients and trading scripts.

## bitbank / bitFlyer arbitrage

The arbitrage bot lives at:

```text
src/trades/bitbank_bitflyer_arbitrage.py
```

Dry-run is the default mode. It streams public order books over websocket, estimates full-size execution prices for the configured order size, computes the BUY/SELL spreads, chooses the bitbank maker quote it would maintain, updates the web monitor, and writes logs. It does not place orders.

Run dry-run:

```bash
python3 src/trades/bitbank_bitflyer_arbitrage.py \
  --threshold-jpy 1000 \
  --threshold-offset-jpy 0 \
  --order-size 0.001 \
  --max-position 0.003 \
  --maker-update-interval 0.5 \
  --monitor-update-interval 1.0
```

Open the monitor:

```text
http://127.0.0.1:8765/
```

Override parameters by changing the CLI values:

```bash
python3 src/trades/bitbank_bitflyer_arbitrage.py \
  --threshold-jpy 1500 \
  --threshold-offset-jpy 200 \
  --order-size 0.0005
```

Live trading is explicit and will place real bitbank maker orders and bitFlyer hedge orders:

```bash
python3 src/trades/bitbank_bitflyer_arbitrage.py --live
```

Show all options:

```bash
python3 src/trades/bitbank_bitflyer_arbitrage.py --help
```
