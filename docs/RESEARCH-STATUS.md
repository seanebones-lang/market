# Research Status

**Status:** Exploratory only; no demonstrated trading edge

The saved backtests from 2026-08-04 are invalid for strategy promotion. They may be retained as
prototype smoke-test artifacts, but they must not be used to justify paper graduation, broker
order permissions, or live capital.

## Why the reports are non-promotable

- The saved reports were produced when signals observed a candle close and filled at that same
  close. G2.1 removes this defect for new runs only; it does not rehabilitate saved reports.
- The saved reports omit spread and slippage. G2.2 adds explicitly synthetic assumptions for new
  runs, and G2.3 separates Robinhood v1 market-maker spread treatment from v2 exchange-taker fees.
  Configured rates are still assumptions, not account-observed costs.
- The saved reports used an ambiguous fee label. G2.3a defines new-run transaction fees per fill;
  it does not change or rehabilitate the saved artifacts.
- The saved reports liquidated terminal inventory without a recorded fill or exit cost. G2.4 fixes
  this for new runs only; it does not rehabilitate those reports.
- The saved reports label marked final equity as realized P&L. G2.5 separates accounting concepts
  for new runs only; it does not rehabilitate the saved labels.
- The best run used 1,500 bars, but its exact input dataset is no longer retained.
- The current cache contains only 600 bars, approximately 25 days.
- There is no walk-forward split, untouched holdout, uncertainty interval, multiple-testing record,
  or matched-exposure benchmark.

## Saved result labels

| Run | Bars | Reported P&L | Promotion status |
|---|---:|---:|---|
| `bt_20260804T203235Z` | 1,500 | `+$0.622300460` | Exploratory; invalid |
| `bt_20260804T203617Z` | 600 | `-$1.600358155` | Exploratory; invalid |
| `bt_20260804T203634Z` | 600 | `-$1.600358155` | Exploratory; invalid |

G1 now provides a versioned five-year input dataset with immutable raw data, normalized candles,
checksums, declared gaps, and strategy-safe segments. It does not rehabilitate these saved runs or
the partially rebuilt engine. G2.1 now enforces next-bar-open eligibility, G2.2 supplies declared
bid/ask and adverse-slippage models, and G2.3 supplies route-specific Robinhood cost contracts. The
G2.3a schema defines each transaction fee per executed fill, and G2.4 makes terminal liquidation a
fully costed sell fill that leaves new runs flat. G2.5 adds an immutable portfolio journal and
separate gross P&L, fee, marked-equity, and net-liquidation accounts. G2.6 adds explicit order,
execution, partial-fill, closed-trade, round-trip, outcome, and open-inventory lifecycle metrics.
G2.7 adds cash, peak-cost-basis-matched buy-and-hold, and periodic-DCA comparators under the same
execution and cost assumptions, plus absolute and net-P&L-over-maximum-drawdown comparisons.
G2.8 adds exact descriptive performance statistics and aligned benchmark-alpha estimates with
explicit hourly sampling, 8,760-period annualization, and undefined-metric statuses. These metrics
do not make the short saved reports valid or establish statistical significance.
The next valid result begins only after the complete G2 gate passes and uses the verified G1
manifest. Profitability can never be guaranteed.
