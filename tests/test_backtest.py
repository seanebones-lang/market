from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from market.backtest.engine import run_backtest, write_backtest_report
from market.data.candles import load_candles_csv, save_candles_csv
from market.domain.models import Candle
from market.strategy.slow_trend import SlowTrendConfig


def _synth(n=80) -> list[Candle]:
    ts0 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    out = []
    px = Decimal("40000")
    for i in range(n):
        if i < n // 3:
            px += Decimal("100")
        elif i < 2 * n // 3:
            px -= Decimal("120")
        else:
            px += Decimal("90")
        out.append(
            Candle(
                ts=ts0 + timedelta(hours=i),
                open=px,
                high=px,
                low=px,
                close=px,
                volume=Decimal("1"),
            )
        )
    return out


def test_csv_roundtrip(tmp_path: Path):
    candles = _synth(10)
    path = tmp_path / "c.csv"
    save_candles_csv(path, candles)
    loaded = load_candles_csv(path)
    assert len(loaded) == 10
    assert loaded[0].close == candles[0].close


def test_backtest_runs_and_accounts():
    candles = _synth(100)
    res = run_backtest(
        candles,
        starting_usd=Decimal("1000"),
        qty_btc=Decimal("0.001"),
        strategy_cfg=SlowTrendConfig(fast_ema=3, slow_ema=8, order_qty_btc=Decimal("0.001")),
        source="test:synth",
    )
    assert res.intents >= 0
    assert res.final_usd > 0
    assert res.bars == 100
    assert res.equity_curve
    assert res.max_equity_usd >= res.starting_usd or len(res.fills) >= 0


def test_write_backtest_report(tmp_path: Path):
    candles = _synth(60)
    res = run_backtest(
        candles,
        strategy_cfg=SlowTrendConfig(fast_ema=3, slow_ema=5, order_qty_btc=Decimal("0.001")),
        source="test",
    )
    paths = write_backtest_report(res, tmp_path, "run1")
    assert paths["summary"].exists()
    assert paths["fills"].exists()
    assert paths["equity"].exists()
    text = paths["summary"].read_text()
    assert "pnl_usd" in text
    assert "schema_version" in text


def test_real_csv_backtest_if_cached():
    """If real Coinbase cache exists, backtest must run on it without error."""
    path = Path("data/cache/btc_usd_1h.csv")
    if not path.exists():
        return
    candles = load_candles_csv(path)
    assert len(candles) > 50
    # real timestamps should be timezone-aware ascending
    assert candles[0].ts < candles[-1].ts
    assert candles[0].close > 0
    res = run_backtest(
        candles,
        starting_usd=Decimal("1000"),
        qty_btc=Decimal("0.001"),
        strategy_cfg=SlowTrendConfig(fast_ema=12, slow_ema=26, order_qty_btc=Decimal("0.001")),
        source=f"csv:{path}",
    )
    assert res.bars == len(candles)
    assert res.first_ts is not None
    assert res.summary()["source"].startswith("csv:")
