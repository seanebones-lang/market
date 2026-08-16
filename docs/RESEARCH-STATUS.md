# Research Status

**Status:** Exploratory only; no demonstrated trading edge

The saved backtests from 2026-08-04 are invalid for strategy promotion. They may be retained as
prototype smoke-test artifacts, but they must not be used to justify paper graduation, broker
order permissions, or live capital.

## Why the reports are non-promotable

- Signals observe a candle close and fill at that same close.
- Spread and slippage are absent from the backtest engine.
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

The next valid research artifact begins only after G1 data contracts and G2 backtest correctness
pass. Profitability can never be guaranteed.
