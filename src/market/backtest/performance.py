"""Deterministic performance statistics for strategy and benchmark portfolios."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from market.backtest.benchmarks import BenchmarkAnalysis, BenchmarkKind
from market.domain.models import Fill

CRYPTO_HOURLY_PERIODS_PER_YEAR = 365 * 24
HOURLY_PERIOD_SECONDS = 60 * 60


class PerformanceAnalysisError(ValueError):
    """Raised when a performance series or reconciliation input is invalid."""


class MetricStatus(str, Enum):
    AVAILABLE = "available"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"
    NONPOSITIVE_PORTFOLIO_VALUE = "nonpositive_portfolio_value"
    ZERO_VARIANCE = "zero_variance"
    NO_DOWNSIDE_RETURNS = "no_downside_returns"
    NO_CLOSED_TRADES = "no_closed_trades"
    NO_LOSING_CLOSED_TRADES = "no_losing_closed_trades"
    ZERO_AVERAGE_NET_LIQUIDATION_VALUE = "zero_average_net_liquidation_value"
    ZERO_STARTING_CASH = "zero_starting_cash"
    ZERO_BENCHMARK_VARIANCE = "zero_benchmark_variance"


@dataclass(frozen=True)
class PerformanceObservation:
    """One bar-close portfolio state used by all return and risk metrics."""

    ts: str
    net_liquidation_value_usd: Decimal
    inventory_btc: Decimal


@dataclass(frozen=True)
class PortfolioStatistics:
    portfolio: str
    observation_count: int
    return_observation_count: int
    period_seconds: int
    periods_per_year: int
    risk_free_rate_annual_pct_assumption: Decimal
    gross_executed_notional_usd: Decimal
    average_net_liquidation_value_usd: Decimal
    turnover_ratio: Decimal | None
    turnover_status: MetricStatus
    exposed_observation_count: int
    exposure_time_pct: Decimal | None
    exposure_status: MetricStatus
    max_drawdown_usd: Decimal
    max_drawdown_pct: Decimal
    max_drawdown_duration_bars: int
    max_drawdown_duration_seconds: int
    current_drawdown_duration_bars: int
    current_drawdown_duration_seconds: int
    mean_period_simple_return: Decimal | None
    sample_period_volatility: Decimal | None
    annualized_volatility: Decimal | None
    volatility_status: MetricStatus
    annualized_sharpe_ratio: Decimal | None
    sharpe_status: MetricStatus
    downside_deviation_per_period: Decimal | None
    annualized_sortino_ratio: Decimal | None
    sortino_status: MetricStatus
    closed_trade_count: int
    gross_closed_trade_profit_usd: Decimal
    gross_closed_trade_loss_usd: Decimal
    profit_factor: Decimal | None
    profit_factor_status: MetricStatus
    expectancy_per_closed_trade_usd: Decimal | None
    expectancy_status: MetricStatus
    cumulative_explicit_fees_usd: Decimal
    explicit_fee_drag_return_percentage_points: Decimal | None
    fee_drag_status: MetricStatus

    def summary(self) -> dict[str, int | str | None]:
        return {
            "portfolio": self.portfolio,
            "observation_count": self.observation_count,
            "return_observation_count": self.return_observation_count,
            "period_seconds": self.period_seconds,
            "periods_per_year": self.periods_per_year,
            "risk_free_rate_annual_pct_assumption": str(self.risk_free_rate_annual_pct_assumption),
            "gross_executed_notional_usd": str(self.gross_executed_notional_usd),
            "average_net_liquidation_value_usd": str(self.average_net_liquidation_value_usd),
            "turnover_ratio": _optional_decimal(self.turnover_ratio),
            "turnover_status": self.turnover_status.value,
            "exposed_observation_count": self.exposed_observation_count,
            "exposure_time_pct": _optional_decimal(self.exposure_time_pct),
            "exposure_status": self.exposure_status.value,
            "max_drawdown_usd": str(self.max_drawdown_usd),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "max_drawdown_duration_bars": self.max_drawdown_duration_bars,
            "max_drawdown_duration_seconds": self.max_drawdown_duration_seconds,
            "current_drawdown_duration_bars": self.current_drawdown_duration_bars,
            "current_drawdown_duration_seconds": self.current_drawdown_duration_seconds,
            "mean_period_simple_return": _optional_decimal(self.mean_period_simple_return),
            "sample_period_volatility": _optional_decimal(self.sample_period_volatility),
            "annualized_volatility": _optional_decimal(self.annualized_volatility),
            "volatility_status": self.volatility_status.value,
            "annualized_sharpe_ratio": _optional_decimal(self.annualized_sharpe_ratio),
            "sharpe_status": self.sharpe_status.value,
            "downside_deviation_per_period": _optional_decimal(self.downside_deviation_per_period),
            "annualized_sortino_ratio": _optional_decimal(self.annualized_sortino_ratio),
            "sortino_status": self.sortino_status.value,
            "closed_trade_count": self.closed_trade_count,
            "gross_closed_trade_profit_usd": str(self.gross_closed_trade_profit_usd),
            "gross_closed_trade_loss_usd": str(self.gross_closed_trade_loss_usd),
            "profit_factor": _optional_decimal(self.profit_factor),
            "profit_factor_status": self.profit_factor_status.value,
            "expectancy_per_closed_trade_usd": _optional_decimal(
                self.expectancy_per_closed_trade_usd
            ),
            "expectancy_status": self.expectancy_status.value,
            "cumulative_explicit_fees_usd": str(self.cumulative_explicit_fees_usd),
            "explicit_fee_drag_return_percentage_points": _optional_decimal(
                self.explicit_fee_drag_return_percentage_points
            ),
            "fee_drag_status": self.fee_drag_status.value,
        }


@dataclass(frozen=True)
class BenchmarkAlpha:
    benchmark: BenchmarkKind
    observation_count: int
    method: str
    periods_per_year: int
    strategy_mean_period_return: Decimal | None
    benchmark_mean_period_return: Decimal | None
    annualized_active_return_difference: Decimal | None
    beta: Decimal | None
    alpha_per_period: Decimal | None
    annualized_alpha: Decimal | None
    status: MetricStatus

    def summary(self) -> dict[str, int | str | None]:
        return {
            "benchmark": self.benchmark.value,
            "observation_count": self.observation_count,
            "method": self.method,
            "periods_per_year": self.periods_per_year,
            "strategy_mean_period_return": _optional_decimal(self.strategy_mean_period_return),
            "benchmark_mean_period_return": _optional_decimal(self.benchmark_mean_period_return),
            "annualized_active_return_difference": _optional_decimal(
                self.annualized_active_return_difference
            ),
            "beta": _optional_decimal(self.beta),
            "alpha_per_period": _optional_decimal(self.alpha_per_period),
            "annualized_alpha": _optional_decimal(self.annualized_alpha),
            "status": self.status.value,
        }


@dataclass(frozen=True)
class ResearchPerformanceAnalysis:
    return_type: str = "simple_net_liquidation_value_return"
    observation_frequency: str = "hourly_bar_close"
    volatility_estimator: str = "sample_standard_deviation_n_minus_1"
    downside_deviation_method: str = "sqrt_mean_squared_negative_return_all_periods"
    annualization_method: str = "sqrt_8760_for_volatility_sharpe_sortino"
    alpha_method: str = "ols_intercept_strategy_returns_on_benchmark_returns"
    period_seconds: int = HOURLY_PERIOD_SECONDS
    periods_per_year: int = CRYPTO_HOURLY_PERIODS_PER_YEAR
    risk_free_rate_annual_pct_assumption: Decimal = Decimal("0")
    strategy_observations: tuple[PerformanceObservation, ...] = ()
    portfolios: tuple[PortfolioStatistics, ...] = ()
    benchmark_alphas: tuple[BenchmarkAlpha, ...] = ()

    def summary(self) -> dict[str, object]:
        return {
            "performance_return_type": self.return_type,
            "performance_observation_frequency": self.observation_frequency,
            "performance_volatility_estimator": self.volatility_estimator,
            "performance_downside_deviation_method": self.downside_deviation_method,
            "performance_annualization_method": self.annualization_method,
            "performance_alpha_method": self.alpha_method,
            "performance_period_seconds": self.period_seconds,
            "performance_periods_per_year": self.periods_per_year,
            "performance_risk_free_rate_annual_pct_assumption": str(
                self.risk_free_rate_annual_pct_assumption
            ),
            "portfolio_statistics": [portfolio.summary() for portfolio in self.portfolios],
            "benchmark_alphas": [alpha.summary() for alpha in self.benchmark_alphas],
        }


@dataclass(frozen=True)
class _ReturnSeries:
    values: tuple[Decimal, ...]
    status: MetricStatus


def _optional_decimal(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _canonical_zero(value: Decimal) -> Decimal:
    return Decimal("0") if value == 0 else value


def _canonical_optional_zero(value: Decimal | None) -> Decimal | None:
    return _canonical_zero(value) if value is not None else None


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    if not values:
        raise PerformanceAnalysisError("mean requires at least one value")
    return _canonical_zero(sum(values, Decimal("0")) / Decimal(len(values)))


def _returns(
    starting_cash_usd: Decimal,
    observations: tuple[PerformanceObservation, ...],
) -> _ReturnSeries:
    if not observations:
        return _ReturnSeries((), MetricStatus.INSUFFICIENT_OBSERVATIONS)
    previous = starting_cash_usd
    returns: list[Decimal] = []
    for observation in observations:
        if previous <= 0:
            return _ReturnSeries((), MetricStatus.NONPOSITIVE_PORTFOLIO_VALUE)
        returns.append(observation.net_liquidation_value_usd / previous - Decimal("1"))
        previous = observation.net_liquidation_value_usd
    return _ReturnSeries(tuple(returns), MetricStatus.AVAILABLE)


def _validate_observations(
    starting_cash_usd: Decimal,
    observations: tuple[PerformanceObservation, ...],
) -> None:
    if not starting_cash_usd.is_finite() or starting_cash_usd < 0:
        raise PerformanceAnalysisError("starting cash must be finite and >= 0")
    previous_ts: str | None = None
    for observation in observations:
        if not observation.ts:
            raise PerformanceAnalysisError("performance observation timestamp is required")
        if previous_ts is not None and observation.ts <= previous_ts:
            raise PerformanceAnalysisError("performance observations must be ordered")
        previous_ts = observation.ts
        if (
            not observation.net_liquidation_value_usd.is_finite()
            or observation.net_liquidation_value_usd < 0
        ):
            raise PerformanceAnalysisError("net liquidation observations must be finite and >= 0")
        if not observation.inventory_btc.is_finite() or observation.inventory_btc < 0:
            raise PerformanceAnalysisError("inventory observations must be finite and >= 0")


def _drawdown_statistics(
    starting_cash_usd: Decimal,
    observations: tuple[PerformanceObservation, ...],
) -> tuple[Decimal, Decimal, int, int]:
    peak = starting_cash_usd
    max_drawdown = Decimal("0")
    max_drawdown_pct = Decimal("0")
    duration = 0
    max_duration = 0
    for observation in observations:
        value = observation.net_liquidation_value_usd
        if value >= peak:
            peak = value
            duration = 0
            continue
        duration += 1
        max_duration = max(max_duration, duration)
        drawdown = peak - value
        max_drawdown = max(max_drawdown, drawdown)
        if peak > 0:
            max_drawdown_pct = max(
                max_drawdown_pct,
                (drawdown / peak) * Decimal("100"),
            )
    return max_drawdown, max_drawdown_pct, max_duration, duration


def calculate_portfolio_statistics(
    *,
    portfolio: str,
    starting_cash_usd: Decimal,
    observations: tuple[PerformanceObservation, ...],
    fills: tuple[Fill, ...],
    closed_trade_net_pnls_usd: tuple[Decimal, ...],
    cumulative_explicit_fees_usd: Decimal,
    period_seconds: int = HOURLY_PERIOD_SECONDS,
    periods_per_year: int = CRYPTO_HOURLY_PERIODS_PER_YEAR,
) -> PortfolioStatistics:
    """Calculate exact-Decimal statistics under one declared sampling contract."""
    if not portfolio:
        raise PerformanceAnalysisError("portfolio name is required")
    if period_seconds <= 0 or periods_per_year <= 0:
        raise PerformanceAnalysisError("annualization periods must be positive")
    _validate_observations(starting_cash_usd, observations)
    if not cumulative_explicit_fees_usd.is_finite() or cumulative_explicit_fees_usd < 0:
        raise PerformanceAnalysisError("cumulative explicit fees must be finite and >= 0")
    fill_fees = sum((fill.fee_usd for fill in fills), Decimal("0"))
    if fill_fees != cumulative_explicit_fees_usd:
        raise PerformanceAnalysisError("fill fees do not reconcile to cumulative fees")
    if any(not pnl.is_finite() for pnl in closed_trade_net_pnls_usd):
        raise PerformanceAnalysisError("closed-trade P&L must be finite")

    gross_executed_notional = sum(
        (fill.notional_usd for fill in fills),
        Decimal("0"),
    )
    average_values = (starting_cash_usd,) + tuple(
        observation.net_liquidation_value_usd for observation in observations
    )
    average_nlv = _mean(average_values)
    if average_nlv == 0:
        turnover = None
        turnover_status = MetricStatus.ZERO_AVERAGE_NET_LIQUIDATION_VALUE
    else:
        turnover = _canonical_zero(gross_executed_notional / average_nlv)
        turnover_status = MetricStatus.AVAILABLE

    exposed_count = sum(observation.inventory_btc > 0 for observation in observations)
    if observations:
        exposure_time_pct = _canonical_zero(
            (Decimal(exposed_count) / Decimal(len(observations))) * Decimal("100")
        )
        exposure_status = MetricStatus.AVAILABLE
    else:
        exposure_time_pct = None
        exposure_status = MetricStatus.INSUFFICIENT_OBSERVATIONS

    max_drawdown, max_drawdown_pct, max_duration, current_duration = _drawdown_statistics(
        starting_cash_usd, observations
    )
    return_series = _returns(starting_cash_usd, observations)
    mean_return: Decimal | None = None
    sample_volatility: Decimal | None = None
    annualized_volatility: Decimal | None = None
    sharpe: Decimal | None = None
    downside_deviation: Decimal | None = None
    sortino: Decimal | None = None
    volatility_status = return_series.status
    sharpe_status = return_series.status
    sortino_status = return_series.status
    annualization_scale = Decimal(periods_per_year).sqrt()

    if return_series.status == MetricStatus.AVAILABLE:
        mean_return = _mean(return_series.values)
        if len(return_series.values) < 2:
            volatility_status = MetricStatus.INSUFFICIENT_OBSERVATIONS
            sharpe_status = MetricStatus.INSUFFICIENT_OBSERVATIONS
        else:
            variance = sum(
                ((value - mean_return) ** 2 for value in return_series.values),
                Decimal("0"),
            ) / Decimal(len(return_series.values) - 1)
            sample_volatility = _canonical_zero(variance.sqrt())
            annualized_volatility = _canonical_zero(sample_volatility * annualization_scale)
            volatility_status = MetricStatus.AVAILABLE
            if sample_volatility == 0:
                sharpe_status = MetricStatus.ZERO_VARIANCE
            else:
                sharpe = _canonical_zero((mean_return / sample_volatility) * annualization_scale)
                sharpe_status = MetricStatus.AVAILABLE

        downside_variance = sum(
            (min(value, Decimal("0")) ** 2 for value in return_series.values),
            Decimal("0"),
        ) / Decimal(len(return_series.values))
        downside_deviation = _canonical_zero(downside_variance.sqrt())
        if downside_deviation == 0:
            sortino_status = MetricStatus.NO_DOWNSIDE_RETURNS
        else:
            sortino = _canonical_zero((mean_return / downside_deviation) * annualization_scale)
            sortino_status = MetricStatus.AVAILABLE

    gross_profit = sum(
        (pnl for pnl in closed_trade_net_pnls_usd if pnl > 0),
        Decimal("0"),
    )
    gross_loss = -sum(
        (pnl for pnl in closed_trade_net_pnls_usd if pnl < 0),
        Decimal("0"),
    )
    if not closed_trade_net_pnls_usd:
        profit_factor = None
        profit_factor_status = MetricStatus.NO_CLOSED_TRADES
        expectancy = None
        expectancy_status = MetricStatus.NO_CLOSED_TRADES
    else:
        expectancy = _canonical_zero(
            sum(closed_trade_net_pnls_usd, Decimal("0")) / Decimal(len(closed_trade_net_pnls_usd))
        )
        expectancy_status = MetricStatus.AVAILABLE
        if gross_loss == 0:
            profit_factor = None
            profit_factor_status = MetricStatus.NO_LOSING_CLOSED_TRADES
        else:
            profit_factor = _canonical_zero(gross_profit / gross_loss)
            profit_factor_status = MetricStatus.AVAILABLE

    if starting_cash_usd == 0:
        fee_drag = None
        fee_drag_status = MetricStatus.ZERO_STARTING_CASH
    else:
        fee_drag = _canonical_zero(
            (cumulative_explicit_fees_usd / starting_cash_usd) * Decimal("100")
        )
        fee_drag_status = MetricStatus.AVAILABLE

    return PortfolioStatistics(
        portfolio=portfolio,
        observation_count=len(observations),
        return_observation_count=len(return_series.values),
        period_seconds=period_seconds,
        periods_per_year=periods_per_year,
        risk_free_rate_annual_pct_assumption=Decimal("0"),
        gross_executed_notional_usd=gross_executed_notional,
        average_net_liquidation_value_usd=average_nlv,
        turnover_ratio=_canonical_optional_zero(turnover),
        turnover_status=turnover_status,
        exposed_observation_count=exposed_count,
        exposure_time_pct=_canonical_optional_zero(exposure_time_pct),
        exposure_status=exposure_status,
        max_drawdown_usd=max_drawdown,
        max_drawdown_pct=max_drawdown_pct,
        max_drawdown_duration_bars=max_duration,
        max_drawdown_duration_seconds=max_duration * period_seconds,
        current_drawdown_duration_bars=current_duration,
        current_drawdown_duration_seconds=current_duration * period_seconds,
        mean_period_simple_return=_canonical_optional_zero(mean_return),
        sample_period_volatility=_canonical_optional_zero(sample_volatility),
        annualized_volatility=_canonical_optional_zero(annualized_volatility),
        volatility_status=volatility_status,
        annualized_sharpe_ratio=_canonical_optional_zero(sharpe),
        sharpe_status=sharpe_status,
        downside_deviation_per_period=_canonical_optional_zero(downside_deviation),
        annualized_sortino_ratio=_canonical_optional_zero(sortino),
        sortino_status=sortino_status,
        closed_trade_count=len(closed_trade_net_pnls_usd),
        gross_closed_trade_profit_usd=gross_profit,
        gross_closed_trade_loss_usd=gross_loss,
        profit_factor=_canonical_optional_zero(profit_factor),
        profit_factor_status=profit_factor_status,
        expectancy_per_closed_trade_usd=_canonical_optional_zero(expectancy),
        expectancy_status=expectancy_status,
        cumulative_explicit_fees_usd=cumulative_explicit_fees_usd,
        explicit_fee_drag_return_percentage_points=_canonical_optional_zero(fee_drag),
        fee_drag_status=fee_drag_status,
    )


def calculate_benchmark_alpha(
    *,
    benchmark: BenchmarkKind,
    starting_cash_usd: Decimal,
    strategy_observations: tuple[PerformanceObservation, ...],
    benchmark_observations: tuple[PerformanceObservation, ...],
    periods_per_year: int = CRYPTO_HOURLY_PERIODS_PER_YEAR,
) -> BenchmarkAlpha:
    """Estimate annualized OLS intercept with exact aligned simple returns."""
    if periods_per_year <= 0:
        raise PerformanceAnalysisError("alpha annualization periods must be positive")
    _validate_observations(starting_cash_usd, strategy_observations)
    _validate_observations(starting_cash_usd, benchmark_observations)
    if len(strategy_observations) != len(benchmark_observations):
        raise PerformanceAnalysisError("alpha observations are not aligned")
    if tuple(item.ts for item in strategy_observations) != tuple(
        item.ts for item in benchmark_observations
    ):
        raise PerformanceAnalysisError("alpha timestamps are not aligned")
    strategy_returns = _returns(starting_cash_usd, strategy_observations)
    benchmark_returns = _returns(starting_cash_usd, benchmark_observations)
    method = "ols_intercept_strategy_returns_on_benchmark_returns"
    if (
        strategy_returns.status != MetricStatus.AVAILABLE
        or benchmark_returns.status != MetricStatus.AVAILABLE
    ):
        status = (
            strategy_returns.status
            if strategy_returns.status != MetricStatus.AVAILABLE
            else benchmark_returns.status
        )
        return BenchmarkAlpha(
            benchmark=benchmark,
            observation_count=0,
            method=method,
            periods_per_year=periods_per_year,
            strategy_mean_period_return=None,
            benchmark_mean_period_return=None,
            annualized_active_return_difference=None,
            beta=None,
            alpha_per_period=None,
            annualized_alpha=None,
            status=status,
        )
    if len(strategy_returns.values) < 2:
        return BenchmarkAlpha(
            benchmark=benchmark,
            observation_count=len(strategy_returns.values),
            method=method,
            periods_per_year=periods_per_year,
            strategy_mean_period_return=_mean(strategy_returns.values),
            benchmark_mean_period_return=_mean(benchmark_returns.values),
            annualized_active_return_difference=None,
            beta=None,
            alpha_per_period=None,
            annualized_alpha=None,
            status=MetricStatus.INSUFFICIENT_OBSERVATIONS,
        )

    strategy_mean = _mean(strategy_returns.values)
    benchmark_mean = _mean(benchmark_returns.values)
    annualized_active = (strategy_mean - benchmark_mean) * Decimal(periods_per_year)
    benchmark_variance_sum = sum(
        ((value - benchmark_mean) ** 2 for value in benchmark_returns.values),
        Decimal("0"),
    )
    if benchmark_variance_sum == 0:
        return BenchmarkAlpha(
            benchmark=benchmark,
            observation_count=len(strategy_returns.values),
            method=method,
            periods_per_year=periods_per_year,
            strategy_mean_period_return=strategy_mean,
            benchmark_mean_period_return=benchmark_mean,
            annualized_active_return_difference=annualized_active,
            beta=None,
            alpha_per_period=None,
            annualized_alpha=None,
            status=MetricStatus.ZERO_BENCHMARK_VARIANCE,
        )
    covariance_sum = sum(
        (
            (strategy - strategy_mean) * (passive - benchmark_mean)
            for strategy, passive in zip(
                strategy_returns.values,
                benchmark_returns.values,
                strict=True,
            )
        ),
        Decimal("0"),
    )
    beta = covariance_sum / benchmark_variance_sum
    alpha_per_period = strategy_mean - beta * benchmark_mean
    return BenchmarkAlpha(
        benchmark=benchmark,
        observation_count=len(strategy_returns.values),
        method=method,
        periods_per_year=periods_per_year,
        strategy_mean_period_return=strategy_mean,
        benchmark_mean_period_return=benchmark_mean,
        annualized_active_return_difference=annualized_active,
        beta=beta,
        alpha_per_period=alpha_per_period,
        annualized_alpha=alpha_per_period * Decimal(periods_per_year),
        status=MetricStatus.AVAILABLE,
    )


def analyze_research_performance(
    *,
    starting_cash_usd: Decimal,
    strategy_observations: tuple[PerformanceObservation, ...],
    strategy_fills: tuple[Fill, ...],
    strategy_closed_trade_net_pnls_usd: tuple[Decimal, ...],
    strategy_cumulative_explicit_fees_usd: Decimal,
    benchmarks: BenchmarkAnalysis,
) -> ResearchPerformanceAnalysis:
    """Calculate strategy/benchmark statistics and aligned benchmark alpha."""
    portfolio_statistics = [
        calculate_portfolio_statistics(
            portfolio="strategy",
            starting_cash_usd=starting_cash_usd,
            observations=strategy_observations,
            fills=strategy_fills,
            closed_trade_net_pnls_usd=strategy_closed_trade_net_pnls_usd,
            cumulative_explicit_fees_usd=strategy_cumulative_explicit_fees_usd,
        )
    ]
    alpha_results: list[BenchmarkAlpha] = []
    for benchmark in benchmarks.results:
        observations = tuple(
            PerformanceObservation(
                ts=point.ts,
                net_liquidation_value_usd=point.net_liquidation_value_usd,
                inventory_btc=point.inventory_btc,
            )
            for point in benchmark.equity_curve
        )
        closed_trade_pnls = (
            (benchmark.net_pnl_after_fees_usd,) if benchmark.sell_execution_count > 0 else ()
        )
        portfolio_statistics.append(
            calculate_portfolio_statistics(
                portfolio=benchmark.kind.value,
                starting_cash_usd=starting_cash_usd,
                observations=observations,
                fills=benchmark.fills,
                closed_trade_net_pnls_usd=closed_trade_pnls,
                cumulative_explicit_fees_usd=benchmark.cumulative_fees_usd,
            )
        )
        alpha_results.append(
            calculate_benchmark_alpha(
                benchmark=benchmark.kind,
                starting_cash_usd=starting_cash_usd,
                strategy_observations=strategy_observations,
                benchmark_observations=observations,
            )
        )

    return ResearchPerformanceAnalysis(
        strategy_observations=strategy_observations,
        portfolios=tuple(portfolio_statistics),
        benchmark_alphas=tuple(alpha_results),
    )
