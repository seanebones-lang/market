# Backtesting Contract

## Current gate status

G2.1 through G2.10 are implemented and the complete G2 research-engine gate passes. The engine no
longer fills a decision at the close that produced
its signal, every run declares one of two deterministic next-open execution models,
venue/API/routing cost profiles keep Robinhood v1 and v2 economics separate, and every transaction
fee is explicitly defined per execution fill. End-of-data liquidation is now a visible, fully
costed sell fill that leaves the reported portfolio flat. Every fill now posts through an immutable accounting
journal, and every mark distinguishes mid-marked equity from costed net liquidation value. Every
run now also carries three cost-equivalent passive benchmarks.

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
  --cash 1000 --qty 1 --fast 2 --slow 3 \
  --transaction-fee-bps-per-fill-assumption 0 \
  --execution-model next_bar_open_bid_ask \
  --quoted-spread-bps-assumption 20 \
  --adverse-slippage-bps-assumption 10
```

At a `$20` next open, this produces a `$19.980` synthetic bid, `$20.020` synthetic ask, and
`$20.040020` simulated buy fill. Summary, event, and fill artifacts record the reference open,
both synthetic touches, pre-slippage touch, fill price, and named assumptions. Artifact schema
version 10 includes this contract together with the G2.3/G2.3a cost fields, G2.4 terminal
liquidation fields, G2.5 accounting records, G2.6 lifecycle records, G2.7 benchmarks, and G2.8
performance statistics.

## Venue cost profiles

G2.3 adds three mutually exclusive profiles. All rates remain configured assumptions; the engine
does not call them observed costs.

| Profile | Venue/API/routing | Spread treatment | Separate transaction fee |
|---|---|---|---|
| `legacy_unclassified` | Unclassified | Depends on execution model | Explicit per-fill assumption; defaults to 5 bps |
| `robinhood_crypto_api_v1_market_maker` | Robinhood Crypto / v1 / market maker | Required positive full-spread assumption | Forbidden; applied rate is zero |
| `robinhood_crypto_api_v2_exchange_taker` | Robinhood Crypto / v2 / exchange | Required positive full-spread assumption | Required positive taker-rate assumption on executed notional |

The Robinhood profiles require `next_bar_open_bid_ask`. The v1 profile rejects any separate fee
input, while v2 requires a positive per-fill taker-fee assumption. This prevents a v1 run from
accidentally adding an exchange fee and prevents a v2 run from silently omitting its configured
taker fee.

The v1 profile embeds route cost in the synthetic touch. Robinhood defines its displayed buy and
sell spread from mid to ask or bid, while this engine's `quoted_spread_bps_assumption` is the **full**
bid-to-ask spread. Therefore a symmetric 96-bps one-sided spread corresponds to a 192-bps engine
input. That conversion is an inference for a symmetric synthetic model, not an observed quote.

The v2 profile calculates each simulated fee as:

```text
fee = executed quantity * simulated fill price
      * transaction_fee_bps_per_fill_assumption / 10,000
```

As verified on 2026-08-16, Robinhood documents v1 as the non-fee-tier API action and v2 as the
fee-tier action. Robinhood also states that v2 API orders are charged the taker rate while its
maker/taker rollout is incomplete. Its 2026-06-22 standard schedule lists taker tiers from 95 bps
down to 3 bps and applies the percentage to executed dollar value. These references can change and
must be rechecked before any research freeze:

- [Robinhood Crypto Trading API](https://robinhood.com/us/en/support/articles/crypto-api/)
- [Robinhood crypto order routing](https://robinhood.com/us/en/support/articles/smart-exchange-routing/)
- [Robinhood crypto fee tiers](https://robinhood.com/us/en/support/articles/crypto-fee-tiers/)
- [Robinhood Crypto standard pricing schedule](https://cdn.robinhood.com/assets/robinhood/legal/rhc-fee-schedule.pdf)

## Fee application contract

G2.3a removes `fee_bps` and `transaction_fee_bps_assumption` from the Python and CLI interfaces.
The only configurable transaction-fee input is now
`transaction_fee_bps_per_fill_assumption`. It is charged once against the executed notional of
every buy or sell fill. It is not a round-trip total, and the engine never divides it in half.

For equal `$100` buy and sell fills at 95 bps, each fill pays `$0.95`; the two-fill lifecycle pays
`$1.90`. Round-trip cost is therefore a derived sum of its actual fill costs, not a separate input.
The paper `SimBroker` uses the same definition and writes it into fill provenance.

The CLI rejects both removed ambiguous flags:

```text
--fee-bps
--transaction-fee-bps-assumption
```

Schema version 11 records `fee_calculation_basis=executed_notional_per_fill`, the configured
`transaction_fee_bps_per_fill_assumption`, and the per-fill rate applied in summary, event, and fill
artifacts.

## Terminal liquidation contract

After the final bar closes and any last-bar decision is expired, G2.4 liquidates remaining BTC with
an explicit sell request and fill:

```text
final bar_close
  -> optional order_expired for a decision with no eligible next bar
  -> terminal_liquidation_requested
  -> sell fill
  -> post_terminal_liquidation equity point
