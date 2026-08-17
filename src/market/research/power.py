"""Synthetic-only design assurance for the preregistered G3 EMA study.

The generator has an explicit variance decomposition and the scoring replay mirrors the
power-relevant parts of the frozen walk-forward contract. It is intentionally independent of the
G1 market dataset: importing or running this module never loads research candles.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HOURS_PER_YEAR = 365 * 24
BPS_PER_UNIT = 10_000.0

FAST_EMA_CANDIDATES = (6, 12, 18, 24, 36, 48, 72)
SLOW_EMA_CANDIDATES = (18, 26, 36, 48, 72, 96, 120, 168, 240, 336)
EMA_PAIRS = tuple(
    (fast, slow)
    for fast in FAST_EMA_CANDIDATES
    for slow in SLOW_EMA_CANDIDATES
    if 1.5 <= slow / fast <= 8
)
if len(EMA_PAIRS) != 36:  # pragma: no cover - import-time contract guard
    raise RuntimeError("the preregistered EMA family must contain exactly 36 pairs")


class SyntheticCalibrationError(ValueError):
    """Raised before power is reported when a synthetic run is not calibrated."""


def _require_finite(value: float, name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class SyntheticSignalConfig:
    """A stationary latent-momentum model with unit-normalized innovation variance.

    ``signal_variance_fraction`` is the unconditional fraction of hourly log-return variance
    supplied by the persistent latent state. The independent residual supplies the balance, so
    total model variance remains ``annual_volatility ** 2 / HOURS_PER_YEAR`` as signal strength
    changes.
    """

    bars: int
    annual_volatility: float
    signal_variance_fraction: float
    signal_half_life_hours: float
    seed: int
    starting_price_usd: float = 63_000.0
    innovation_degrees_of_freedom: float | None = 5.0
    burn_in_bars: int = 3_360
    calibration_relative_tolerance: float = 0.08
    latent_variance_relative_tolerance: float = 0.35
    maximum_absolute_hourly_log_return: float = 0.50
    maximum_price_multiple: float = 1_000.0

    def __post_init__(self) -> None:
        if isinstance(self.bars, bool) or not isinstance(self.bars, int) or self.bars < 2:
            raise ValueError("bars must be an integer >= 2")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        if (
            isinstance(self.burn_in_bars, bool)
            or not isinstance(self.burn_in_bars, int)
            or self.burn_in_bars < 0
        ):
            raise ValueError("burn_in_bars must be a nonnegative integer")
        for name in (
            "annual_volatility",
            "signal_variance_fraction",
            "signal_half_life_hours",
            "starting_price_usd",
            "calibration_relative_tolerance",
            "latent_variance_relative_tolerance",
            "maximum_absolute_hourly_log_return",
            "maximum_price_multiple",
        ):
            _require_finite(float(getattr(self, name)), name)
        if self.annual_volatility <= 0:
            raise ValueError("annual_volatility must be > 0")
        if not 0 <= self.signal_variance_fraction < 1:
            raise ValueError("signal_variance_fraction must be in [0, 1)")
        if self.signal_half_life_hours <= 0:
            raise ValueError("signal_half_life_hours must be > 0")
        if self.starting_price_usd <= 0:
            raise ValueError("starting_price_usd must be > 0")
        if not 0 < self.calibration_relative_tolerance < 1:
            raise ValueError("calibration_relative_tolerance must be in (0, 1)")
        if not 0 < self.latent_variance_relative_tolerance < 1:
            raise ValueError("latent_variance_relative_tolerance must be in (0, 1)")
        if self.maximum_absolute_hourly_log_return <= 0:
            raise ValueError("maximum_absolute_hourly_log_return must be > 0")
        if self.maximum_price_multiple <= 1:
            raise ValueError("maximum_price_multiple must be > 1")
        if self.innovation_degrees_of_freedom is not None:
            _require_finite(
                self.innovation_degrees_of_freedom,
                "innovation_degrees_of_freedom",
            )
            if self.innovation_degrees_of_freedom <= 2:
                raise ValueError("innovation_degrees_of_freedom must be > 2 or None")

    @property
    def hourly_volatility(self) -> float:
        return self.annual_volatility / math.sqrt(HOURS_PER_YEAR)

    @property
    def latent_autocorrelation(self) -> float:
        return 0.5 ** (1.0 / self.signal_half_life_hours)


@dataclass(frozen=True)
class CalibrationDiagnostics:
    expected_annual_volatility: float
    realized_annual_volatility: float
    annual_volatility_relative_error: float
    latent_mean: float
    latent_variance: float
    latent_variance_relative_error: float
    maximum_absolute_hourly_log_return: float
    minimum_price_usd: float
    maximum_price_usd: float
    price_multiple: float


@dataclass(frozen=True)
class SyntheticPath:
    opens_usd: tuple[float, ...]
    closes_usd: tuple[float, ...]
    hourly_log_returns: tuple[float, ...]
    latent_states: tuple[float, ...]
    diagnostics: CalibrationDiagnostics


def _sample_variance(values: Sequence[float]) -> float:
    if len(values) < 2:
        raise ValueError("sample variance requires at least two observations")
    return statistics.variance(values)


def _standardized_innovation(
    generator: random.Random,
    degrees_of_freedom: float | None,
) -> float:
    if degrees_of_freedom is None:
        return generator.gauss(0.0, 1.0)
    chi_squared = generator.gammavariate(degrees_of_freedom / 2.0, 2.0)
    student_t = generator.gauss(0.0, 1.0) / math.sqrt(chi_squared / degrees_of_freedom)
    return student_t * math.sqrt((degrees_of_freedom - 2.0) / degrees_of_freedom)


def generate_variance_normalized_path(config: SyntheticSignalConfig) -> SyntheticPath:
    """Generate and validate one synthetic OHLC-lite price path.

    Opens equal the prior close, which makes the next-bar-open timing explicit without injecting
    an uncalibrated overnight component. The returned path is not released when a calibration
    check fails.
    """

    generator = random.Random(config.seed)
    rho = config.latent_autocorrelation
    state_innovation_scale = math.sqrt(1.0 - rho * rho)
    signal_scale = math.sqrt(config.signal_variance_fraction)
    residual_scale = math.sqrt(1.0 - config.signal_variance_fraction)

    latent_state = 0.0
    for _ in range(config.burn_in_bars):
        latent_state = rho * latent_state + state_innovation_scale * _standardized_innovation(
            generator,
            config.innovation_degrees_of_freedom,
        )

    opens: list[float] = []
    closes: list[float] = []
    log_returns: list[float] = []
    latent_states: list[float] = []
    price = config.starting_price_usd
    for _ in range(config.bars):
        residual = _standardized_innovation(
            generator,
            config.innovation_degrees_of_freedom,
        )
        log_return = config.hourly_volatility * (
            signal_scale * latent_state + residual_scale * residual
        )
        next_price = price * math.exp(log_return)
        if not math.isfinite(next_price) or next_price <= 0:
            raise SyntheticCalibrationError("synthetic price became nonfinite or nonpositive")
        opens.append(price)
        closes.append(next_price)
        log_returns.append(log_return)
        latent_states.append(latent_state)
        price = next_price
        latent_state = rho * latent_state + state_innovation_scale * _standardized_innovation(
            generator,
            config.innovation_degrees_of_freedom,
        )

    realized_annual_volatility = math.sqrt(_sample_variance(log_returns) * HOURS_PER_YEAR)
    annual_volatility_error = abs(realized_annual_volatility / config.annual_volatility - 1.0)
    latent_variance = _sample_variance(latent_states)
    latent_variance_error = abs(latent_variance - 1.0)
    maximum_absolute_return = max(abs(value) for value in log_returns)
    minimum_price = min(min(opens), closes[-1])
    maximum_price = max(max(opens), closes[-1])
    price_multiple = maximum_price / minimum_price
    diagnostics = CalibrationDiagnostics(
        expected_annual_volatility=config.annual_volatility,
        realized_annual_volatility=realized_annual_volatility,
        annual_volatility_relative_error=annual_volatility_error,
        latent_mean=statistics.fmean(latent_states),
        latent_variance=latent_variance,
        latent_variance_relative_error=latent_variance_error,
        maximum_absolute_hourly_log_return=maximum_absolute_return,
        minimum_price_usd=minimum_price,
        maximum_price_usd=maximum_price,
        price_multiple=price_multiple,
    )
    calibration_failures: list[str] = []
    if annual_volatility_error > config.calibration_relative_tolerance:
        calibration_failures.append("annual volatility outside tolerance")
    if latent_variance_error > config.latent_variance_relative_tolerance:
        calibration_failures.append("latent variance outside tolerance")
    if maximum_absolute_return > config.maximum_absolute_hourly_log_return:
        calibration_failures.append("hourly log return exceeds hard bound")
    if price_multiple > config.maximum_price_multiple:
        calibration_failures.append("price multiple exceeds hard bound")
    if calibration_failures:
        raise SyntheticCalibrationError("; ".join(calibration_failures))
    return SyntheticPath(
        opens_usd=tuple(opens),
        closes_usd=tuple(closes),
        hourly_log_returns=tuple(log_returns),
        latent_states=tuple(latent_states),
        diagnostics=diagnostics,
    )


@dataclass(frozen=True)
class CostScenario:
    name: str
    full_spread_bps: float
    adverse_slippage_bps: float
    fee_bps_per_fill: float

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("cost scenario name is required")
        for name in ("full_spread_bps", "adverse_slippage_bps", "fee_bps_per_fill"):
            value = float(getattr(self, name))
            _require_finite(value, name)
            if not 0 <= value < BPS_PER_UNIT:
                raise ValueError(f"{name} must be in [0, 10000)")

    @property
    def buy_fill_multiplier(self) -> float:
        return (1.0 + self.full_spread_bps / (2.0 * BPS_PER_UNIT)) * (
            1.0 + self.adverse_slippage_bps / BPS_PER_UNIT
        )

    @property
    def sell_fill_multiplier(self) -> float:
        return (1.0 - self.full_spread_bps / (2.0 * BPS_PER_UNIT)) * (
            1.0 - self.adverse_slippage_bps / BPS_PER_UNIT
        )

    @property
    def fee_rate(self) -> float:
        return self.fee_bps_per_fill / BPS_PER_UNIT

    @property
    def flat_price_round_trip_drag_bps(self) -> float:
        entry_notional = self.buy_fill_multiplier
        net_pnl = self.sell_fill_multiplier * (1.0 - self.fee_rate) - entry_notional * (
            1.0 + self.fee_rate
        )
        return -net_pnl / entry_notional * BPS_PER_UNIT


PRIMARY_COSTS = CostScenario(
    name="primary",
    full_spread_bps=20.0,
    adverse_slippage_bps=10.0,
    fee_bps_per_fill=95.0,
)
DOUBLED_COSTS = CostScenario(
    name="doubled",
    full_spread_bps=40.0,
    adverse_slippage_bps=20.0,
    fee_bps_per_fill=190.0,
)
FRICTIONLESS_COSTS = CostScenario(
    name="frictionless",
    full_spread_bps=0.0,
    adverse_slippage_bps=0.0,
    fee_bps_per_fill=0.0,
)


def ema_series(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    """Float implementation of the production strategy's SMA-seeded EMA definition."""

    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError("period must be a positive integer")
    output: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(output)
    previous = math.fsum(values[:period]) / period
    output[period - 1] = previous
    multiplier = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        previous = (values[index] - previous) * multiplier + previous
        output[index] = previous
    return tuple(output)


