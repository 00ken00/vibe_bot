.PHONY: help sync-coincheck-bitflyer-arbitrage-logs

help:
	@echo "Available targets:"
	@echo "  sync-coincheck-bitflyer-arbitrage-logs"

sync-coincheck-bitflyer-arbitrage-logs:
	rsync -avP \
		vibe-bot:~/vibe_bot/logs/trades/coincheck_bitflyer_arbitrage/ \
		./logs/trades/coincheck_bitflyer_arbitrage/
