import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from market.backtest.costs import VenueCostProfile
from market.backtest.engine import (
    BacktestEventType,
    EquityPointStage,
    ExecutionAssumptions,
    ExecutionModel,
    TerminalLiquidationModel,
    calculate_execution_price,
    calculate_terminal_liquidation_price,
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
        starting_cash_usd=Decimal("1000"),
        qty_btc=Decimal("0.001"),
        strategy_cfg=SlowTrendConfig(fast_ema=3, slow_ema=8, order_qty_btc=Decimal("0.001")),
        source="test:synth",
    )
    assert res.intents == 1
    assert res.allowed == 1
    assert res.blocked == 0
    assert len(res.fills) == 2
    assert [fill.side for fill in res.fills] == [Side.BUY, Side.SELL]
    assert res.position_before_terminal_liquidation_btc == Decimal("0.001")
    assert res.final_inventory_btc == 0
    assert res.terminal_liquidation_fills == 1
    assert res.terminal_liquidation_qty_btc == Decimal("0.001")
    assert res.terminal_liquidation_fee_usd == res.fills[-1].fee_usd
    assert res.final_cash_usd > res.starting_cash_usd
    assert res.bars == 100
    assert res.equity_curve
    assert res.max_marked_equity_usd == (res.final_cash_usd + res.terminal_liquidation_fee_usd)
    assert res.equity_curve[-1].stage == EquityPointStage.POST_TERMINAL_LIQUIDATION
    assert res.equity_curve[-1].marked_equity_usd == res.final_cash_usd
    assert res.equity_curve[-1].inventory_btc == 0


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
    assert paths["accounting"].exists()
    assert paths["lifecycle"].exists()
    assert paths["benchmarks"].exists()
    assert paths["benchmark_fills"].exists()
    assert paths["benchmark_equity"].exists()
    assert paths["performance"].exists()
    assert paths["performance_observations"].exists()
    assert paths["equity"].exists()
    text = paths["summary"].read_text()
    summary = json.loads(text)
    assert "schema_version" in text
    assert '"execution_model": "next_bar_open"' in text
    assert '"quoted_spread_bps_assumption": "0"' in text
    assert '"adverse_slippage_bps_assumption": "0"' in text
    assert '"venue_cost_profile": "legacy_unclassified"' in text
    assert '"cost_input_classification": "legacy_unclassified"' in text
    assert '"market_data_source": "test"' in text
    assert '"fee_calculation_basis": "executed_notional_per_fill"' in text
    assert '"transaction_fee_bps_per_fill_assumption": "5"' in text
    assert '"transaction_fee_bps_per_fill_applied": "5"' in text
    assert '"schema_version": 10' in text
    assert '"terminal_liquidation_model": "last_bar_close"' in text
    assert '"terminal_liquidation_fills": 1' in text
    assert summary["accounting_method"] == ("weighted_average_gross_cost_basis_fees_separate")
    assert summary["accounting_journal_entries"] == len(res.accounting_journal)
    assert summary["final_cash_usd"] == str(res.final_cash_usd)
    assert summary["final_inventory_btc"] == "0"
    assert summary["final_inventory_cost_basis_usd"] == "0"
    assert summary["realized_gross_pnl_usd"] == str(res.realized_gross_pnl_usd)
    assert summary["unrealized_gross_pnl_usd"] == "0"
    assert summary["cumulative_fees_usd"] == str(res.cumulative_fees_usd)
    assert summary["marked_equity_usd"] == str(res.marked_equity_usd)
    assert summary["net_liquidation_value_usd"] == str(res.net_liquidation_value_usd)
    assert summary["accounting_identity_residual_usd"] == "0"
    assert summary["order_count"] == res.lifecycle.order_count
    assert summary["execution_count"] == res.lifecycle.execution_count
    assert summary["round_trip_count"] == res.lifecycle.round_trip_count
    assert summary["closed_trade_count"] == res.lifecycle.closed_trade_count
    assert summary["open_inventory_btc"] == "0"
    assert summary["benchmark_count"] == 3
    assert summary["benchmark_dca_interval_bars"] == 168
    assert len(summary["benchmarks"]) == 3
    assert len(summary["benchmark_comparisons"]) == 3
    assert summary["performance_periods_per_year"] == 8760
    assert len(summary["portfolio_statistics"]) == 4
    assert len(summary["benchmark_alphas"]) == 3
    assert "pnl_usd" not in summary
    assert "final_usd" not in summary
    assert "fees_usd" not in summary
    assert "return_pct" not in summary
    assert '"fee_bps"' not in text
    assert '"transaction_fee_bps_assumption"' not in text
    event_lines = paths["events"].read_text().splitlines()
    assert len(event_lines) == len(res.events)
    fill_row = json.loads(paths["fills"].read_text().splitlines()[0])
    assert fill_row["venue"] == "unclassified"
    assert fill_row["market_data_source"] == "test"
    accounting_rows = [json.loads(line) for line in paths["accounting"].read_text().splitlines()]
    assert len(accounting_rows) == len(res.accounting_journal)
    assert accounting_rows[0]["entry_type"] == "opening_balance"
    assert accounting_rows[-1]["entry_type"] == "fill"
    assert accounting_rows[-1]["accounting_identity_residual_usd"] == "0"
    lifecycle_rows = [json.loads(line) for line in paths["lifecycle"].read_text().splitlines()]
    assert lifecycle_rows[0]["type"] == "lifecycle_summary"
    assert sum(row["type"] == "order_lifecycle" for row in lifecycle_rows) == (
        res.lifecycle.order_count
    )
    assert sum(row["type"] == "closed_trade" for row in lifecycle_rows) == (
        res.lifecycle.closed_trade_count
    )
    assert sum(row["type"] == "round_trip" for row in lifecycle_rows) == (
        res.lifecycle.round_trip_count
    )
    benchmark_rows = [json.loads(line) for line in paths["benchmarks"].read_text().splitlines()]
    assert benchmark_rows[0]["type"] == "benchmark_contract"
    assert sum(row["type"] == "benchmark_result" for row in benchmark_rows) == 3
    assert sum(row["type"] == "benchmark_comparison" for row in benchmark_rows) == 3
    benchmark_fill_rows = [
        json.loads(line) for line in paths["benchmark_fills"].read_text().splitlines()
    ]
    assert len(benchmark_fill_rows) == sum(
        len(benchmark.fills) for benchmark in res.benchmarks.results
    )
    assert all(row["type"] == "benchmark_fill" for row in benchmark_fill_rows)
    assert all(row["execution_model"] == "next_bar_open" for row in benchmark_fill_rows)
    assert all(row["transaction_fee_bps_per_fill_applied"] == "5" for row in benchmark_fill_rows)
    benchmark_equity_rows = [
        json.loads(line) for line in paths["benchmark_equity"].read_text().splitlines()
    ]
    assert len(benchmark_equity_rows) == sum(
        len(benchmark.equity_curve) for benchmark in res.benchmarks.results
    )
    assert all(row["type"] == "benchmark_equity" for row in benchmark_equity_rows)
    assert all("liquidation_sell_price_usd" in row for row in benchmark_equity_rows)
    assert all("estimated_liquidation_fee_usd" in row for row in benchmark_equity_rows)
    performance_rows = [json.loads(line) for line in paths["performance"].read_text().splitlines()]
    assert performance_rows[0]["type"] == "performance_contract"
    assert sum(row["type"] == "portfolio_statistics" for row in performance_rows) == 4
    assert sum(row["type"] == "benchmark_alpha" for row in performance_rows) == 3
    assert performance_rows[0]["performance_periods_per_year"] == 8760
    performance_observation_rows = [
        json.loads(line) for line in paths["performance_observations"].read_text().splitlines()
    ]
    assert len(performance_observation_rows) == res.bars
    assert all(
        row["type"] == "strategy_performance_observation" for row in performance_observation_rows
    )
    equity_rows = [json.loads(line) for line in paths["equity"].read_text().splitlines()]
    assert equity_rows[-1]["stage"] == "post_terminal_liquidation"
    assert equity_rows[-1]["cash_usd"] == str(res.final_cash_usd)
    assert equity_rows[-1]["inventory_btc"] == "0"
    assert equity_rows[-1]["marked_equity_usd"] == str(res.marked_equity_usd)
    assert equity_rows[-1]["net_liquidation_value_usd"] == str(res.net_liquidation_value_usd)


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


