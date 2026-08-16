# Backtesting Contract

## Current gate status

G2.1 and G2.2 are implemented. The engine no longer fills a decision at the close that produced its
signal, and every run declares one of two deterministic next-open execution models. The rest of G2
remains incomplete, so backtest output is still non-promotable.

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

Every accepted order records its signal bar, decision time, eligible bar, fill bar, fill-bar open,
execution model, and execution assumptions. `events.jsonl` preserves the exact ordered event
sequence with monotonic sequence numbers.

An accepted decision on the last available bar cannot fill. It produces an `order_expired` event
with reason `end_of_data_before_eligible_bar`.

## Execution models

`next_bar_open` is the cost-free timing baseline. It fills at the next bar's open and rejects any
nonzero spread or slippage assumption so a caller cannot provide inputs that the model silently
ignores.

`next_bar_open_bid_ask` treats the next bar's open as a **synthetic reference mid**, derives a
side-specific touch from an assumed full quoted spread, then applies adverse slippage from that
touch. For reference open `O`, full-spread assumption `S` bps, and adverse-slippage assumption `L`
bps:

```text
synthetic bid = O * (1 - S / (2 * 10,000))
synthetic ask = O * (1 + S / (2 * 10,000))
buy fill      = synthetic ask * (1 + L / 10,000)
sell fill     = synthetic bid * (1 - L / 10,000)
```

Both inputs default to zero, use exact `Decimal` values, and must be nonnegative. The full spread
must be below 20,000 bps and adverse slippage below 10,000 bps so all derived prices remain
positive. These are deliberately named `quoted_spread_bps_assumption` and
`adverse_slippage_bps_assumption`; they are not observed Coinbase or Robinhood costs.

Example:

```bash
./market.sh backtest \
  --csv tests/fixtures/backtest/future_jump.csv \
  --cash 1000 --qty 1 --fast 2 --slow 3 --fee-bps 0 \
  --execution-model next_bar_open_bid_ask \
  --quoted-spread-bps-assumption 20 \
  --adverse-slippage-bps-assumption 10
```

At a `$20` next open, this produces a `$19.980` synthetic bid, `$20.020` synthetic ask, and
`$20.040020` simulated buy fill. Summary, event, and fill artifacts record the reference open,
both synthetic touches, pre-slippage touch, fill price, and named assumptions. Artifact schema
version 3 identifies this contract.

## Anti-look-ahead proof

`tests/fixtures/backtest/future_jump.csv` creates an EMA cross at a `$12` close followed by a `$20`
next open. The golden timing test requires the fill to occur at `$20`, proving the strategy cannot
capture the unseen close-to-open jump. It also asserts this event order:

```text
signal bar_close < decision_accepted < next bar_open < order_eligible < fill
```

## Known invalidities still open

- Spread and slippage are configurable research assumptions, not calibrated venue costs. Robinhood
  routing versions and Coinbase tiers remain G2.3 work.
- The fee input is still ambiguous about per-side versus round-trip semantics; G2.3a remains open.
- Terminal inventory is still converted to cash without a visible costed fill; G2.4 remains open.
- Cash, inventory, cost basis, realized/unrealized P&L, and liquidation value are not yet governed
  by the G2.5 accounting journal.
- Trade lifecycle counts, benchmarks, statistics, and fully reproducible run identity remain open.

No result from this intermediate engine can support a profitability or live-money decision.
