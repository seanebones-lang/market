# market

Autonomous **spot BTC** trader with a broker-adapter architecture.

Primary target execution path: the **official Robinhood Crypto Trading API (BTC-USD)**.
The strategy, risk, ledger, and operations layers remain broker-agnostic.

> **LIVE TRADING DISABLED.** The repository has completed data gate G1 only. No build in
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
./market.sh build-dataset --start 2021-08-16 --end 2026-08-16 --gap-policy segment
./market.sh verify-dataset --manifest data/research/manifests/coinbase-btc-usd-1h-20210816T000000Z-20260816T000000Z-00c5f0b63bef9236.manifest.json
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

The versioned five-year research artifact is immutable and checksum-verified. It contains three
declared Coinbase history gaps and is admitted only as four independently warmed segments. See
`docs/DATA.md`. G2.1 now enforces next-bar-open timing, but the backtester remains exploratory until
the rest of G2 fixes execution costs, terminal liquidation, and accounting.

Live mode is hard-refused by both the CLI and a build-level transport lock. Runtime flags cannot
enable order submission.

## Docs

- `docs/THOUGHTS.md` — why this is harder than AK47, constraints, recommendations
- `docs/ARCHITECTURE.md` — system design
- `docs/DATA.md` — candle schema, quality gate, immutable datasets, and gap policy
- `docs/BACKTESTING.md` — event timing, anti-look-ahead proof, and remaining engine blockers
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
