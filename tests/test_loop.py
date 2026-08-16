from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from market.app.loop import TradingLoop, build_sim_loop, seed_trending_candles
from market.config import load_config
from market.domain.models import Mode
from market.execution.sim import SimBroker
from market.ledger.jsonl import JsonlLedger
from market.ops.freeze import FreezeControl
from market.ops.heartbeat import Heartbeat
from market.risk.gate import RiskConfig, RiskGate, RiskState
from market.strategy.slow_trend import SlowTrendConfig, SlowTrendV1


def test_load_sim_config():
    cfg = load_config(Path("config/sim.yaml"))
    assert cfg.mode == Mode.SIM
    assert cfg.risk.max_position_btc == Decimal("0.002")


def test_sim_loop_runs(tmp_path: Path):
    cfg = load_config(Path("config/sim.yaml"))
    cfg = cfg.model_copy(update={"data_dir": tmp_path, "loop_seconds": 0})
    # loosen spacing for test
    risk = RiskConfig(
        max_position_btc=Decimal("0.01"),
        max_notional_usd=Decimal("5000"),
        max_daily_loss_usd=Decimal("500"),
        max_orders_per_hour=100,
        min_seconds_between_orders=0,
        allow_entries=True,
    )
    loop = TradingLoop(
        config=cfg,
        broker=SimBroker(usd=Decimal("5000"), bid=Decimal("100000"), ask=Decimal("100010")),
        strategy=SlowTrendV1(
            SlowTrendConfig(fast_ema=3, slow_ema=5, order_qty_btc=Decimal("0.001"))
        ),
        risk=RiskGate(risk),
        intents_ledger=JsonlLedger(tmp_path / "intents.jsonl"),
        acks_ledger=JsonlLedger(tmp_path / "acks.jsonl"),
        fills_ledger=JsonlLedger(tmp_path / "fills.jsonl"),
        freeze=FreezeControl(tmp_path / "FREEZE"),
        heartbeat=Heartbeat(tmp_path / "hb.json"),
        candles=seed_trending_candles(40, start=Decimal("100000"), step=Decimal("100")),
        risk_state=RiskState(),
    )
    # drive prices to create activity
    base = datetime(2026, 2, 1, tzinfo=UTC)
    for i in range(30):
        # continue up then down via quote
        if i < 15:
            px = Decimal("100000") + Decimal(i) * Decimal("200")
        else:
            px = Decimal("103000") - Decimal(i - 15) * Decimal("300")
        loop.broker.set_quote(px - Decimal("5"), px + Decimal("5"))
        loop.tick(now=base + timedelta(hours=i))
    assert loop.stats.ticks == 30
    assert (tmp_path / "hb.json").exists()
    assert loop.stats.intents == 2
    assert loop.stats.submits == 2
    assert loop.stats.fills == 2
    assert len(loop.intents_ledger.read_all()) == 2
    assert len(loop.acks_ledger.read_all()) == 2
    assert len(loop.fills_ledger.read_all()) == 2


def test_freeze_blocks_entries(tmp_path: Path):
    cfg = load_config(Path("config/sim.yaml"))
    risk = RiskConfig(min_seconds_between_orders=0, max_orders_per_hour=100)
    loop = TradingLoop(
        config=cfg,
        broker=SimBroker(usd=Decimal("5000")),
        strategy=SlowTrendV1(SlowTrendConfig(fast_ema=3, slow_ema=5)),
        risk=RiskGate(risk),
        intents_ledger=JsonlLedger(tmp_path / "i.jsonl"),
        acks_ledger=JsonlLedger(tmp_path / "a.jsonl"),
        fills_ledger=JsonlLedger(tmp_path / "f.jsonl"),
        freeze=FreezeControl(tmp_path / "FREEZE"),
        heartbeat=Heartbeat(tmp_path / "hb.json"),
        candles=seed_trending_candles(40),
    )
    loop.freeze.freeze("test")
    # force a buy intent by monkeypatch evaluate
    from market.domain.models import Intent, Side

    loop.strategy.evaluate = lambda candles, pos: Intent(  # type: ignore[method-assign]
        side=Side.BUY, qty_btc="0.001", reason="forced"
    )
    r = loop.tick(now=datetime(2026, 3, 1, tzinfo=UTC))
    assert r["allow"] is False
    assert "freeze_entries" in r["violations"]
    assert loop.stats.submits == 0


def test_live_dry_never_submits(tmp_path: Path):
    cfg = load_config(Path("config/sim.yaml"))
    cfg = cfg.model_copy(update={"mode": Mode.LIVE_DRY})
    risk = RiskConfig(min_seconds_between_orders=0, max_orders_per_hour=100)
    submits = {"n": 0}
    broker = SimBroker(usd=Decimal("5000"))
    real_place = broker.place_order

    def wrapped(intent):
        submits["n"] += 1
        return real_place(intent)

    broker.place_order = wrapped  # type: ignore[method-assign]
    loop = TradingLoop(
        config=cfg,
        broker=broker,
        strategy=SlowTrendV1(SlowTrendConfig(fast_ema=3, slow_ema=5)),
        risk=RiskGate(risk),
        intents_ledger=JsonlLedger(tmp_path / "i.jsonl"),
        acks_ledger=JsonlLedger(tmp_path / "a.jsonl"),
        fills_ledger=JsonlLedger(tmp_path / "f.jsonl"),
        freeze=FreezeControl(tmp_path / "FREEZE"),
        heartbeat=Heartbeat(tmp_path / "hb.json"),
        candles=seed_trending_candles(40),
    )
    from market.domain.models import Intent, Side

    loop.strategy.evaluate = lambda c, p: Intent(side=Side.BUY, qty_btc="0.001", reason="forced")  # type: ignore[method-assign]
    r = loop.tick(now=datetime(2026, 3, 1, tzinfo=UTC))
    assert r["allow"] is True
    assert r["submitted"] is False
    assert submits["n"] == 0
    shadows = [x for x in loop.acks_ledger.read_all() if x.get("type") == "shadow_ack"]
    assert len(shadows) == 1


def test_build_sim_loop(tmp_path: Path):
    cfg = load_config(Path("config/sim.yaml"))
    cfg = cfg.model_copy(update={"data_dir": tmp_path / "data"})
    loop = build_sim_loop(cfg, root=tmp_path)
    stats = loop.run(iterations=3, sleep_fn=lambda s: None)
    assert stats.ticks == 3
