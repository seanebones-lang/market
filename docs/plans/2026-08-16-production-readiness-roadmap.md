# Market Production-Readiness Roadmap

**Date:** 2026-08-16
**Status:** Planning baseline; no live-money approval
**Decision owner:** CTO
**Operating rule:** Complete and approve one gate before beginning work that depends on it.

## Executive decision

The system is a useful prototype, but it is not an investable strategy or a production trading
system. Keep all live order submission disabled.

The project has two independent questions, and both must be answered:

1. **Research:** Is there a repeatable, net-of-cost out-of-sample edge?
2. **Engineering:** Can the system trade that edge without losing control of state, orders, or risk?

Passing one does not compensate for failing the other. Robinhood connectivity is not evidence of
an edge, and a profitable backtest is not evidence of execution safety.

## Current evidence baseline

- Best saved run: 1,500 hourly bars, 43 fills, `$0.622300460` headline P&L on `$1,000`,
  `$1.357279540` modeled fees, and `$4.155304095` max drawdown.
- Recent saved run: 600 hourly bars, 17 fills, and `-$1.600358155` P&L.
- The current cached data contains 600 sequential hourly bars with no duplicate timestamps, gaps,
  invalid OHLC rows, or zero-volume rows. It is structurally clean but far too short for strategy
  validation.
- The 1,500-bar input used by the best saved report is no longer retained. The active cache has
  been overwritten with 600 bars, so that published result cannot be reproduced byte-for-byte.
- Test baseline on 2026-08-16: **52 passed, 1 failed**. The failing test calls Coinbase over the
  public internet from the unit-test suite.
- Ruff currently reports a large lint backlog, including the untracked duplicate
  `src/market/app/loop 2.py`. That file is user-owned until its disposition is explicitly decided.

### Confirmed P0 defects

- The backtest observes a bar close and fills at that same close
  (`src/market/backtest/engine.py:131-154`).
- Terminal inventory is liquidated without an exit fee
  (`src/market/backtest/engine.py:213-218`).
- Marked ending equity is named `realized_pnl_usd`
  (`src/market/backtest/engine.py:51-63`).
- Paper trading turns every quote poll into a new candle
  (`src/market/app/loop.py:73-91`, `215-242`).
- Daily P&L exists in memory but is never updated from fills or marks
  (`src/market/risk/gate.py:22-28`, `src/market/app/loop.py`).
- Risk state and submitted-order identity are lost on restart
  (`src/market/app/loop.py:43-57`).
- The order path submits before durably recording the order attempt
  (`src/market/app/loop.py:148-177`).
- Reconciliation reads broker implementation internals and only writes a log on mismatch
  (`src/market/app/loop.py:248-258`).
- The live branch records acknowledgements but not executions/fills
  (`src/market/app/loop.py:179-194`).
- Heartbeat freshness can be queried, but stale heartbeat state is not used to stop trading.
- There is no implemented panic-flatten command or independent watchdog.
- `max_notional_usd` caps a resized buy order, not total projected account exposure including
  position and open orders (`src/market/risk/gate.py:72-96`).
- A daily-loss violation currently blocks all intents, including a risk-reducing sell
  (`src/market/risk/gate.py:53-55`).
- Configuration ignores unknown fields, discards the declared strategy timeframe, and converts
  nonempty strings such as `"false"` to `True` (`src/market/config.py:38-65`).

## Current Robinhood facts to design against

These facts were verified from official Robinhood sources on 2026-08-16:

- The official US Crypto Trading API supports market data, accounts, holdings, orders, and
  programmatic crypto trading.
- Credentials can be created with specific API actions. Authentication uses `x-api-key`,
  `x-signature`, and `x-timestamp`; request timestamps are valid for 30 seconds. Signatures use an
  Ed25519 private key over the API key, timestamp, path, method, and body.
- V1 places orders without fee tiers. V2 places orders with fee tiers; all read-only actions are
  available on both versions.
- V2 exposes account fee-tier status, trading-pair increments and limits, holdings, orders,
  executions, fees, best price, and quantity-aware estimated prices.
- Robinhood requires a UUID `client_order_id` for order idempotency validation.
- Robinhood documents HTTP `429`, `500`, and `503` responses. Retrying an order blindly after an
  ambiguous response is therefore prohibited.
