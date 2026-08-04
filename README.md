# market

Autonomous **spot BTC** trader with a broker-adapter architecture.

Primary target execution path: **Robinhood crypto (BTC)**.
Design assumes Robinhood is hostile to bots (no official retail trading API) and builds the system so the hard parts (strategy, risk, ledger, ops) are broker-agnostic.

> Not financial advice. Live trading can lose the entire bankroll. Unofficial Robinhood automation can violate ToS and get the account locked.

## Status

Phase 1–3 — sim core + RH live guards + hist backtest.
Paper/sim/live-dry only. No live orders. No credentials committed.

```bash
cd ~/Desktop/market
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/python -m market run --iterations 40
.venv/bin/python -m market run --config config/live-dry.yaml
.venv/bin/python -m market fetch-candles
.venv/bin/python -m market backtest --csv data/cache/btc_usd_1h.csv
.venv/bin/python -m market freeze --reason "manual"
.venv/bin/python -m market unfreeze
```

Live mode is hard-refused unless `MARKET_RH_LIVE=1` and still aborts until real RH transport exists.

## Docs

- `docs/THOUGHTS.md` — why this is harder than AK47, constraints, recommendations
- `docs/ARCHITECTURE.md` — system design
- `docs/plans/2026-08-04-market-btc-autotrader.md` — implementation plan
- `docs/RISK.md` — hard risk rails before any live mode
- `docs/START-HERE.md` — operator entry point

## Local path

```text
~/Desktop/market
```

Repo: https://github.com/seanebones-lang/market (private)