```

The final close is a synthetic reference mid. A `next_bar_open` run uses
`terminal_liquidation_model=last_bar_close` and sells at that close. A
`next_bar_open_bid_ask` run uses `last_bar_close_bid_ask`: it derives the final synthetic bid from
the same full-spread assumption and applies the same adverse sell slippage used by ordinary fills.
The venue cost profile then charges its per-fill rate against the terminal fill's executed
notional. Robinhood v1 therefore embeds terminal cost in the synthetic spread with zero separate
fee, while v2 applies its declared taker-fee assumption to the terminal fill.

Terminal artifacts record the final reference close, synthetic bid and ask, pre-slippage touch,
fill price, executed quantity, fee, venue profile, and reason
`terminal_liquidation_end_of_data`. Summary fields distinguish pre-liquidation inventory from the
actual final position:

```text
position_before_terminal_liquidation_btc
terminal_liquidation_fills
terminal_liquidation_qty_btc
terminal_liquidation_fee_usd
final_inventory_btc
```

When there is inventory, `final_inventory_btc` is zero and the last equity artifact has
`stage=post_terminal_liquidation`. When there is no inventory, no synthetic terminal order or fill
is created.

## Portfolio accounting contract

G2.5 routes the opening balance and every ordinary or terminal fill through one exact-`Decimal`,
append-only portfolio journal. A fill records its cash, BTC inventory, gross cost-basis, gross
realized-P&L, and fee deltas together with all after-state balances. Its `event_sequence` points to
the corresponding fill in `events.jsonl`; fills also record their `accounting_journal_sequence`.
The engine rejects overbuys, oversells, negative fees, invalid prices, and invalid marks before
they can create a journal entry.

The accounting method is weighted-average gross cost basis with fees separate:

```text
buy cost-basis increase = executed quantity * buy fill price
sell allocated basis    = pre-sell cost basis * sold quantity / pre-sell inventory
realized gross P&L       = sell executed notional - allocated basis
unrealized gross P&L     = inventory * mark price - remaining cost basis
```

Entry and exit fees are never hidden in cost basis or gross P&L. This makes the reconciliation
identity explicit at every mark:

```text
marked equity
  = cash + inventory * mark price
  = starting cash + realized gross P&L + unrealized gross P&L - cumulative fees
```

Every journal entry and equity snapshot records `accounting_identity_residual_usd`; a nonzero
residual raises an error. `accounting.jsonl` preserves the immutable opening and fill transitions.

Marked equity values inventory at the candle close mid. Net liquidation value answers a different
question: how much cash the portfolio would hold after selling its open BTC using the run's
declared spread, adverse slippage, and per-fill fee assumptions:

```text
net liquidation value
  = cash + inventory * estimated liquidation sell price - estimated liquidation fee
```

Schema version 11 and the Python result model therefore use explicit names instead of the old
ambiguous `pnl_usd`, `final_usd`, `fees_usd`, and `return_pct` names. They report final cash and
inventory, remaining cost basis, average entry, realized and unrealized gross P&L, cumulative fees,
marked equity, net liquidation value, both after-fee P&L views, and net-liquidation return. For a
terminally liquidated flat run, marked equity and net liquidation value converge to final cash.

## Order and trade lifecycle contract

G2.6 uses the following noninterchangeable units:

| Unit | Definition |
|---|---|
| Order | One risk-accepted strategy request or one terminal-liquidation request |
| Execution | One `Fill`; an order may have zero, one, or multiple executions |
| Partial-fill execution | An execution whose quantity is smaller than its parent order's requested quantity |
| Closed trade | One sell execution that reduces positive inventory and realizes matched P&L |
| Round trip | One complete transition from flat to positive inventory and back to flat |
| Open round trip | A flat-to-long cycle that has not returned to flat |

An order aggregates executions by client order ID and ends as `filled`, `partially_filled`,
`expired`, `execution_rejected`, or `unfilled`. A partially filled order may also carry an expired
or rejected unfilled disposition. Aggregate executed quantity may never exceed requested quantity,
and every execution must reconcile exactly to its G2.5 accounting-journal entry.

Closed-trade outcomes use weighted-average basis and both sides' fees. For each sell execution:

```text
allocated entry fees = remaining unallocated entry fees * sold quantity / pre-sell inventory
net realized P&L     = sell notional - allocated gross basis
                       - allocated entry fees - exit fee