@dataclass(frozen=True)
class SyntheticTrade:
    entry_index: int
    exit_index: int
    holding_bars: int
    quantity_btc: float
    entry_fill_price_usd: float
    exit_fill_price_usd: float
    net_pnl_usd: float
    net_return_bps: float
    terminal_liquidation: bool


@dataclass(frozen=True)
class PathScore:
    fast_ema: int
    slow_ema: int
    cost_scenario: str
    score_start: int
    score_end: int
    trades: tuple[SyntheticTrade, ...]
    aggregate_net_pnl_usd: float
    mean_net_trade_return_bps: float | None
    maximum_account_drawdown_usd: float

    @property
    def closed_trade_count(self) -> int:
        return len(self.trades)


def _score_with_emas(
    *,
    opens_usd: Sequence[float],
    closes_usd: Sequence[float],
    fast_values: Sequence[float | None],
    slow_values: Sequence[float | None],
    fast_ema: int,
    slow_ema: int,
    cost_scenario: CostScenario,
    score_start: int,
    score_end: int,
    value_offset: int,
    starting_cash_usd: float,
    requested_quantity_btc: float,
    maximum_order_notional_usd: float,
) -> PathScore:
    cash = starting_cash_usd
    peak_nlv = starting_cash_usd
    maximum_drawdown = 0.0
    quantity = 0.0
    entry_index: int | None = None
    entry_fill_price = 0.0
    entry_fee = 0.0
    pending_side: str | None = None
    pending_quantity = 0.0
    trades: list[SyntheticTrade] = []

    def close_position(index: int, reference_price: float, terminal: bool) -> None:
        nonlocal cash, quantity, entry_index, entry_fill_price, entry_fee
        if quantity <= 0 or entry_index is None:
            raise RuntimeError("synthetic sell attempted while flat")
        exit_fill_price = reference_price * cost_scenario.sell_fill_multiplier
        exit_notional = quantity * exit_fill_price
        exit_fee = exit_notional * cost_scenario.fee_rate
        entry_notional = quantity * entry_fill_price
        net_pnl = exit_notional - exit_fee - entry_notional - entry_fee
        cash += exit_notional - exit_fee
        trades.append(
            SyntheticTrade(
                entry_index=entry_index,
                exit_index=index,
                holding_bars=index - entry_index,
                quantity_btc=quantity,
                entry_fill_price_usd=entry_fill_price,
                exit_fill_price_usd=exit_fill_price,
                net_pnl_usd=net_pnl,
                net_return_bps=net_pnl / entry_notional * BPS_PER_UNIT,
                terminal_liquidation=terminal,
            )
        )
        quantity = 0.0
        entry_index = None
        entry_fill_price = 0.0
        entry_fee = 0.0

    for index in range(score_start, score_end):
        if pending_side == "buy":
            entry_fill_price = opens_usd[index] * cost_scenario.buy_fill_multiplier
            entry_notional = pending_quantity * entry_fill_price
            entry_fee = entry_notional * cost_scenario.fee_rate
            if entry_notional + entry_fee > cash + 1e-9:
                raise SyntheticCalibrationError("synthetic entry is unaffordable")
            cash -= entry_notional + entry_fee
            quantity = pending_quantity
            entry_index = index
        elif pending_side == "sell":
            close_position(index, opens_usd[index], False)
        pending_side = None
        pending_quantity = 0.0

        if quantity > 0:
            estimated_exit_fill = closes_usd[index] * cost_scenario.sell_fill_multiplier
            estimated_exit_notional = quantity * estimated_exit_fill
            nlv = cash + estimated_exit_notional * (1.0 - cost_scenario.fee_rate)
        else:
            nlv = cash
        peak_nlv = max(peak_nlv, nlv)
        maximum_drawdown = max(maximum_drawdown, peak_nlv - nlv)

        local_index = index - value_offset
        if local_index <= 0:
            continue
        fast_previous = fast_values[local_index - 1]
        slow_previous = slow_values[local_index - 1]
        fast_current = fast_values[local_index]
        slow_current = slow_values[local_index]
        if (
            fast_previous is None
            or slow_previous is None
            or fast_current is None
            or slow_current is None
        ):
            continue
        bullish_cross = fast_previous <= slow_previous and fast_current > slow_current
        bearish_cross = fast_previous >= slow_previous and fast_current < slow_current
        if bullish_cross and quantity == 0:
            pending_side = "buy"
            pending_quantity = min(
                requested_quantity_btc,
                maximum_order_notional_usd / closes_usd[index],
            )
        elif bearish_cross and quantity > 0:
            pending_side = "sell"

    # A decision on the last close has no eligible next bar and expires. Existing inventory is
    # liquidated against the last close using the same directional cost model as the engine.
    if quantity > 0:
        close_position(score_end - 1, closes_usd[score_end - 1], True)
        peak_nlv = max(peak_nlv, cash)
        maximum_drawdown = max(maximum_drawdown, peak_nlv - cash)

    aggregate_pnl = cash - starting_cash_usd
    mean_return = statistics.fmean(trade.net_return_bps for trade in trades) if trades else None
    return PathScore(
        fast_ema=fast_ema,
        slow_ema=slow_ema,
        cost_scenario=cost_scenario.name,
        score_start=score_start,
        score_end=score_end,
        trades=tuple(trades),
        aggregate_net_pnl_usd=aggregate_pnl,
        mean_net_trade_return_bps=mean_return,
        maximum_account_drawdown_usd=maximum_drawdown,
    )


