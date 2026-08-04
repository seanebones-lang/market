"""CLI entrypoint: python -m market / market run"""

from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

from rich.console import Console

from market.app.loop import build_sim_loop
from market.config import load_config
from market.domain.models import Mode

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="market", description="BTC spot autotrader")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run trading loop")
    run_p.add_argument("--config", default="config/sim.yaml")
    run_p.add_argument("--iterations", type=int, default=None)
    run_p.add_argument("--root", default=".")
    run_p.add_argument(
        "--mode",
        choices=["sim", "paper", "live-dry", "live"],
        default=None,
        help="Override config mode",
    )

    sub.add_parser("version", help="Print version")

    freeze_p = sub.add_parser("freeze", help="Write FREEZE file (block entries)")
    freeze_p.add_argument("--root", default=".")
    freeze_p.add_argument("--config", default="config/sim.yaml")
    freeze_p.add_argument("--reason", default="manual")

    unfreeze_p = sub.add_parser("unfreeze", help="Remove FREEZE file")
    unfreeze_p.add_argument("--root", default=".")
    unfreeze_p.add_argument("--config", default="config/sim.yaml")

    fetch_p = sub.add_parser("fetch-candles", help="Fetch public BTC-USD candles (Coinbase)")
    fetch_p.add_argument("--out", default="data/cache/btc_usd_1h.csv")
    fetch_p.add_argument("--granularity", type=int, default=3600)
    fetch_p.add_argument("--batches", type=int, default=3)
    fetch_p.add_argument("--root", default=".")

    bt_p = sub.add_parser("backtest", help="Run slow_trend backtest on candle CSV")
    bt_p.add_argument("--csv", default="data/cache/btc_usd_1h.csv")
    bt_p.add_argument("--root", default=".")
    bt_p.add_argument("--qty", default="0.001")
    bt_p.add_argument("--cash", default="1000")
    bt_p.add_argument("--fast", type=int, default=12)
    bt_p.add_argument("--slow", type=int, default=26)

    args = parser.parse_args(argv)

    if args.cmd == "version":
        from market import __version__

        console.print(__version__)
        return 0

    root = Path(getattr(args, "root", ".")).resolve()

    if args.cmd == "fetch-candles":
        from market.data.candles import fetch_coinbase_candles, save_candles_csv

        out = Path(args.out)
        if not out.is_absolute():
            out = root / out
        console.print(f"fetching BTC-USD granularity={args.granularity} batches={args.batches}")
        candles = fetch_coinbase_candles(
            granularity=args.granularity, limit_batches=args.batches
        )
        save_candles_csv(out, candles)
        console.print(f"[green]wrote[/green] {len(candles)} candles → {out}")
        if candles:
            console.print(f"range {candles[0].ts.isoformat()} → {candles[-1].ts.isoformat()}")
        return 0

    if args.cmd == "backtest":
        from market.backtest.engine import run_backtest
        from market.data.candles import load_candles_csv
        from market.strategy.slow_trend import SlowTrendConfig

        csv_path = Path(args.csv)
        if not csv_path.is_absolute():
            csv_path = root / csv_path
        if not csv_path.exists():
            console.print(f"[red]missing[/red] {csv_path} — run: market fetch-candles")
            return 2
        candles = load_candles_csv(csv_path)
        cfg = SlowTrendConfig(
            fast_ema=args.fast,
            slow_ema=args.slow,
            order_qty_btc=Decimal(args.qty),
        )
        result = run_backtest(
            candles,
            starting_usd=Decimal(args.cash),
            qty_btc=Decimal(args.qty),
            strategy_cfg=cfg,
        )
        console.print(
            f"bars={len(candles)} fills={len(result.fills)} intents={result.intents} "
            f"allowed={result.allowed} blocked={result.blocked}"
        )
        console.print(
            f"start_usd={result.starting_usd} end_usd={result.final_usd} "
            f"pnl={result.realized_pnl_usd}"
        )
        return 0

    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    config = load_config(cfg_path)

    if getattr(args, "mode", None):
        config = config.model_copy(update={"mode": Mode(args.mode)})

    if args.cmd == "freeze":
        loop = build_sim_loop(config, root=root)
        loop.freeze.freeze(args.reason)
        console.print(f"[yellow]FROZEN[/yellow] {loop.freeze.path}")
        return 0

    if args.cmd == "unfreeze":
        loop = build_sim_loop(config, root=root)
        loop.freeze.unfreeze()
        console.print(f"[green]unfrozen[/green] {loop.freeze.path}")
        return 0

    if args.cmd == "run":
        if config.mode == Mode.LIVE:
            if os.environ.get("MARKET_RH_LIVE", "0") != "1":
                console.print(
                    "[red]refusing live mode[/red]: set MARKET_RH_LIVE=1 explicitly "
                    "(and understand ToS/ban risk)"
                )
                return 3
            console.print("[red bold]LIVE MODE REQUESTED — not fully wired; aborting[/red bold]")
            return 3

        loop = build_sim_loop(config, root=root)
        iterations = args.iterations if args.iterations is not None else config.iterations
        if iterations is None:
            iterations = 5
        if iterations == 0:
            iterations = None
        console.print(
            f"[bold]market[/bold] mode={config.mode.value} broker={config.broker.name} "
            f"iterations={iterations}"
        )
        sleep_fn = (lambda _s: None) if iterations is not None else None
        # live-dry / paper: still use sim broker for marks, never submit
        if config.mode in {Mode.LIVE_DRY, Mode.PAPER}:
            stats = loop.run(iterations=iterations, sleep_fn=sleep_fn, demo_prices=True)
        else:
            stats = loop.run(iterations=iterations, sleep_fn=sleep_fn)
        console.print(
            f"ticks={stats.ticks} intents={stats.intents} allowed={stats.allowed} "
            f"blocked={stats.blocked} submits={stats.submits} fills={stats.fills}"
        )
        console.print(f"balances={loop.broker.get_balances().model_dump()}")
        console.print(f"position={loop.broker.get_btc_position().model_dump()}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
