# GMO / bitFlyer Arbitrage

This bot trades GMO `BTC_JPY` as a taker and hedges on bitFlyer. It reuses the
stage idea from the bitbank / bitFlyer arbitrage bot, but it does not maintain a
maker order. A stage trigger only creates a candidate trade. The candidate must
then pass the filter gates before the bot places orders.

Run dry-run:

```bash
python3 -m vibe_bot.trades.gmo_bitflyer.arbitrage
```

Run live:

```bash
python3 -m vibe_bot.trades.gmo_bitflyer.arbitrage --live
```

## Spread Definitions

The bot keeps full order books for both exchanges and uses executable VWAP for
the configured `--order-size`.

`BUY` action:

```text
buy_price = gmo_ask_vwap - bitflyer_bid_vwap
```

This means buy GMO and sell bitFlyer. Lower spread is better.

`SELL` action:

```text
sell_price = gmo_bid_vwap - bitflyer_ask_vwap
```

This means sell GMO and buy bitFlyer. Higher spread is better.

The trend filter uses mid spread:

```text
mid_spread = GMO mid price - bitFlyer mid price
```

## Trade Gate

The gate lives in `arbitrage.py` in `_check_trade_condition()`. The order is:

1. Maintenance guard and cooldown
2. Stage trigger
3. Filter warmup
4. EMA trend agreement
5. Noise buffer
6. Persistence
7. Slippage-capped order prices

If any gate blocks the trade, the event log writes `trade_condition_blocked`
with a clear `reason`, such as `stage_trigger_not_crossed`, `trend_disagrees`,
`edge_below_noise_buffer`, or `persistence`.

## 1. Maintenance Guard and Cooldown

The bitFlyer maintenance guard blocks trading during the configured JST window.

Parameters:

```text
--disable-bitflyer-maintenance-guard
--bitflyer-maintenance-start-jst 03:59:30
--bitflyer-maintenance-end-jst 04:12:30
```

To disable this gate:

```bash
--disable-bitflyer-maintenance-guard
```

Cooldown prevents immediate repeated entries after a trade attempt.

Parameter:

```text
--entry-cooldown-seconds 5.0
```

Larger values trade less frequently. Smaller values allow faster repeated
entries.

To disable cooldown:

```bash
--entry-cooldown-seconds 0
```

## 2. Stage Trigger

The stage trigger decides whether there is a candidate trade at all.

Parameters:

```text
--threshold-jpy 1000
--threshold-offset-jpy 0
--stage-size 0.001
--max-stages 3
--order-size 0.001
```

When position is zero:

```text
BUY trigger  = threshold_offset_jpy - threshold_jpy
SELL trigger = threshold_offset_jpy + threshold_jpy
```

Example with `--threshold-offset-jpy 0 --threshold-jpy 1000`:

```text
BUY candidate  when buy_price  < -1000
SELL candidate when sell_price >  1000
```

For later stages, the trigger moves by another `threshold_jpy` per stage. With a
long GMO position, the next BUY-open trigger becomes more negative. With a short
GMO position, the next SELL-open trigger becomes more positive.

How each parameter affects the condition:

```text
--threshold-jpy
```

Higher means fewer entries and wider spacing between stages. Lower means more
entries and tighter stage spacing. It must be positive, so this gate cannot be
fully disabled by setting it to zero.

```text
--threshold-offset-jpy
```

Moves the whole trigger ladder. Use this when the GMO / bitFlyer spread has a
persistent baseline bias.

```text
--stage-size
```

Target exposure added by each stage.

```text
--max-stages
```

Caps maximum directional exposure. `max_position = stage_size * max_stages`.

```text
--order-size
```

Maximum size per trade attempt. The actual stage amount is the smaller of
`order_size` and the amount needed to reach the next stage.

To make this gate very permissive:

```bash
--threshold-jpy 1
```

There is no CLI setting that truly disables stage triggers.

## 3. Filter Warmup

The EMA trend and residual noise filter needs samples before it can approve a
trade.

Parameter:

```text
--min-filter-samples 20
```

Higher values wait longer after startup or reconnect before trading. Lower
values allow earlier trading with less stable estimates.

To minimize warmup:

```bash
--min-filter-samples 1
```

This does not disable the trend or noise gates by itself; it only reduces the
sample requirement.

## 4. EMA Trend Agreement

