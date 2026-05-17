# GMO / bitFlyer Historical Arbitrage Rough Analysis

Generated from `src/vibe_bot/trades/history/history.py` using the
`gmo_bitflyer` pair.

## Data

Configuration:

```text
pair: gmo_bitflyer
left: GMO BTC_JPY
right: bitFlyer FX_BTC_JPY
days: 14
candle: 5 minutes
spread: GMO close - bitFlyer close
```

Result:

```text
points: 3901
range: 2026-05-03 13:20 JST to 2026-05-17 13:10 JST
mean spread:   +1,867 JPY
median spread: +1,371 JPY
p5 / p95:      -7,522 / +12,473 JPY
min / max:     -21,564 / +24,123 JPY
```

## Spread Distribution

```text
p0    -21564
p1    -10723
p5     -7522
p10    -5760
p25    -2613
p50     1371
p75     6187
p90    10144
p95    12473
p99    15853
p100   24123
```

Threshold hit frequency:

| Threshold JPY | BUY side: spread <= -threshold | SELL side: spread >= threshold |
|---:|---:|---:|
| 500 | 1489 / 3901 = 38.17% | 2170 / 3901 = 55.63% |
| 1000 | 1380 / 3901 = 35.38% | 2037 / 3901 = 52.22% |
| 1500 | 1244 / 3901 = 31.89% | 1920 / 3901 = 49.22% |
| 2000 | 1115 / 3901 = 28.58% | 1807 / 3901 = 46.32% |
| 3000 | 897 / 3901 = 22.99% | 1575 / 3901 = 40.37% |
| 5000 | 489 / 3901 = 12.54% | 1193 / 3901 = 30.58% |
| 7500 | 197 / 3901 = 5.05% | 777 / 3901 = 19.92% |
| 10000 | 55 / 3901 = 1.41% | 411 / 3901 = 10.54% |

## Simple Strategy Simulation

Very rough stage-1 style simulation:

```text
open long spread  when spread <= -threshold
open short spread when spread >= +threshold
close when spread crosses 0
```

This uses candle closes only. It ignores bid/ask, order-book depth, latency,
partial fills, GMO FAK misses, and bitFlyer market-order slippage.

Per 1 BTC notional:

| Threshold | Trades | Gross JPY | Position Fees JPY | Current Funding Impact JPY | Rough Net JPY |
|---:|---:|---:|---:|---:|---:|
| 1000 | 308 | 1,517,374 | 125,641 | -11,374 | 1,380,359 |
| 2000 | 258 | 1,511,136 | 119,989 | -13,902 | 1,377,246 |
| 3000 | 209 | 1,398,382 | 113,143 | -8,846 | 1,276,392 |
| 5000 | 123 | 1,080,755 | 95,626 | -8,846 | 976,283 |
| 7500 | 66 | 765,223 | 77,406 | -12,638 | 675,179 |
| 10000 | 30 | 449,757 | 51,323 | -12,638 | 385,796 |

For `0.001 BTC`, divide the numbers by 1000.

Example:

```text
threshold 5000 rough net ~= 976 JPY per 0.001 BTC over 14 days
```

Average gross profit per trade before execution cost:

| Threshold | Avg Gross JPY / BTC | Avg Gross JPY / 0.001 BTC | Avg Hold Hours |
|---:|---:|---:|---:|
| 1000 | 4,934 | 4.9 | 0.97 |
| 2000 | 5,859 | 5.9 | 1.11 |
| 3000 | 6,697 | 6.7 | 1.29 |
| 5000 | 8,787 | 8.8 | 1.85 |
| 7500 | 11,594 | 11.6 | 2.78 |
| 10000 | 14,992 | 15.0 | 4.06 |

## Fee Assumptions

Position fee estimate:

```text
GMO leverage fee:        0.04% / day
bitFlyer leverage point: 0.04% / day
combined estimate:       0.08% / day
```

At average BTC price around `12,637,800 JPY`, combined position fee is roughly:

```text
12,637,800 * 0.0008 / 24 ~= 421 JPY per BTC per hour
```

bitFlyer current funding fetched during analysis:

```text
current_funding_rate: 0.0001
next_funding_rate_settledate: 2026-05-17T05:00:00
```

Funding estimate at this rate:

```text
12,637,800 * 0.0001 ~= 1,264 JPY per BTC per 8h settlement
```

The simulation counted settlement crossings by direction. With current positive
funding, bitFlyer short receives and bitFlyer long pays. In this sample, the
net current-funding impact was slightly negative because more short-spread
positions crossed funding settlements.

## Rough Conclusion

The historical close-spread signal looks potentially profitable. Position fees
and current funding do not kill the strategy in this 14-day sample.

The main concern is execution quality. The expected edge per `0.001 BTC` trade
is small:

```text
threshold 1000:  ~4.9 JPY / trade before execution cost
threshold 5000:  ~8.8 JPY / trade before execution cost
threshold 10000: ~15.0 JPY / trade before execution cost
```

Because the real strategy uses GMO FAK limit and bitFlyer market IOC hedge,
low thresholds are likely too optimistic after bid/ask, slippage, latency, and
missed fills.

Practical initial live-test suggestion:

```text
Use threshold around 5000 JPY or higher.
Keep trend/noise/persistence gates enabled.
Compare expected spread vs actual execution spread in logs.
Avoid judging profitability from candle-close history alone.
```
