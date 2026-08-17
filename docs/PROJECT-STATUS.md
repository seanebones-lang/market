# Market BTC Research Platform — Project Status

- **Updated:** 2026-08-17
- **Overall status:** Research platform only; no demonstrated trading edge
- **Capital authorization:** None; live order submission remains hard-disabled

## What we are building

`market` is a broker-agnostic BTC spot research and execution platform. Coinbase supplies the
current public research/signal candles. The official Robinhood Crypto Trading API is the intended
first execution adapter, but no build is authorized to submit an order.

The platform is designed to answer two different questions without conflating them:

1. Can a strategy hypothesis survive reproducible out-of-sample testing after realistic costs?
2. If one eventually does, can the runtime execute it with durable accounting, risk controls,
   reconciliation, restart recovery, and operator safety?

Passing engineering gates does not establish profitability. Passing a research gate would not
guarantee future returns.

## Gate status

| Gate | Status | Meaning |
|---|---|---|
| G0 | Complete | Live submission is locked; foundational safety decisions are recorded |
| G1 | Complete | Five-year hourly BTC dataset is immutable, checksummed, and gap-segmented |
| G2 | Complete | Backtest timing, costs, accounting, lifecycle, benchmarks, statistics, tests, and reproducibility pass |
| G3 | Open | No strategy edge exists in evidence; EMA protocol 1.0 was retired before execution at G3.2b |
| G4-G10 | Locked | Paper-runtime graduation, broker execution, and any capital stages remain unauthorized |

The controlling details are in
`docs/plans/2026-08-16-production-readiness-roadmap.md`.

## Current research conclusion

There is no promotable strategy result.

- The saved 2026-08-04 backtests are invalid for promotion because they predate corrected timing,
  costs, terminal accounting, and reproducible data retention.
- G1 supplies 43,811 admitted hourly bars across four independently warmed segments, with 13
  declared missing hours.
- G3.1 preregistered a 36-pair long-only hourly EMA study without running it.
- G3.2 froze nine walk-forward folds and a final strategy holdout without generating strategy
  output.
- G3.2b replaced an invalid synthetic power calibration and ran 500 variance-normalized,
  development-length simulations. No scenario passed even the representable subset of protocol
  criteria.
- Protocol 1.0 was therefore retired as an inadequate discovery design before any G1 EMA parameter
  run. That decision is not evidence for or against EMA profitability.

In the deliberately extreme EMA-friendly scenario, median primary expectancy was +154.8 bps per
trade, but median doubled-cost expectancy was -79.1 bps and median out-of-sample trade count was
95.5. None of 100 replicates passed the represented joint criteria. See
`docs/evidence/G3.2b-2026-08-17.md` and ADR 0003.

The frozen final year remains strategy-unseen but its broad market path is known. It cannot be
relabeled as an untouched confirmatory holdout for a successor protocol designed after that
disclosure.

## Engineering capability now

- Close-confirmed hourly candles and strict data-quality admission
- Next-bar-open strategy execution with directional spread and slippage
- Route-specific Robinhood v1 market-maker and v2 exchange-taker cost assumptions
- Visible, fully costed terminal liquidation
- Immutable portfolio journal with reconciled cash, inventory, fees, gross/net P&L, and NLV
- Order, execution, partial-fill, closed-trade, and round-trip lifecycle analysis
- Cash, matched-notional buy-and-hold, and periodic-DCA benchmarks
- Descriptive risk/performance statistics and benchmark-relative alpha estimates
- Immutable schema-11 evidence bundles with input/config/source checksums
- Synthetic design-assurance tooling with fail-closed variance and trade-dispersion checks
- Offline Robinhood v2 cost-observation derivation with strict response schemas, per-endpoint
  receive times, separated spread/depth/fee measures, redaction, and immutable verification
- Prospective 30-day route-cost sampling protocol and offline corpus analyzer with complete-cycle
  coverage, fixed missingness rules, daily-block uncertainty, and content-addressed summaries

These capabilities make the system a trustworthy research instrument. They do not make it a
validated trading system.

## Next decision

G3.3 must not resume until a prospective protocol 2.0 is approved. That protocol must bind:

1. the intended execution route and observed or explicitly estimated costs;
2. a sample-size rule supported by prospective power;
3. training, validation, zero-trade, fold-selection, and final-parameter semantics;
4. bootstrap/block and adaptive multiple-testing methods;
5. executable benchmark, neighborhood, delay, gap, regime, concentration, and risk rules;
6. a strategy-agnostic experiment/evidence schema; and
7. the role of the existing strategy-unseen audit window and a future confirmatory window.

G3.2c now supplies the offline schema, derivation, redaction, and immutable evidence mechanics for
that input. G3.2d now freezes the future sampling cadence, quantities, coverage, quantiles,
uncertainty, and base/stress mapping before any real values exist. Its tests remain entirely
synthetic and do not establish a current cost. No dated production run plan exists.
The highest-value empirical input remains a separately authorized read-only Robinhood sampler for
best bid/ask, quantity-aware estimated prices, fee tier, fee ratio, and estimated fee. No broker
credential was created or used and no broker endpoint was contacted during G3.2b-G3.2d.

## Common commands

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
./market.sh verify-dataset --manifest data/research/manifests/coinbase-btc-usd-1h-20210816T000000Z-20260816T000000Z-00c5f0b63bef9236.manifest.json
./market.sh verify-research-splits --plan config/research/g3-ema-v1-splits.json
env PYTHONPATH=src .venv/bin/python -m market.research.power_cli \
  --split-plan config/research/g3-ema-v1-splits.json \
  --study-definition config/research/g3-ema-v1-power-study.json \
  --output /tmp/g3-ema-v1-power-study.json
./market.sh derive-rh-v2-cost \
  --fixture tests/fixtures/robinhood/v2_cost_snapshot.json \
  --out-dir /tmp/market-rh-v2-cost
./market.sh verify-backtest --manifest data/backtests/RUN_ID/manifest.json
```

Start with `docs/START-HERE.md`; use `docs/RESEARCH-STATUS.md` for the complete research evidence
history and the production-readiness roadmap for authorization boundaries.
