"""CLI entrypoint: python -m market / market run"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from market.app.loop import build_sim_loop
from market.config import load_config

console = Console()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="market", description="BTC spot autotrader")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run trading loop")
    run_p.add_argument("--config", default="config/sim.yaml")
    run_p.add_argument("--iterations", type=int, default=None)
    run_p.add_argument("--root", default=".")

    sub.add_parser("version", help="Print version")

    freeze_p = sub.add_parser("freeze", help="Write FREEZE file (block entries)")
    freeze_p.add_argument("--root", default=".")
    freeze_p.add_argument("--config", default="config/sim.yaml")
    freeze_p.add_argument("--reason", default="manual")

    unfreeze_p = sub.add_parser("unfreeze", help="Remove FREEZE file")
    unfreeze_p.add_argument("--root", default=".")
    unfreeze_p.add_argument("--config", default="config/sim.yaml")

    args = parser.parse_args(argv)

    if args.cmd == "version":
        from market import __version__

        console.print(__version__)
        return 0

    root = Path(args.root).resolve()
    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = root / cfg_path
    config = load_config(cfg_path)

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