def test_terminal_liquidation_price_uses_final_close_and_adverse_sell_costs():
    assumptions = ExecutionAssumptions(
        model=ExecutionModel.NEXT_BAR_OPEN_BID_ASK,
        quoted_spread_bps_assumption=Decimal("20"),
        adverse_slippage_bps_assumption=Decimal("10"),
    )

    price = calculate_terminal_liquidation_price(assumptions, Decimal("20"))

    assert price.reference_close_usd == Decimal("20")
    assert price.synthetic_bid_usd == Decimal("19.980")
    assert price.synthetic_ask_usd == Decimal("20.020")
    assert price.pre_slippage_touch_usd == Decimal("19.980")
    assert price.fill_price_usd == Decimal("19.960020")


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
        starting_cash_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        source="fixture:future-jump",
    )

    assert result.execution_model == ExecutionModel.NEXT_BAR_OPEN
    assert result.intents == 1
    assert result.allowed == 1
    assert len(result.fills) == 2
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

    terminal_fill = result.fills[-1]
    assert terminal_fill.side == Side.SELL
    assert terminal_fill.price_usd == Decimal("20")
    assert terminal_fill.fee_usd == Decimal("0.0100")
    assert terminal_fill.raw["terminal_liquidation"] is True
    assert terminal_fill.raw["reference_close_usd"] == "20"
    assert result.cumulative_fees_usd == Decimal("0.0200")
    assert result.final_cash_usd == Decimal("999.9800")
    assert result.final_inventory_btc == 0
    assert result.position_before_terminal_liquidation_btc == 1
    assert result.terminal_liquidation_model == TerminalLiquidationModel.LAST_BAR_CLOSE
    request_event, terminal_fill_event = result.events[-2:]
    assert request_event.event_type == BacktestEventType.TERMINAL_LIQUIDATION_REQUESTED
    assert terminal_fill_event.event_type == BacktestEventType.FILL
    assert request_event.sequence < terminal_fill_event.sequence
    assert request_event.client_order_id == terminal_fill.client_order_id
    assert terminal_fill_event.client_order_id == terminal_fill.client_order_id


