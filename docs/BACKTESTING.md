# Backtesting Contract

## Current gate status

G2.1 is implemented. The engine no longer fills a decision at the close that produced its signal.
The rest of G2 remains incomplete, so backtest output is still non-promotable.

## Event order

For each contiguous, quality-approved hourly bar:

```text
bar t open
  -> fill any order accepted after bar t-1 close
  -> bar t close
  -> mark portfolio at bar t close
  -> evaluate strategy using bars through t
  -> evaluate risk
  -> accept or block decision
  -> accepted order becomes eligible at bar t+1 open
```

The current declared execution model is `next_bar_open`. Every accepted order records its signal
bar, decision time, eligible bar, fill bar, and fill-bar open. `events.jsonl` preserves the exact
ordered event sequence with monotonic sequence numbers.

An accepted decision on the last available bar cannot fill. It produces an `order_expired` event
with reason `end_of_data_before_eligible_bar`.

## Anti-look-ahead proof

`tests/fixtures/backtest/future_jump.csv` creates an EMA cross at a `$12` close followed by a `$20`
next open. The golden timing test requires the fill to occur at `$20`, proving the strategy cannot
capture the unseen close-to-open jump. It also asserts this event order:

```text
signal bar_close < decision_accepted < next bar_open < order_eligible < fill
```

## Known invalidities still open

- Only a raw next-open execution model exists; spread and adverse slippage belong to G2.2.
- The fee input is still ambiguous and does not model Robinhood routing versions or Coinbase tiers;
  G2.3 and G2.3a remain open.
- Terminal inventory is still converted to cash without a visible costed fill; G2.4 remains open.
- Cash, inventory, cost basis, realized/unrealized P&L, and liquidation value are not yet governed
  by the G2.5 accounting journal.
- Trade lifecycle counts, benchmarks, statistics, and fully reproducible run identity remain open.

No result from this intermediate engine can support a profitability or live-money decision.
