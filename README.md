# market

Autonomous **spot BTC** trader with a broker-adapter architecture.

Primary target execution path: the **official Robinhood Crypto Trading API (BTC-USD)**.
The strategy, risk, ledger, and operations layers remain broker-agnostic.

> **LIVE TRADING DISABLED.** The repository is in production-readiness gate G0. No build in
> this repository is approved or able to submit a live Robinhood order.

> Not financial advice. Live trading can lose the entire bankroll. Passing the project gates does
> not guarantee profitability.

## Status

Prototype sim, paper, and backtest components exist. The saved research runs are exploratory and
invalid for strategy promotion. See `docs/RESEARCH-STATUS.md` and the production-readiness roadmap.

```bash
cd ~/Desktop/market
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/python -m market run --iterations 40
.venv/bin/python -m market paper --ticks 5 --sleep 2
.venv/bin/python -m market run --config config/live-dry.yaml
.venv/bin/python -m market fetch-candles
.venv/bin/python -m market backtest --csv data/cache/btc_usd_1h.csv
.venv/bin/python -m market freeze --reason "manual"
.venv/bin/python -m market unfreeze
```

## Backtest on actual data

```bash
cd ~/Desktop/market

# easiest (always works):
./market.sh backtest --fetch --batches 5 --cash 1000 --qty 0.001

# or after install:
.venv/bin/python -m market backtest --fetch --batches 5 --cash 1000 --qty 0.001
```

Candles are real Coinbase Exchange public BTC-USD bars (not synthetic).

Live mode is hard-refused by both the CLI and a build-level transport lock. Runtime flags cannot
enable order submission.

## Docs

- `docs/THOUGHTS.md` — why this is harder than AK47, constraints, recommendations
- `docs/ARCHITECTURE.md` — system design
- `docs/RESEARCH-STATUS.md` — why current results cannot support promotion
- `docs/plans/2026-08-16-production-readiness-roadmap.md` — controlling gate plan
- `docs/plans/2026-08-04-market-btc-autotrader.md` — implementation plan
- `docs/RISK.md` — hard risk rails before any live mode
- `docs/START-HERE.md` — operator entry point

## Local path

```text
~/Desktop/market
```

Repo: https://github.com/seanebones-lang/market (private)
