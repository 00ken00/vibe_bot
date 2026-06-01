.PHONY: help pull-remote update-remote-env sync-coincheck-bitflyer-arbitrage-logs

help:
	@echo "Available targets:"
	@echo "  pull-remote"
	@echo "  update-remote-env"
	@echo "  sync-coincheck-bitflyer-arbitrage-logs"

pull-remote:
	ssh vibe-bot -t 'cd vibe_bot && git pull'


update-remote-env:
	scp .env vibe-bot:vibe_bot/.env

sync-trades-logs:
	rsync -avP vibe-bot:~/vibe_bot/logs/trades/ ./logs/trades/
