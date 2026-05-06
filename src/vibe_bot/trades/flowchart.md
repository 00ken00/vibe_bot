# bitbank / bitFlyer Arbitrage Flow

## Main Loop

```mermaid
flowchart TD
    A[run_bot] --> L1[log: bot_started]
    L1 --> QF[Start WebSocketQuoteFeed]
    L1 --> TR[Start ArbitrageTrader]
    L1 --> WEB[Start web HTTP and browser WS]

    QF --> QW[Connect public websocket feeds]
    QW --> QWL[log: quote_ws_connected]
    QWL --> QB[Maintain bitbank order book]
    QWL --> BF[Maintain bitFlyer order book]
    QB --> QP[Update Quote from order-size VWAP]
    BF --> QP

    TR --> MODE{"live mode?"}
    MODE -- no --> TICK{Every maker_update_interval}
    MODE -- yes --> INIT[Fetch bitbank assets and margin positions, fetch bitFlyer positions]
    INIT --> CHECK{"positions match within min_order_size?"}
    CHECK -- no --> FAIL[log: position_initialization_mismatch and stop]
    CHECK -- yes --> SETPOS[Set initial strategy position]
    SETPOS --> TICK
    TICK --> READY{"quote.ready?"}
    READY -- no --> WAIT[Action: WAITING_FOR_QUOTES]
    READY -- yes --> REFRESH[Refresh active maker if live]
    REFRESH --> TARGET[Choose target]
    TARGET --> HAS{"target exists?"}
    HAS -- no --> IDLE[Action: IDLE]
    IDLE --> CANCEL0[Cancel active maker if any]
    HAS -- yes --> SAME{"same as active maker?"}
    SAME -- yes --> MAINTAIN[Action: MAINTAIN_BUY or MAINTAIN_SELL]
    SAME -- no --> REPLACE[Replace maker]
```

## Target Conditions

```mermaid
flowchart TD
    START[quote ready] --> CALC[Compute buy_price and sell_price from VWAP]
    CALC --> POS{position}

    POS -- position > 0 --> LC{"sell_price > offset?"}
    LC -- yes --> CLOSE_LONG[Target SELL, trigger = offset]
    LC -- no --> NONE1[No target]

    POS -- position < 0 --> SC{"buy_price < offset?"}
    SC -- yes --> CLOSE_SHORT[Target BUY, trigger = offset]
    SC -- no --> NONE2[No target]

    POS -- position == 0 --> ENTRY["buy_open = offset - threshold<br/>sell_open = offset + threshold"]
    ENTRY --> BE[buy_edge = buy_open - buy_price]
    ENTRY --> SE[sell_edge = sell_price - sell_open]
    BE --> EDGE{"buy_edge > 0 or sell_edge > 0?"}
    SE --> EDGE
    EDGE -- no --> NONE3[No target]
    EDGE -- yes --> COMP{"sell_edge > buy_edge?"}
    COMP -- yes --> OPEN_SHORT[Target SELL, trigger = sell_open]
    COMP -- no --> OPEN_LONG[Target BUY, trigger = buy_open]
```

## Maker Price Construction

```mermaid
flowchart TD
    T[Target action + trigger] --> A{action}

    A -- BUY --> BP[passive = bitbank_ask - tick_size]
    BP --> BPROF[profitable = bitFlyer bid VWAP + trigger]
    BPROF --> BPRICE["price = floor_to_tick min(passive, profitable)"]
    BPRICE --> BVALID{"price < bitbank_ask and price > 0?"}
    BVALID -- yes --> BPOS{"current position < 0?"}
    BPOS -- yes --> BCMARGIN["Build bitbank margin buy maker, position_side = short"]
    BPOS -- no --> BCSPOT[Build bitbank spot buy maker]
    BVALID -- no --> NONEB[No maker target]

    A -- SELL --> SP[passive = bitbank_bid + tick_size]
    SP --> SPROF[profitable = bitFlyer ask VWAP + trigger]
    SPROF --> SPRICE["price = ceil_to_tick max(passive, profitable)"]
    SPRICE --> SVALID{"price > bitbank_bid and price > 0?"}
    SVALID -- yes --> SPOS{"current position <= 0?"}
    SPOS -- yes --> SMARGIN["Build bitbank margin sell maker, position_side = short"]
    SPOS -- no --> SSSPOT[Build bitbank spot sell maker]
    SVALID -- no --> NONES[No maker target]
```