def score_ema_path(
    *,
    opens_usd: Sequence[float],
    closes_usd: Sequence[float],
    fast_ema: int,
    slow_ema: int,
    cost_scenario: CostScenario,
    score_start: int = 0,
    score_end: int | None = None,
    indicator_context_bars: int = 338,
    starting_cash_usd: float = 1_000.0,
    requested_quantity_btc: float = 0.001,
    maximum_order_notional_usd: float = 150.0,
) -> PathScore:
    """Score one EMA pair with flat reset, next-open fills, and terminal liquidation."""

    if len(opens_usd) != len(closes_usd):
        raise ValueError("opens_usd and closes_usd must have equal length")
    score_end = len(closes_usd) if score_end is None else score_end
    if not 0 <= score_start < score_end <= len(closes_usd):
        raise ValueError("score range must be nonempty and within the price path")
    if not 0 <= indicator_context_bars:
        raise ValueError("indicator_context_bars must be >= 0")
    if fast_ema <= 0 or slow_ema <= fast_ema:
        raise ValueError("EMA periods must satisfy 0 < fast_ema < slow_ema")
    for name, values in (("opens_usd", opens_usd), ("closes_usd", closes_usd)):
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise SyntheticCalibrationError(f"{name} contains a nonfinite or nonpositive price")
    for name, value in (
        ("starting_cash_usd", starting_cash_usd),
        ("requested_quantity_btc", requested_quantity_btc),
        ("maximum_order_notional_usd", maximum_order_notional_usd),
    ):
        _require_finite(value, name)
        if value <= 0:
            raise ValueError(f"{name} must be > 0")

    context_start = max(0, score_start - indicator_context_bars)
    context_closes = closes_usd[context_start:score_end]
    return _score_with_emas(
        opens_usd=opens_usd,
        closes_usd=closes_usd,
        fast_values=ema_series(context_closes, fast_ema),
        slow_values=ema_series(context_closes, slow_ema),
        fast_ema=fast_ema,
        slow_ema=slow_ema,
        cost_scenario=cost_scenario,
        score_start=score_start,
        score_end=score_end,
        value_offset=context_start,
        starting_cash_usd=starting_cash_usd,
        requested_quantity_btc=requested_quantity_btc,
        maximum_order_notional_usd=maximum_order_notional_usd,
    )


