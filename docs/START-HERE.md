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
.venv/bin/python -m market backtest --csv data/cache/btc_usd_1h.csv
./market.sh verify-backtest --manifest data/backtests/RUN_ID/manifest.json
```

Nothing graduates to `live` without passing G0-G8 in
`docs/plans/2026-08-16-production-readiness-roadmap.md`.

## Immediate next human decisions

1. Treat `docs/research/G3.1-EMA-PREREGISTRATION.md` as binding protocol version 1.0; do not run or
   tune the five-year strategy before the split is frozen.
2. Complete G3.2 by freezing chronological walk-forward windows and the one untouched final
   holdout.
3. Build the G3.3 experiment registry for every preregistered pair, including failures and
   zero-trade results.
4. Reject or promote the EMA hypothesis only from the predeclared out-of-sample evidence.
5. Do not begin G4 or create a Robinhood credential until the controlling roadmap unlocks it.
