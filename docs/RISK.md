# Risk rails (non-negotiable before live)

## Hard caps (v1 defaults — change only deliberately)

- Max position: small BTC notional (example $100–$200)
- Max daily loss: hard halt (example $25)
- Max orders/hour: low (example 4)
- Min time between orders: minutes, not seconds
- Entries default **OFF** in live until unlock
- Kill switch file: `data/state/FREEZE` stops new entries immediately

## Pre-live checklist

- [ ] Sim 7 days, no ledger corruption
- [ ] Reconcile tests for partial fill + reject + duplicate client_order_id
- [ ] live-dry produces zero broker submits (asserted)
- [ ] Auth failure path freezes entries
- [ ] Heartbeat staleness freezes entries
- [ ] Manual flatten/halt CLI works offline-ish
- [ ] Bankroll is disposable
- [ ] User explicitly sets `mode=live` + `allow_entries=true` + unlock token

## Failure modes to design for

1. Double submit after timeout (idempotency)
2. Session expired mid-order
3. Quote stale, still trading
4. Bot crash after submit before local journal write
5. Bot thinks flat, broker has BTC
6. Bot thinks long, broker flat (desync)
7. Infinite retry loops
8. Clock jump / laptop sleep gaps

## Freeze semantics

| Flag | Entries | Exits |
|------|---------|-------|
| running | yes | yes |
| freeze_entries | no | yes |
| halt | no | no (cancel opens only) |
| panic_flatten | no | reduce-only flatten attempts |

## Accounting

- Fees count as loss
- Unrealized is reported but daily halt uses realized + marked drawdown rule (define one, don’t mix silently)
