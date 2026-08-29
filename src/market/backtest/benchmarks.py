"""Cost-equivalent passive benchmarks for deterministic backtests."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from market.backtest.accounting import (
    PortfolioAccount,
    PortfolioJournalEntry,
    PortfolioJournalEntryType,
)
from market.backtest.costs import BPS_DIVISOR, ResolvedVenueCost
from market.domain.models import Fill, Side


class BenchmarkAnalysisError(ValueError):
    """Raised when benchmark inputs or accounting results are inconsistent."""


class BenchmarkKind(str, Enum):
    CASH = "cash"
    MATCHED_NOTIONAL_BUY_AND_HOLD = "matched_notional_buy_and_hold"
    PERIODIC_DCA = "periodic_dca"


class RiskAdjustedComparisonStatus(str, Enum):
    AVAILABLE = "available"
    STRATEGY_ZERO_DRAWDOWN = "strategy_zero_drawdown"
    BENCHMARK_ZERO_DRAWDOWN = "benchmark_zero_drawdown"
    BOTH_ZERO_DRAWDOWN = "both_zero_drawdown"


@dataclass(frozen=True)
class BenchmarkMarketPoint:
    """Executable benchmark prices derived from one approved candle."""

    ts: str
    close_ts: str
    reference_open_usd: Decimal
    buy_fill_price_usd: Decimal
    mark_price_usd: Decimal
    liquidation_sell_price_usd: Decimal


@dataclass(frozen=True)
class BenchmarkEquityPoint:
    sequence: int
    ts: str
    cash_usd: Decimal
    inventory_btc: Decimal
    mark_price_usd: Decimal
    liquidation_sell_price_usd: Decimal
    estimated_liquidation_fee_usd: Decimal
    net_liquidation_value_usd: Decimal


@dataclass(frozen=True)
class BenchmarkResult:
    kind: BenchmarkKind
    starting_cash_usd: Decimal
    target_gross_buy_notional_usd: Decimal
    executed_gross_buy_notional_usd: Decimal
    buy_execution_count: int
    sell_execution_count: int
    scheduled_entry_count: int
    dca_interval_bars: int | None
    cumulative_fees_usd: Decimal
    final_cash_usd: Decimal
    final_inventory_btc: Decimal
    final_net_liquidation_value_usd: Decimal
    net_pnl_after_fees_usd: Decimal
    net_return_pct: Decimal
    max_net_liquidation_drawdown_usd: Decimal
    net_pnl_over_max_drawdown_ratio: Decimal | None
    fills: tuple[Fill, ...] = ()
    equity_curve: tuple[BenchmarkEquityPoint, ...] = ()

    def summary(self) -> dict[str, int | str | bool | None]:
        return {
            "benchmark": self.kind.value,
            "starting_cash_usd": str(self.starting_cash_usd),
            "target_gross_buy_notional_usd": str(self.target_gross_buy_notional_usd),
            "executed_gross_buy_notional_usd": str(self.executed_gross_buy_notional_usd),
            "buy_execution_count": self.buy_execution_count,
            "sell_execution_count": self.sell_execution_count,
            "scheduled_entry_count": self.scheduled_entry_count,
            "dca_interval_bars": self.dca_interval_bars,
            "cumulative_fees_usd": str(self.cumulative_fees_usd),
            "final_cash_usd": str(self.final_cash_usd),
            "final_inventory_btc": str(self.final_inventory_btc),
            "final_net_liquidation_value_usd": str(self.final_net_liquidation_value_usd),
            "net_pnl_after_fees_usd": str(self.net_pnl_after_fees_usd),
            "net_return_pct": str(self.net_return_pct),
            "max_net_liquidation_drawdown_usd": str(self.max_net_liquidation_drawdown_usd),
            "net_pnl_over_max_drawdown_ratio": (
                str(self.net_pnl_over_max_drawdown_ratio)
                if self.net_pnl_over_max_drawdown_ratio is not None
                else None
            ),
            "risk_adjusted_ratio_defined": (self.net_pnl_over_max_drawdown_ratio is not None),
        }


@dataclass(frozen=True)
class BenchmarkComparison:
    benchmark: BenchmarkKind
    strategy_net_pnl_after_fees_usd: Decimal
    benchmark_net_pnl_after_fees_usd: Decimal
    strategy_minus_benchmark_net_pnl_usd: Decimal
    strategy_net_return_pct: Decimal
    benchmark_net_return_pct: Decimal
    strategy_minus_benchmark_return_percentage_points: Decimal
    strategy_max_net_liquidation_drawdown_usd: Decimal
    benchmark_max_net_liquidation_drawdown_usd: Decimal
    strategy_minus_benchmark_max_drawdown_usd: Decimal
    strategy_net_pnl_over_max_drawdown_ratio: Decimal | None
    benchmark_net_pnl_over_max_drawdown_ratio: Decimal | None
    strategy_minus_benchmark_risk_adjusted_ratio: Decimal | None
    risk_adjusted_comparison_status: RiskAdjustedComparisonStatus

    def summary(self) -> dict[str, str | None]:
        return {
            "benchmark": self.benchmark.value,
            "strategy_net_pnl_after_fees_usd": str(self.strategy_net_pnl_after_fees_usd),
            "benchmark_net_pnl_after_fees_usd": str(self.benchmark_net_pnl_after_fees_usd),
            "strategy_minus_benchmark_net_pnl_usd": str(self.strategy_minus_benchmark_net_pnl_usd),
            "strategy_net_return_pct": str(self.strategy_net_return_pct),
            "benchmark_net_return_pct": str(self.benchmark_net_return_pct),
            "strategy_minus_benchmark_return_percentage_points": str(
                self.strategy_minus_benchmark_return_percentage_points
            ),
            "strategy_max_net_liquidation_drawdown_usd": str(
                self.strategy_max_net_liquidation_drawdown_usd
            ),
            "benchmark_max_net_liquidation_drawdown_usd": str(
                self.benchmark_max_net_liquidation_drawdown_usd
            ),
            "strategy_minus_benchmark_max_drawdown_usd": str(
                self.strategy_minus_benchmark_max_drawdown_usd
            ),
            "strategy_net_pnl_over_max_drawdown_ratio": (
                str(self.strategy_net_pnl_over_max_drawdown_ratio)
                if self.strategy_net_pnl_over_max_drawdown_ratio is not None
                else None
            ),
            "benchmark_net_pnl_over_max_drawdown_ratio": (
                str(self.benchmark_net_pnl_over_max_drawdown_ratio)
                if self.benchmark_net_pnl_over_max_drawdown_ratio is not None
                else None
            ),
            "strategy_minus_benchmark_risk_adjusted_ratio": (
                str(self.strategy_minus_benchmark_risk_adjusted_ratio)
                if self.strategy_minus_benchmark_risk_adjusted_ratio is not None
                else None
            ),
            "risk_adjusted_comparison_status": (self.risk_adjusted_comparison_status.value),
        }


@dataclass(frozen=True)
class BenchmarkAnalysis:
    matched_notional_method: str = (
        "peak_strategy_gross_inventory_cost_basis_capped_by_entry_fee_affordability"
    )
    matched_notional_requested_usd: Decimal = Decimal("0")
    matched_notional_applied_usd: Decimal = Decimal("0")
    matched_notional_was_capped: bool = False
    dca_interval_bars: int = 168
    risk_adjusted_metric: str = "net_pnl_after_fees_usd_over_max_drawdown_usd"
    strategy_net_pnl_over_max_drawdown_ratio: Decimal | None = None
    results: tuple[BenchmarkResult, ...] = ()
    comparisons: tuple[BenchmarkComparison, ...] = ()

    def summary(self) -> dict[str, object]:
        return {
            "benchmark_count": len(self.results),
            "benchmark_matched_notional_method": self.matched_notional_method,
            "benchmark_matched_notional_requested_usd": str(self.matched_notional_requested_usd),
            "benchmark_matched_notional_applied_usd": str(self.matched_notional_applied_usd),
            "benchmark_matched_notional_was_capped": self.matched_notional_was_capped,
            "benchmark_dca_interval_bars": self.dca_interval_bars,
            "benchmark_risk_adjusted_metric": self.risk_adjusted_metric,
            "strategy_net_pnl_over_max_drawdown_ratio": (
                str(self.strategy_net_pnl_over_max_drawdown_ratio)
                if self.strategy_net_pnl_over_max_drawdown_ratio is not None
                else None
            ),
            "benchmarks": [result.summary() for result in self.results],
            "benchmark_comparisons": [comparison.summary() for comparison in self.comparisons],
        }


def _ratio(net_pnl_after_fees_usd: Decimal, max_drawdown_usd: Decimal) -> Decimal | None:
    if max_drawdown_usd < 0:
        raise BenchmarkAnalysisError("maximum drawdown cannot be negative")
    if max_drawdown_usd == 0:
        return None
    return net_pnl_after_fees_usd / max_drawdown_usd


def _validate_market_points(points: list[BenchmarkMarketPoint]) -> None:
    previous_ts: str | None = None
    for point in points:
        if not point.ts or not point.close_ts:
            raise BenchmarkAnalysisError("benchmark market timestamps are required")
        if previous_ts is not None and point.ts <= previous_ts:
            raise BenchmarkAnalysisError("benchmark market points must be ordered")
        previous_ts = point.ts
        for value in (
            point.reference_open_usd,
            point.buy_fill_price_usd,
            point.mark_price_usd,
            point.liquidation_sell_price_usd,
        ):
            if not value.is_finite() or value <= 0:
                raise BenchmarkAnalysisError("benchmark prices must be finite and > 0")


def _result(
    *,
    kind: BenchmarkKind,
    starting_cash_usd: Decimal,
    target_gross_buy_notional_usd: Decimal,
    scheduled_entry_count: int,
    dca_interval_bars: int | None,
    fills: list[Fill],
    equity_curve: list[BenchmarkEquityPoint],
    final_cash_usd: Decimal,
    final_inventory_btc: Decimal,
    cumulative_fees_usd: Decimal,
    max_drawdown_usd: Decimal,
) -> BenchmarkResult:
    executed_gross_buy_notional = sum(
        (fill.notional_usd for fill in fills if fill.side == Side.BUY),
        Decimal("0"),
    )
    final_nlv = final_cash_usd
    net_pnl = final_nlv - starting_cash_usd
    net_return_pct = (
        (net_pnl / starting_cash_usd) * Decimal("100") if starting_cash_usd != 0 else Decimal("0")
    )
    return BenchmarkResult(
        kind=kind,
        starting_cash_usd=starting_cash_usd,
        target_gross_buy_notional_usd=target_gross_buy_notional_usd,
        executed_gross_buy_notional_usd=executed_gross_buy_notional,
        buy_execution_count=sum(fill.side == Side.BUY for fill in fills),
        sell_execution_count=sum(fill.side == Side.SELL for fill in fills),
        scheduled_entry_count=scheduled_entry_count,
        dca_interval_bars=dca_interval_bars,
        cumulative_fees_usd=cumulative_fees_usd,
        final_cash_usd=final_cash_usd,
        final_inventory_btc=final_inventory_btc,
        final_net_liquidation_value_usd=final_nlv,
        net_pnl_after_fees_usd=net_pnl,
        net_return_pct=net_return_pct,
        max_net_liquidation_drawdown_usd=max_drawdown_usd,
        net_pnl_over_max_drawdown_ratio=_ratio(net_pnl, max_drawdown_usd),
        fills=tuple(fills),
        equity_curve=tuple(equity_curve),
    )


def _cash_benchmark(
    starting_cash_usd: Decimal,
    points: list[BenchmarkMarketPoint],
) -> BenchmarkResult:
    equity_curve = [
        BenchmarkEquityPoint(
            sequence=index,
            ts=point.close_ts,
            cash_usd=starting_cash_usd,
            inventory_btc=Decimal("0"),
            mark_price_usd=point.mark_price_usd,
            liquidation_sell_price_usd=point.liquidation_sell_price_usd,
            estimated_liquidation_fee_usd=Decimal("0"),
            net_liquidation_value_usd=starting_cash_usd,
        )
        for index, point in enumerate(points, start=1)
    ]
    return _result(
        kind=BenchmarkKind.CASH,
        starting_cash_usd=starting_cash_usd,
        target_gross_buy_notional_usd=Decimal("0"),
        scheduled_entry_count=0,
        dca_interval_bars=None,
        fills=[],
        equity_curve=equity_curve,
        final_cash_usd=starting_cash_usd,
        final_inventory_btc=Decimal("0"),
        cumulative_fees_usd=Decimal("0"),
        max_drawdown_usd=Decimal("0"),
    )


def _installments(total_notional_usd: Decimal, count: int) -> list[Decimal]:
    if count <= 0:
        return []
    equal_installment = total_notional_usd / Decimal(count)
    installments = [equal_installment for _ in range(count)]
    installments[-1] = total_notional_usd - sum(installments[:-1], Decimal("0"))
    return installments


def _invested_benchmark(
    *,
    kind: BenchmarkKind,
    starting_cash_usd: Decimal,
    target_gross_buy_notional_usd: Decimal,
    points: list[BenchmarkMarketPoint],
    venue_cost: ResolvedVenueCost,
    dca_interval_bars: int,
) -> BenchmarkResult:
    account = PortfolioAccount(starting_cash_usd=starting_cash_usd)
    if kind == BenchmarkKind.MATCHED_NOTIONAL_BUY_AND_HOLD:
        scheduled_indices = [0] if points else []
        interval: int | None = None
    elif kind == BenchmarkKind.PERIODIC_DCA:
        scheduled_indices = list(range(0, len(points), dca_interval_bars))
        interval = dca_interval_bars
    else:
        raise BenchmarkAnalysisError("invested benchmark kind is invalid")

    installments = _installments(target_gross_buy_notional_usd, len(scheduled_indices))
    installments_by_index = dict(zip(scheduled_indices, installments, strict=True))
    fills: list[Fill] = []
    equity_curve: list[BenchmarkEquityPoint] = []
    peak = starting_cash_usd
    max_drawdown = Decimal("0")
    event_sequence = 0

    for index, point in enumerate(points):
        installment = installments_by_index.get(index, Decimal("0"))
        if installment > 0:
            quantity = installment / point.buy_fill_price_usd
            fee = venue_cost.calculate_fee_usd(
                executed_quantity=quantity,
                fill_price_usd=point.buy_fill_price_usd,
            )
            event_sequence += 1
            fill = Fill(
                client_order_id=f"benchmark-{kind.value}-buy-{index + 1}",
                broker_order_id=f"benchmark-{kind.value}-fill-{len(fills) + 1}",
                side=Side.BUY,
                qty_btc=quantity,
                price_usd=point.buy_fill_price_usd,
                fee_usd=fee,
                ts=point.ts,
                raw={
                    "benchmark": kind.value,
                    "benchmark_entry_bar_index": index,
                    "reference_open_usd": str(point.reference_open_usd),
                    "target_gross_buy_notional_usd": str(installment),
                },
            )
            account.apply_fill(fill, event_sequence=event_sequence)
            fills.append(fill)

        estimated_liquidation_fee = (
            venue_cost.calculate_fee_usd(
                executed_quantity=account.inventory_btc,
                fill_price_usd=point.liquidation_sell_price_usd,
            )
            if account.inventory_btc > 0
            else Decimal("0")
        )
        snapshot = account.snapshot(
            mark_price_usd=point.mark_price_usd,
            estimated_liquidation_price_usd=point.liquidation_sell_price_usd,
            estimated_liquidation_fee_usd=estimated_liquidation_fee,
        )
        peak = max(peak, snapshot.net_liquidation_value_usd)
        max_drawdown = max(
            max_drawdown,
            peak - snapshot.net_liquidation_value_usd,
        )
        equity_curve.append(
            BenchmarkEquityPoint(
                sequence=len(equity_curve) + 1,
                ts=point.close_ts,
                cash_usd=snapshot.cash_usd,
                inventory_btc=snapshot.inventory_btc,
                mark_price_usd=point.mark_price_usd,
                liquidation_sell_price_usd=point.liquidation_sell_price_usd,
                estimated_liquidation_fee_usd=estimated_liquidation_fee,
                net_liquidation_value_usd=snapshot.net_liquidation_value_usd,
            )
        )

    if account.inventory_btc > 0:
        final_point = points[-1]
        event_sequence += 1
        sell_fee = venue_cost.calculate_fee_usd(
            executed_quantity=account.inventory_btc,
            fill_price_usd=final_point.liquidation_sell_price_usd,
        )
        sell = Fill(
            client_order_id=f"benchmark-{kind.value}-terminal-liquidation",
            broker_order_id=f"benchmark-{kind.value}-fill-{len(fills) + 1}",
            side=Side.SELL,
            qty_btc=account.inventory_btc,
            price_usd=final_point.liquidation_sell_price_usd,
            fee_usd=sell_fee,
            ts=final_point.close_ts,
            raw={
                "benchmark": kind.value,
                "terminal_liquidation": True,
                "reference_close_usd": str(final_point.mark_price_usd),
            },
        )
        account.apply_fill(sell, event_sequence=event_sequence)
        fills.append(sell)
        # Tolerance for Decimal arithmetic precision in terminal liquidation reconciliation
        if abs(equity_curve[-1].net_liquidation_value_usd - account.cash_usd) >= Decimal("1E-20"):
            raise BenchmarkAnalysisError(
                "benchmark terminal liquidation does not match prior net liquidation value"
            )

    return _result(
        kind=kind,
        starting_cash_usd=starting_cash_usd,
        target_gross_buy_notional_usd=target_gross_buy_notional_usd,
        scheduled_entry_count=len(scheduled_indices),
        dca_interval_bars=interval,
        fills=fills,
        equity_curve=equity_curve,
        final_cash_usd=account.cash_usd,
        final_inventory_btc=account.inventory_btc,
        cumulative_fees_usd=account.cumulative_fees_usd,
        max_drawdown_usd=max_drawdown,
    )


def _comparison_status(
    strategy_ratio: Decimal | None,
    benchmark_ratio: Decimal | None,
) -> RiskAdjustedComparisonStatus:
    if strategy_ratio is None and benchmark_ratio is None:
        return RiskAdjustedComparisonStatus.BOTH_ZERO_DRAWDOWN
    if strategy_ratio is None:
        return RiskAdjustedComparisonStatus.STRATEGY_ZERO_DRAWDOWN
    if benchmark_ratio is None:
        return RiskAdjustedComparisonStatus.BENCHMARK_ZERO_DRAWDOWN
    return RiskAdjustedComparisonStatus.AVAILABLE


def analyze_benchmarks(
    *,
    starting_cash_usd: Decimal,
    strategy_accounting_journal: tuple[PortfolioJournalEntry, ...],
    strategy_net_pnl_after_fees_usd: Decimal,
    strategy_max_net_liquidation_drawdown_usd: Decimal,
    market_points: list[BenchmarkMarketPoint],
    venue_cost: ResolvedVenueCost,
    dca_interval_bars: int = 168,
) -> BenchmarkAnalysis:
    """Build capital-matched passive comparisons under the strategy's cost contract."""
    if not starting_cash_usd.is_finite() or starting_cash_usd < 0:
        raise BenchmarkAnalysisError("starting cash must be finite and >= 0")
    if (
        isinstance(dca_interval_bars, bool)
        or not isinstance(dca_interval_bars, int)
        or dca_interval_bars <= 0
    ):
        raise BenchmarkAnalysisError("dca_interval_bars must be a positive integer")
    if not strategy_net_pnl_after_fees_usd.is_finite():
        raise BenchmarkAnalysisError("strategy net P&L must be finite")
    if (
        not strategy_max_net_liquidation_drawdown_usd.is_finite()
        or strategy_max_net_liquidation_drawdown_usd < 0
    ):
        raise BenchmarkAnalysisError("strategy maximum drawdown must be finite and >= 0")
    if not strategy_accounting_journal:
        raise BenchmarkAnalysisError("strategy accounting journal is required")
    opening = strategy_accounting_journal[0]
    if (
        opening.entry_type != PortfolioJournalEntryType.OPENING_BALANCE
        or opening.cash_after_usd != starting_cash_usd
        or opening.inventory_after_btc != 0
        or opening.inventory_cost_basis_after_usd != 0
    ):
        raise BenchmarkAnalysisError("strategy opening balance does not match starting cash")
    for expected_sequence, entry in enumerate(strategy_accounting_journal, start=1):
        if entry.journal_sequence != expected_sequence:
            raise BenchmarkAnalysisError("strategy journal sequence is not contiguous")
        if entry.accounting_identity_residual_usd != 0:
            raise BenchmarkAnalysisError("strategy accounting journal does not reconcile")
        if (
            not entry.inventory_cost_basis_after_usd.is_finite()
            or entry.inventory_cost_basis_after_usd < 0
        ):
            raise BenchmarkAnalysisError("strategy inventory cost basis is invalid")
    _validate_market_points(market_points)

    requested_notional = max(
        (entry.inventory_cost_basis_after_usd for entry in strategy_accounting_journal),
        default=Decimal("0"),
    )
    fee_rate = venue_cost.transaction_fee_bps_per_fill_applied / BPS_DIVISOR
    max_affordable_gross_notional = (
        starting_cash_usd / (Decimal("1") + fee_rate) if starting_cash_usd > 0 else Decimal("0")
    )
    applied_notional = min(requested_notional, max_affordable_gross_notional)
    if applied_notional > 0 and not market_points:
        raise BenchmarkAnalysisError("positive matched notional requires market points")

    results = (
        _cash_benchmark(starting_cash_usd, market_points),
        _invested_benchmark(
            kind=BenchmarkKind.MATCHED_NOTIONAL_BUY_AND_HOLD,
            starting_cash_usd=starting_cash_usd,
            target_gross_buy_notional_usd=applied_notional,
            points=market_points,
            venue_cost=venue_cost,
            dca_interval_bars=dca_interval_bars,
        ),
        _invested_benchmark(
            kind=BenchmarkKind.PERIODIC_DCA,
            starting_cash_usd=starting_cash_usd,
            target_gross_buy_notional_usd=applied_notional,
            points=market_points,
            venue_cost=venue_cost,
            dca_interval_bars=dca_interval_bars,
        ),
    )
    strategy_return_pct = (
        (strategy_net_pnl_after_fees_usd / starting_cash_usd) * Decimal("100")
        if starting_cash_usd != 0
        else Decimal("0")
    )
    strategy_ratio = _ratio(
        strategy_net_pnl_after_fees_usd,
        strategy_max_net_liquidation_drawdown_usd,
    )
    comparisons: list[BenchmarkComparison] = []
    for result in results:
        status = _comparison_status(strategy_ratio, result.net_pnl_over_max_drawdown_ratio)
        ratio_difference = (
            strategy_ratio - result.net_pnl_over_max_drawdown_ratio
            if status == RiskAdjustedComparisonStatus.AVAILABLE
            and strategy_ratio is not None
            and result.net_pnl_over_max_drawdown_ratio is not None
            else None
        )
        comparisons.append(
            BenchmarkComparison(
                benchmark=result.kind,
                strategy_net_pnl_after_fees_usd=strategy_net_pnl_after_fees_usd,
                benchmark_net_pnl_after_fees_usd=result.net_pnl_after_fees_usd,
                strategy_minus_benchmark_net_pnl_usd=(
                    strategy_net_pnl_after_fees_usd - result.net_pnl_after_fees_usd
                ),
                strategy_net_return_pct=strategy_return_pct,
                benchmark_net_return_pct=result.net_return_pct,
                strategy_minus_benchmark_return_percentage_points=(
                    strategy_return_pct - result.net_return_pct
                ),
                strategy_max_net_liquidation_drawdown_usd=(
                    strategy_max_net_liquidation_drawdown_usd
                ),
                benchmark_max_net_liquidation_drawdown_usd=(
                    result.max_net_liquidation_drawdown_usd
                ),
                strategy_minus_benchmark_max_drawdown_usd=(
                    strategy_max_net_liquidation_drawdown_usd
                    - result.max_net_liquidation_drawdown_usd
                ),
                strategy_net_pnl_over_max_drawdown_ratio=strategy_ratio,
                benchmark_net_pnl_over_max_drawdown_ratio=(result.net_pnl_over_max_drawdown_ratio),
                strategy_minus_benchmark_risk_adjusted_ratio=ratio_difference,
                risk_adjusted_comparison_status=status,
            )
        )

    return BenchmarkAnalysis(
        matched_notional_requested_usd=requested_notional,
        matched_notional_applied_usd=applied_notional,
        matched_notional_was_capped=applied_notional != requested_notional,
        dca_interval_bars=dca_interval_bars,
        strategy_net_pnl_over_max_drawdown_ratio=strategy_ratio,
        results=results,
        comparisons=tuple(comparisons),
    )