The bot calculates an EMA of `mid_spread`.

Parameter:

```text
--ema-alpha 0.08
```

For a BUY candidate, the trend must be at or below the BUY trigger:

```text
trend_spread <= trigger_price
```

For a SELL candidate, the trend must be at or above the SELL trigger:

```text
trend_spread >= trigger_price
```

How `--ema-alpha` affects the condition:

```text
lower alpha = slower trend, more resistant to noisy spikes
higher alpha = faster trend, easier for short-term moves to affect approval
```

To make the trend gate as close to instantaneous as the code allows:

```bash
--ema-alpha 1
```

That makes `trend_spread` equal the latest `mid_spread`. This effectively removes
the long-period trend behavior, but the comparison still exists. There is no CLI
setting that fully disables trend agreement.

## 5. Noise Buffer

The bot tracks residual noise:

```text
residual = raw_mid_spread - ema_trend_spread
residual_noise = RMS(residuals over noise_window)
```

Then it requires extra edge:

```text
required_extra_edge = max(min_extra_edge_jpy, residual_noise * noise_multiplier)
```

For BUY:

```text
edge = trigger_price - buy_price
trade only if edge >= required_extra_edge
```

For SELL:

```text
edge = sell_price - trigger_price
trade only if edge >= required_extra_edge
```

Parameters:

```text
--noise-window 60
--noise-multiplier 2.0
--min-extra-edge-jpy 0
```

How each parameter affects the condition:

```text
--noise-window
```

Higher values smooth the noise estimate over more samples. Lower values react
faster to current noise. It must be at least 2.

```text
--noise-multiplier
```

Higher values demand more extra edge when the spread is noisy. Lower values make
the gate easier to pass.

```text
--min-extra-edge-jpy
```

Always requires at least this much extra edge, even when measured noise is low.

To disable the volatility part of the noise buffer:

```bash
--noise-multiplier 0 --min-extra-edge-jpy 0
```

The comparison still exists, but `required_extra_edge` becomes zero once the
filter is warmed up.

## 6. Persistence

The same candidate must remain valid for the configured time before execution.
The candidate identity is:

```text
action, stage_index, trigger_price, amount
```

Parameter:

```text
--persistence-seconds 2.0
```

Higher values reject more short-lived spikes. Lower values react faster.

To disable persistence:

```bash
--persistence-seconds 0
```

## 7. Slippage-Capped Orders

Live mode does not send blind market orders. It sends aggressive limit orders:

```text
GMO:      LIMIT FAK
bitFlyer: LIMIT IOC
```

Parameter:

```text
--max-slippage-jpy 500
```

For BUY:

```text
GMO BUY limit        = gmo_ask_vwap + max_slippage_jpy
bitFlyer SELL limit  = bitflyer_bid_vwap - max_slippage_jpy
```

For SELL:

```text
GMO SELL limit       = gmo_bid_vwap - max_slippage_jpy
bitFlyer BUY limit   = bitflyer_ask_vwap + max_slippage_jpy
```

Higher values increase fill probability but allow worse execution. Lower values
reduce slippage risk but increase partial or missed fills.

To make this gate strict:

```bash
--max-slippage-jpy 0
```

There is no safe CLI setting that fully disables slippage protection. Very large
values approximate market-order behavior, but that is intentionally not the
default.

## Practical Presets

Conservative:

```bash
python3 -m vibe_bot.trades.gmo_bitflyer.arbitrage \
  --threshold-jpy 1500 \
  --ema-alpha 0.05 \
  --noise-window 120 \
  --noise-multiplier 2.5 \
  --persistence-seconds 3 \
  --max-slippage-jpy 300
```

Fast dry-run exploration:

```bash
python3 -m vibe_bot.trades.gmo_bitflyer.arbitrage \
  --threshold-jpy 500 \
  --ema-alpha 0.2 \
  --noise-window 20 \
  --noise-multiplier 1 \
  --persistence-seconds 0.5
```

Near-minimal filtering:

```bash
python3 -m vibe_bot.trades.gmo_bitflyer.arbitrage \
  --threshold-jpy 1 \
  --ema-alpha 1 \
  --min-filter-samples 1 \
  --noise-multiplier 0 \
  --min-extra-edge-jpy 0 \
  --persistence-seconds 0 \
  --entry-cooldown-seconds 0
```

This still uses the stage trigger and slippage-capped limit orders.
