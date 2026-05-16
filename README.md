# vibe-bot

Async exchange clients and trading scripts.

## Strategy Docs

- [bitbank / bitFlyer](src/vibe_bot/trades/bitbank_bitflyer/README.md)
- [GMO / bitFlyer](src/vibe_bot/trades/gmo_bitflyer/README.md)

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

The image does not copy this repository. It only copies `requirements-dev.lock`
during build, installs Python/Jupyter dependencies from that lock file, and uses
`PYTHONPATH=/workspace/vibe_bot/src`.
