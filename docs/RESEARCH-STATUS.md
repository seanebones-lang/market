# Research Status

**Status:** Exploratory only; no demonstrated trading edge

The saved backtests from 2026-08-04 are invalid for strategy promotion. They may be retained as
prototype smoke-test artifacts, but they must not be used to justify paper graduation, broker
order permissions, or live capital.

## Why the reports are non-promotable

- The saved reports were produced when signals observed a candle close and filled at that same
  close. G2.1 removes this defect for new runs only; it does not rehabilitate saved reports.
- The saved reports omit spread and slippage. G2.2 adds explicitly synthetic assumptions for new
  runs, but those assumptions are not yet calibrated to venue/routing evidence.
- Terminal inventory is liquidated without a recorded fill or exit cost.
- Marked final equity is labeled realized P&L.
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
the partially rebuilt engine. G2.1 now enforces next-bar-open eligibility, and G2.2 supplies
declared bid/ask and adverse-slippage models. The next valid result begins only after the complete
G2 gate passes and uses the verified G1 manifest. Profitability can never be guaranteed.
