# Backtesting Contract

## Current gate status

G2.1 through G2.4 are implemented. The engine no longer fills a decision at the close that produced
its signal, every run declares one of two deterministic next-open execution models,
venue/API/routing cost profiles keep Robinhood v1 and v2 economics separate, and every transaction
fee is explicitly defined per execution fill. End-of-data liquidation is now a visible, fully
costed sell fill that leaves the reported portfolio flat. The rest of G2 remains incomplete, so
backtest output is still non-promotable.

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
version 6 includes this contract together with the G2.3/G2.3a cost fields and G2.4 terminal
liquidation fields.

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

Schema version 6 records `fee_calculation_basis=executed_notional_per_fill`, the configured
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
final_position_btc
```

When there is inventory, `final_position_btc` is zero and the last equity artifact has
`stage=post_terminal_liquidation`. When there is no inventory, no synthetic terminal order or fill
is created.

## Anti-look-ahead proof

`tests/fixtures/backtest/future_jump.csv` creates an EMA cross at a `$12` close followed by a `$20`
next open. The golden timing test requires the fill to occur at `$20`, proving the strategy cannot
capture the unseen close-to-open jump. It also asserts this event order:

```text
signal bar_close < decision_accepted < next bar_open < order_eligible < fill
```

## Known invalidities still open

- Robinhood profile rates remain configured research assumptions, not account-observed costs. A
  future pre-trade adapter must read or verify the applicable account tier.
- Cash, inventory, cost basis, realized/unrealized P&L, and liquidation value are not yet governed
  by the G2.5 accounting journal.
- Trade lifecycle counts, benchmarks, statistics, and fully reproducible run identity remain open.

No result from this intermediate engine can support a profitability or live-money decision.
