# Architecture

## Goal

Loop that can buy/sell **actual BTC** under hard risk rails, with Robinhood as an adapter — not as the brain.

## High-level

```text
                 ┌──────────────┐
                 │  MarketData  │  (RH mark + optional external WS)
                 └──────┬───────┘
                        │
                        v
┌────────────┐   ┌──────────────┐   ┌─────────────┐   ┌──────────────┐
│  Features  │-->│   Strategy   │-->│  RiskGate   │-->│   BrokerPort │
└────────────┘   │ (pure/no I/O)│   │ (pure+state)│   │ sim/rh/coin  │
                 └──────────────┘   └─────────────┘   └──────┬───────┘
                                                             │
                        ┌────────────────────────────────────┘
                        v
                 ┌──────────────┐
                 │  Reconciler  │<-- broker balances/orders/fills
                 └──────┬───────┘
                        v
                 ┌──────────────┐
                 │    Ledger    │  append-only JSONL / SQLite
                 └──────┬───────┘
                        v
                 ┌──────────────┐
                 │  Ops/Alert   │  freeze, heartbeat, Telegram
                 └──────────────┘
```

## Core types (conceptual)

```text
Intent:
  side: buy|sell
  qty_btc: Decimal
  order_type: market|limit
  limit_price: Optional[Decimal]
  reason: str
  signal_snapshot: dict
  client_order_id: str
  ts: datetime

RiskDecision:
  allow: bool
  intent: Optional[Intent]  # maybe size-reduced
  violations: list[str]

Fill:
  client_order_id: str
  broker_order_id: str
  side, qty, price, fee, ts
  raw: dict
```

## BrokerPort (interface)

```python
class BrokerPort(Protocol):
    def get_balances(self) -> Balances: ...
    def get_btc_position(self) -> Position: ...
    def get_open_orders(self) -> list[Order]: ...
    def place_order(self, intent: Intent) -> OrderAck: ...
    def cancel_order(self, broker_order_id: str) -> None: ...
    def get_order(self, broker_order_id: str) -> Order: ...
    def get_quote(self, symbol: str = "BTC") -> Quote: ...
```

### Adapters

1. `SimBroker` — deterministic fills from quote + slippage model
2. `RobinhoodBroker` — unofficial session client, isolated
3. (later) `CoinbaseAdvancedBroker` — official API keys

All strategy/risk code imports **only** `BrokerPort`.

## App loop

```text
every N seconds:
  if freeze: manage exits-only or halt; continue
  snapshot = data.poll()
  pos = broker.get_btc_position()
  bal = broker.get_balances()
  intent = strategy.evaluate(snapshot, pos, bal, clock)
  decision = risk.evaluate(intent, pos, bal, daily_pnl, config)
  if mode == live and decision.allow:
      ack = broker.place_order(decision.intent)
      ledger.append(ack)
  elif mode in {sim, paper, live-dry}:
      ledger.append(shadow)
  reconcile()
  heartbeat()
```

## State stores

- `data/ledger/fills.jsonl` — append-only
- `data/ledger/intents.jsonl`
- `data/state/runtime.json` — freeze flags, daily loss, last heartbeat
- `data/state/session.json` — non-secret session metadata only
- secrets only in env / macOS keychain / local `.env` (gitignored)

## Config

```yaml
mode: sim  # sim | paper | live-dry | live
symbol: BTC
loop_seconds: 30
risk:
  max_position_btc: 0.002
  max_notional_usd: 150
  max_daily_loss_usd: 25
  max_orders_per_hour: 4
  min_seconds_between_orders: 300
  allow_entries: true
strategy:
  name: slow_trend_v1
  timeframe: 1h
broker:
  name: sim  # sim | robinhood | coinbase
```

## Robinhood adapter notes

- Isolate all unofficial HTTP/session code under `execution/robinhood/`
- Never let strategy import it
- Expect:
  - login challenges
  - cookie/session refresh
  - schema drift
- Feature-flag every live call
- Default: entries disabled until explicit unlock file/env

## Observability

Mirroring AK47 lessons:

- `verify_live` style check = process up **and** last successful broker snapshot fresh
- Telegram alert on: reject, freeze, daily loss hit, auth failure, reconcile mismatch
- Daily summary: realized/unrealized, turnover, fees, violations blocked

## Non-goals (v1)

- Multi-asset portfolio optimization
- HFT / websocket market making on RH
- ML training loop in-process
- Cross-margin / leverage
- “Autonomous strategy discovery”
