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


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


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

    dataset_p = sub.add_parser(
        "build-dataset",
        help="Build an immutable, quality-gated Coinbase BTC-USD research dataset",
    )
    dataset_p.add_argument("--start", required=True, help="UTC inclusive start (date or ISO hour)")
    dataset_p.add_argument("--end", required=True, help="UTC exclusive end (date or ISO hour)")
    dataset_p.add_argument("--out-dir", default="data/research")
    dataset_p.add_argument("--root", default=".")
    dataset_p.add_argument(
        "--gap-policy",
        choices=["reject", "segment"],
        default="reject",
        help="Reject missing bars or preserve declared gaps as separately warmed segments",
    )

    verify_dataset_p = sub.add_parser(
        "verify-dataset",
        help="Verify research artifact checksums and return strategy-safe segments",
    )
    verify_dataset_p.add_argument("--manifest", required=True)
    verify_dataset_p.add_argument("--root", default=".")

    verify_splits_p = sub.add_parser(
        "verify-research-splits",
        help="Verify a frozen research split plan against its checksummed dataset",
    )
    verify_splits_p.add_argument("--plan", required=True)
    verify_splits_p.add_argument("--root", default=".")

    verify_backtest_p = sub.add_parser(
        "verify-backtest",
        help="Verify a checksummed, self-contained backtest report",
    )
    verify_backtest_p.add_argument("--manifest", required=True)
    verify_backtest_p.add_argument("--root", default=".")

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
    bt_p.add_argument(
        "--venue-cost-profile",
        choices=[
            "legacy_unclassified",
            "robinhood_crypto_api_v1_market_maker",
            "robinhood_crypto_api_v2_exchange_taker",
        ],
        default="legacy_unclassified",
        help="Declared venue/API/routing cost contract",
    )
    bt_p.add_argument(
        "--transaction-fee-bps-per-fill-assumption",
        default=None,
        help=(
            "Fee bps charged once on every execution fill's notional; defaults to 5 only for "
            "legacy_unclassified, omit for v1, required for v2"
        ),
    )
    bt_p.add_argument(
        "--execution-model",
        choices=["next_bar_open", "next_bar_open_bid_ask"],
        default="next_bar_open",
        help="Declared next-bar execution model",
    )
    bt_p.add_argument(
        "--quoted-spread-bps-assumption",
        default="0",
        help=(
            "Assumed full bid-ask spread in bps, centered on each next open and the final close "
            "used for terminal liquidation"
        ),
    )
    bt_p.add_argument(
        "--adverse-slippage-bps-assumption",
        default="0",
        help="Assumed adverse slippage in bps from the side-specific synthetic touch",
    )
    bt_p.add_argument(
        "--benchmark-dca-interval-bars",
        type=_positive_int,
        default=168,
        help="Positive bar interval between periodic-DCA benchmark entries (default: 168)",
    )
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
    bt_p.add_argument(
        "--random-seed",
        type=int,
        default=0,
        help="Recorded nonnegative deterministic seed (current engine uses no randomness)",
    )

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

    if args.cmd == "build-dataset":
        from datetime import datetime

        from market.data.candles import fetch_coinbase_candle_range
        from market.data.dataset import write_research_dataset

        def parse_boundary(value: str) -> datetime:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed

        start = parse_boundary(args.start)
        end = parse_boundary(args.end)
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = root / out_dir
        console.print(f"fetching closed BTC-USD 1h range {start.isoformat()} → {end.isoformat()}")
        allow_declared_gaps = args.gap_policy == "segment"
        result = fetch_coinbase_candle_range(
            start,
            end,
            allow_declared_gaps=allow_declared_gaps,
        )
        artifacts = write_research_dataset(
            out_dir,
            result,
            allow_declared_gaps=allow_declared_gaps,
        )
        manifest = artifacts.manifest
        regime_labels = sorted(
            {f"{window.trend}/{window.volatility}" for window in manifest.regimes}
        )
        console.print(
            f"[green]quality {manifest.quality_status.upper()}[/green] bars={manifest.bars} "
            f"missing={manifest.missing_bars} segments={manifest.contiguous_segments} "
            f"sha256={manifest.normalized_sha256}"
        )
        console.print(f"regimes={', '.join(regime_labels)}")
        console.print(f"manifest={artifacts.manifest_path}")
        return 0

    if args.cmd == "verify-dataset":
        from market.data.dataset import load_research_segments

        manifest_path = Path(args.manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        segments, manifest = load_research_segments(manifest_path)
        console.print(
            f"[green]verified[/green] dataset={manifest.dataset_id} "
            f"status={manifest.quality_status} bars={sum(len(segment) for segment in segments)}"
        )
        console.print(
            f"strategy_segments={len(segments)} lengths={','.join(str(len(s)) for s in segments)}"
        )
        console.print(f"sha256={manifest.normalized_sha256}")
        return 0

    if args.cmd == "verify-research-splits":
        from market.research.splits import verify_research_split_plan

        verification = verify_research_split_plan(args.plan, repository_root=root)
        plan = verification.plan
        console.print(
            f"[green]verified[/green] plan={plan.plan_id} dataset={plan.dataset_id} "
            f"folds={verification.fold_count} stitched_test_bars={verification.stitched_test_bars}"
        )
        console.print(
            f"development={plan.development.start.isoformat()}→{plan.development.end.isoformat()} "
            f"holdout={plan.final_holdout.start.isoformat()}→{plan.final_holdout.end.isoformat()} "
            f"holdout_access={plan.holdout_access}"
        )
        console.print(f"plan_sha256={verification.plan_sha256}")
        return 0

    if args.cmd == "verify-backtest":
        from market.backtest.reproducibility import verify_backtest_report

        manifest_path = Path(args.manifest)
        if not manifest_path.is_absolute():
            manifest_path = root / manifest_path
        backtest_manifest = verify_backtest_report(manifest_path)
        console.print(
            f"[green]verified[/green] run_id={backtest_manifest.run_id} "
            f"engine={backtest_manifest.provenance.engine_version} "
            f"artifacts={len(backtest_manifest.artifacts)} "
            f"input_sha256={backtest_manifest.provenance.input_data_sha256}"
        )
        console.print(
            f"code_revision={backtest_manifest.source_revision.commit_sha} "
            f"code_status={backtest_manifest.source_revision.status} "
            "code_identity_reproducible="
            f"{backtest_manifest.code_identity_reproducible}"
        )
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

    from market.backtest.costs import VenueCostProfile
    from market.backtest.engine import (
        BACKTEST_ENGINE_VERSION,
        ExecutionModel,
        run_backtest,
        write_backtest_report,
    )
    from market.backtest.reproducibility import verify_backtest_report
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
        starting_cash_usd=Decimal(args.cash),
        qty_btc=Decimal(args.qty),
        strategy_cfg=cfg,
        source=source,
        execution_model=ExecutionModel(args.execution_model),
        quoted_spread_bps_assumption=Decimal(args.quoted_spread_bps_assumption),
        adverse_slippage_bps_assumption=Decimal(args.adverse_slippage_bps_assumption),
        venue_cost_profile=VenueCostProfile(args.venue_cost_profile),
        transaction_fee_bps_per_fill_assumption=(
            Decimal(args.transaction_fee_bps_per_fill_assumption)
            if args.transaction_fee_bps_per_fill_assumption is not None
            else None
        ),
        benchmark_dca_interval_bars=int(args.benchmark_dca_interval_bars),
        random_seed=int(args.random_seed),
    )

    run_id = args.run_id or datetime.now(UTC).strftime("bt_%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    paths = write_backtest_report(result, out_dir, run_id, repository_root=root)
    manifest = verify_backtest_report(paths["manifest"])

    console.print("[bold]BACKTEST (actual data)[/bold]")
    console.print(f"source={result.source}")
    console.print(
        f"execution_model={result.execution_model.value} "
        f"quoted_spread_bps_assumption={result.quoted_spread_bps_assumption} "
        f"adverse_slippage_bps_assumption={result.adverse_slippage_bps_assumption}"
    )
    console.print(
        f"venue_cost_profile={result.venue_cost_profile.value} "
        "fee_calculation_basis=executed_notional_per_fill "
        "transaction_fee_bps_per_fill_assumption="
        f"{result.transaction_fee_bps_per_fill_assumption} "
        "transaction_fee_bps_per_fill_applied="
        f"{result.transaction_fee_bps_per_fill_applied}"
    )
    console.print(
        f"bars={result.bars} range={result.first_ts} → {result.last_ts} "
        f"strategy=slow_trend {result.fast_ema}/{result.slow_ema}"
    )
    console.print(
        f"engine_version={BACKTEST_ENGINE_VERSION} "
        f"input_data_sha256={result.provenance.input_data_sha256} "
        f"resolved_config_sha256={result.provenance.resolved_config_sha256} "
        f"random_seed={result.provenance.random_seed} randomness_used=false"
    )
    console.print(
        f"code_revision={manifest.source_revision.commit_sha} "
        f"code_status={manifest.source_revision.status} "
        f"code_identity_reproducible={manifest.code_identity_reproducible}"
    )
    console.print(
        f"fills={len(result.fills)} intents={result.intents} "
        f"allowed={result.allowed} blocked={result.blocked}"
    )
    console.print(
        f"orders={result.lifecycle.order_count} "
        f"executions={result.lifecycle.execution_count} "
        f"partial_fill_executions={result.lifecycle.partial_fill_execution_count} "
        f"round_trips={result.lifecycle.round_trip_count} "
        f"closed_trades={result.lifecycle.closed_trade_count} "
        f"wins={result.lifecycle.winning_closed_trade_count} "
        f"losses={result.lifecycle.losing_closed_trade_count} "
        f"breakeven={result.lifecycle.breakeven_closed_trade_count} "
        f"open_inventory_btc={result.lifecycle.open_inventory_btc}"
    )
    console.print(
        f"terminal_liquidation_model={result.terminal_liquidation_model.value} "
        f"terminal_liquidation_fills={result.terminal_liquidation_fills} "
        f"terminal_liquidation_qty_btc={result.terminal_liquidation_qty_btc} "
        f"terminal_liquidation_fee_usd={result.terminal_liquidation_fee_usd} "
        f"final_inventory_btc={result.final_inventory_btc}"
    )
    console.print(
        f"starting_cash_usd={result.starting_cash_usd} "
        f"final_cash_usd={result.final_cash_usd} "
        f"realized_gross_pnl_usd={result.realized_gross_pnl_usd} "
        f"unrealized_gross_pnl_usd={result.unrealized_gross_pnl_usd} "
        f"cumulative_fees_usd={result.cumulative_fees_usd}"
    )
    console.print(
        f"marked_equity_usd={result.marked_equity_usd} "
        f"net_liquidation_value_usd={result.net_liquidation_value_usd} "
        f"net_liquidation_pnl_after_fees_usd="
        f"{result.net_liquidation_pnl_after_fees_usd} "
        f"net_liquidation_return%={result.net_liquidation_return_pct} "
        f"max_net_liquidation_drawdown_usd="
        f"{result.max_net_liquidation_drawdown_usd}"
    )
    console.print(
        "benchmark_matched_notional_method="
        f"{result.benchmarks.matched_notional_method} "
        "benchmark_matched_notional_applied_usd="
        f"{result.benchmarks.matched_notional_applied_usd} "
        f"benchmark_dca_interval_bars={result.benchmarks.dca_interval_bars}"
    )
    for benchmark in result.benchmarks.results:
        risk_adjusted = (
            str(benchmark.net_pnl_over_max_drawdown_ratio)
            if benchmark.net_pnl_over_max_drawdown_ratio is not None
            else "undefined_zero_drawdown"
        )
        console.print(
            f"benchmark={benchmark.kind.value} "
            f"net_pnl_after_fees_usd={benchmark.net_pnl_after_fees_usd} "
            f"net_return_pct={benchmark.net_return_pct} "
            "max_net_liquidation_drawdown_usd="
            f"{benchmark.max_net_liquidation_drawdown_usd} "
            f"net_pnl_over_max_drawdown_ratio={risk_adjusted}"
        )
    for comparison in result.benchmarks.comparisons:
        risk_adjusted_difference = (
            str(comparison.strategy_minus_benchmark_risk_adjusted_ratio)
            if comparison.strategy_minus_benchmark_risk_adjusted_ratio is not None
            else "undefined"
        )
        console.print(
            f"strategy_vs={comparison.benchmark.value} "
            "absolute_pnl_difference_usd="
            f"{comparison.strategy_minus_benchmark_net_pnl_usd} "
            "risk_adjusted_ratio_difference="
            f"{risk_adjusted_difference} "
            f"risk_adjusted_status={comparison.risk_adjusted_comparison_status.value}"
        )
    console.print(
        "performance_sampling=hourly_bar_close "
        f"periods_per_year={result.performance.periods_per_year} "
        "risk_free_rate_annual_pct_assumption="
        f"{result.performance.risk_free_rate_annual_pct_assumption}"
    )
    for statistics in result.performance.portfolios:
        console.print(
            f"performance={statistics.portfolio} "
            f"turnover_ratio={statistics.turnover_ratio} "
            f"exposure_time_pct={statistics.exposure_time_pct} "
            "max_drawdown_duration_bars="
            f"{statistics.max_drawdown_duration_bars} "
            f"annualized_volatility={statistics.annualized_volatility} "
            f"annualized_sharpe={statistics.annualized_sharpe_ratio} "
            f"sharpe_status={statistics.sharpe_status.value} "
            f"annualized_sortino={statistics.annualized_sortino_ratio} "
            f"sortino_status={statistics.sortino_status.value} "
            f"profit_factor={statistics.profit_factor} "
            f"profit_factor_status={statistics.profit_factor_status.value} "
            "expectancy_per_closed_trade_usd="
            f"{statistics.expectancy_per_closed_trade_usd} "
            "explicit_fee_drag_return_percentage_points="
            f"{statistics.explicit_fee_drag_return_percentage_points}"
        )
    for alpha in result.performance.benchmark_alphas:
        console.print(
            f"alpha_vs={alpha.benchmark.value} "
            f"annualized_alpha={alpha.annualized_alpha} "
            f"beta={alpha.beta} "
            "annualized_active_return_difference="
            f"{alpha.annualized_active_return_difference} "
            f"alpha_status={alpha.status.value}"
        )
    if result.fills:
        f0, f1 = result.fills[0], result.fills[-1]
        console.print(f"first_fill {f0.side.value} {f0.qty_btc}@{f0.price_usd} {f0.ts.isoformat()}")
        console.print(f"last_fill  {f1.side.value} {f1.qty_btc}@{f1.price_usd} {f1.ts.isoformat()}")
    console.print(f"[green]wrote[/green] {paths['summary']}")
    console.print(f"       {paths['input_data']} ({len(result.input_candles)} rows)")
    console.print(f"       {paths['events']} ({len(result.events)} rows)")
    console.print(f"       {paths['fills']} ({len(result.fills)} rows)")
    console.print(f"       {paths['accounting']} ({len(result.accounting_journal)} rows)")
    lifecycle_rows = (
        1
        + len(result.lifecycle.orders)
        + len(result.lifecycle.closed_trades)
        + len(result.lifecycle.round_trips)
    )
    console.print(f"       {paths['lifecycle']} ({lifecycle_rows} rows)")
    benchmark_rows = 1 + len(result.benchmarks.results) + len(result.benchmarks.comparisons)
    benchmark_fill_rows = sum(len(benchmark.fills) for benchmark in result.benchmarks.results)
    benchmark_equity_rows = sum(
        len(benchmark.equity_curve) for benchmark in result.benchmarks.results
    )
    console.print(f"       {paths['benchmarks']} ({benchmark_rows} rows)")
    console.print(f"       {paths['benchmark_fills']} ({benchmark_fill_rows} rows)")
    console.print(f"       {paths['benchmark_equity']} ({benchmark_equity_rows} rows)")
    performance_rows = (
        1 + len(result.performance.portfolios) + len(result.performance.benchmark_alphas)
    )
    console.print(f"       {paths['performance']} ({performance_rows} rows)")
    console.print(
        f"       {paths['performance_observations']} "
        f"({len(result.performance.strategy_observations)} rows)"
    )
    console.print(f"       {paths['equity']} ({len(result.equity_curve)} points)")
    console.print(f"       {paths['manifest']} ({len(manifest.artifacts)} artifacts verified)")
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
            starting_cash_usd=Decimal(args.cash),
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
            f"start={bt.starting_cash_usd} end={bt.net_liquidation_value_usd} "
            f"net_liquidation_pnl={bt.net_liquidation_pnl_after_fees_usd}"
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
