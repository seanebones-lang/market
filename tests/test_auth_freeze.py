from pathlib import Path

import pytest

from market.app.loop import TradingLoop
from market.config import load_config
from market.execution.robinhood import RobinhoodAuthError
from market.execution.sim import SimBroker
from market.ledger.jsonl import JsonlLedger
from market.ops.freeze import FreezeControl
from market.ops.heartbeat import Heartbeat
from market.risk.gate import RiskConfig, RiskGate
from market.strategy.slow_trend import SlowTrendV1


def test_auth_error_freezes_entries(tmp_path: Path):
    cfg = load_config(Path("config/sim.yaml"))
    freeze = FreezeControl(tmp_path / "FREEZE")
    loop = TradingLoop(
        config=cfg,
        broker=SimBroker(),
        strategy=SlowTrendV1(),
        risk=RiskGate(RiskConfig()),
        intents_ledger=JsonlLedger(tmp_path / "i.jsonl"),
        acks_ledger=JsonlLedger(tmp_path / "a.jsonl"),
        fills_ledger=JsonlLedger(tmp_path / "f.jsonl"),
        freeze=freeze,
        heartbeat=Heartbeat(tmp_path / "hb.json"),
    )
    loop.on_auth_error(RobinhoodAuthError("boom"))
    assert freeze.frozen
    assert loop.risk_state.freeze_entries
    assert loop.stats.auth_freezes == 1
    assert any(r.get("type") == "auth_freeze" for r in loop.acks_ledger.read_all())


@pytest.mark.parametrize("live_env", [None, "1"])
def test_cli_refuses_live_for_every_runtime_flag(tmp_path: Path, monkeypatch, live_env):
    from market.app.cli import main

    if live_env is None:
        monkeypatch.delenv("MARKET_RH_LIVE", raising=False)
    else:
        monkeypatch.setenv("MARKET_RH_LIVE", live_env)
    # write a live config
    cfg = tmp_path / "live.yaml"
    cfg.write_text(
        """
mode: live
symbol: BTC
loop_seconds: 0
broker:
  name: robinhood
risk:
  max_position_btc: "0.001"
  max_notional_usd: "100"
  max_daily_loss_usd: "10"
  max_orders_per_hour: 1
  min_seconds_between_orders: 300
  allow_entries: false
strategy:
  fast_ema: 3
  slow_ema: 8
  order_qty_btc: "0.001"
data_dir: data
""",
        encoding="utf-8",
    )
    rc = main(["run", "--config", str(cfg), "--root", str(tmp_path), "--iterations", "1"])
    assert rc == 3


def test_live_dry_cli_zero_submits(tmp_path: Path):
    from market.app.cli import main

    cfg = tmp_path / "dry.yaml"
    cfg.write_text(
        f"""
mode: live-dry
symbol: BTC
loop_seconds: 0
iterations: 20
broker:
  name: sim
risk:
  max_position_btc: "0.002"
  max_notional_usd: "250"
  max_daily_loss_usd: "25"
  max_orders_per_hour: 10
  min_seconds_between_orders: 0
  allow_entries: true
strategy:
  fast_ema: 3
  slow_ema: 8
  order_qty_btc: "0.001"
data_dir: {tmp_path / "data"}
""",
        encoding="utf-8",
    )
    rc = main(["run", "--config", str(cfg), "--root", str(tmp_path), "--iterations", "20"])
    assert rc == 0
    # no fills file content from real submits
    fills = tmp_path / "data" / "ledger" / "fills.jsonl"
    assert fills.exists()
    assert fills.read_text().strip() == ""
    acks = (tmp_path / "data" / "ledger" / "acks.jsonl").read_text()
    # shadow only if any allowed
    assert "ack" not in acks.replace("shadow_ack", "")
