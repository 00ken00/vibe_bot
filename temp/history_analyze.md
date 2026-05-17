# Non-Bitbank Historical Arbitrage Rough Analysis

Generated from public historical candle data on 2026-05-17.

This analysis excludes bitbank and compares:

- `coincheck_bitflyer`
- `coincheck_gmo`
- `gmo_bitflyer`

## Data Window

- Window: 2026-05-12 17:00 to 2026-05-17 16:00 JST
- Resolution: 1-hour candles
- Coincheck source: Coincheck chart candles
- GMO source: GMO 1-hour klines
- bitFlyer source: 30-minute LightChart candles aggregated into hourly closes

The spread is calculated as:

```text
left exchange candle close - right exchange candle close
```

## Summary

| Pair | Samples | Mean Spread | Std Dev | Mean Abs Spread | P95 Abs Spread | Min Spread | Max Spread | Positive / Negative | Sign Crossings | >=5k | >=10k | >=20k |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `coincheck_bitflyer` | 120 | 10,342.65 | 6,300.58 | 10,558.92 | 21,637.00 | -4,967.00 | 29,768.00 | 115 / 5 | 10 | 98 | 62 | 9 |
| `coincheck_gmo` | 119 | 9,317.84 | 6,674.36 | 10,513.74 | 18,239.00 | -17,230.00 | 20,407.00 | 106 / 13 | 6 | 108 | 70 | 1 |
| `gmo_bitflyer` | 127 | 1,277.46 | 7,310.06 | 6,135.78 | 13,406.00 | -14,494.00 | 16,008.00 | 67 / 60 | 24 | 69 | 26 | 0 |

## Ranking

### Highest Raw Spread Magnitude

1. `coincheck_bitflyer`
2. `coincheck_gmo`
3. `gmo_bitflyer`

`coincheck_bitflyer` has the highest mean absolute spread, highest p95 absolute spread, and highest maximum spread in this sample.

### Most Mean-Reverting Behavior

1. `gmo_bitflyer`
2. `coincheck_bitflyer`
3. `coincheck_gmo`

`gmo_bitflyer` crossed spread sign 24 times in the sample, much more often than the Coincheck pairs. This suggests more two-way movement around zero, but the spread magnitude is materially smaller.

## Interpretation

`coincheck_bitflyer` is still the strongest non-bitbank pair by raw spread magnitude. It was positive in 115 of 120 hourly candles, so the direction implied by candle closes was usually:

```text
buy bitFlyer, sell Coincheck
```

However, that persistent positive spread may represent a stable basis rather than a clean mean-reverting arbitrage. It did go negative 5 times and crossed sign 10 times over the 5-day sample, but most of the time Coincheck stayed above bitFlyer.

`coincheck_gmo` also shows large spreads, with more candles above 10k JPY than `coincheck_bitflyer`, but it had a lower p95 and much fewer extreme >=20k JPY candles.

`gmo_bitflyer` is weaker on spread size but more balanced directionally. If the strategy depends on spread oscillation around zero, it is structurally more interesting. If the strategy is simply looking for the largest raw gap, it is less attractive.

## Caveats

This is only a rough historical screen.

It uses candle closes only and ignores:

- Live bid/ask spread
- Order-book depth
- Queue position
- Maker/taker fees
- Slippage
- Latency
- Funding, SFD, or other bitFlyer FX-specific costs
- Deposit, withdrawal, and inventory-transfer constraints

The next useful step is to compare live executable bid/ask spreads and depth for `coincheck_bitflyer`, especially after fees and realistic fill assumptions.