@dataclass(frozen=True)
class FoldLayout:
    fold_id: str
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int


def load_fold_layouts(split_plan_path: str | Path) -> tuple[FoldLayout, ...]:
    """Read only the already-public development fold lengths from a frozen split plan."""

    payload = json.loads(Path(split_plan_path).read_text(encoding="utf-8"))
    layouts: list[FoldLayout] = []
    for fold in payload["folds"]:
        validation_start = int(fold["train"]["expected_bars"])
        validation_end = validation_start + int(fold["validation"]["expected_bars"])
        test_start = validation_end
        test_end = test_start + int(fold["test"]["expected_bars"])
        layouts.append(
            FoldLayout(
                fold_id=str(fold["fold_id"]),
                validation_start=validation_start,
                validation_end=validation_end,
                test_start=test_start,
                test_end=test_end,
            )
        )
    if not layouts or layouts[-1].test_end != int(payload["development"]["expected_bars"]):
        raise ValueError("fold lengths do not terminate at the development bar count")
    return tuple(layouts)


@dataclass(frozen=True)
class FoldPowerScore:
    fold_id: str
    selected_fast_ema: int
    selected_slow_ema: int
    validation_mean_net_trade_return_bps: float
    primary_test: PathScore
    doubled_test: PathScore


@dataclass(frozen=True)
class WalkForwardPowerScore:
    folds: tuple[FoldPowerScore, ...]

    @property
    def primary_trades(self) -> tuple[SyntheticTrade, ...]:
        return tuple(trade for fold in self.folds for trade in fold.primary_test.trades)

    @property
    def doubled_trades(self) -> tuple[SyntheticTrade, ...]:
        return tuple(trade for fold in self.folds for trade in fold.doubled_test.trades)

    @property
    def primary_aggregate_net_pnl_usd(self) -> float:
        return math.fsum(fold.primary_test.aggregate_net_pnl_usd for fold in self.folds)

    @property
    def doubled_aggregate_net_pnl_usd(self) -> float:
        return math.fsum(fold.doubled_test.aggregate_net_pnl_usd for fold in self.folds)

    @property
    def positive_primary_fold_count(self) -> int:
        return sum(fold.primary_test.aggregate_net_pnl_usd > 0 for fold in self.folds)


