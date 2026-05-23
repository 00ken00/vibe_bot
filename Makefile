.PHONY: help pull-remote update-remote-env sync-coincheck-bitflyer-arbitrage-logs

help:
	@echo "Available targets:"
	@echo "  pull-remote"
	@echo "  update-remote-env"
	@echo "  sync-coincheck-bitflyer-arbitrage-logs"

pull-remote:
	gcloud compute ssh veryshj123@vibe-bot -- -t 'cd vibe_bot && git pull'

update-remote-env:
	gcloud compute scp .env veryshj123@vibe-bot:vibe_bot/.env

sync-coincheck-bitflyer-arbitrage-logs:
	rsync -avP \
		vibe-bot:~/vibe_bot/logs/trades/coincheck_bitflyer_arbitrage/ \
		./logs/trades/coincheck_bitflyer_arbitrage/