- Executed v2 API orders count toward 30-day fee-tier volume and, during the current rollout, are
  charged the taker rate. V1 API orders do not count toward fee-tier volume.
- Robinhood says exchange-routing fees can range from 0.00% to 0.95%. Market-maker routing has no
  separately labeled fee, but cost is embedded in the spread. As of 2026-06-15, Robinhood states
  that it receives `$0.95` per `$100` of notional executed through market-maker routing.
- Best-price data does not include order-size impact and might not be the final execution price.
  Cost models must use size-aware estimates and observed executions, not a fixed five-basis-point
  assumption.

Primary sources:

- [Robinhood Crypto Trading API documentation](https://docs.robinhood.com/crypto/trading/)
- [Robinhood Crypto Trading API overview](https://robinhood.com/us/en/support/articles/crypto-api/)
- [Robinhood crypto fee tiers](https://robinhood.com/us/en/support/articles/crypto-fee-tiers/)
- [Robinhood crypto order routing](https://robinhood.com/us/en/support/articles/smart-exchange-routing/)
- [Robinhood crypto order behavior](https://robinhood.com/us/en/support/articles/crypto-buying-and-selling/)

## Accountability

| Role | Owns | Cannot self-approve |
|---|---|---|
| Quant/research lead | data, hypotheses, backtests, statistics, research report | strategy graduation |
| Trading/execution lead | cost model, order semantics, reconciliation, venue behavior | live enablement |
| Backend/data engineer | event engine, persistence, broker adapter, accounting | production release |
| SRE/security engineer | secrets, monitoring, watchdog, recovery, deployment | disabling risk controls |
| QA engineer | deterministic tests, contract tests, failure injection, evidence pack | accepting known failures |
| CTO | scope, risk appetite, gate decisions, final go/no-go | none; decisions require evidence |

For a small team, one person may hold several roles, but the evidence and approval boundaries still
apply. A developer should not mark their own safety-critical change production-ready without an
independent review.

## Gate sequence

```text
G0 Live lock and clean baseline
  -> G1 Data contract and deterministic research dataset
  -> G2 Correct event-driven backtester and accounting
  -> G3 Out-of-sample strategy evidence
  -> G4 Persistent paper-trading runtime
  -> G5 Official Robinhood read-only adapter
  -> G6 Execution and risk hardening
  -> G7 Operational and failure qualification
  -> G8 Sustained shadow/paper evidence
  -> G9 Supervised micro-live
  -> G10 Controlled scaling (optional)
```

Only **G0** is authorized to start. G1-G10 are planned, not active.

---

## G0 — Live lock and clean baseline

**Purpose:** Make the repository honest, deterministic, and incapable of accidental live trading
while the foundation is rebuilt.

### Tasks

- [x] **G0.1** Preserve the existing hard live abort and add a test proving every CLI/configuration
  combination produces zero external order POSTs.
- [x] **G0.2** Replace all stale claims that Robinhood has no official retail crypto API.
- [x] **G0.3** Remove username/password/TOTP design from the planned Robinhood path. Replace it with
  official API-key and Ed25519-signing terminology. Do not create a trading-enabled credential yet.
- [x] **G0.4** Move the internet ticker check out of unit tests. Use an injected/mock transport in
  unit tests and place real network checks behind an explicit integration marker.
- [x] **G0.5** Replace assertions that cannot fail, including `intents >= 0`, `len(fills) >= 0`, and
  ledger length `>= 0`, with behavior-specific assertions.
- [x] **G0.6** Preserve the legacy `src/market/app/loop 2.py` snapshot in Git history, then remove
  the obsolete duplicate from the active package so it cannot break imports or quality checks.
- [x] **G0.7** Add CI for offline unit tests, lint, type checking, dependency audit, and coverage of
  safety-critical branches.
- [x] **G0.8** Record architecture decisions for accounting semantics, event timing, data sources,
  Robinhood API version, risk-loss definition, and order recovery.
- [x] **G0.9** Add a prominent `LIVE_TRADING_DISABLED` project status to operator documentation.
- [x] **G0.10** Make configuration fail closed: forbid extra keys, parse booleans strictly, validate
  broker/mode/timeframe combinations, require `0 < fast_ema < slow_ema`, and reject negative or
  unbounded risk settings.
- [x] **G0.11** Mark all existing backtest reports exploratory and invalid for promotion because
  their timing, terminal accounting, and dataset-reproducibility requirements do not pass.

### Exit criteria

- Clean offline test run with no network access.
- No meaningful assertions that pass by construction.
- No reachable live order-submit path.
- Repository docs describe the official Robinhood Crypto Trading API correctly.
- CTO records `G0: PASS`; otherwise later gates remain locked.

---

## G1 — Data contract and deterministic research dataset

**Purpose:** Ensure every strategy sees complete, closed, reproducible market bars.

### Tasks

- [x] **G1.1** Define a candle contract: UTC open time, timeframe, source, open/high/low/close,
  volume, received time, close-confirmed time, and data-quality flags.
- [x] **G1.2** Reject invalid OHLC relationships, duplicate/out-of-order timestamps, nonpositive
  prices, unsupported intervals, and future timestamps.
- [x] **G1.3** Detect missing bars. Never silently forward-fill tradable bars; declare a gap and
  require a full indicator re-warm after repair.
- [x] **G1.4** Exclude the currently forming hourly bar from strategy evaluation.
- [x] **G1.5** Separate raw immutable data from normalized research data. Store source, retrieval
  time, schema version, and checksum.
- [x] **G1.6** Acquire at least five full years of hourly BTC-USD data spanning bull, bear,
  high-volatility, low-volatility, and sideways regimes. Prefer independent cross-checking of a
  sample against a second source.
- [x] **G1.7** Version datasets and produce a machine-readable quality report before every study.
- [x] **G1.8** Add deterministic fixture datasets containing gaps, duplicates, late bars, a partial
  final bar, and extreme price moves.

### Exit criteria

- The same dataset version produces the same checksum and normalized bars on repeated runs.
- The quality checker intentionally fails every corrupt fixture.
- No strategy can receive an unclosed or unresolved-gap candle.
- Research dataset covers at least three materially different market regimes.
- Quant lead and backend lead record `G1: PASS`.

**Gate record (2026-08-16):** `G1: PASS WITH DECLARED-GAP SEGMENTATION`. The five-year Coinbase
artifact contains 13 documented missing hours and is admitted only as four checksum-verified,
independently warmed segments. See `docs/evidence/G1-2026-08-16.md`. G2 is unlocked; later gates and
live money remain locked.

---

## G2 — Correct event-driven backtester and accounting

**Purpose:** Make simulated decisions executable without look-ahead and make every reported dollar
reconcilable.

### Tasks

- [x] **G2.1** Replace same-close fills with an explicit event sequence: bar `t` closes, strategy
  decides after close, order becomes eligible at bar `t+1`, and fills use a declared execution
  model.
- [x] **G2.2** Support execution models for next-open market fills and bid/ask plus configurable
  adverse slippage. Record the selected model in every run.
- [x] **G2.3** Model venue/routing-specific costs. Keep Robinhood v1 spread-inclusive and v2
  exchange-fee assumptions distinct. Never label a cost assumption as an observed cost.
- [ ] **G2.3a** Define every fee input as per-side or round-trip. Remove the CLI's current ambiguity,
  where `fee_bps` is described as round-trip but charged on every fill.
- [ ] **G2.4** Charge costs on every fill, including terminal liquidation. Terminal liquidation
  must be represented as a real fill event.
- [ ] **G2.5** Implement double-entry-style portfolio accounting for cash, BTC inventory, cost
  basis, realized P&L, unrealized P&L, fees, marked equity, and net liquidation value.
- [ ] **G2.6** Distinguish order count, execution count, partial fills, round trips, closed trades,
  wins, losses, and open inventory.
- [ ] **G2.7** Add cash, matched-notional buy-and-hold, and periodic-DCA benchmarks. Compare both
  absolute and risk-adjusted performance.
- [ ] **G2.8** Report turnover, exposure time, drawdown duration, volatility, Sharpe/Sortino with
  stated annualization, profit factor, expectancy, fee drag, and benchmark alpha.
- [ ] **G2.9** Add golden accounting tests, next-bar anti-look-ahead tests, spread/slippage direction
  tests, partial-fill tests, insufficient-cash tests, and terminal-fee tests.
- [ ] **G2.10** Make run artifacts reproducible: code revision, data checksum, config, random seed,
  engine version, costs, trades, equity curve, and metrics.

**Increment record (2026-08-16):** `G2.1: PASS`; `G2.2: PASS`; `G2.3: PASS`. The future-jump fixture
proves next-open fills and end-of-data decisions expire unfilled. Directional tests prove buys
cross the synthetic ask and slip upward while sells cross the synthetic bid and slip downward.
Venue-profile tests keep Robinhood v1 spread-only treatment separate from v2 exchange-taker fee
assumptions. See `docs/evidence/G2.1-2026-08-16.md`, `docs/evidence/G2.2-2026-08-16.md`, and
`docs/evidence/G2.3-2026-08-16.md`. This is not a G2 gate pass; G2.3a-G2.10 and all live-money stages
remain locked.

### Exit criteria

- A synthetic “future jump” fixture proves a signal cannot capture its own bar’s close-to-open
  move.
- Cash + marked inventory + cumulative costs reconcile at every event to the cent-equivalent
  precision used by the venue.
- Terminal liquidation is visible and charged.
- Golden fixtures are independently hand-calculated and match the engine.
- QA and trading/execution lead record `G2: PASS`.

---

## G3 — Out-of-sample strategy evidence

**Purpose:** Decide whether EMA crossover—or any replacement hypothesis—has evidence of an edge.

### Tasks

- [ ] **G3.1** Write the hypothesis, economic rationale, allowed inputs, parameter search space, and
  rejection criteria before running the study.
- [ ] **G3.2** Freeze an untouched final holdout. Use anchored or rolling walk-forward train,
  validation, and test windows for all model/parameter choices.
- [ ] **G3.3** Keep a complete experiment registry, including failed runs. Apply a multiple-testing
  correction or deflated performance statistic when searching many variants.
- [ ] **G3.4** Evaluate parameter neighborhoods, not only the best point. Reject isolated peaks.
- [ ] **G3.5** Bootstrap trades or blocks of returns to report uncertainty and the probability that
  net expectancy is nonpositive.
- [ ] **G3.6** Stress base costs, current observed/estimated venue costs, doubled costs, execution
  delay, missing bars, and adverse slippage.
- [ ] **G3.7** Compare by regime and against benchmarks at matched capital and exposure.
- [ ] **G3.8** Perform the final holdout evaluation once. If it fails, record failure; do not tune on
  it and relabel it out-of-sample.
- [ ] **G3.9** Produce a signed research memo with data lineage, assumptions, all tested variants,
  results, limitations, and go/no-go recommendation.

### Minimum graduation standard

- Positive aggregate walk-forward out-of-sample net expectancy after realistic costs.
- Still positive under doubled cost assumptions.
- Positive results are not dependent on one short period, one trade, or one parameter pair.
- At least 70% of stitched out-of-sample folds are positive, with no single trade or fold
  contributing more than 50% of total out-of-sample profit.
- Beats cash and provides a defensible advantage over matched-exposure buy-and-hold on the chosen
  risk objective.
- Drawdown and tail loss remain within a predeclared risk budget.
- At least 100 out-of-sample closed trades, or a written statistical-power justification approved
  by the quant lead and CTO for a lower-frequency strategy.
- The 95% block-bootstrap lower bound exceeds a predeclared economically meaningful hurdle, and
  results survive reasonable signal-delay and execution perturbations.
- Untouched holdout passes the predeclared criteria.

If these criteria fail, stop strategy graduation. The engineering platform may continue as a safe
research tool, but no live-money stage is allowed.

---

## G4 — Persistent paper-trading runtime

**Purpose:** Make paper, shadow, and eventual live modes share one event and state model.

### Tasks

- [ ] **G4.1** Introduce SQLite in WAL mode with migrations and tables for bars, data-quality
  incidents, strategy decisions, risk decisions, order intents, submissions, broker orders,
  executions, fills, positions, daily equity anchors, risk state, reconciliations, and heartbeats.
- [ ] **G4.2** Use an append-only event journal plus derived snapshots. Every derived position or P&L
  value must be rebuildable from events.
- [ ] **G4.3** Replace one-candle-per-poll behavior with a UTC hourly aggregator that emits exactly
  one close-confirmed bar per interval.
- [ ] **G4.4** Separate signal data from execution quotes. Store source timestamps and receive
  timestamps for both.
- [ ] **G4.5** Enforce freshness thresholds: closed-bar age for signals, seconds-scale quote age for
  execution, and clock-skew tolerance.
- [ ] **G4.6** On a gap, stale quote, clock jump, process sleep, or data-source disagreement, freeze
  new entries and require recovery plus indicator re-warm.
- [ ] **G4.7** Restore risk state, orders, daily anchor, and last processed bar on restart without
  duplicating a decision.
- [ ] **G4.8** Use the same strategy and risk interfaces in backtest, paper, shadow, and live modes.
- [ ] **G4.9** Strengthen the canonical broker port with typed paginated order history,
  query-by-client-ID, account snapshots, venue capabilities, health state, and structured failure
  categories. Remove the duplicate loop-local protocol and `Any` broker type.

### Exit criteria

- Two-second quote polling for more than an hour still creates exactly one closed hourly bar.
- Restart at every event boundary produces no duplicate strategy decision or order intent.
- Stale/gapped data blocks entries before strategy execution.
- Event replay reconstructs balances, positions, orders, P&L, and risk state exactly.
- Backend, QA, and SRE leads record `G4: PASS`.

---

## G5 — Official Robinhood read-only adapter

**Purpose:** Observe the real venue safely before enabling any order action.

### Tasks

- [ ] **G5.1** Replace the username/password session stub with an official API client boundary.
- [ ] **G5.2** Target v2 for fee-tier-aware read operations. Document any deliberate v1 use
  separately.
- [ ] **G5.3** Generate an API credential with read-only actions only. Store the private key outside
  the repository, logs, database, shell history, and test fixtures.
- [ ] **G5.4** Implement deterministic canonical body serialization and official Ed25519 signature
  test vectors.
- [ ] **G5.4a** Generate canonical hyphenated UUID client order IDs with `str(uuid4())`; do not rely
  on the current compact `.hex` form unless Robinhood explicitly documents it as accepted.
- [ ] **G5.5** Implement accounts, holdings, trading pairs, orders, best price, and size-aware
  estimated-price reads with pagination.
- [ ] **G5.6** Validate account status, `is_api_tradable`, increments, minimum amount, maximum size,
  buying power, and quantity available for trading.
- [ ] **G5.7** Capture Robinhood timestamps, local receive time, fee ratio, estimated fee, estimated
  total cost/credit, and actual order execution/fee fields.
- [ ] **G5.8** Add bounded timeouts, rate-limit handling, exponential backoff with jitter for safe
  reads, structured errors, schema validation, and metrics. Never log credentials or signatures.
- [ ] **G5.9** Run a read-only shadow process and compare holdings/orders/account truth to the app.

### Exit criteria

- Official signature examples pass byte-for-byte.
- The credential is technically unable to place an order.
- Pagination, malformed responses, `401`, `403`, `429`, `500`, `503`, timeouts, and clock skew are
  covered by contract tests.
- Seven consecutive days of read-only snapshots show no unresolved account/order discrepancy.
- Trading/execution, security, and CTO record `G5: PASS`.

---

## G6 — Execution and risk hardening

**Purpose:** Build an order system that can recover from every ambiguous outcome without doubling
exposure.

### Tasks

- [ ] **G6.1** Define a durable order state machine: `created -> risk_approved -> submit_pending ->
  submitted/unknown -> open/partial -> filled/canceled/rejected`, with explicit terminal states.
- [ ] **G6.2** Persist the intent and UUID client order ID before network submission. Commit the
  submit-pending state durably before calling Robinhood.
- [ ] **G6.3** On timeout or ambiguous response, query broker orders by persisted identity and
  reconcile before any retry. Never generate a new client order ID for the same logical order.
- [ ] **G6.4** Normalize partial executions and fees into immutable fill events. Journal live fills
  through the same path as paper fills.
- [ ] **G6.4a** Implement real broker cancellation, then poll and reconcile until Robinhood reports a
  terminal order state. A cancel response alone is not proof that an execution did not win the
  race.
- [ ] **G6.5** Reconcile broker holdings, balances, all nonterminal orders, recent executions, and
  local state on startup, before every new order, and periodically while running.
- [ ] **G6.6** Any unexplained mismatch, extra broker order, missing order, or impossible state must
  durably freeze entries and alert. Logging alone is a failure.
- [ ] **G6.7** Calculate projected gross BTC notional from current marked position plus all
  nonterminal buy exposure plus the proposed order. Cap total exposure, not individual order size.
- [ ] **G6.8** Define daily loss as current net liquidation value minus a persisted UTC day-start
  net liquidation anchor, inclusive of realized/unrealized P&L and fees. Add an intraday
  peak-to-trough drawdown rail separately if desired.
- [ ] **G6.9** Enforce buying power, available BTC, order increments/minimums, spread ceiling,
  slippage ceiling, stale-data state, order rate, cooldown, daily loss, total drawdown, and max
  exposure in one fail-closed risk decision.
- [ ] **G6.9a** Loss, stale-data, and entry-freeze rails must continue to permit validated
  risk-reducing exits; they must not trap an existing BTC position.
- [ ] **G6.10** Implement durable modes: normal, entries-frozen/exits-allowed, halted/cancel-only,
  and panic-flatten. Restart must never clear a restrictive mode automatically.
- [ ] **G6.11** Implement panic flatten as a supervised, reduce-only state machine with stale-data
  policy, size verification, bounded retries, reconciliation, and final confirmation.
- [ ] **G6.12** Keep order permissions disabled after implementation. Test with fakes and shadow
  requests until later gates pass.

### Exit criteria

- Failure injection at every database and network boundary creates no duplicate order or excessive
  position.
- Kill/restart after submit but before response resolves the order from broker truth.
- Every reconciliation mismatch freezes entries within one control cycle.
- Daily loss and exposure rails survive restart and match independently calculated values.
- Trading/execution, backend, QA, and CTO record `G6: PASS`.

---

## G7 — Operational and failure qualification

**Purpose:** Prove the system remains safe when dependencies, processes, clocks, and humans fail.

### Tasks

- [ ] **G7.1** Add an independent watchdog that can detect stale heartbeats and activate a durable
  entry freeze without relying on the trading loop.
- [ ] **G7.2** Monitor data freshness, last closed bar, broker latency/error rate, clock offset,
  reconciliation status, position/notional, P&L, drawdown, open orders, and freeze mode.
- [ ] **G7.2a** Record process liveness separately from the last successful end-to-end cycle. Do not
  publish a healthy trading heartbeat before data, broker, risk, persistence, and reconciliation
  work has succeeded.
- [ ] **G7.3** Alert on stale data, auth/signature failure, risk halt, reconcile mismatch, rejected or
  unknown order, unexpected fill, watchdog action, database failure, and process restart.
- [ ] **G7.4** Build operator commands for status, freeze entries, halt/cancel, reconcile, and panic
  flatten. Require confirmation and emit an audit event for destructive actions.
- [ ] **G7.5** Write runbooks for startup, shutdown, restart, API outage, data outage, clock drift,
  database recovery, unexpected position, stuck order, lost credentials, and manual flatten.
- [ ] **G7.6** Back up the database and test restore. Keep secrets out of backups.
- [ ] **G7.7** Pin dependencies, scan them, run the service with least privilege, protect file and
  database permissions, and document key rotation/revocation.
- [ ] **G7.8** Add soak, property, contract, and chaos tests. Simulate timeout-after-accept, partial
  fill, duplicate response, 429, 5xx, corrupted state, disk-full, sleep/wake, and delayed bars.
- [ ] **G7.9** Add a read-only operator dashboard only after the underlying metrics and commands are
  trustworthy. A UI must not become a second source of truth.

### Exit criteria

- Every documented failure drill has an expected safe state and a recorded successful test.
- Watchdog freeze is demonstrated with the main process hung and killed.
- Backup restore reconstructs the same order, fill, position, P&L, and risk state.
- No critical alert is dependent on the failed component it monitors.
- SRE, security, QA, and CTO record `G7: PASS`.

---

## G8 — Sustained shadow and paper evidence

**Purpose:** Demonstrate strategy behavior, venue-cost assumptions, and operational stability in
forward time.

### Tasks

- [ ] **G8.1** Run continuous forward paper trading using genuinely closed bars and simulated fills
  derived from timestamped, size-aware Robinhood estimates where available.
- [ ] **G8.2** Record base-cost and doubled-cost shadow equity concurrently.
- [ ] **G8.3** Produce daily reports for decisions, orders, fills, fees, spread, slippage assumption,
  exposure, P&L, drawdown, benchmark, data incidents, reconciliations, and freezes.
- [ ] **G8.4** Perform scheduled restart, stale-data, reconciliation, watchdog, and panic-control
  drills without resetting the evaluation window.
- [ ] **G8.5** Compare paper decisions with deterministic replay of the same captured data.

### Exit criteria

- Minimum 90 consecutive calendar days and 50 completed round trips, whichever takes longer,
  unless a stricter predeclared power analysis applies.
- Positive net paper expectancy under realistic costs and no failure under doubled costs.
- No unexplained order/state discrepancy, duplicate decision, stale-data trade, or lost event.
- Paper/replay decisions match exactly for the same event stream.
- All failure drills pass and every freeze is explained.
- Quant, trading/execution, SRE, QA, and CTO record `G8: PASS`.

---

## G9 — Supervised micro-live

**Purpose:** Validate actual venue mechanics with disposable capital, not prove profitability.

### Preconditions

- G0-G8 all show `PASS` with evidence.
- The user explicitly approves real-money activation and a disposable-loss budget.
- A new action-scoped credential is created only for this stage.
- Legal, tax-lot, account, and Robinhood agreement implications are reviewed by the account owner.

### Tasks

- [ ] **G9.1** Begin entries-disabled. Read and reconcile the real account before enabling a single
  supervised order.
- [ ] **G9.2** Execute one manually approved minimum-size buy/hold/sell round trip while the operator
  is present.
- [ ] **G9.3** Verify order state, executions, fees, position, buying power, tax-lot record, ledger,
  alerts, and flatten controls independently.
- [ ] **G9.4** Continue at a fixed micro notional with no automatic size increase. Use a loss cap
  smaller than the disposable budget and stop on the first unexplained discrepancy.
- [ ] **G9.5** Compare observed spread, fee, fill latency, and slippage with research assumptions.

### Immediate stop conditions

- Unknown or duplicate order, unexpected fill, reconciliation mismatch, stale-data trade, risk
  state loss, missed alert, incorrect P&L, cost beyond the approved stress model, or inability to
  flatten/reconcile.

### Exit criteria

- At least 30 days of micro-live operation and enough executions to validate the order lifecycle.
- Zero unresolved safety incidents.
- Actual costs remain inside the approved stressed model.
- Live decisions and accounting reconcile to broker truth.
- CTO and account owner explicitly record `G9: PASS` before any size change.

---

## G10 — Controlled scaling (optional)

**Purpose:** Increase exposure only when live evidence supports it.

### Rules

- [ ] Define size steps and rollback thresholds before scaling.
- [ ] Change one variable at a time; never change strategy and size together.
- [ ] Require a new approval for each step.
- [ ] Keep a permanent absolute capital cap and drawdown stop.
- [ ] Re-run research and paper qualification after material strategy, venue, routing, fee, API, or
  data changes.
- [ ] Automatically return to entries-frozen on any unexplained operational event.

There is no automatic right to scale. `G10: NO-GO` is an acceptable permanent outcome.

## Definition of done for every task

A checkbox is complete only when all of the following exist:

1. Implemented behavior or approved document.
2. Deterministic tests, including the relevant failure path.
3. Operator-visible evidence or artifact.
4. Updated documentation and migration/runbook when applicable.
5. Independent review for safety-critical changes.
6. No regression in earlier gate criteria.

## First authorized work package

Start with **G0 only**, in this order:

1. Make the test suite fully offline and replace vacuous assertions.
2. Add an explicit live-submit prohibition test at the transport boundary.
3. Update Robinhood documentation and credential design to the official API.
4. Decide the untracked `loop 2.py` disposition with the owner.
5. Add CI and record the clean baseline.

Do not begin the backtester rewrite until `G0: PASS` is recorded. Do not create a Robinhood API
credential with order permissions until G0-G8 have passed.
