import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from market.backtest.costs import VenueCostProfile
from market.backtest.engine import ExecutionModel, run_backtest
from market.domain.models import Candle
from market.research.power import (
    DOUBLED_COSTS,
    EMA_PAIRS,
    FRICTIONLESS_COSTS,
    PRIMARY_COSTS,
    CostScenario,
    FoldLayout,
    FoldPowerScore,
    PathScore,
    PowerScenario,
    PowerStudyConfig,
    SyntheticCalibrationError,
    SyntheticSignalConfig,
    SyntheticTrade,
    WalkForwardPowerScore,
    design_screen,
    ema_series,
    generate_variance_normalized_path,
    load_fold_layouts,
    run_power_study,
    score_ema_path,
    write_power_study,
)
from market.research.power_cli import load_study_definition
from market.risk.gate import RiskConfig
from market.strategy.slow_trend import SlowTrendConfig
from market.strategy.slow_trend import ema_series as decimal_ema_series


def _deterministic_prices() -> tuple[list[float], list[float]]:
    closes = [
        100,
        99,
        98,
        97,
        96,
        95,
        94,
        100,
        106,
        112,
        118,
        115,
        108,
        101,
        94,
        87,
        92,
        99,
        106,
        113,
        120,
    ]
    opens = [100.0, *[float(value) for value in closes[:-1]]]
    return opens, [float(value) for value in closes]


def _candles(opens: list[float], closes: list[float]) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Candle(
            ts=start + timedelta(hours=index),
            open=Decimal(str(open_price)),
            high=Decimal(str(max(open_price, close_price))),
            low=Decimal(str(min(open_price, close_price))),
            close=Decimal(str(close_price)),
            volume=Decimal("1"),
        )
        for index, (open_price, close_price) in enumerate(zip(opens, closes, strict=True))
    ]


def test_preregistered_family_and_cost_drag_are_bound() -> None:
    assert len(EMA_PAIRS) == 36
    assert (12, 26) in EMA_PAIRS
    assert PRIMARY_COSTS.flat_price_round_trip_drag_bps == pytest.approx(229.5408787017)
    assert DOUBLED_COSTS.flat_price_round_trip_drag_bps == pytest.approx(458.1670192549)
    assert FRICTIONLESS_COSTS.flat_price_round_trip_drag_bps == 0


def test_variance_normalized_generator_is_deterministic_and_calibrated() -> None:
    config = SyntheticSignalConfig(
        bars=8_000,
        annual_volatility=0.60,
        signal_variance_fraction=0.02,
        signal_half_life_hours=48,
        innovation_degrees_of_freedom=None,
        seed=17,
    )

    first = generate_variance_normalized_path(config)
    second = generate_variance_normalized_path(config)

    assert first == second
    assert len(first.opens_usd) == len(first.closes_usd) == config.bars
    assert first.opens_usd[1] == first.closes_usd[0]
    assert first.diagnostics.annual_volatility_relative_error <= (
        config.calibration_relative_tolerance
    )
    assert first.diagnostics.latent_variance_relative_error <= (
        config.latent_variance_relative_tolerance
    )
    assert all(price > 0 for price in first.closes_usd)


def test_generator_fails_closed_instead_of_releasing_uncalibrated_path() -> None:
    with pytest.raises(SyntheticCalibrationError, match="annual volatility outside tolerance"):
        generate_variance_normalized_path(
            SyntheticSignalConfig(
                bars=250,
                annual_volatility=0.60,
                signal_variance_fraction=0.01,
                signal_half_life_hours=48,
                seed=3,
                calibration_relative_tolerance=0.000001,
            )
        )


def test_float_ema_matches_production_sma_seeded_definition() -> None:
    values = [Decimal(index) / Decimal("10") for index in range(1, 80)]
    expected = decimal_ema_series(values, 26)
    actual = ema_series([float(value) for value in values], 26)

    assert [value is None for value in actual] == [value is None for value in expected]
    for float_value, decimal_value in zip(actual, expected, strict=True):
        if float_value is not None and decimal_value is not None:
            assert float_value == pytest.approx(float(decimal_value), rel=1e-13, abs=1e-13)


@pytest.mark.parametrize("cost_scenario", [PRIMARY_COSTS, DOUBLED_COSTS])
def test_synthetic_score_reconciles_to_production_engine(
    cost_scenario: CostScenario,
) -> None:
    opens, closes = _deterministic_prices()
    synthetic = score_ema_path(
        opens_usd=opens,
        closes_usd=closes,
        fast_ema=3,
        slow_ema=5,
        cost_scenario=cost_scenario,
        indicator_context_bars=0,
    )
    engine = run_backtest(
        _candles(opens, closes),
        starting_cash_usd=Decimal("1000"),
        strategy_cfg=SlowTrendConfig(
            fast_ema=3,
            slow_ema=5,
            order_qty_btc=Decimal("0.001"),
        ),
        risk_cfg=RiskConfig(
            max_position_btc=Decimal("0.002"),
            max_notional_usd=Decimal("150"),
            max_daily_loss_usd=Decimal("25"),
            max_orders_per_hour=4,
            min_seconds_between_orders=300,
            allow_entries=True,
        ),
        execution_model=ExecutionModel.NEXT_BAR_OPEN_BID_ASK,
        quoted_spread_bps_assumption=Decimal(str(cost_scenario.full_spread_bps)),
        adverse_slippage_bps_assumption=Decimal(str(cost_scenario.adverse_slippage_bps)),
        venue_cost_profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
        transaction_fee_bps_per_fill_assumption=Decimal(str(cost_scenario.fee_bps_per_fill)),
        record_equity_every=0,
        source="test:synthetic-power-reconciliation",
    )

    assert synthetic.closed_trade_count == engine.lifecycle.closed_trade_count
    assert synthetic.aggregate_net_pnl_usd == pytest.approx(
        float(engine.net_liquidation_pnl_after_fees_usd), abs=1e-12
    )
    for synthetic_trade, engine_trade in zip(
        synthetic.trades, engine.lifecycle.closed_trades, strict=True
    ):
        engine_return_bps = (
            engine_trade.realized_net_pnl_after_fees_usd
            / engine_trade.allocated_gross_cost_basis_usd
            * Decimal("10000")
        )
        assert synthetic_trade.net_return_bps == pytest.approx(float(engine_return_bps), abs=1e-10)