def test_future_jump_applies_declared_spread_and_slippage_after_next_open():
    candles = load_candles_csv(FUTURE_JUMP_FIXTURE)
    result = run_backtest(
        candles,
        starting_cash_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        transaction_fee_bps_per_fill_assumption=Decimal("0"),
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

    terminal_fill = result.fills[-1]
    assert terminal_fill.side == Side.SELL
    assert terminal_fill.price_usd == Decimal("19.960020")
    assert terminal_fill.fee_usd == 0
    assert terminal_fill.raw["reference_close_usd"] == "20"
    assert terminal_fill.raw["pre_slippage_touch_usd"] == "19.980"
    assert terminal_fill.raw["terminal_liquidation_model"] == "last_bar_close_bid_ask"
    assert result.final_cash_usd == Decimal("999.920000")
    assert result.terminal_liquidation_model == (TerminalLiquidationModel.LAST_BAR_CLOSE_BID_ASK)


def test_robinhood_v1_profile_embeds_cost_in_spread_without_fee():
    candles = load_candles_csv(FUTURE_JUMP_FIXTURE)
    result = run_backtest(
        candles,
        starting_cash_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        source="fixture:future-jump",
        execution_model=ExecutionModel.NEXT_BAR_OPEN_BID_ASK,
        quoted_spread_bps_assumption=Decimal("192"),
        venue_cost_profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER,
    )

    fill = result.fills[0]
    assert fill.price_usd == Decimal("20.1920")
    assert fill.fee_usd == 0
    assert fill.raw["venue_cost_profile"] == ("robinhood_crypto_api_v1_market_maker")
    assert fill.raw["transaction_fee_treatment"] == ("spread_inclusive_no_separate_transaction_fee")
    assert fill.raw["cost_input_classification"] == "configured_assumption"
    assert result.summary()["routing"] == "market_maker"
    assert result.summary()["api_version"] == "v1"
    terminal_fill = result.fills[-1]
    assert terminal_fill.price_usd == Decimal("19.8080")
    assert terminal_fill.fee_usd == 0
    assert result.cumulative_fees_usd == 0


def test_robinhood_v2_profile_charges_taker_fee_assumption_on_fill_notional():
    candles = load_candles_csv(FUTURE_JUMP_FIXTURE)
    result = run_backtest(
        candles,
        starting_cash_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        source="fixture:future-jump",
        execution_model=ExecutionModel.NEXT_BAR_OPEN_BID_ASK,
        quoted_spread_bps_assumption=Decimal("20"),
        adverse_slippage_bps_assumption=Decimal("10"),
        venue_cost_profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
        transaction_fee_bps_per_fill_assumption=Decimal("95"),
    )

    fill = result.fills[0]
    assert fill.price_usd == Decimal("20.040020")
    assert fill.fee_usd == Decimal("0.190380190")
    assert fill.raw["venue_cost_profile"] == ("robinhood_crypto_api_v2_exchange_taker")
    assert fill.raw["transaction_fee_treatment"] == (
        "exchange_taker_fee_per_fill_on_executed_notional_assumption"
    )
    assert fill.raw["fee_calculation_basis"] == "executed_notional_per_fill"
    assert fill.raw["transaction_fee_bps_per_fill_assumption"] == "95"
    assert fill.raw["transaction_fee_bps_per_fill_applied"] == "95"
    assert result.summary()["routing"] == "exchange"
    assert result.summary()["api_version"] == "v2"
    assert result.summary()["transaction_fee_bps_per_fill_assumption"] == "95"
    assert "fee_bps" not in result.summary()
    assert "transaction_fee_bps_assumption" not in result.summary()
    assert "observed" not in str(result.summary()).lower()
    terminal_fill = result.fills[-1]
    assert terminal_fill.price_usd == Decimal("19.960020")
    assert terminal_fill.fee_usd == Decimal("0.189620190")
    assert result.terminal_liquidation_fee_usd == Decimal("0.189620190")
    assert result.cumulative_fees_usd == Decimal("0.380000380")
    assert result.summary()["terminal_liquidation_fee_usd"] == "0.1896201900"


def test_signal_on_final_bar_expires_without_fill():
    candles = load_candles_csv(FUTURE_JUMP_FIXTURE)[:-1]
    result = run_backtest(
        candles,
        starting_cash_usd=Decimal("1000"),
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
    assert result.final_inventory_btc == 0
    assert result.position_before_terminal_liquidation_btc == 0
    assert result.terminal_liquidation_fills == 0
    assert result.terminal_liquidation_qty_btc == 0
    assert result.terminal_liquidation_fee_usd == 0
    assert result.equity_curve[-1].stage == EquityPointStage.BAR_CLOSE_MARK
