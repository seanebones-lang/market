from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from market.backtest.benchmarks import BenchmarkKind
from market.backtest.costs import VenueCostProfile
from market.backtest.engine import ExecutionModel, run_backtest
from market.backtest.performance import (
    CRYPTO_HOURLY_PERIODS_PER_YEAR,
    MetricStatus,
    PerformanceAnalysisError,
    PerformanceObservation,
    calculate_benchmark_alpha,
    calculate_portfolio_statistics,
)
from market.data.candles import load_candles_csv
from market.domain.models import Fill, Side
from market.strategy.slow_trend import SlowTrendConfig

FUTURE_JUMP_FIXTURE = Path(__file__).parent / "fixtures" / "backtest" / "future_jump.csv"
START = datetime(2024, 1, 1, tzinfo=UTC)


def _observation(index: int, value: str, inventory: str = "0") -> PerformanceObservation:
    return PerformanceObservation(
        ts=(START + timedelta(hours=index + 1)).isoformat(),
        net_liquidation_value_usd=Decimal(value),
        inventory_btc=Decimal(inventory),
    )


def _fill(order_id: str, side: Side, price: str, fee: str = "1") -> Fill:
    return Fill(
        client_order_id=order_id,
        broker_order_id=f"broker-{order_id}",
        side=side,
        qty_btc=Decimal("1"),
        price_usd=Decimal(price),
        fee_usd=Decimal(fee),
        ts=START,
    )


def _observations_from_returns(returns: tuple[Decimal, ...]) -> tuple[PerformanceObservation, ...]:
    value = Decimal("100")
    observations = []
    for index, period_return in enumerate(returns):
        value *= Decimal("1") + period_return
        observations.append(_observation(index, str(value)))
    return tuple(observations)


def test_portfolio_statistics_match_hand_calculated_hourly_metrics():
    observations = (
        _observation(0, "100", "0"),
        _observation(1, "110", "1"),
        _observation(2, "99", "0"),
    )
    fills = (
        _fill("buy", Side.BUY, "10"),
        _fill("sell", Side.SELL, "12"),
    )

    statistics = calculate_portfolio_statistics(
        portfolio="strategy",
        starting_cash_usd=Decimal("100"),
        observations=observations,
        fills=fills,
        closed_trade_net_pnls_usd=(Decimal("3"), Decimal("-1")),
        cumulative_explicit_fees_usd=Decimal("2"),
    )

    assert statistics.observation_count == 3
    assert statistics.return_observation_count == 3
    assert statistics.gross_executed_notional_usd == Decimal("22")
    assert statistics.average_net_liquidation_value_usd == Decimal("102.25")
    assert statistics.turnover_ratio == Decimal("22") / Decimal("102.25")
    assert statistics.turnover_status == MetricStatus.AVAILABLE
    assert statistics.exposed_observation_count == 1
    assert statistics.exposure_time_pct == Decimal("100") / Decimal("3")
    assert statistics.exposure_status == MetricStatus.AVAILABLE

    assert statistics.max_drawdown_usd == Decimal("11")
    assert statistics.max_drawdown_pct == Decimal("10")
    assert statistics.max_drawdown_duration_bars == 1
    assert statistics.max_drawdown_duration_seconds == 3600
    assert statistics.current_drawdown_duration_bars == 1
    assert statistics.current_drawdown_duration_seconds == 3600

    assert statistics.mean_period_simple_return == 0
    assert statistics.sample_period_volatility == Decimal("0.1")
    annualization_scale = Decimal(CRYPTO_HOURLY_PERIODS_PER_YEAR).sqrt()
    assert statistics.annualized_volatility == Decimal("0.1") * annualization_scale
    assert statistics.volatility_status == MetricStatus.AVAILABLE
    assert statistics.annualized_sharpe_ratio == 0
    assert statistics.sharpe_status == MetricStatus.AVAILABLE
    assert statistics.downside_deviation_per_period == (Decimal("0.01") / Decimal("3")).sqrt()
    assert statistics.annualized_sortino_ratio == 0
    assert statistics.sortino_status == MetricStatus.AVAILABLE

    assert statistics.closed_trade_count == 2
    assert statistics.gross_closed_trade_profit_usd == Decimal("3")
    assert statistics.gross_closed_trade_loss_usd == Decimal("1")
    assert statistics.profit_factor == Decimal("3")
    assert statistics.profit_factor_status == MetricStatus.AVAILABLE
    assert statistics.expectancy_per_closed_trade_usd == Decimal("1")
    assert statistics.expectancy_status == MetricStatus.AVAILABLE
    assert statistics.cumulative_explicit_fees_usd == Decimal("2")
    assert statistics.explicit_fee_drag_return_percentage_points == Decimal("2")
    assert statistics.fee_drag_status == MetricStatus.AVAILABLE


