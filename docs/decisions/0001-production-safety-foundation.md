# ADR 0001: Production Safety Foundation

- **Status:** Accepted for G0
- **Date:** 2026-08-16
- **Decision owner:** CTO gate

## Context

The prototype conflates candle observation and execution timing, uses incomplete accounting, keeps
risk/order state in memory, and describes an obsolete unofficial Robinhood login path. These
decisions define the non-negotiable contracts for later implementation.

## Decisions

### Event timing

A strategy may use only close-confirmed bars. A decision based on bar `t` is created after that bar
closes and may execute no earlier than the next eligible event. The baseline market model fills at
bar `t+1` open plus side-specific spread, slippage, fee, and rounding.

### Accounting

Cash, inventory, realized P&L, unrealized P&L, fees, marked equity, and net liquidation value are
separate concepts. Every cash or inventory change must trace to an immutable event. Terminal
liquidation, when requested, is a normal costed fill rather than a silent cash adjustment.

### Data sources

Signal data and execution data are separate. Coinbase may provide research/signal candles;
Robinhood provides execution quotes, account state, and order truth. Every observation records
source time and receive time. Unclosed, stale, duplicate, out-of-order, or gapped bars fail closed.

### Robinhood API

Use only the official US Crypto Trading API. Start with v2 read actions and action-scoped
credentials. Authentication uses the documented API key, Ed25519 signature, and timestamp headers.
No username, password, TOTP, cookie, or reverse-engineered endpoint belongs in the design.

### Loss and exposure

Daily loss is current net liquidation value minus a persisted UTC day-start net liquidation anchor,
inclusive of realized/unrealized P&L and fees. Intraday peak-to-trough drawdown is a separate rail.
Exposure includes marked position, all nonterminal risk-increasing orders, and the proposed order.
Loss or stale-data rails block increases but continue to permit validated risk-reducing exits.

### Order recovery

Persist a deterministic UUID client order ID and submit-pending event before any POST. A timeout is
an unknown outcome, never permission to create a fresh order. Recovery queries authoritative broker
orders/executions and freezes new entries until the original outcome is resolved.

## Consequences

- Existing backtests are exploratory and non-promotable.
- SQLite and an append-only event model are required before live execution work.
- Runtime flags cannot enable live orders in G0.
- Later code and tests must cite this ADR when changing these contracts.
