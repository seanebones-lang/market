from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from market.backtest.engine import (
    BacktestEventType,
    ExecutionAssumptions,
    ExecutionModel,
    calculate_execution_price,
    run_backtest,
    write_backtest_report,
)
from market.data.candles import load_candles_csv, save_candles_csv
from market.domain.models import Candle, Side
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
    assert '"quoted_spread_bps_assumption": "0"' in text
    assert '"adverse_slippage_bps_assumption": "0"' in text
    event_lines = paths["events"].read_text().splitlines()
    assert len(event_lines) == len(res.events)


def test_next_open_execution_price_has_no_synthetic_cost():
    price = calculate_execution_price(
        ExecutionAssumptions(),
        Side.BUY,
        Decimal("100"),
    )

    assert price.reference_open_usd == Decimal("100")
    assert price.synthetic_bid_usd == Decimal("100")
    assert price.synthetic_ask_usd == Decimal("100")
    assert price.pre_slippage_touch_usd == Decimal("100")
    assert price.fill_price_usd == Decimal("100")


def test_bid_ask_execution_is_adverse_for_buys_and_sells():
    assumptions = ExecutionAssumptions(
        model=ExecutionModel.NEXT_BAR_OPEN_BID_ASK,
        quoted_spread_bps_assumption=Decimal("20"),
        adverse_slippage_bps_assumption=Decimal("10"),
    )

    buy = calculate_execution_price(assumptions, Side.BUY, Decimal("100"))
    sell = calculate_execution_price(assumptions, Side.SELL, Decimal("100"))

    assert buy.synthetic_bid_usd == Decimal("99.900")
    assert buy.synthetic_ask_usd == Decimal("100.100")
    assert buy.pre_slippage_touch_usd == Decimal("100.100")
    assert buy.fill_price_usd == Decimal("100.200100")
    assert sell.pre_slippage_touch_usd == Decimal("99.900")
    assert sell.fill_price_usd == Decimal("99.800100")
    assert buy.fill_price_usd > buy.synthetic_ask_usd > buy.reference_open_usd
    assert sell.reference_open_usd > sell.synthetic_bid_usd > sell.fill_price_usd


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"quoted_spread_bps_assumption": Decimal("-1")}, "must be >= 0"),
        ({"quoted_spread_bps_assumption": Decimal("20000")}, "must be < 20000"),
        ({"adverse_slippage_bps_assumption": Decimal("-1")}, "must be >= 0"),
        ({"adverse_slippage_bps_assumption": Decimal("10000")}, "must be < 10000"),
        ({"quoted_spread_bps_assumption": Decimal("NaN")}, "finite number"),
        ({"adverse_slippage_bps_assumption": Decimal("Infinity")}, "finite number"),
        (
            {
                "model": ExecutionModel.NEXT_BAR_OPEN,
                "quoted_spread_bps_assumption": Decimal("1"),
            },
            "requires zero spread and slippage assumptions",
        ),
    ],
)
def test_execution_assumptions_reject_invalid_values(changes: dict[str, object], message: str):
    values: dict[str, object] = {"model": ExecutionModel.NEXT_BAR_OPEN_BID_ASK}
    values.update(changes)
    with pytest.raises(ValidationError, match=message):
        ExecutionAssumptions.model_validate(values)


def test_execution_assumptions_reject_float():
    with pytest.raises((TypeError, ValidationError), match="float not allowed"):
        ExecutionAssumptions(
            model=ExecutionModel.NEXT_BAR_OPEN_BID_ASK,
            quoted_spread_bps_assumption=1.0,  # type: ignore[arg-type]
        )


def test_execution_price_rejects_nonpositive_reference():
    with pytest.raises(ValueError, match="must be > 0"):
        calculate_execution_price(ExecutionAssumptions(), Side.BUY, Decimal("0"))


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


def test_future_jump_applies_declared_spread_and_slippage_after_next_open():
    candles = load_candles_csv(FUTURE_JUMP_FIXTURE)
    result = run_backtest(
        candles,
        starting_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        fee_bps=Decimal("0"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        source="fixture:future-jump",
        execution_model=ExecutionModel.NEXT_BAR_OPEN_BID_ASK,
        quoted_spread_bps_assumption=Decimal("20"),
        adverse_slippage_bps_assumption=Decimal("10"),
    )

    fill = result.fills[0]
    assert fill.price_usd == Decimal("20.040020")
    assert fill.price_usd > candles[5].open
    assert fill.raw["reference_open_usd"] == "20"
    assert fill.raw["synthetic_bid_usd"] == "19.980"
    assert fill.raw["synthetic_ask_usd"] == "20.020"
    assert fill.raw["pre_slippage_touch_usd"] == "20.020"
    assert fill.raw["fill_price_usd"] == "20.040020"
    assert fill.raw["quoted_spread_bps_assumption"] == "20"
    assert fill.raw["adverse_slippage_bps_assumption"] == "10"
    assert result.summary()["execution_model"] == "next_bar_open_bid_ask"
    assert result.summary()["quoted_spread_bps_assumption"] == "20"
    assert result.summary()["adverse_slippage_bps_assumption"] == "10"

    fill_event = next(
        event for event in result.events if event.event_type == BacktestEventType.FILL
    )
    assert fill_event.details["reference_open_usd"] == "20"
    assert fill_event.details["fill_price_usd"] == "20.040020"


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
