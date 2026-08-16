"""CLI entrypoint: python -m market / market run"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import UTC
from decimal import Decimal
from pathlib import Path

from rich.console import Console

from market.app.loop import build_paper_live_loop, build_sim_loop
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

    paper_p = sub.add_parser(
        "paper",
        help="Paper trade with LIVE Coinbase BTC prices (no Robinhood, fake fills)",
    )
    paper_p.add_argument("--config", default="config/paper-live.yaml")
    paper_p.add_argument("--root", default=".")
    paper_p.add_argument("--cash", default="1000")
    paper_p.add_argument("--ticks", type=int, default=5, help="Live poll ticks after hist replay")
    paper_p.add_argument("--sleep", type=float, default=2.0, help="Seconds between live ticks")
    paper_p.add_argument(
        "--batches", type=int, default=2, help="Coinbase candle batches (~300/bar)"
    )
    paper_p.add_argument("--no-replay", action="store_true", help="Skip hist candle replay")

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

    bt_p = sub.add_parser(
        "backtest",
        help="Backtest slow_trend on REAL candle data (CSV cache and/or fresh Coinbase fetch)",
    )
    bt_p.add_argument("--csv", default="data/cache/btc_usd_1h.csv")
    bt_p.add_argument("--root", default=".")
    bt_p.add_argument("--qty", default="0.001")
    bt_p.add_argument("--cash", default="1000")
    bt_p.add_argument("--fast", type=int, default=12)
    bt_p.add_argument("--slow", type=int, default=26)
    bt_p.add_argument("--fee-bps", default="5", help="Round-trip fee model in bps per fill")
    bt_p.add_argument(
        "--fetch",
        action="store_true",
        help="Fetch fresh Coinbase BTC-USD candles before backtest (actual market data)",
    )
    bt_p.add_argument("--granularity", type=int, default=3600, help="Candle seconds (3600=1h)")
    bt_p.add_argument("--batches", type=int, default=5, help="Fetch batches (~300 candles each)")
    bt_p.add_argument(
        "--out-dir",
        default="data/backtests",
        help="Write summary/fills/equity under this dir",
    )
    bt_p.add_argument("--run-id", default=None, help="Optional run id (default: timestamp)")

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
        candles = fetch_coinbase_candles(granularity=args.granularity, limit_batches=args.batches)
        save_candles_csv(out, candles)
        console.print(f"[green]wrote[/green] {len(candles)} candles → {out}")
        if candles:
            console.print(f"range {candles[0].ts.isoformat()} → {candles[-1].ts.isoformat()}")
        return 0

    if args.cmd == "backtest":
        return _cmd_backtest(args, root)

    if args.cmd == "paper":
        return _cmd_paper(args, root)

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
                    "after all production-readiness gates pass"
                )
                return 3
            console.print(
                "[red bold]LIVE MODE REQUESTED — order submission is disabled by this build[/red bold]"
            )
            return 3

        if config.mode == Mode.PAPER:
            return _cmd_paper(
                argparse.Namespace(
                    config=str(cfg_path),
                    root=str(root),
                    cash="1000",
                    ticks=5,
                    sleep=2.0,
                    batches=2,
                    no_replay=False,
                ),
                root,
            )

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
        if config.mode == Mode.LIVE_DRY:
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


def _cmd_backtest(args: argparse.Namespace, root: Path) -> int:
    from datetime import datetime

    from market.backtest.engine import run_backtest, write_backtest_report
    from market.data.candles import fetch_coinbase_candles, load_candles_csv, save_candles_csv
    from market.strategy.slow_trend import SlowTrendConfig

    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = root / csv_path

    source = f"csv:{csv_path}"
    if args.fetch or not csv_path.exists():
        if not args.fetch and not csv_path.exists():
            console.print(f"[yellow]no cache[/yellow] {csv_path} — fetching Coinbase…")
        else:
            console.print(
                f"[bold]fetching actual BTC-USD[/bold] granularity={args.granularity}s "
                f"batches={args.batches}"
            )
        candles = fetch_coinbase_candles(
            granularity=int(args.granularity),
            limit_batches=int(args.batches),
        )
        if not candles:
            console.print("[red]fetch returned 0 candles[/red]")
            return 2
        save_candles_csv(csv_path, candles)
        source = f"coinbase:BTC-USD:{args.granularity}s"
        console.print(
            f"[green]cached[/green] {len(candles)} bars → {csv_path} "
            f"({candles[0].ts.isoformat()} → {candles[-1].ts.isoformat()})"
        )
    else:
        candles = load_candles_csv(csv_path)
        console.print(
            f"loaded {len(candles)} bars from {csv_path} "
            f"({candles[0].ts.isoformat()} → {candles[-1].ts.isoformat()})"
        )

    cfg = SlowTrendConfig(
        fast_ema=int(args.fast),
        slow_ema=int(args.slow),
        order_qty_btc=Decimal(args.qty),
    )
    result = run_backtest(
        candles,
        starting_usd=Decimal(args.cash),
        qty_btc=Decimal(args.qty),
        fee_bps=Decimal(args.fee_bps),
        strategy_cfg=cfg,
        source=source,
    )

    run_id = args.run_id or datetime.now(UTC).strftime("bt_%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    paths = write_backtest_report(result, out_dir, run_id)

    console.print("[bold]BACKTEST (actual data)[/bold]")
    console.print(f"source={result.source}")
    console.print(
        f"bars={result.bars} range={result.first_ts} → {result.last_ts} "
        f"strategy=slow_trend {result.fast_ema}/{result.slow_ema}"
    )
    console.print(
        f"fills={len(result.fills)} intents={result.intents} "
        f"allowed={result.allowed} blocked={result.blocked}"
    )
    console.print(
        f"start_usd={result.starting_usd} end_usd={result.final_usd} "
        f"pnl={result.realized_pnl_usd} return%={result.return_pct} "
        f"fees={result.fees_usd} max_dd={result.max_drawdown_usd}"
    )
    if result.fills:
        f0, f1 = result.fills[0], result.fills[-1]
        console.print(f"first_fill {f0.side.value} {f0.qty_btc}@{f0.price_usd} {f0.ts.isoformat()}")
        console.print(f"last_fill  {f1.side.value} {f1.qty_btc}@{f1.price_usd} {f1.ts.isoformat()}")
    console.print(f"[green]wrote[/green] {paths['summary']}")
    console.print(f"       {paths['fills']} ({len(result.fills)} rows)")
    console.print(f"       {paths['equity']} ({len(result.equity_curve)} points)")
    return 0


def _cmd_paper(args: argparse.Namespace, root: Path) -> int:
    from market.backtest.engine import run_backtest
    from market.data.candles import fetch_coinbase_ticker
    from market.strategy.slow_trend import SlowTrendConfig

    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    if not cfg_path.exists():
        # fall back to sim knobs
        cfg_path = root / "config" / "sim.yaml"
    config = load_config(cfg_path)

    console.print("[bold cyan]PAPER LIVE[/bold cyan] — Coinbase public BTC-USD (no Robinhood)")
    loop, meta = build_paper_live_loop(
        config,
        root=root,
        starting_usd=Decimal(args.cash),
        candle_batches=int(args.batches),
    )
    q = meta["quote"]
    console.print(
        f"live mark bid={q.bid} ask={q.ask} mid={q.mid} "
        f"ticker_price={meta['raw_ticker'].get('price')}"
    )
    console.print(
        f"candles={meta['candles']} {meta['candle_start']} → {meta['candle_end']} "
        f"last_close={meta['last_close']}"
    )

    # 1) hist replay on the same live candle set (paper P&L with real prices)
    if not args.no_replay and loop.candles:
        from market.risk.gate import RiskConfig

        hist_risk = RiskConfig(
            max_position_btc=config.risk.max_position_btc,
            max_notional_usd=config.risk.max_notional_usd,
            max_daily_loss_usd=Decimal("100000"),
            max_orders_per_hour=10000,
            min_seconds_between_orders=0,
            allow_entries=True,
        )
        bt = run_backtest(
            loop.candles,
            starting_usd=Decimal(args.cash),
            qty_btc=config.strategy.order_qty_btc,
            strategy_cfg=SlowTrendConfig(
                fast_ema=config.strategy.fast_ema,
                slow_ema=config.strategy.slow_ema,
                order_qty_btc=config.strategy.order_qty_btc,
            ),
            risk_cfg=hist_risk,
        )
        console.print(
            f"[bold]hist replay[/bold] fills={len(bt.fills)} intents={bt.intents} "
            f"start={bt.starting_usd} end={bt.final_usd} pnl={bt.realized_pnl_usd}"
        )
        if bt.fills:
            last = bt.fills[-1]
            console.print(
                f"last hist fill: {last.side.value} qty={last.qty_btc} "
                f"px={last.price_usd} fee={last.fee_usd} ts={last.ts.isoformat()}"
            )

    # 2) now-signal on live candles + live mark
    pos = loop.broker.get_btc_position()
    intent = loop.strategy.evaluate(loop.candles, pos)
    console.print(
        f"[bold]now signal[/bold] position_btc={pos.qty_btc} "
        f"intent={intent.model_dump(mode='json') if intent else None}"
    )

    # 3) short live poll loop — paper fills at live marks if signal+risk allow
    ticks = max(int(args.ticks), 0)
    if ticks:
        console.print(f"live poll ticks={ticks} sleep={args.sleep}s (paper fills only)")
        stats = loop.run(
            iterations=ticks,
            sleep_fn=lambda s: time.sleep(args.sleep),
            demo_prices=False,
            live_data=True,
            quote_fn=fetch_coinbase_ticker,
        )
        bal = loop.broker.get_balances()
        pos = loop.broker.get_btc_position()
        mark = loop.broker.get_quote().mid
        equity = bal.usd + (pos.qty_btc * mark)
        console.print(
            f"ticks={stats.ticks} intents={stats.intents} allowed={stats.allowed} "
            f"blocked={stats.blocked} paper_fills={stats.fills}"
        )
        console.print(f"balances={bal.model_dump()} position={pos.model_dump()}")
        console.print(f"mark={mark} equity≈{equity}")
        console.print(
            f"ledgers: {loop.intents_ledger.path.name}, "
            f"{loop.acks_ledger.path.name}, {loop.fills_ledger.path.name}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