def _selection_key(score: PathScore) -> tuple[float, float, int, int]:
    mean = score.mean_net_trade_return_bps
    return (
        mean if mean is not None else -math.inf,
        -score.maximum_account_drawdown_usd,
        -score.fast_ema,
        -score.slow_ema,
    )


def evaluate_walk_forward_power(
    path: SyntheticPath,
    layouts: Sequence[FoldLayout],
    *,
    pairs: Sequence[tuple[int, int]] = EMA_PAIRS,
    indicator_context_bars: int = 338,
) -> WalkForwardPowerScore:
    """Select on each validation window and score the following test under two cost profiles."""

    if not layouts:
        raise ValueError("at least one fold layout is required")
    if max(layout.test_end for layout in layouts) > len(path.closes_usd):
        raise ValueError("synthetic path is shorter than the requested fold layout")
    if not pairs:
        raise ValueError("at least one EMA pair is required")

    fold_scores: list[FoldPowerScore] = []
    for layout in layouts:
        validation_context_start = max(
            0,
            layout.validation_start - indicator_context_bars,
        )
        validation_closes = path.closes_usd[validation_context_start : layout.validation_end]
        validation_emas = {
            period: ema_series(validation_closes, period)
            for period in sorted({period for pair in pairs for period in pair})
        }
        validation_scores = [
            _score_with_emas(
                opens_usd=path.opens_usd,
                closes_usd=path.closes_usd,
                fast_values=validation_emas[fast],
                slow_values=validation_emas[slow],
                fast_ema=fast,
                slow_ema=slow,
                cost_scenario=PRIMARY_COSTS,
                score_start=layout.validation_start,
                score_end=layout.validation_end,
                value_offset=validation_context_start,
                starting_cash_usd=1_000.0,
                requested_quantity_btc=0.001,
                maximum_order_notional_usd=150.0,
            )
            for fast, slow in pairs
        ]
        selected = max(validation_scores, key=_selection_key)
        if selected.mean_net_trade_return_bps is None:
            raise SyntheticCalibrationError(
                f"{layout.fold_id} produced no validation trade for any candidate"
            )
        test_context_start = max(0, layout.test_start - indicator_context_bars)
        test_closes = path.closes_usd[test_context_start : layout.test_end]
        test_fast_values = ema_series(test_closes, selected.fast_ema)
        test_slow_values = ema_series(test_closes, selected.slow_ema)
        score_arguments: dict[str, Any] = {
            "opens_usd": path.opens_usd,
            "closes_usd": path.closes_usd,
            "fast_values": test_fast_values,
            "slow_values": test_slow_values,
            "fast_ema": selected.fast_ema,
            "slow_ema": selected.slow_ema,
            "score_start": layout.test_start,
            "score_end": layout.test_end,
            "value_offset": test_context_start,
            "starting_cash_usd": 1_000.0,
            "requested_quantity_btc": 0.001,
            "maximum_order_notional_usd": 150.0,
        }
        primary_test = _score_with_emas(cost_scenario=PRIMARY_COSTS, **score_arguments)
        doubled_test = _score_with_emas(cost_scenario=DOUBLED_COSTS, **score_arguments)
        fold_scores.append(
            FoldPowerScore(
                fold_id=layout.fold_id,
                selected_fast_ema=selected.fast_ema,
                selected_slow_ema=selected.slow_ema,
                validation_mean_net_trade_return_bps=selected.mean_net_trade_return_bps,
                primary_test=primary_test,
                doubled_test=doubled_test,
            )
        )
    return WalkForwardPowerScore(folds=tuple(fold_scores))


