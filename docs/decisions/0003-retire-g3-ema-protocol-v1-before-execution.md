# ADR 0003: Retire G3 EMA Protocol 1.0 Before Execution

- **Status:** Accepted for G3 design assurance
- **Date:** 2026-08-17
- **Decision owners:** Research gate; pending human approval for any successor protocol

## Context

G3.1 preregistered a 36-pair hourly BTC EMA study and G3.2 froze nine walk-forward folds plus a
final strategy holdout. Before G3.3 or any registered parameter run, an independent review raised
power, cost, selection, and holdout-regime concerns. The first exploratory synthetic calculation
was unusable because its momentum calibration generated approximately 15,000% per-trade standard
deviation.

The corrected G3.2b checkpoint built a stationary, variance-normalized, fail-closed synthetic model
and reconciled its EMA/cost scoring to the production backtest engine. It then ran 500 development-
length synthetic replicates across null through deliberately extreme EMA-friendly momentum
conditions. It used no G1 strategy data and generated no final-holdout strategy output.

None of the 100 replicates in any scenario passed even the representable subset of protocol
criteria. In the extreme scenario, median primary net expectancy was positive at 154.8 bps per
trade, but median doubled-cost expectancy was negative at -79.1 bps and median trade count was
95.5. No replicate passed criteria 1 through 5 together. Criteria 7, 9, 10, and 11 would add
requirements rather than recover power.

Protocol 1.0 also leaves the final-pair rule and the relation between candidate-wise multiplicity
and adaptive walk-forward selection unresolved. Encoding those choices in G3.3 would invent
material protocol terms after the split freeze.

## Decision

1. Retire `g3-ema-v1` protocol 1.0 before strategy execution.
2. Do not use G1 strategy runs merely to obtain a predictable no-go from an underpowered design.
3. Treat the outcome as a study-design no-go, not evidence for or against EMA profitability.
4. Keep G3 open and pause G3.3 until a complete prospective protocol 2.0 is approved.
5. Preserve the existing G3.1 protocol and G3.2 split files unchanged as the historical contract;
   record supersession in status/evidence rather than rewriting them.
6. Require protocol 2.0 to bind observed/estimated route costs, sample-size/power logic, the final
   parameter rule, resampling and multiplicity semantics, and executable stress/benchmark rules.
7. Build experiment identity and evidence schemas generically so a non-EMA hypothesis can reuse
   them.
8. Label the frozen final year strategy-unseen but market-path-known. It may serve as a secondary
   audit only; a successor's untouched confirmatory evaluation requires future data.

## Consequences

- G3.3-G3.9, paper graduation, and all live-money stages remain locked.
- The engineering platform and G1/G2 evidence remain valid; only this research protocol is
  retired.
- No negative EMA result is entered into the research record because the market hypothesis was
  never executed.
- The highest-value next empirical input is a read-only measurement of the intended Robinhood
  route's estimated price, bid/ask, fee tier, and estimated fee. Broker contact and credentials
  require separate authorization.
- A future protocol may use the current development dataset under its own prospective change
  control, but it cannot inherit protocol 1.0's confirmatory claim or relabel the disclosed final
  year as blind.

## Evidence

- `docs/reviews/2026-08-17-g3-study-design-review.md`
- `docs/evidence/G3.2b-2026-08-17.md`
- `docs/evidence/G3.2b-power-study-2026-08-17.json`