## Maker Replacement, Logs, And bitbank Place Order

```mermaid
sequenceDiagram
    participant Trader
    participant Logger
    participant Bitbank
    participant BitbankHttp

    Trader->>Trader: _replace_maker(target)
    Trader->>Trader: _cancel_active_maker("replace")

    alt dry_run
        Trader->>Logger: event maker_quote
        Trader->>Trader: active_maker = DRY-RUN target
        Trader->>Trader: Action = QUOTE_BUY_DRY_RUN or QUOTE_SELL_DRY_RUN
    else live
        Trader->>Logger: event maker_place_attempt
        Trader->>Bitbank: place_order(pair, side, limit, amount, price, post_only=True, position_side)
        Bitbank->>BitbankHttp: signed REST request
        BitbankHttp-->>Logger: event private_api_trace<br/>exchange=bitbank method=POST raw_response
        Bitbank-->>Trader: Order
        Trader->>Trader: active_maker = placed target
        Trader->>Trader: Action = PLACED_BUY or PLACED_SELL
        Trader->>Logger: event maker_placed
    end
```

## Active Maker Refresh And Hedge

```mermaid
sequenceDiagram
    participant Trader
    participant Logger
    participant Bitbank
    participant BitbankHttp
    participant BitFlyer
    participant BitFlyerHttp

    Trader->>Trader: _refresh_active_maker()

    alt no active maker or dry_run
        Trader-->>Trader: return
    else live active maker
        Trader->>Bitbank: order_info(pair, order_id)
        Bitbank->>BitbankHttp: signed REST request
        BitbankHttp-->>Logger: event private_api_trace<br/>exchange=bitbank method=POST raw_response
        Bitbank-->>Trader: Order

        alt executed_amount increased
            Trader->>Trader: _hedge_fill(delta)
            Trader->>BitFlyer: send_child_order(MARKET, IOC)
            BitFlyer->>BitFlyerHttp: signed REST request
            BitFlyerHttp-->>Logger: event private_api_trace<br/>exchange=bitflyer method=POST raw_response
            BitFlyer-->>Trader: child_order_acceptance_id

            loop until average execution found or 3s timeout
                Trader->>BitFlyer: executions(child_order_acceptance_id)
                BitFlyer->>BitFlyerHttp: signed REST request
                BitFlyerHttp-->>Logger: event private_api_trace<br/>exchange=bitflyer method=GET raw_response
                BitFlyer-->>Trader: executions
            end

            Trader->>Trader: update position, realized_pnl, filled_base, trade_count
            Trader->>Logger: CSV trade row with slippage and cashflow
        end

        alt order terminal
            Trader->>Trader: active_maker = None
            Trader->>Logger: event maker_done
        end
    end
```

## Cancel Path

```mermaid
sequenceDiagram
    participant Trader
    participant Logger
    participant Bitbank
    participant BitbankHttp

    Trader->>Trader: _cancel_active_maker(reason)

    alt no active maker
        Trader-->>Trader: return
    else dry_run or DRY-RUN maker
        Trader->>Trader: active_maker = None
        Trader->>Logger: event maker_removed
    else live maker
        Trader->>Trader: active_maker = None
        Trader->>Trader: Action = CANCELING_MAKER
        Trader->>Bitbank: cancel_order(pair, order_id)
        Bitbank->>BitbankHttp: signed REST request
        BitbankHttp-->>Logger: event private_api_trace<br/>exchange=bitbank method=POST raw_response

        alt success
            Bitbank-->>Trader: canceled order
            Trader->>Logger: event maker_canceled
            Trader->>Trader: Action = CANCELED_MAKER
        else failure
            Bitbank-->>Trader: error
            Trader->>Logger: event maker_cancel_failed
            Trader->>Trader: Action = CANCEL_FAILED
            Trader-->>Trader: raise
        end
    end
```

## Private API Trace Events

```mermaid
flowchart TD
    A[Private client method] --> H[Exchange HTTP client]
    H --> R[Signed REST request]
    R --> LOG[log: private_api_trace with params, body, status, raw_response]
    LOG --> P[Parse response and map API errors]
    P --> EX{exchange}
    EX -- bitbank --> BB{method}
    BB --> BB1[place_order]
    BB --> BB2[cancel_order]
    BB --> BB3[order_info]
    EX -- bitFlyer --> BF{method}
    BF --> BF1[send_child_order]
    BF --> BF2[executions]
```
