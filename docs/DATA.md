# Market Data Contract

## Scope

G1 defines one normalized research interval: closed `BTC-USD` one-hour bars. The contract is
provider-aware and fail-closed. It does not authorize strategy promotion or live execution.

## Candle schema

| Field | Contract |
|---|---|
| `schema_version` | Integer schema version; currently `1` |
| `ts` | UTC inclusive bar-open time, exactly aligned to an hourly boundary |
| `timeframe` | `1h`; every other interval is rejected |
| `source` | Nonempty provider/product identifier |
| `open/high/low/close` | Positive `Decimal` values satisfying `low <= open, close <= high` |
| `volume` | Nonnegative `Decimal` base volume |
| `received_at` | UTC time at which this process received the observation |
| `close_confirmed_at` | UTC time at which this process could confirm the historical bar was closed |
| `is_closed` | Must be true before strategy admission |
| `quality_flags` | Sorted provider/normalization flags; any flag blocks strategy admission |

Bar ranges are half-open: a bar at `10:00Z` represents `[10:00Z, 11:00Z)`. A 30-second
confirmation grace keeps the newest boundary out of ingestion while a venue may still publish its
final update.

## Sequence quality

The checker does not sort, deduplicate, forward-fill, or repair input. It reports and rejects:

- duplicates and out-of-order timestamps;
- missing hourly bars;
- mixed source or timeframe;
- unclosed/current bars and future timestamps or confirmations;
- provider quality flags; and
- close-to-close moves over the declared extreme-move threshold.

Invalid row relationships are rejected by the `Candle` model before sequence validation.

## Coinbase acquisition

The source is the official Coinbase Exchange public `BTC-USD` candles endpoint. Coinbase documents
a maximum of 300 candles per response, warns that historical data can be incomplete, and may return
bars before the requested start. The ingestor therefore:

1. requests at most 299 hourly intervals per page so boundary extras cannot displace an in-range
   bar;
2. retains each untouched response in the raw artifact;
3. filters normalized data to the exact requested half-open range;
4. sorts the documented descending provider response into ascending research order;
5. never deduplicates or fills; and
6. validates the complete range before admission.

Official references:

- <https://docs.cdp.coinbase.com/api-reference/exchange-api/rest-api/products/get-product-candles>
- <https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product-candles>

## Immutable dataset layout

```text
data/research/
  raw/          canonical provider responses and request boundaries
  normalized/   schema-v1 deterministic CSV
  quality/      machine-readable checker result and strategy-admission policy
  manifests/    paths, sizes, SHA-256 checksums, range, source, and regimes
```

Files are content-addressed and opened with exclusive-create semantics. An existing artifact may be
reused only when its bytes are identical; it is never overwritten.

Build and verify:

```bash
./market.sh build-dataset \
  --start 2021-08-16 --end 2026-08-16 \
  --gap-policy segment --out-dir data/research

./market.sh verify-dataset \
  --manifest data/research/manifests/coinbase-btc-usd-1h-20210816T000000Z-20260816T000000Z-00c5f0b63bef9236.manifest.json
```

## Declared-gap policy

The retained Coinbase range contains 13 missing hours across three provider-history gaps. Coinbase's
separate Advanced Trade candle endpoint was sampled over all three windows and showed the same
omissions. No alternate-venue price or synthetic zero-volume bar was substituted.

The manifest therefore has `quality_status=pass_segmented` and
`strategy_admission=segments_only`. The ordinary flat dataset loader refuses it. The segment loader
verifies all checksums, confirms that gaps are the only defects, splits the data into four contiguous
series, and re-runs the strict checker on each. Indicators start without prior state on every segment,
so their normal warm-up requirement is applied again after each gap.

Any new defect, boundary omission, partial bar, flag, or checksum mismatch still rejects the entire
dataset.
