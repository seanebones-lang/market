# ADR 0002: Deterministic Candle Data and Declared Gaps

- **Status:** Accepted for G1
- **Date:** 2026-08-16
- **Decision owners:** Quant lead, backend lead, CTO gate

## Context

The prototype used short, mutable candle caches and could admit the currently forming bar. It had no
source/receive/closure metadata, reproducible checksum, gap control, or immutable raw evidence.

Coinbase's five-year `BTC-USD` history also contains 13 absent hourly buckets across three windows.
The same windows are absent from Coinbase's separate Advanced Trade candle endpoint. Treating those
hours as traded flat, copying another venue's prices, or silently joining both sides of an outage
would manufacture information.

## Decision

1. Normalize only schema-v1, UTC-aligned, close-confirmed hourly candles with exact Decimal OHLCV.
2. Preserve canonical raw responses separately from normalized research rows.
3. Content-address every dataset with SHA-256 and an immutable manifest and quality report.
4. Reject duplicates, order defects, current/future bars, flags, invalid values, and undeclared
   gaps.
5. Permit known provider gaps only through explicit `segment` admission. Never create replacement
   tradable bars.
6. Refuse segmented data through the ordinary flat loader. The segment loader verifies checksums,
   splits on every gap, validates each segment, and forces indicators to warm from empty state.
7. Keep quote polling separate from candle creation in paper mode.

## Consequences

- The retained five-year series is reproducible but is not a single continuous trading clock.
- Research engines must consume its four segments explicitly and aggregate results without carrying
  indicators, orders, or positions across a provider outage unless a later event model specifies
  that behavior.
- The current backtester remains non-promotable for its separate timing/accounting defects. G2 must
  replace it before this dataset is used as evidence of an edge.
- A second-venue sample is still desirable for market-data comparison, but it must remain a separate
  dataset rather than becoming an undeclared repair source.