outcome              = win if positive, loss if negative, breakeven if zero
```

Multiple sell executions can therefore create multiple closed trades inside one round trip. A
two-execution entry and two-execution exit is two orders, four executions, four partial-fill
executions, two closed trades, and one round trip. This prevents the old practice of using `fills`,
`trades`, and `round trips` as synonyms.

Schema version 11 carries `lifecycle.jsonl` with one lifecycle summary, one record per order, one per
closed trade, and one per completed round trip. Summary and CLI output separately report order and
execution states, partial fills, closed-trade outcomes, round trips, and open inventory/basis/entry
fees.

## Benchmark contract

G2.7 produces three benchmarks from the same approved bars:

| Benchmark | Capital and schedule |
|---|---|
| Cash | Holds the full starting cash balance; no executions or costs |
| Matched-notional buy-and-hold | Buys once at the first bar open |
| Periodic DCA | Splits the same total gross buy notional across configurable bar intervals |

The matched gross notional is the strategy's maximum concurrent gross inventory cost basis across
its immutable journal. It is not cumulative buy turnover, which could reuse the same capital many
times and create an impossible passive allocation. If that gross amount plus its entry fee would
exceed starting cash, the benchmark caps it at:

```text
maximum affordable gross notional
  = starting cash / (1 + configured per-fill fee rate)
