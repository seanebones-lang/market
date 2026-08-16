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
.venv/bin/python -m market backtest --csv data/cache/btc_usd_1h.csv
```

Nothing graduates to `live` without passing G0-G8 in
`docs/plans/2026-08-16-production-readiness-roadmap.md`.

## Immediate next human decisions

1. Complete G0 and record a clean offline baseline.
2. Rebuild research timing, accounting, data, and walk-forward validation.
3. Reject or promote the EMA hypothesis from untouched out-of-sample evidence.
4. Create a Robinhood credential with read actions only after the read-only adapter gate begins.
