# START-HERE

## What this is

A planned autotrader for **actual BTC spot** with Robinhood as the first intended broker adapter.

## What this is not (yet)

- Not live
- Not paper-trading wired
- Not a Kalshi/prediction-market bot (that is AK47)
- Not a promise Robinhood will stay automatable

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
| `live` | real BTC buy/sell | real |

Nothing graduates to `live` without passing the risk checklist in `docs/RISK.md`.

## Immediate next human decisions

1. Confirm Robinhood-only vs broker-adapter with Coinbase Advanced as safer primary.
2. Bankroll cap for v1 (recommend tiny: e.g. $50–$200).
3. Strategy class for v1 (trend / mean-revert / schedule DCA+exit — pick one).
4. Accept ToS / ban risk if using unofficial RH client.
