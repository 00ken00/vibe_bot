---
name: bitbank-bitflyer-analysis
description: Analyze vibe_bot bitbank/bitFlyer arbitrage logs for expected vs actual profit, hedge slippage, private stream latency, stale maker behavior, neutral-position config, deferred hedges, and position balance. Use when asked to inspect or explain bitbank-bitFlyer arbitrage performance.
---

# bitbank/bitFlyer Arbitrage Log Analysis

Use this for `logs/trades/bitbank_bitflyer_arbitrage/`.

## Core Workflow

1. Pick the newest matching run files:
   - `events-<run_id>.jsonl`
   - `trades-<run_id>.csv`
   - `quotes-<run_id>-<YYYYMMDD>.csv` (runs after 2026-06-12): one row per
     order-book websocket update, columns `timestamp` (epoch sec), `exchange`
     (`bitbank`/`bitflyer`), `best_bid`, `best_ask`, `vwap_size`, `bid_vwap`,
     `ask_vwap` (VWAP over `order_size * hedge_vwap_multiplier`; bitFlyer rows
     only — empty on bitbank rows since the hedge executes on bitFlyer),
     `base_size`, `bid_vwap_base`, `ask_vwap_base` (VWAP over plain
     `order_size`, both exchanges). Use this for price trajectories around
     fills; an empty VWAP on a bitFlyer row means insufficient visible depth. Files rotate daily (JST); finished days are gzipped to
     `.csv.gz` and pruned after 7 days, so fetch them promptly.

   Runs after 2026-06-12 also have a slimmer events file: `private_api_trace`
   is logged only for failed calls, `maker_place_attempt` was removed (quote
   context now lives in `quotes-<run_id>.csv`), `maker_canceled` no longer
   repeats the maker dict (see the adjacent `maker_done`), and
   `bitbank_private_ws_order_ignored` omits `CANCELED_UNFILLED` self-cancel
   echoes.
2. Read startup events:
   - `bot_started`
   - `position_initialized`
   - confirm `bitbank_neutral_spot_amount` and `bitflyer_neutral_position_amount`.
3. Summarize health:
   - `bitbank_private_ws_connected`, `bitbank_private_ws_error`
   - `maker_filled`
   - `bitflyer_hedge_attempt`, `bitflyer_position_updated`
   - `bitflyer_hedge_deferred`, `combined_pnl_skipped`
   - `error`
4. Compute expected vs actual gross on hedge-executed rows:
   - BUY maker expected: `(bitflyer_expected_price - bitbank_price) * amount`
   - BUY maker actual: `(bitflyer_average_price - bitbank_price) * amount`
   - SELL maker expected: `(bitbank_price - bitflyer_expected_price) * amount`
   - SELL maker actual: `(bitbank_price - bitflyer_average_price) * amount`
5. Compute bitFlyer hedge impact:
   - `actual - expected`
   - negative means adverse hedge execution.
6. Check final state:
   - `bitbank_position`
   - `bitflyer_position`
   - `unhedged_position`
   - `realized_pnl_jpy`

## Important Diagnostic Note

Do not treat `expected edge at maker placement vs actual edge after hedge` as proof that the bot should have canceled earlier.

That comparison explains realized performance, but maker validity should be judged by:

```text
current expected edge while the maker is live vs threshold
```

For BUY maker:

```text
current_bitflyer_bid_vwap - maker_price >= threshold
```

For SELL maker:

```text
maker_price - current_bitflyer_ask_vwap >= threshold
```

If the current expected edge remains above threshold, the maker is still valid by strategy even if the edge degraded from placement. A stale-maker issue is stronger when logs show maker orders rested for seconds and the current expected edge likely fell below threshold or below threshold plus a safety buffer before fill.

`_same_maker()` comparing only maker price/action/amount can miss hedge-price degradation when bitbank passive price still constrains the maker price. Diagnose this as a risk, but distinguish:

- **edge degraded but still above threshold**: expected behavior
- **edge below threshold while maker remained live**: likely stale-maker bug or stale quote problem

## Latency Fields

Prefer logged fields when present:

- `bitbank_fill_detection_source`
- `bitbank_execution_to_notice_ms`
- `notice_to_hedge_request_ms`
- `hedge_request_to_execution_ms`
- `notice_to_hedge_execution_ms`

`private_ws_spot_order` means bitbank private stream detected the fill. `fallback_order_info_poll`, `fallback_order_info_after_cancel_error`, or `cancel_order_response` mean REST/cancel reconciliation detected it.

For fallback-detected fills, `order_info.executed_at` can be null even when the
order is fully or partially filled. If `bitbank_execution_timestamp` is missing
and the exact fill time matters, use the bitbank skill/client to query:

```python
await PrivateClient().trade_history(pair="btc_jpy", order_id=<order_id>)
```

The returned trade rows include `executed_at` server timestamps. Compare that
timestamp against:

- the `maker_placed` timestamp, to measure maker rest time before fill
- `bitbank_fill_notice_timestamp`, to measure fill-to-detection latency
- `bitflyer_execution_timestamp`, to measure fill-to-hedge-execution latency

This distinction matters when diagnosing stale maker exposure: maker live time
is not the same as fill detection latency.

## Common Pitfalls

- Neutral amounts omitted: existing inventory is treated as active strategy position.
- Deferred hedges below bitFlyer minimum: small fills may be aggregated into later hedges; per-row cashflow can be skipped.
- `combined_pnl_skipped`: hedge size differed from current bitbank fill, so CSV cashflow may under/over-represent per-row formula calculations.
- Quote WebSocket 502/503 errors can make maker maintenance decisions depend on stale quote state.