```

Both invested benchmarks retain all unused cash. They execute buys at the declared synthetic
first/periodic bar-open buy price and liquidate all BTC at the final candle close using the same
synthetic bid, adverse slippage, and venue fee treatment as the strategy. DCA defaults to one entry
every 168 hourly bars and is configurable with `--benchmark-dca-interval-bars`. The schedule begins
at the first bar and never observes future prices.

Absolute comparison reports strategy-minus-benchmark after-fee P&L and return percentage points.
G2.7's deliberately narrow risk-adjusted measure is:

```text
net P&L over maximum drawdown = final net P&L after fees / maximum dollar NLV drawdown
```

The ratio is undefined when maximum drawdown is zero; artifacts record that state instead of
inventing zero or infinity. This is not called Sharpe or Calmar and is not annualized.

Schema version 11 carries `benchmarks.jsonl`, `benchmark_fills.jsonl`, and
`benchmark_equity.jsonl`. These preserve the benchmark contract, three results, three comparisons,
every passive execution, and every bar-close net-liquidation point.

## Performance-statistics contract

G2.8 calculates strategy and benchmark statistics from every costed hourly bar-close NLV, even when
the display-oriented strategy equity curve is downsampled. The first return compares the first
close to starting cash; every later return compares adjacent closes:

```text
simple return[t] = NLV[t] / NLV[t-1] - 1
```

The sampling and annualization contract is explicit:

```text
bar frequency                = 1 hour
crypto periods per year      = 365 * 24 = 8,760
risk-free annual assumption  = 0%
period volatility            = sample standard deviation (n - 1 denominator)
annualized volatility        = period volatility * sqrt(8,760)
annualized Sharpe            = mean hourly excess return / sample volatility * sqrt(8,760)
downside deviation           = sqrt(mean(min(hourly return, 0)^2 over all periods))
annualized Sortino           = mean hourly excess return / downside deviation * sqrt(8,760)
```

Volatility is a fractional return, not a percentage. Sharpe and Sortino are undefined for zero
variance or zero downside deviation. Fewer than two returns cannot produce sample volatility or
Sharpe. Artifacts carry explicit statuses for those cases instead of writing zero or infinity.

The remaining metrics use these definitions:

| Metric | Definition |
|---|---|
| Turnover | Gross absolute executed notional, including terminal liquidation, divided by arithmetic mean NLV across starting cash and all closes |
| Exposure time | Percentage of hourly close observations with positive BTC inventory |
| Drawdown duration | Consecutive hourly closes below the previous costed-NLV peak; both maximum and current duration are reported |
| Profit factor | Sum of positive fee-aware closed-trade P&L divided by absolute sum of negative fee-aware closed-trade P&L |
| Expectancy | Mean fee-aware net P&L per closed trade |
| Explicit fee drag | Cumulative explicit transaction fees as starting-capital return percentage points; spread/slippage remain separate |

Profit factor is undefined when there are no losing trades, and both profit factor and expectancy
are undefined when there are no closed trades.

For each benchmark, G2.8 aligns hourly strategy and benchmark returns and estimates:

```text
strategy return[t] = alpha_per_hour + beta * benchmark return[t] + residual[t]
annualized alpha   = alpha_per_hour * 8,760
```

This is an arithmetic OLS intercept, reported as a fractional return. It is not a claim of
statistical significance and currently has no confidence interval. Alpha is undefined when the
benchmark has zero return variance, as cash normally does. The separate annualized active-return
difference remains available in that case.

Schema version 11 carries `performance.jsonl` with one contract, four portfolio-statistic rows, and
three benchmark-alpha rows. `performance_observations.jsonl` preserves every unsampled strategy
NLV/inventory observation used by the calculations; benchmark observations remain in
`benchmark_equity.jsonl`.

## Anti-look-ahead proof

`tests/fixtures/backtest/future_jump.csv` creates an EMA cross at a `$12` close followed by a `$20`
next open. The golden timing test requires the fill to occur at `$20`, proving the strategy cannot
capture the unseen close-to-open jump. It also asserts this event order:

```text
signal bar_close < decision_accepted < next bar_open < order_eligible < fill
```

## G2.9 golden and failure matrix

The G2.9 acceptance matrix binds each required behavior to exact, deterministic tests:

| Requirement | Proof |
|---|---|
| Golden accounting | Hand-calculated weighted-average buys, partial sale, full sale, marks, and exact Robinhood v2 journal reconciliation |
| Next-bar anti-look-ahead | The `$12` signal close cannot fill before the following `$20` open |
| Spread/slippage direction | Buys execute above the synthetic ask and sells below the synthetic bid |
| Partial fills | Multiple executions, remaining quantity, allocated basis/entry fees, closed trades, and round trips reconcile exactly |
| Insufficient cash | A buy affordable at the signal close but unaffordable after a next-open gap is rejected without any account mutation |
| Terminal fee | The final sell is an explicit fill and pays the full configured per-fill fee on terminal executed notional |

The event engine does not invent partial executions from hourly OHLC data. Partial-fill acceptance
therefore feeds deterministic broker-like execution streams into the lifecycle and accounting
analyzers. Live execution normalization and recovery remain G6 work.

For the insufficient-cash boundary, the golden fixture starts with `$15`, produces a one-BTC buy
signal at a `$12` close, and gaps to a `$20` next open. The risk-approved request becomes
unaffordable at execution and emits `execution_rejected` with reason
`insufficient_cash_at_execution`. It produces zero fills, fees, inventory, P&L, or terminal orders,
and the journal remains the single `$15` opening balance.

## G2.10 reproducibility and integrity contract

Schema version 11 removes wall-clock/UUID nondeterminism from backtest order identity. Strategy
orders use a deterministic sequence/bar/side identifier, and every run records a nonnegative seed
with `randomness_used=false`; the current engine has no stochastic execution path.

Every report directory is immutable by run ID and includes the exact canonical input candles in
`input_candles.jsonl`. The summary and `manifest.json` bind:

- `market-event-backtester/1.0` and artifact schema 11;
- the canonical candle-sequence SHA-256 and bar count;
- the full resolved strategy, risk, execution, cost, benchmark, and reporting configuration;
- the resolved-config SHA-256, seed, and randomness-use declaration;
- the Git commit and `clean`, `dirty`, or `unavailable` status; and
- 12 artifact paths, byte sizes, record counts, and SHA-256 digests.

The manifest maps input data, executions, closed trades, equity, and metrics to their authoritative
artifacts. `verify-backtest` checks every file, summary identity, config checksum, preserved-candle
fingerprint, row sequence, and report-root path. Same-size tampering fails checksum verification.
A duplicate run ID raises instead of overwriting prior evidence.

A code identity is reproducible only when Git status is `clean`. Dirty or unavailable revisions
remain verifiable as artifact bundles but are explicitly non-promotable as code-identical research
runs. Unit tests inject a fixed clean revision to prove byte-identical repeated output; the CLI has
no revision-override flag.

## Known invalidities still open

- Robinhood profile rates remain configured research assumptions, not account-observed costs. A
  future pre-trade adapter must read or verify the applicable account tier.
- G3 walk-forward, uncertainty, parameter-stability, and untouched-holdout evidence do not exist.

The corrected engine is suitable for G3 research. Existing short saved runs still cannot support a
profitability or live-money decision.