def test_undefined_statistics_have_explicit_statuses():
    observations = tuple(_observation(index, "100") for index in range(3))
    statistics = calculate_portfolio_statistics(
        portfolio="cash",
        starting_cash_usd=Decimal("100"),
        observations=observations,
        fills=(),
        closed_trade_net_pnls_usd=(),
        cumulative_explicit_fees_usd=Decimal("0"),
    )

    assert statistics.sample_period_volatility == 0
    assert statistics.annualized_volatility == 0
    assert statistics.volatility_status == MetricStatus.AVAILABLE
    assert statistics.annualized_sharpe_ratio is None
    assert statistics.sharpe_status == MetricStatus.ZERO_VARIANCE
    assert statistics.downside_deviation_per_period == 0
    assert statistics.annualized_sortino_ratio is None
    assert statistics.sortino_status == MetricStatus.NO_DOWNSIDE_RETURNS
    assert statistics.profit_factor is None
    assert statistics.profit_factor_status == MetricStatus.NO_CLOSED_TRADES
    assert statistics.expectancy_per_closed_trade_usd is None
    assert statistics.expectancy_status == MetricStatus.NO_CLOSED_TRADES
    assert statistics.turnover_ratio == 0
    assert statistics.exposure_time_pct == 0
    assert statistics.explicit_fee_drag_return_percentage_points == 0


def test_insufficient_and_zero_capital_series_are_not_fabricated():
    one_observation = calculate_portfolio_statistics(
        portfolio="one-bar",
        starting_cash_usd=Decimal("100"),
        observations=(_observation(0, "101"),),
        fills=(),
        closed_trade_net_pnls_usd=(),
        cumulative_explicit_fees_usd=Decimal("0"),
    )
    assert one_observation.sample_period_volatility is None
    assert one_observation.volatility_status == MetricStatus.INSUFFICIENT_OBSERVATIONS
    assert one_observation.annualized_sharpe_ratio is None
    assert one_observation.sharpe_status == MetricStatus.INSUFFICIENT_OBSERVATIONS

    zero_capital = calculate_portfolio_statistics(
        portfolio="zero",
        starting_cash_usd=Decimal("0"),
        observations=(_observation(0, "0"),),
        fills=(),
        closed_trade_net_pnls_usd=(),
        cumulative_explicit_fees_usd=Decimal("0"),
    )
    assert zero_capital.return_observation_count == 0
    assert zero_capital.volatility_status == MetricStatus.NONPOSITIVE_PORTFOLIO_VALUE
    assert zero_capital.turnover_ratio is None
    assert zero_capital.turnover_status == MetricStatus.ZERO_AVERAGE_NET_LIQUIDATION_VALUE
    assert zero_capital.explicit_fee_drag_return_percentage_points is None
    assert zero_capital.fee_drag_status == MetricStatus.ZERO_STARTING_CASH


