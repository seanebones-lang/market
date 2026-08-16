# Thoughts: Robinhood BTC autotrader vs AK47

## Short answer

Yes — **much more complex than AK47**, and for structural reasons, not just “more code.”

AK47 is a closed game: short-lived binary contracts, known resolution, one venue, one product shape, tight loop, small wallet, freeze discipline.

This is open-ended **spot crypto custody + continuous market + hostile broker surface + irreversible fills**.

## Why AK47 was “simpler” (relatively)

| AK47 | Robinhood BTC spot |
|------|--------------------|
| Venue designed around API/bots-ish access (Kalshi) | Robinhood: official API, continuous inventory risk |
| Contract dies in 15m; P&L settles cleanly | BTC position can sit forever; path dependency |
| Binary yes/no + fee model you already modeled | Spread, partial fills, funding friction, transfer delays |
| Small discrete action space | Continuous size, timing, inventory |
| `/proc` + `verify_live` can pin process truth | Must pin **broker balances + orders** as truth |
| Freeze = stop trading known instrument | Freeze = flatten or hold inventory risk |
| Edge can be measured per window | Edge drowns in noise + fees unless highly selective |
| Short-lived contract risk | Personal crypto account, API-key, custody, and account-restriction risk |

## The Robinhood problem (the real boss fight)

Robinhood now provides an official US Crypto Trading API. The difficult parts are therefore not
reverse-engineered login sessions; they are cost, state, and failure correctness:

1. V1 and v2 use different fee/routing behavior.
2. Best price is not a size-aware execution guarantee.
3. Partial executions, cancellation races, timeouts, and account restrictions must be reconciled.
4. API credentials and Ed25519 private keys require least-privilege handling and rotation.
5. A personal spot account still has continuous custody, tax-lot, and inventory risk.

**Recommendation:** keep a strict `BrokerPort`, implement `SimBroker`, then add the official
`RobinhoodBroker` in read-only mode. Do not enable order permissions until the research,
persistence, execution, and operations gates pass.

## Complexity map (honest)

### 1. Execution / brokerage layer (hard)

- Signed-request credential lifecycle
- Balance + position sync
- Order submit / cancel / status
- Idempotency keys (don’t double-buy on retry)
- Reconcile local intent vs broker fills
- Clock skew, retries, rate limits

### 2. Market data layer (medium)

- BTCUSD marks from RH and/or independent feed (Coinbase/WS, etc.)
- Never trade solely on a single stale poll if avoidable
- Separate **signal price** from **execution venue price**

### 3. Strategy layer (deceptively hard)

- AK47 edge was structural/windowed.
- Spot BTC retail bot usually loses to:
  - fees/spread
  - overtrading
  - regime change
  - inventory panic
- v1 should be boring: one regime, one signal, tiny size, long cooldown.

### 4. Risk / kill switches (mandatory, harder than strategy)

- Max notional
- Max daily loss
- Max position BTC
- Max orders/hour
- Cooldown after error
- Flatten / halt commands
- Dead-man’s switch if heartbeat dies
- No-increase mode (exits only)

### 5. Ledger + ops (where most bots die)

- Append-only fill journal
- Daily P&L that matches broker
- Alerting (Telegram already in your stack)
- Crash recovery: “what did I mean to do vs what filled?”

### 6. Legal / account risk (non-code)

- The account owner must review the current Robinhood agreements, credential permissions, and
  personal/self-directed account constraints before live use.
- Tax lot tracking matters once real.
- Do not put rent money in v1.

## What “good” looks like for v1

Not alpha. **Survival + correctness.**

Success criteria before caring about returns:

1. Sim mode runs 7 days without state corruption.
2. Dry-run builds orders that would have been valid.
3. Reconciler detects missing/extra fills.
4. Kill switch stops new entries in <1 loop.
5. Live size so small a total loss is emotionally irrelevant.
6. Every order has: reason, signal snapshot, risk checks, client_order_id.

## Strategy stance (opinionated)

For first live BTC on a fragile broker:

- **Prefer slow** (minutes–hours), not sub-second.
- **Prefer fewer trades**.
- **Prefer inventory caps** over “always in market.”
- Avoid leverage fantasies (RH spot BTC is not your futures desk).
- Consider “signal off exchange, execute rarely” rather than tick scalping.

Candidate v1 strategies (pick one only):

1. **Slow trend** — EMA cross or Donchian breakout, 1h+, wide stops/time stops.
2. **Mean reversion bands** — only fade extremes with hard invalidation; easy to get run over.
3. **Rules DCA + discretionary exit engine** — least “clever,” often best ops trainer.
4. **External signal follower** — your own model/score; bot only handles risk+execution.

Do **not** port AK47’s 15m microstructure assumptions onto spot BTC 1:1. Different game.

## Architecture principle stolen from AK47 (keep)

- Process liveness ≠ strategy health
- File sanity checks are not market truth
- Freeze discipline beats cleverness
- Small size until multi-day NET proves itself
- Discussion-only mode until you explicitly unlock workstreams that spend money

## Complexity vs AK47 (rough)

If AK47 live ops complexity = 1.0:

| Area | Multiplier |
|------|------------|
| Venue API stability | 3–5× |
| Position/inventory risk | 4× |
| Reconcile/ledger | 3× |
| Auth/session | 3× |
| Strategy evaluation | 2–3× |
| Regulatory/account/credential risk | 5×+ |
| Overall project | **~3–5×** AK47 to reach equivalent ops confidence |

## Suggested product shape

```text
market/
  strategy/        # pure functions: features -> intent
  risk/            # gates intents
  execution/       # BrokerPort adapters
  data/            # prices, clocks
  ledger/          # fills, snapshots
  ops/             # heartbeat, alerts, freeze
  app/             # loop / CLI
```

One loop:

```text
poll data -> strategy intent -> risk gate -> (optionally) execute -> reconcile -> journal -> sleep
```

## Bottom line

- Worth building **if** the goal is a real multi-broker execution desk with hard risk rails.
- Not worth building **if** the goal is “get rich on RH BTC bot next week.”
- Robinhood can be a target adapter, but the **system** should not be married to it.
- Start sim → dry-run → micro-live. Same emotional discipline as AK47 Run6 freeze, but inventory makes freezes scarier.
