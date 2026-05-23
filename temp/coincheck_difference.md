# GMO / bitFlyer vs Coincheck / bitFlyer Arbitrage

Generated on 2026-05-17.

## Summary

The main difference is that `gmo_bitflyer` uses GMO's stronger order controls and leverage/margin position model, while `coincheck_bitflyer` uses Coincheck spot orders and has weaker limit-order control.

| Area | `gmo_bitflyer` | `coincheck_bitflyer` |
|---|---|---|
| Primary exchange | GMO | Coincheck |
| Primary leg order | GMO `LIMIT` with `time_in_force="FAK"` | Coincheck `buy` / `sell` limit order |
| Hedge leg | bitFlyer `MARKET IOC` | bitFlyer `MARKET IOC` |
| Limit order behavior | Immediate-or-cancel via FAK | Place limit, poll fills, then cancel remainder |
| Partial fill handling | Native FAK semantics | Manual cancel-after-poll |
| Position model | GMO open positions | Coincheck spot BTC balance |
| Can short primary leg | Yes, via GMO margin/leverage | Not directly; Coincheck is spot-balance based |
| Config pair | `BTC_JPY` | `btc_jpy` |
| Quote source | GMO public websocket order book | Coincheck REST snapshot plus websocket order book diffs |
| Order success metric | GMO filled / attempted | Coincheck filled / attempted |
| Risk profile | Margin/position risk | Spot inventory risk |

## Execution Flow

`gmo_bitflyer`:

```text
GMO LIMIT FAK -> hedge filled amount on bitFlyer MARKET IOC
```

`coincheck_bitflyer`:

```text
Coincheck LIMIT -> poll transactions -> cancel unfilled remainder -> hedge filled amount on bitFlyer MARKET IOC
```

## Practical Implications

`gmo_bitflyer` can open both long and short stages on GMO because GMO supports margin/leverage positions.

`coincheck_bitflyer` is based on Coincheck spot balance. A `SELL` action requires existing Coincheck BTC. It does not create a real short position on Coincheck.

`coincheck_bitflyer` is structurally similar to `gmo_bitflyer`, but less atomic. Coincheck does not provide the same FAK-style limit order behavior used in the GMO bot. The implementation places a Coincheck limit order, polls for fills, and cancels the unfilled remainder.

That means `coincheck_bitflyer` has more timing risk:

- Coincheck may fill partially.
- Coincheck fills may arrive more slowly than the polling window.
- The bitFlyer hedge happens only after detected Coincheck fills.
- The bitFlyer hedge can still partially fill or fail.

## Risk Difference

`gmo_bitflyer` risk is mainly margin/position risk and execution slippage.

`coincheck_bitflyer` risk is mainly spot inventory risk, delayed fill detection, and less atomic hedge timing.

For strategies that need strict immediate-or-cancel behavior on the primary leg, `gmo_bitflyer` is safer structurally. For strategies that can tolerate spot inventory and manual cancel-after-poll behavior, `coincheck_bitflyer` is usable but should be tested conservatively.
