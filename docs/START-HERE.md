# START-HERE

## What this is

A research and execution platform for **BTC spot**, with the official Robinhood Crypto Trading API
as the first intended broker adapter.

## What this is not (yet)

- Not live
- Not approved for live money
- Not evidence of a profitable strategy
- Not a Kalshi/prediction-market bot (that is AK47)

## Truth order (when live)

1. Broker account balances + open orders (source of truth)
2. Local ledger / fill journal
3. Strategy intent logs
4. Dashboards / summaries (never trust alone)

## Modes (planned)

| Mode | Orders | Money |
|------|--------|-------|
| `sim` | simulated fills | fake |
| `paper` | no broker submit | fake or shadow |
| `live-dry` | build orders, log, do not submit | none |
| `live` | disabled by CLI and build lock | none |

## Commands now

```bash
.venv/bin/python -m market run --iterations 40
.venv/bin/python -m market run --config config/live-dry.yaml
.venv/bin/python -m market fetch-candles
./market.sh verify-dataset --manifest data/research/manifests/coinbase-btc-usd-1h-20210816T000000Z-20260816T000000Z-00c5f0b63bef9236.manifest.json
./market.sh verify-research-splits --plan config/research/g3-ema-v1-splits.json
env PYTHONPATH=src .venv/bin/python -m market.research.power_cli \
  --split-plan config/research/g3-ema-v1-splits.json \
  --study-definition config/research/g3-ema-v1-power-study.json \
  --output /tmp/g3-ema-v1-power-study.json
./market.sh derive-rh-v2-cost \
  --fixture tests/fixtures/robinhood/v2_cost_snapshot.json \
  --out-dir /tmp/market-rh-v2-cost
.venv/bin/python -m market backtest --csv data/cache/btc_usd_1h.csv
./market.sh verify-backtest --manifest data/backtests/RUN_ID/manifest.json
```

Nothing graduates to `live` without passing G0-G8 in
`docs/plans/2026-08-16-production-readiness-roadmap.md`.

## Immediate next human decisions

1. Preserve G3 protocol 1.0 and its split plan as historical contracts; ADR 0003 retires that
   study before execution after the G3.2b power checkpoint.
2. Do not run G1 EMA parameters or calculate holdout strategy output under the retired protocol.
3. Approve a complete prospective protocol 2.0 before resuming G3.3. It must bind measured or
   explicitly route-specific costs, sample-size/power, final-selection, resampling, multiplicity,
   and stress semantics.
4. Treat the final 2025-08-16 through 2026-08-16 window as strategy-unseen but market-path-known;
   it may not be relabeled as an untouched confirmatory holdout for protocol 2.0.
5. Keep the registry/evidence layer strategy-agnostic so the next hypothesis need not be an EMA.
6. Treat G3.2c as an offline schema only. Its fixture is synthetic and does not set a cost
   assumption.
7. G3.2d freezes how a future 30-day sample must run and be analyzed. No dated production run plan
   exists yet.
8. G3.2e has a narrow, recorded exception for one macOS-Keychain-backed credential with only Read
   crypto accounts, Read crypto products, and Read crypto quotes. Follow its contract stage by
   stage; do not enable order actions, holdings/order-history reads, or live capital.
9. Implementation readiness is not permission to skip the action-scope check or sanitized
   preflight. Do not start the 30-day window until both pass and a future run plan is frozen.