def test_indicator_context_does_not_carry_a_position_or_pnl() -> None:
    opens, closes = _deterministic_prices()
    score = score_ema_path(
        opens_usd=opens,
        closes_usd=closes,
        fast_ema=3,
        slow_ema=5,
        cost_scenario=FRICTIONLESS_COSTS,
        score_start=10,
        score_end=len(closes),
        indicator_context_bars=8,
    )

    assert all(trade.entry_index >= 10 for trade in score.trades)
    assert score.aggregate_net_pnl_usd == pytest.approx(
        sum(trade.net_pnl_usd for trade in score.trades)
    )


def test_real_split_layout_uses_only_public_development_lengths() -> None:
    layouts = load_fold_layouts("config/research/g3-ema-v1-splits.json")

    assert len(layouts) == 9
    assert layouts[0] == FoldLayout(
        fold_id="wf-01",
        validation_start=13_176,
        validation_end=15_309,
        test_start=15_309,
        test_end=17_517,
    )
    assert layouts[-1].test_end == 35_061


def test_trade_dispersion_guard_rejects_exploded_calibration() -> None:
    trade = SyntheticTrade(
        entry_index=1,
        exit_index=2,
        holding_bars=1,
        quantity_btc=0.001,
        entry_fill_price_usd=100.0,
        exit_fill_price_usd=100.0,
        net_pnl_usd=1.0,
        net_return_bps=100_000_000.0,
        terminal_liquidation=False,
    )
    opposite = SyntheticTrade(
        entry_index=3,
        exit_index=4,
        holding_bars=1,
        quantity_btc=0.001,
        entry_fill_price_usd=100.0,
        exit_fill_price_usd=100.0,
        net_pnl_usd=-1.0,
        net_return_bps=-100_000_000.0,
        terminal_liquidation=False,
    )
    path_score = PathScore(
        fast_ema=12,
        slow_ema=26,
        cost_scenario="primary",
        score_start=0,
        score_end=5,
        trades=(trade, opposite),
        aggregate_net_pnl_usd=0.0,
        mean_net_trade_return_bps=0.0,
        maximum_account_drawdown_usd=1.0,
    )
    fold = FoldPowerScore(
        fold_id="wf-01",
        selected_fast_ema=12,
        selected_slow_ema=26,
        validation_mean_net_trade_return_bps=0.0,
        primary_test=path_score,
        doubled_test=path_score,
    )

    with pytest.raises(SyntheticCalibrationError, match="per-trade standard deviation"):
        design_screen(WalkForwardPowerScore(folds=(fold,)))


def test_small_power_study_is_labeled_and_json_serializable(tmp_path: Path) -> None:
    layouts = (
        FoldLayout("wf-01", 1_000, 1_500, 1_500, 2_000),
        FoldLayout("wf-02", 1_500, 2_000, 2_000, 2_500),
    )
    payload = run_power_study(
        scenarios=(
            PowerScenario(
                name="unit-null",
                annual_volatility=0.60,
                signal_variance_fraction=0.0,
                signal_half_life_hours=48,
                innovation_degrees_of_freedom=None,
            ),
        ),
        layouts=layouts,
        bars=2_500,
        config=PowerStudyConfig(
            simulations_per_scenario=2,
            base_seed=100,
            bootstrap_resamples=99,
        ),
        pairs=((3, 8), (5, 13)),
    )

    assert payload["study_type"] == "synthetic_design_assurance_not_strategy_evidence"
    assert payload["design_screen"]["included_protocol_criteria"] == [1, 2, 3, 4, 5, 6, 8]
    assert payload["design_screen"]["excluded_protocol_criteria"] == [7, 9, 10, 11]
    assert payload["scenarios"][0]["simulations"] == 2
    json.dumps(payload, allow_nan=False)

    output_path = tmp_path / "power.json"
    assert write_power_study(payload, output_path) == output_path
    assert json.loads(output_path.read_text())["schema_version"] == 1
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_power_study(payload, output_path)


def test_committed_study_definition_is_strict_and_complete() -> None:
    bars, config, scenarios = load_study_definition("config/research/g3-ema-v1-power-study.json")

    assert bars == 35_061
    assert config.simulations_per_scenario == 100
    assert config.bootstrap_resamples == 499
    assert config.maximum_trade_sd_bps == 30_000
    assert len(scenarios) == 5
    assert scenarios[0].signal_variance_fraction == 0
    assert scenarios[-1].name == "extreme_detectability_stress"
