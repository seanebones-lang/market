# Market BTC Autotrader Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task when execution is approved.

**Goal:** Build a broker-agnostic BTC spot autotrader loop with sim-first execution and an isolated Robinhood adapter for optional live buys/sells under hard risk rails.

**Architecture:** Pure strategy → risk gate → BrokerPort → reconcile/ledger → ops. Robinhood is an adapter, not the core. Modes: sim / paper / live-dry / live.

**Tech Stack:** Python 3.12+, pydantic settings, httpx, websockets (later), SQLite or JSONL ledger, pytest, rich CLI, python-dotenv. Optional unofficial RH client isolated behind interface. Telegram alerts via existing stack later.

---

## Phase 0 — Scaffold (this commit)

### Task 0: Repo skeleton + docs

**Objective:** Establish desktop+GitHub SoT with plan docs and empty packages.

**Files:**
- Create: `README.md`, `docs/*`, `pyproject.toml`, `src/market/...`, `tests/`, `.gitignore`, `.env.example`

**Verify:** `git status` clean after commit; tree exists under `~/Desktop/market`.

---

## Phase 1 — Domain core (no broker)

### Task 1: Decimal money/BTC types + Intent models

**Objective:** Define non-float domain models.

**Files:**
- Create: `src/market/domain/models.py`
- Test: `tests/test_models.py`

**Step 1:** Failing tests for `Intent`, `Position`, `Balances` require `Decimal`, reject float construction helpers.

**Step 2:** Implement models with pydantic v2.

**Step 3:** `pytest tests/test_models.py -v` PASS

**Step 4:** Commit `feat: domain models for intent/position/balances`

### Task 2: Append-only ledger

**Objective:** Journal intents/acks/fills durably.

**Files:**
- Create: `src/market/ledger/jsonl.py`
- Test: `tests/test_ledger.py`

**Requirements:**
- atomic append
- read-all
- no silent overwrite of history

### Task 3: RiskGate pure logic

**Objective:** Block/resize intents from config + state.

**Files:**
- Create: `src/market/risk/gate.py`
- Test: `tests/test_risk_gate.py`

**Cases:**
- max position
- max daily loss
- orders/hour
- min spacing
- freeze_entries
- halt

### Task 4: Slow trend strategy v1 (pure)

**Objective:** One boring strategy: dual EMA on closed candles.

**Files:**
- Create: `src/market/strategy/slow_trend.py`
- Test: `tests/test_slow_trend.py`

**Rules (initial):**
- buy when fast EMA crosses above slow and flat
- sell when opposite and long
- no pyramiding
- emit reason + snapshot

---

## Phase 2 — Sim broker + loop

### Task 5: BrokerPort + SimBroker

**Objective:** Executable loop without real money.

**Files:**
- Create: `src/market/execution/port.py`
- Create: `src/market/execution/sim.py`
- Test: `tests/test_sim_broker.py`

### Task 6: Reconciler

**Objective:** Detect desync between local journal and broker.

**Files:**
- Create: `src/market/execution/reconcile.py`
- Test: `tests/test_reconcile.py`

### Task 7: App loop CLI

**Objective:** `python -m market run --mode sim`

**Files:**
- Create: `src/market/app/cli.py`
- Create: `src/market/app/loop.py`
- Create: `src/market/config.py`
- Create: `config/sim.yaml`

**Verify:** 50 loop iterations produce ledger rows, no exceptions.

### Task 8: Freeze + heartbeat

**Objective:** `data/state/FREEZE` and stale heartbeat block entries.

**Files:**
- Create: `src/market/ops/freeze.py`
- Create: `src/market/ops/heartbeat.py`
- Test: `tests/test_ops.py`

---

## Phase 3 — Dry-run “Robinhood shape”

### Task 9: RobinhoodBroker skeleton (no live default)

**Objective:** Adapter implementing BrokerPort with explicit `enabled` guard.

**Files:**
- Create: `src/market/execution/robinhood/client.py`
- Create: `src/market/execution/robinhood/broker.py`
- Test: `tests/test_robinhood_guard.py`

**Hard rule:** Instantiating live submit path requires `MARKET_RH_LIVE=1` and mode=live.

### Task 10: live-dry mode

**Objective:** Build orders, log fully, assert zero submit calls.

**Files:**
- Modify: `src/market/app/loop.py`
- Test: `tests/test_live_dry.py`

### Task 11: Auth/session failure handling

**Objective:** On auth errors → freeze entries + alert hook.

**Files:**
- Modify: `src/market/execution/robinhood/*`
- Test: `tests/test_auth_freeze.py`

---

## Phase 4 — Micro live (manual unlock only)

### Task 12: Micro-live runbook

**Objective:** Operator doc for $ disposable test buy/sell roundtrip.

**Files:**
- Create: `docs/RUNBOOK_LIVE.md`

### Task 13: Telegram alerts (optional)

**Objective:** Mirror critical events.

**Files:**
- Create: `src/market/ops/alerts.py`

### Task 14: Daily report

**Objective:** Summarize fills/fees/pnl/violations.

**Files:**
- Create: `src/market/ops/daily_report.py`

---

## Phase 5 — Hardening (only after micro live survives)

### Task 15: CoinbaseAdvancedBroker (recommended durable path)

### Task 16: External websocket marks

### Task 17: Tax lot export

### Task 18: Backtest harness on historical candles (strategy only)

---

## Explicit non-goals until Phase 4 stable

- Multi-strategy portfolio
- Grid bots
- Funding arb
- Copy-trading
- Auto strategy search
- Any size-up rules based on short windows

---

## Acceptance gates

| Gate | Requirement |
|------|-------------|
| G0 | Docs + package import |
| G1 | pytest green on domain/risk/strategy/sim |
| G2 | sim loop 24h unattended locally |
| G3 | live-dry zero submits proven |
| G4 | micro live roundtrip with caps |
| G5 | multi-day NET discussion only until evidence |

---

## Open decisions (need Sean)

1. Robinhood-first adapter vs Coinbase-first official API?
2. v1 strategy pick: slow_trend vs DCA-exit vs external signal?
3. Disposable bankroll number?
4. Laptop loop vs always-on small VPS (not AK47 droplet by default)?