def test_benchmark_alpha_recovers_known_ols_intercept_and_beta():
    benchmark_returns = (Decimal("0.01"), Decimal("-0.01"), Decimal("0.02"))
    strategy_returns = tuple(Decimal("0.01") + Decimal("2") * value for value in benchmark_returns)

    alpha = calculate_benchmark_alpha(
        benchmark=BenchmarkKind.MATCHED_NOTIONAL_BUY_AND_HOLD,
        starting_cash_usd=Decimal("100"),
        strategy_observations=_observations_from_returns(strategy_returns),
        benchmark_observations=_observations_from_returns(benchmark_returns),
    )

    assert alpha.status == MetricStatus.AVAILABLE
    assert alpha.observation_count == 3
    assert alpha.beta == Decimal("2")
    assert alpha.alpha_per_period == Decimal("0.01")
    assert alpha.annualized_alpha == Decimal("87.60")
    expected_active = (
        sum(strategy_returns, Decimal("0")) / Decimal("3")
        - sum(benchmark_returns, Decimal("0")) / Decimal("3")
    ) * Decimal(CRYPTO_HOURLY_PERIODS_PER_YEAR)
    assert alpha.annualized_active_return_difference == expected_active


def test_cash_alpha_is_explicitly_undefined_for_zero_benchmark_variance():
    strategy = _observations_from_returns((Decimal("0.01"), Decimal("-0.01"), Decimal("0.02")))
    cash = _observations_from_returns((Decimal("0"), Decimal("0"), Decimal("0")))

    alpha = calculate_benchmark_alpha(
        benchmark=BenchmarkKind.CASH,
        starting_cash_usd=Decimal("100"),
        strategy_observations=strategy,
        benchmark_observations=cash,
    )

    assert alpha.status == MetricStatus.ZERO_BENCHMARK_VARIANCE
    assert alpha.beta is None
    assert alpha.alpha_per_period is None
    assert alpha.annualized_alpha is None
    assert alpha.annualized_active_return_difference is not None


def test_performance_inputs_fail_closed():
    base = {
        "portfolio": "strategy",
        "starting_cash_usd": Decimal("100"),
        "observations": (_observation(0, "100"), _observation(1, "101")),
        "fills": (),
        "closed_trade_net_pnls_usd": (),
        "cumulative_explicit_fees_usd": Decimal("0"),
    }
    with pytest.raises(PerformanceAnalysisError, match="portfolio name"):
        calculate_portfolio_statistics(**{**base, "portfolio": ""})
    with pytest.raises(PerformanceAnalysisError, match="annualization periods"):
        calculate_portfolio_statistics(**{**base, "periods_per_year": 0})
    with pytest.raises(PerformanceAnalysisError, match="ordered"):
        calculate_portfolio_statistics(
            **{**base, "observations": tuple(reversed(base["observations"]))}
        )
    with pytest.raises(PerformanceAnalysisError, match="net liquidation"):
        calculate_portfolio_statistics(**{**base, "observations": (_observation(0, "-1"),)})
    with pytest.raises(PerformanceAnalysisError, match="inventory observations"):
        calculate_portfolio_statistics(
            **{
                **base,
                "observations": (replace(_observation(0, "100"), inventory_btc=Decimal("-1")),),
            }
        )
    with pytest.raises(PerformanceAnalysisError, match="starting cash"):
        calculate_portfolio_statistics(**{**base, "starting_cash_usd": Decimal("NaN")})
    with pytest.raises(PerformanceAnalysisError, match="cumulative explicit fees"):
        calculate_portfolio_statistics(**{**base, "cumulative_explicit_fees_usd": Decimal("-1")})
    with pytest.raises(PerformanceAnalysisError, match="fill fees"):
        calculate_portfolio_statistics(**{**base, "fills": (_fill("buy", Side.BUY, "10"),)})
    with pytest.raises(PerformanceAnalysisError, match="closed-trade"):
        calculate_portfolio_statistics(**{**base, "closed_trade_net_pnls_usd": (Decimal("NaN"),)})

    strategy = _observations_from_returns((Decimal("0"), Decimal("0.01")))
    benchmark = tuple(
        replace(item, ts=(START + timedelta(hours=index + 10)).isoformat())
        for index, item in enumerate(strategy)
    )
    with pytest.raises(PerformanceAnalysisError, match="timestamps"):
        calculate_benchmark_alpha(
            benchmark=BenchmarkKind.PERIODIC_DCA,
            starting_cash_usd=Decimal("100"),
            strategy_observations=strategy,
            benchmark_observations=benchmark,
        )
    with pytest.raises(PerformanceAnalysisError, match="not aligned"):
        calculate_benchmark_alpha(
            benchmark=BenchmarkKind.PERIODIC_DCA,
            starting_cash_usd=Decimal("100"),
            strategy_observations=strategy,
            benchmark_observations=benchmark[:-1],
        )
    with pytest.raises(PerformanceAnalysisError, match="annualization periods"):
        calculate_benchmark_alpha(
            benchmark=BenchmarkKind.PERIODIC_DCA,
            starting_cash_usd=Decimal("100"),
            strategy_observations=strategy,
            benchmark_observations=strategy,
            periods_per_year=0,
        )