def one_sided_moving_block_lower_bound(
    values: Sequence[float],
    *,
    block_length: int,
    resamples: int,
    alpha: float,
    seed: int,
) -> float | None:
    """Percentile lower bound used only as a labeled design-screen approximation."""

    if len(values) < 2:
        return None
    if block_length <= 0 or block_length > len(values):
        raise ValueError("block_length must be in [1, len(values)]")
    if resamples < 99:
        raise ValueError("resamples must be >= 99")
    if not 0 < alpha < 0.5:
        raise ValueError("alpha must be in (0, 0.5)")
    generator = random.Random(seed)
    starts = range(len(values) - block_length + 1)
    bootstrap_means: list[float] = []
    for _ in range(resamples):
        sample: list[float] = []
        while len(sample) < len(values):
            start = generator.choice(starts)
            sample.extend(values[start : start + block_length])
        bootstrap_means.append(statistics.fmean(sample[: len(values)]))
    bootstrap_means.sort()
    rank = max(0, math.ceil(alpha * resamples) - 1)
    return bootstrap_means[rank]


@dataclass(frozen=True)
class DesignScreen:
    """A declared subset of protocol criteria; never a full G3 pass decision."""

    lower_bound_bps: float | None
    primary_mean_net_trade_return_bps: float | None
    doubled_mean_net_trade_return_bps: float | None
    primary_trade_sd_bps: float | None
    primary_trade_count: int
    positive_primary_fold_count: int
    criterion_1_positive_primary: bool
    criterion_2_lower_bound_above_10_bps: bool
    criterion_3_positive_doubled: bool
    criterion_4_seventy_percent_positive_folds: bool
    criterion_5_at_least_100_trades: bool
    criterion_6_no_profit_concentration: bool
    criterion_8_risk_limits: bool
    included_criteria_pass: bool


def _profit_concentration_pass(score: WalkForwardPowerScore) -> bool:
    total_profit = score.primary_aggregate_net_pnl_usd
    if total_profit <= 0:
        return False
    largest_trade = max((trade.net_pnl_usd for trade in score.primary_trades), default=0.0)
    largest_fold = max(fold.primary_test.aggregate_net_pnl_usd for fold in score.folds)
    return largest_trade <= 0.5 * total_profit and largest_fold <= 0.5 * total_profit


def design_screen(
    score: WalkForwardPowerScore,
    *,
    bootstrap_block_length: int = 5,
    bootstrap_resamples: int = 499,
    bootstrap_seed: int = 0,
    maximum_trade_sd_bps: float = 30_000.0,
) -> DesignScreen:
    """Evaluate the criteria representable without market data, benchmarks, or multiplicity."""

    primary_returns = tuple(trade.net_return_bps for trade in score.primary_trades)
    doubled_returns = tuple(trade.net_return_bps for trade in score.doubled_trades)
    primary_mean = statistics.fmean(primary_returns) if primary_returns else None
    doubled_mean = statistics.fmean(doubled_returns) if doubled_returns else None
    primary_sd = statistics.stdev(primary_returns) if len(primary_returns) >= 2 else None
    if primary_sd is not None and (
        not math.isfinite(primary_sd) or primary_sd > maximum_trade_sd_bps
    ):
        raise SyntheticCalibrationError(
            "primary per-trade standard deviation exceeds the fail-closed design bound"
        )
    lower_bound = (
        one_sided_moving_block_lower_bound(
            primary_returns,
            block_length=min(bootstrap_block_length, len(primary_returns)),
            resamples=bootstrap_resamples,
            alpha=0.05,
            seed=bootstrap_seed,
        )
        if primary_returns
        else None
    )
    criterion_1 = (
        score.primary_aggregate_net_pnl_usd > 0 and primary_mean is not None and primary_mean > 0
    )
    criterion_2 = lower_bound is not None and lower_bound > 10.0
    criterion_3 = (
        score.doubled_aggregate_net_pnl_usd > 0 and doubled_mean is not None and doubled_mean > 0
    )
    required_positive_folds = math.ceil(0.70 * len(score.folds))
    criterion_4 = score.positive_primary_fold_count >= required_positive_folds
    criterion_5 = len(primary_returns) >= 100
    criterion_6 = _profit_concentration_pass(score)
    expected_shortfall_95 = (
        statistics.fmean(sorted(primary_returns)[: max(1, math.ceil(0.05 * len(primary_returns)))])
        if primary_returns
        else -math.inf
    )
    worst_trade = min(primary_returns, default=-math.inf)
    criterion_8 = (
        max(fold.primary_test.maximum_account_drawdown_usd for fold in score.folds) <= 100.0
        and expected_shortfall_95 >= -1_000.0
        and worst_trade >= -2_000.0
    )
    criteria = (
        criterion_1,
        criterion_2,
        criterion_3,
        criterion_4,
        criterion_5,
        criterion_6,
        criterion_8,
    )
    return DesignScreen(
        lower_bound_bps=lower_bound,
        primary_mean_net_trade_return_bps=primary_mean,
        doubled_mean_net_trade_return_bps=doubled_mean,
        primary_trade_sd_bps=primary_sd,
        primary_trade_count=len(primary_returns),
        positive_primary_fold_count=score.positive_primary_fold_count,
        criterion_1_positive_primary=criterion_1,
        criterion_2_lower_bound_above_10_bps=criterion_2,
        criterion_3_positive_doubled=criterion_3,
        criterion_4_seventy_percent_positive_folds=criterion_4,
        criterion_5_at_least_100_trades=criterion_5,
        criterion_6_no_profit_concentration=criterion_6,
        criterion_8_risk_limits=criterion_8,
        included_criteria_pass=all(criteria),
    )


