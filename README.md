# vibe-bot

Async exchange clients and trading scripts.

## bitbank / bitFlyer arbitrage

The arbitrage bot lives at:

```text
src/vibe_bot/trades/bitbank_bitflyer/
```

Run it with `python3 -m vibe_bot.trades.bitbank_bitflyer.arbitrage`.

Trade logic diagrams are in [src/vibe_bot/trades/bitbank_bitflyer/flowchart.md](src/vibe_bot/trades/bitbank_bitflyer/flowchart.md).

Dry-run is the default mode. It streams public order books over websocket, estimates the bitFlyer hedge VWAP for the configured order size, computes BUY/SELL spreads from bitbank aggressive maker prices, chooses the bitbank maker quote it would maintain, updates the web monitor, and writes logs. It does not place orders.

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

Live trading is explicit and will place real bitbank maker orders and bitFlyer hedge orders:

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

## bitFlyer-only experimental strategy

The bitFlyer-only strategy uses the same spread and stage logic as the arbitrage bot, but it does not place bitbank maker orders. Instead, it watches bitbank public transactions and treats matching trades as synthetic bitbank maker fills, then places only the bitFlyer leg.

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

If the arbitrage bot is already using the default web ports, choose different ports:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.bitflyer_only \
  --web-port 8775 \
  --ws-port 8776
```

Logs are written to:

```text
logs/trades/bitbank_bitflyer_only
```

This is not true arbitrage. The script assumes the synthetic bitbank maker is first in queue at its price. If a bitbank transaction is smaller than the synthetic maker amount, the script treats it as a partial fill and only sends a bitFlyer order when the fill amount is at least `--bitflyer-min-order-size`.

Show all options:

```bash
python3 -m vibe_bot.trades.bitbank_bitflyer.bitflyer_only --help
```

## GMO / bitFlyer arbitrage

The GMO taker / bitFlyer taker bot lives at:

```text
src/vibe_bot/trades/gmo_bitflyer/
```

Run dry-run:

```bash
python3 -m vibe_bot.trades.gmo_bitflyer.arbitrage \
  --threshold-jpy 1000 \
  --threshold-offset-jpy 0 \
  --order-size 0.001 \
  --stage-size 0.001 \
  --max-stages 3
```

It keeps the same stage trigger idea as the bitbank / bitFlyer bot, but it does
not maintain a maker order. A stage trigger only creates a candidate trade; the
bot executes only after the EMA trend agrees with the direction, the executable
spread clears a volatility-based noise buffer, the candidate persists for the
configured time, and both legs use slippage-capped IOC/FAK limit orders.

Live trading is explicit and will place real GMO and bitFlyer orders:

```bash
python3 -m vibe_bot.trades.gmo_bitflyer.arbitrage --live
```

Show all options:

```bash
python3 -m vibe_bot.trades.gmo_bitflyer.arbitrage --help
```

Open the GMO / bitFlyer monitor locally:

```text
http://127.0.0.1:8775/
```

## Docker JupyterLab Environment

Build the image on the remote server:

```bash
docker build -t vibe-bot-jupyter .
```

Run JupyterLab with the repo mounted, so `git pull` updates are reflected immediately:

```bash
docker run --rm -d \
  --name vibe-bot-jupyter \
  -p 8888:8888 \
  -p 8765:8765 \
  -p 8766:8766 \
  -v "$PWD":/workspace/vibe_bot \
  --env-file .env \
  vibe-bot-jupyter
```

Inside JupyterLab terminal, run:

```bash
git pull
python3 -m vibe_bot.trades.bitbank_bitflyer.arbitrage --help
```

Pull the latest code on the remote VM from your local shell:

```bash
gcloud compute ssh veryshj123@vibe-bot -- -t 'cd vibe_bot && git pull'
```

The image does not copy this repository. It only copies `requirements-dev.lock` during build, installs Python/Jupyter dependencies from that lock file, and uses `PYTHONPATH=/workspace/vibe_bot/src`.