def test_future_jump_engine_reports_strategy_and_benchmark_statistics():
    result = run_backtest(
        load_candles_csv(FUTURE_JUMP_FIXTURE),
        starting_cash_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        source="fixture:future-jump",
        execution_model=ExecutionModel.NEXT_BAR_OPEN_BID_ASK,
        quoted_spread_bps_assumption=Decimal("20"),
        adverse_slippage_bps_assumption=Decimal("10"),
        venue_cost_profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
        transaction_fee_bps_per_fill_assumption=Decimal("95"),
        benchmark_dca_interval_bars=2,
    )

    assert len(result.performance.portfolios) == 4
    assert len(result.performance.benchmark_alphas) == 3
    strategy = result.performance.portfolios[0]
    assert strategy.portfolio == "strategy"
    assert strategy.observation_count == 6
    assert strategy.return_observation_count == 6
    assert strategy.gross_executed_notional_usd == Decimal("40.000040")
    assert strategy.exposed_observation_count == 1
    assert strategy.exposure_time_pct == Decimal("100") / Decimal("6")
    assert strategy.max_drawdown_usd == result.max_net_liquidation_drawdown_usd
    assert strategy.max_drawdown_duration_bars == 1
    assert strategy.closed_trade_count == 1
    assert strategy.gross_closed_trade_profit_usd == 0
    assert strategy.gross_closed_trade_loss_usd == Decimal("0.4600003800")
    assert strategy.profit_factor == 0
    assert strategy.profit_factor_status == MetricStatus.AVAILABLE
    assert strategy.expectancy_per_closed_trade_usd == Decimal("-0.4600003800")
    assert strategy.cumulative_explicit_fees_usd == Decimal("0.3800003800")
    assert strategy.explicit_fee_drag_return_percentage_points == Decimal("0.03800003800")
    assert strategy.volatility_status == MetricStatus.AVAILABLE
    assert strategy.sharpe_status == MetricStatus.AVAILABLE
    assert strategy.sortino_status == MetricStatus.AVAILABLE

    cash = result.performance.portfolios[1]
    assert cash.portfolio == BenchmarkKind.CASH.value
    assert cash.turnover_ratio == 0
    assert cash.exposure_time_pct == 0
    assert cash.sharpe_status == MetricStatus.ZERO_VARIANCE
    assert cash.sortino_status == MetricStatus.NO_DOWNSIDE_RETURNS

    cash_alpha, buy_hold_alpha, dca_alpha = result.performance.benchmark_alphas
    assert cash_alpha.status == MetricStatus.ZERO_BENCHMARK_VARIANCE
    assert buy_hold_alpha.status == MetricStatus.AVAILABLE
    assert dca_alpha.status == MetricStatus.AVAILABLE


def test_performance_observations_are_complete_when_display_equity_is_downsampled():
    result = run_backtest(
        load_candles_csv(FUTURE_JUMP_FIXTURE),
        starting_cash_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        record_equity_every=0,
    )

    assert len(result.performance.strategy_observations) == result.bars == 6
    assert result.performance.portfolios[0].observation_count == 6
    assert len(result.equity_curve) == 1