@dataclass(frozen=True)
class PowerScenario:
    name: str
    annual_volatility: float
    signal_variance_fraction: float
    signal_half_life_hours: float
    innovation_degrees_of_freedom: float | None = 5.0


@dataclass(frozen=True)
class PowerStudyConfig:
    simulations_per_scenario: int = 40
    base_seed: int = 31_415
    bootstrap_block_length: int = 5
    bootstrap_resamples: int = 499
    maximum_trade_sd_bps: float = 30_000.0

    def __post_init__(self) -> None:
        if self.simulations_per_scenario <= 0:
            raise ValueError("simulations_per_scenario must be > 0")
        if self.base_seed < 0:
            raise ValueError("base_seed must be >= 0")


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile requires observations")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _rate(screens: Sequence[DesignScreen], field: str) -> float:
    return statistics.fmean(float(bool(getattr(screen, field))) for screen in screens)


def run_power_study(
    *,
    scenarios: Sequence[PowerScenario],
    layouts: Sequence[FoldLayout],
    bars: int,
    config: PowerStudyConfig | None = None,
    pairs: Sequence[tuple[int, int]] = EMA_PAIRS,
) -> dict[str, Any]:
    """Run a deterministic synthetic design screen and return JSON-serializable evidence."""

    config = config or PowerStudyConfig()
    if not scenarios:
        raise ValueError("at least one power scenario is required")
    scenario_outputs: list[dict[str, Any]] = []
    for scenario_index, scenario in enumerate(scenarios):
        screens: list[DesignScreen] = []
        diagnostics: list[CalibrationDiagnostics] = []
        selected_pairs: dict[str, int] = {}
        replicate_outputs: list[dict[str, Any]] = []
        for simulation_index in range(config.simulations_per_scenario):
            seed = config.base_seed + scenario_index * 1_000_000 + simulation_index
            path = generate_variance_normalized_path(
                SyntheticSignalConfig(
                    bars=bars,
                    annual_volatility=scenario.annual_volatility,
                    signal_variance_fraction=scenario.signal_variance_fraction,
                    signal_half_life_hours=scenario.signal_half_life_hours,
                    innovation_degrees_of_freedom=(scenario.innovation_degrees_of_freedom),
                    seed=seed,
                )
            )
            score = evaluate_walk_forward_power(path, layouts, pairs=pairs)
            screen = design_screen(
                score,
                bootstrap_block_length=config.bootstrap_block_length,
                bootstrap_resamples=config.bootstrap_resamples,
                bootstrap_seed=seed + 500_000,
                maximum_trade_sd_bps=config.maximum_trade_sd_bps,
            )
            diagnostics.append(path.diagnostics)
            screens.append(screen)
            replicate_selected_pairs: list[dict[str, Any]] = []
            for fold in score.folds:
                pair = f"{fold.selected_fast_ema}/{fold.selected_slow_ema}"
                selected_pairs[pair] = selected_pairs.get(pair, 0) + 1
                replicate_selected_pairs.append(
                    {
                        "fold_id": fold.fold_id,
                        "fast_ema": fold.selected_fast_ema,
                        "slow_ema": fold.selected_slow_ema,
                        "validation_mean_net_trade_return_bps": (
                            fold.validation_mean_net_trade_return_bps
                        ),
                        "primary_test": {
                            "closed_trade_count": fold.primary_test.closed_trade_count,
                            "aggregate_net_pnl_usd": (fold.primary_test.aggregate_net_pnl_usd),
                            "mean_net_trade_return_bps": (
                                fold.primary_test.mean_net_trade_return_bps
                            ),
                            "maximum_account_drawdown_usd": (
                                fold.primary_test.maximum_account_drawdown_usd
                            ),
                        },
                        "doubled_test": {
                            "closed_trade_count": fold.doubled_test.closed_trade_count,
                            "aggregate_net_pnl_usd": (fold.doubled_test.aggregate_net_pnl_usd),
                            "mean_net_trade_return_bps": (
                                fold.doubled_test.mean_net_trade_return_bps
                            ),
                            "maximum_account_drawdown_usd": (
                                fold.doubled_test.maximum_account_drawdown_usd
                            ),
                        },
                    }
                )
            replicate_outputs.append(
                {
                    "simulation_index": simulation_index,
                    "seed": seed,
                    "calibration": asdict(path.diagnostics),
                    "design_screen": asdict(screen),
                    "selected_pairs_by_fold": replicate_selected_pairs,
                }
            )

        trade_counts = [float(screen.primary_trade_count) for screen in screens]
        primary_means = [
            screen.primary_mean_net_trade_return_bps
            for screen in screens
            if screen.primary_mean_net_trade_return_bps is not None
        ]
        doubled_means = [
            screen.doubled_mean_net_trade_return_bps
            for screen in screens
            if screen.doubled_mean_net_trade_return_bps is not None
        ]
        trade_sds = [
            screen.primary_trade_sd_bps
            for screen in screens
            if screen.primary_trade_sd_bps is not None
        ]
        scenario_outputs.append(
            {
                "scenario": asdict(scenario),
                "simulations": len(screens),
                "calibration": {
                    "realized_annual_volatility_min": min(
                        item.realized_annual_volatility for item in diagnostics
                    ),
                    "realized_annual_volatility_median": _quantile(
                        [item.realized_annual_volatility for item in diagnostics], 0.5
                    ),
                    "realized_annual_volatility_max": max(
                        item.realized_annual_volatility for item in diagnostics
                    ),
                    "maximum_observed_absolute_hourly_log_return": max(
                        item.maximum_absolute_hourly_log_return for item in diagnostics
                    ),
                    "maximum_observed_price_multiple": max(
                        item.price_multiple for item in diagnostics
                    ),
                    "replicates_above_100x_price_multiple": sum(
                        item.price_multiple > 100.0 for item in diagnostics
                    ),
                },
                "outcomes": {
                    "primary_trade_count_median": _quantile(trade_counts, 0.5),
                    "primary_trade_count_p10": _quantile(trade_counts, 0.1),
                    "primary_trade_count_p90": _quantile(trade_counts, 0.9),
                    "primary_mean_net_trade_return_bps_median": _quantile(primary_means, 0.5),
                    "doubled_mean_net_trade_return_bps_median": _quantile(doubled_means, 0.5),
                    "primary_trade_sd_bps_max": max(trade_sds),
                    "included_criteria_pass_rate": _rate(screens, "included_criteria_pass"),
                },
                "criterion_pass_rates": {
                    field: _rate(screens, field)
                    for field in (
                        "criterion_1_positive_primary",
                        "criterion_2_lower_bound_above_10_bps",
                        "criterion_3_positive_doubled",
                        "criterion_4_seventy_percent_positive_folds",
                        "criterion_5_at_least_100_trades",
                        "criterion_6_no_profit_concentration",
                        "criterion_8_risk_limits",
                    )
                },
                "selected_pair_counts": dict(sorted(selected_pairs.items())),
                "replicates": replicate_outputs,
            }
        )
    return {
        "schema_version": 1,
        "study_type": "synthetic_design_assurance_not_strategy_evidence",
        "generator": "variance_normalized_stationary_latent_momentum_v1",
        "variance_contract": {
            "latent_state": "z_t = rho * z_(t-1) + sqrt(1-rho^2) * eta_t",
            "rho": "0.5 ** (1 / signal_half_life_hours)",
            "hourly_log_return": (
                "annual_volatility / sqrt(8760) * "
                "(sqrt(signal_variance_fraction) * z_t + "
                "sqrt(1-signal_variance_fraction) * epsilon_t)"
            ),
            "innovation_variance": 1,
            "unconditional_latent_variance": 1,
            "unconditional_hourly_log_return_variance": ("annual_volatility ** 2 / 8760"),
            "open_price": "prior close",
            "calibration_bounds": {
                "annual_volatility_relative_tolerance": 0.08,
                "latent_variance_relative_tolerance": 0.35,
                "maximum_absolute_hourly_log_return": 0.50,
                "maximum_price_multiple_numerical_safety": 1_000.0,
            },
        },
        "costs": {
            "primary": asdict(PRIMARY_COSTS),
            "primary_flat_price_round_trip_drag_bps": (
                PRIMARY_COSTS.flat_price_round_trip_drag_bps
            ),
            "doubled": asdict(DOUBLED_COSTS),
            "doubled_flat_price_round_trip_drag_bps": (
                DOUBLED_COSTS.flat_price_round_trip_drag_bps
            ),
        },
        "walk_forward": {
            "development_bars": bars,
            "fold_count": len(layouts),
            "candidate_pair_count": len(pairs),
            "selection": "highest validation mean primary-cost net trade return",
            "test": "selected pair under primary and doubled costs",
            "indicator_context_bars": 338,
            "starting_state": "flat_1000_usd",
            "ending_state": "costed_terminal_liquidation",
            "fold_layouts": [asdict(layout) for layout in layouts],
        },
        "design_screen": {
            "included_protocol_criteria": [1, 2, 3, 4, 5, 6, 8],
            "excluded_protocol_criteria": [7, 9, 10, 11],
            "exclusion_reason": (
                "benchmarks, final-pair neighborhood semantics, regime/delay stress, and "
                "candidate-family inference are not yet frozen"
            ),
            "bootstrap": {
                "method": "moving_block_percentile_lower_bound_design_approximation",
                "block_length_trades": config.bootstrap_block_length,
                "resamples": config.bootstrap_resamples,
                "alpha": 0.05,
            },
            "maximum_trade_sd_bps_fail_closed": config.maximum_trade_sd_bps,
        },
        "simulation_config": asdict(config),
        "scenarios": scenario_outputs,
    }


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def write_power_study(payload: dict[str, Any], output_path: str | Path) -> Path:
    """Write a complete study result without silently replacing an existing artifact."""

    path = Path(output_path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing power artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))
    return path
