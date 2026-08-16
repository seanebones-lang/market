from decimal import Decimal
from pathlib import Path

import httpx

from market.app.loop import TradingLoop, seed_trending_candles
from market.config import load_config
from market.domain.models import Mode, utcnow
from market.execution.sim import SimBroker
from market.ledger.jsonl import JsonlLedger
from market.ops.freeze import FreezeControl
from market.ops.heartbeat import Heartbeat
from market.risk.gate import RiskConfig, RiskGate
from market.strategy.slow_trend import SlowTrendConfig, SlowTrendV1


def test_paper_mode_sim_fills_at_mark(tmp_path: Path):
    cfg = load_config(Path("config/sim.yaml")).model_copy(update={"mode": Mode.PAPER})
    risk = RiskConfig(
        min_seconds_between_orders=0, max_orders_per_hour=100, max_notional_usd=Decimal("5000")
    )
    broker = SimBroker(usd=Decimal("5000"), bid=Decimal("100000"), ask=Decimal("100010"))
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
    r = loop.tick(now=utcnow())
    assert r["allow"] is True
    assert r["submitted"] is True
    assert loop.stats.fills == 1
    assert broker.get_btc_position().qty_btc == Decimal("0.001")


def test_fetch_ticker_shape():
    from market.data.candles import fetch_coinbase_ticker

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/products/BTC-USD/ticker"
        return httpx.Response(
            200,
            json={"price": "60000", "bid": "59999.50", "ask": "60000.50"},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        q = fetch_coinbase_ticker(client)
    assert q.bid > 0
    assert q.ask >= q.bid
    assert q.mid == Decimal("60000.00")
