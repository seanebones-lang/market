from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from market.backtest.engine import (
    BacktestEventType,
    ExecutionModel,
    run_backtest,
    write_backtest_report,
)
from market.data.candles import load_candles_csv, save_candles_csv
from market.domain.models import Candle
from market.strategy.slow_trend import SlowTrendConfig

FUTURE_JUMP_FIXTURE = Path(__file__).parent / "fixtures" / "backtest" / "future_jump.csv"


def _synth(n=80) -> list[Candle]:
    ts0 = datetime(2024, 1, 1, tzinfo=UTC)
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
    assert res.intents == 1
    assert res.allowed == 1
    assert res.blocked == 0
    assert len(res.fills) == 1
    assert res.final_usd > res.starting_usd
    assert res.bars == 100
    assert res.equity_curve
    assert res.max_equity_usd == res.final_usd


def test_write_backtest_report(tmp_path: Path):
    candles = _synth(60)
    res = run_backtest(
        candles,
        strategy_cfg=SlowTrendConfig(fast_ema=3, slow_ema=5, order_qty_btc=Decimal("0.001")),
        source="test",
    )
    paths = write_backtest_report(res, tmp_path, "run1")
    assert paths["summary"].exists()
    assert paths["events"].exists()
    assert paths["fills"].exists()
    assert paths["equity"].exists()
    text = paths["summary"].read_text()
    assert "pnl_usd" in text
    assert "schema_version" in text
    assert '"execution_model": "next_bar_open"' in text
    event_lines = paths["events"].read_text().splitlines()
    assert len(event_lines) == len(res.events)


def test_future_jump_cannot_fill_at_signal_close():
    candles = load_candles_csv(FUTURE_JUMP_FIXTURE)
    result = run_backtest(
        candles,
        starting_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        source="fixture:future-jump",
    )

    assert result.execution_model == ExecutionModel.NEXT_BAR_OPEN
    assert result.intents == 1
    assert result.allowed == 1
    assert len(result.fills) == 1
    fill = result.fills[0]
    signal_bar = candles[4]
    fill_bar = candles[5]
    assert fill.price_usd == Decimal("20")
    assert fill.price_usd == fill_bar.open
    assert fill.price_usd != signal_bar.close
    assert fill.ts == fill_bar.ts
    assert fill.raw["signal_bar_ts"] == signal_bar.ts.isoformat()
    assert fill.raw["signal_bar_close"] == "12"
    assert fill.raw["execution_model"] == "next_bar_open"

    related = [event for event in result.events if event.client_order_id == fill.client_order_id]
    assert [event.event_type for event in related] == [
        BacktestEventType.DECISION_ACCEPTED,
        BacktestEventType.ORDER_ELIGIBLE,
        BacktestEventType.FILL,
    ]
    signal_close = next(
        event
        for event in result.events
        if event.event_type == BacktestEventType.BAR_CLOSE
        and event.bar_ts == signal_bar.ts.isoformat()
    )
    next_open = next(
        event
        for event in result.events
        if event.event_type == BacktestEventType.BAR_OPEN
        and event.bar_ts == fill_bar.ts.isoformat()
    )
    assert signal_close.sequence < related[0].sequence < next_open.sequence
    assert next_open.sequence < related[1].sequence < related[2].sequence


def test_signal_on_final_bar_expires_without_fill():
    candles = load_candles_csv(FUTURE_JUMP_FIXTURE)[:-1]
    result = run_backtest(
        candles,
        starting_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        source="fixture:future-jump",
    )
    assert result.intents == 1
    assert result.allowed == 1
    assert result.fills == []
    assert result.end_of_data_orders == 1
    assert result.events[-1].event_type == BacktestEventType.ORDER_EXPIRED
    assert result.events[-1].details["reason"] == "end_of_data_before_eligible_bar"
