from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from market.backtest.accounting import PortfolioAccount
from market.backtest.benchmarks import (
    BenchmarkAnalysisError,
    BenchmarkKind,
    BenchmarkMarketPoint,
    RiskAdjustedComparisonStatus,
    analyze_benchmarks,
)
from market.backtest.costs import (
    VenueCostAssumptions,
    VenueCostProfile,
    resolve_venue_cost,
)
from market.backtest.engine import ExecutionModel, run_backtest
from market.data.candles import load_candles_csv
from market.domain.models import Fill, Side
from market.strategy.slow_trend import SlowTrendConfig

FUTURE_JUMP_FIXTURE = Path(__file__).parent / "fixtures" / "backtest" / "future_jump.csv"
START = datetime(2024, 1, 1, tzinfo=UTC)


def _fill(
    *,
    order_id: str,
    side: Side,
    quantity: str,
    price: str,
    fee: str = "0",
) -> Fill:
    return Fill(
        client_order_id=order_id,
        broker_order_id=f"broker-{order_id}",
        side=side,
        qty_btc=Decimal(quantity),
        price_usd=Decimal(price),
        fee_usd=Decimal(fee),
        ts=START,
    )


def _cost(fee_bps: str = "100"):
    return resolve_venue_cost(
        VenueCostAssumptions(
            profile=VenueCostProfile.LEGACY_UNCLASSIFIED,
            transaction_fee_bps_per_fill_assumption=Decimal(fee_bps),
        ),
        execution_model="next_bar_open",
        quoted_spread_bps_assumption=Decimal("0"),
    )


def _point(index: int, price: str) -> BenchmarkMarketPoint:
    ts = START + timedelta(hours=index)
    close_ts = ts + timedelta(hours=1)
    value = Decimal(price)
    return BenchmarkMarketPoint(
        ts=ts.isoformat(),
        close_ts=close_ts.isoformat(),
        reference_open_usd=value,
        buy_fill_price_usd=value,
        mark_price_usd=value,
        liquidation_sell_price_usd=value,
    )


def _strategy_journal(starting_cash: str = "1000", gross_basis: str = "200"):
    account = PortfolioAccount(starting_cash_usd=Decimal(starting_cash))
    account.apply_fill(
        _fill(
            order_id="strategy-buy",
            side=Side.BUY,
            quantity=str(Decimal(gross_basis) / Decimal("100")),
            price="100",
        ),
        event_sequence=1,
    )
    return account.journal


def test_cash_buy_hold_and_periodic_dca_have_hand_calculated_results():
    analysis = analyze_benchmarks(
        starting_cash_usd=Decimal("1000"),
        strategy_accounting_journal=_strategy_journal(),
        strategy_net_pnl_after_fees_usd=Decimal("50"),
        strategy_max_net_liquidation_drawdown_usd=Decimal("25"),
        market_points=[_point(0, "100"), _point(1, "80"), _point(2, "200")],
        venue_cost=_cost(),
        dca_interval_bars=2,
    )

    assert analysis.matched_notional_requested_usd == Decimal("200")
    assert analysis.matched_notional_applied_usd == Decimal("200")
    assert not analysis.matched_notional_was_capped
    assert analysis.strategy_net_pnl_over_max_drawdown_ratio == Decimal("2")

    cash, buy_hold, dca = analysis.results
    assert [result.kind for result in analysis.results] == [
        BenchmarkKind.CASH,
        BenchmarkKind.MATCHED_NOTIONAL_BUY_AND_HOLD,
        BenchmarkKind.PERIODIC_DCA,
    ]
    assert cash.net_pnl_after_fees_usd == 0
    assert cash.max_net_liquidation_drawdown_usd == 0
    assert cash.net_pnl_over_max_drawdown_ratio is None
    assert not cash.fills

    assert buy_hold.executed_gross_buy_notional_usd == Decimal("200")
    assert buy_hold.buy_execution_count == 1
    assert buy_hold.sell_execution_count == 1
    assert buy_hold.cumulative_fees_usd == Decimal("6")
    assert buy_hold.final_cash_usd == Decimal("1194")
    assert buy_hold.final_inventory_btc == 0
    assert buy_hold.net_pnl_after_fees_usd == Decimal("194")
    assert buy_hold.net_return_pct == Decimal("19.400")
    assert buy_hold.max_net_liquidation_drawdown_usd == Decimal("43.60")
    assert buy_hold.net_pnl_over_max_drawdown_ratio == Decimal("194") / Decimal("43.60")
    assert [point.net_liquidation_value_usd for point in buy_hold.equity_curve] == [
        Decimal("996"),
        Decimal("956.4"),
        Decimal("1194"),
    ]

    assert dca.scheduled_entry_count == 2
    assert dca.dca_interval_bars == 2
    assert dca.executed_gross_buy_notional_usd == Decimal("200")
    assert dca.buy_execution_count == 2
    assert dca.sell_execution_count == 1
    assert dca.cumulative_fees_usd == Decimal("5.00")
    assert dca.final_cash_usd == Decimal("1095.00")
    assert dca.final_inventory_btc == 0
    assert dca.net_pnl_after_fees_usd == Decimal("95.00")
    assert dca.net_return_pct == Decimal("9.500")
    assert dca.max_net_liquidation_drawdown_usd == Decimal("21.80")
    assert dca.net_pnl_over_max_drawdown_ratio == Decimal("95") / Decimal("21.8")
    assert [point.net_liquidation_value_usd for point in dca.equity_curve] == [
        Decimal("998"),
        Decimal("978.2"),
        Decimal("1095.0"),
    ]

    cash_comparison, buy_hold_comparison, dca_comparison = analysis.comparisons
    assert cash_comparison.strategy_minus_benchmark_net_pnl_usd == Decimal("50")
    assert (
        cash_comparison.risk_adjusted_comparison_status
        == RiskAdjustedComparisonStatus.BENCHMARK_ZERO_DRAWDOWN
    )
    assert cash_comparison.strategy_minus_benchmark_risk_adjusted_ratio is None
    assert buy_hold_comparison.strategy_minus_benchmark_net_pnl_usd == Decimal("-144")
    assert buy_hold_comparison.strategy_minus_benchmark_risk_adjusted_ratio == Decimal("2") - (
        Decimal("194") / Decimal("43.6")
    )
    assert dca_comparison.strategy_minus_benchmark_net_pnl_usd == Decimal("-45")


def test_matched_notional_is_capped_by_entry_fee_affordability():
    analysis = analyze_benchmarks(
        starting_cash_usd=Decimal("1010"),
        strategy_accounting_journal=_strategy_journal("1010", "1010"),
        strategy_net_pnl_after_fees_usd=Decimal("0"),
        strategy_max_net_liquidation_drawdown_usd=Decimal("0"),
        market_points=[_point(0, "100")],
        venue_cost=_cost(),
        dca_interval_bars=1,
    )

    assert analysis.matched_notional_requested_usd == Decimal("1010")
    assert analysis.matched_notional_applied_usd == Decimal("1000")
    assert analysis.matched_notional_was_capped
    for result in analysis.results[1:]:
        assert result.executed_gross_buy_notional_usd == Decimal("1000")
        assert result.final_cash_usd == Decimal("990")


def test_zero_strategy_exposure_collapses_matched_benchmarks_to_cash():
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))
    analysis = analyze_benchmarks(
        starting_cash_usd=Decimal("1000"),
        strategy_accounting_journal=account.journal,
        strategy_net_pnl_after_fees_usd=Decimal("0"),
        strategy_max_net_liquidation_drawdown_usd=Decimal("0"),
        market_points=[_point(0, "100"), _point(1, "200")],
        venue_cost=_cost(),
        dca_interval_bars=1,
    )

    assert analysis.matched_notional_applied_usd == 0
    assert all(result.final_cash_usd == Decimal("1000") for result in analysis.results)
    assert all(not result.fills for result in analysis.results)
    assert all(
        comparison.risk_adjusted_comparison_status
        == RiskAdjustedComparisonStatus.BOTH_ZERO_DRAWDOWN
        for comparison in analysis.comparisons
    )


def test_benchmark_inputs_fail_closed():
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))
    base = {
        "starting_cash_usd": Decimal("1000"),
        "strategy_accounting_journal": account.journal,
        "strategy_net_pnl_after_fees_usd": Decimal("0"),
        "strategy_max_net_liquidation_drawdown_usd": Decimal("0"),
        "market_points": [_point(0, "100")],
        "venue_cost": _cost(),
        "dca_interval_bars": 1,
    }
    with pytest.raises(BenchmarkAnalysisError, match="positive integer"):
        analyze_benchmarks(**{**base, "dca_interval_bars": 0})
    with pytest.raises(BenchmarkAnalysisError, match="prices"):
        analyze_benchmarks(
            **{
                **base,
                "market_points": [replace(_point(0, "100"), buy_fill_price_usd=Decimal("0"))],
            }
        )
    with pytest.raises(BenchmarkAnalysisError, match="ordered"):
        analyze_benchmarks(
            **{
                **base,
                "market_points": [_point(1, "100"), _point(0, "100")],
            }
        )
    bad_journal = (replace(account.journal[0], accounting_identity_residual_usd=Decimal("1")),)
    with pytest.raises(BenchmarkAnalysisError, match="does not reconcile"):
        analyze_benchmarks(**{**base, "strategy_accounting_journal": bad_journal})


@pytest.mark.parametrize("interval", [-1, True, 1.5])
def test_benchmark_rejects_other_invalid_dca_intervals(interval):
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))
    with pytest.raises(BenchmarkAnalysisError, match="positive integer"):
        analyze_benchmarks(
            starting_cash_usd=Decimal("1000"),
            strategy_accounting_journal=account.journal,
            strategy_net_pnl_after_fees_usd=Decimal("0"),
            strategy_max_net_liquidation_drawdown_usd=Decimal("0"),
            market_points=[_point(0, "100")],
            venue_cost=_cost(),
            dca_interval_bars=interval,
        )


def test_benchmark_rejects_invalid_strategy_and_market_contracts():
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))
    base = {
        "starting_cash_usd": Decimal("1000"),
        "strategy_accounting_journal": account.journal,
        "strategy_net_pnl_after_fees_usd": Decimal("0"),
        "strategy_max_net_liquidation_drawdown_usd": Decimal("0"),
        "market_points": [_point(0, "100")],
        "venue_cost": _cost(),
        "dca_interval_bars": 1,
    }
    with pytest.raises(BenchmarkAnalysisError, match="starting cash"):
        analyze_benchmarks(**{**base, "starting_cash_usd": Decimal("NaN")})
    with pytest.raises(BenchmarkAnalysisError, match="strategy net P&L"):
        analyze_benchmarks(**{**base, "strategy_net_pnl_after_fees_usd": Decimal("NaN")})
    with pytest.raises(BenchmarkAnalysisError, match="maximum drawdown"):
        analyze_benchmarks(**{**base, "strategy_max_net_liquidation_drawdown_usd": Decimal("-1")})
    with pytest.raises(BenchmarkAnalysisError, match="journal is required"):
        analyze_benchmarks(**{**base, "strategy_accounting_journal": ()})
    with pytest.raises(BenchmarkAnalysisError, match="opening balance"):
        analyze_benchmarks(**{**base, "starting_cash_usd": Decimal("999")})

    noncontiguous = (account.journal[0], replace(account.journal[0], journal_sequence=3))
    with pytest.raises(BenchmarkAnalysisError, match="sequence"):
        analyze_benchmarks(**{**base, "strategy_accounting_journal": noncontiguous})
    valid_invested_journal = _strategy_journal()
    invalid_basis = (
        valid_invested_journal[0],
        replace(valid_invested_journal[1], inventory_cost_basis_after_usd=Decimal("-1")),
    )
    with pytest.raises(BenchmarkAnalysisError, match="cost basis"):
        analyze_benchmarks(**{**base, "strategy_accounting_journal": invalid_basis})

    missing_timestamp = replace(_point(0, "100"), ts="")
    with pytest.raises(BenchmarkAnalysisError, match="timestamps"):
        analyze_benchmarks(**{**base, "market_points": [missing_timestamp]})
    nonfinite_price = replace(_point(0, "100"), mark_price_usd=Decimal("NaN"))
    with pytest.raises(BenchmarkAnalysisError, match="prices"):
        analyze_benchmarks(**{**base, "market_points": [nonfinite_price]})
    with pytest.raises(BenchmarkAnalysisError, match="requires market points"):
        analyze_benchmarks(
            **{
                **base,
                "strategy_accounting_journal": _strategy_journal(),
                "market_points": [],
            }
        )


def test_future_jump_engine_compares_strategy_to_costed_passive_benchmarks():
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
        benchmark_dca_interval_bars=168,
    )

    assert result.benchmarks.matched_notional_requested_usd == Decimal("20.040020")
    assert result.benchmarks.matched_notional_applied_usd == Decimal("20.040020")
    cash, buy_hold, dca = result.benchmarks.results
    assert cash.net_pnl_after_fees_usd == 0
    assert buy_hold.net_pnl_after_fees_usd == Decimal("19.3103994300")
    assert dca.net_pnl_after_fees_usd == buy_hold.net_pnl_after_fees_usd
    assert buy_hold.executed_gross_buy_notional_usd == Decimal("20.040020")
    assert buy_hold.cumulative_fees_usd == Decimal("0.5696205700")
    assert buy_hold.final_inventory_btc == 0
    comparison = result.benchmarks.comparisons[1]
    assert comparison.strategy_minus_benchmark_net_pnl_usd == Decimal("-19.7703998100")
    assert comparison.risk_adjusted_comparison_status == RiskAdjustedComparisonStatus.AVAILABLE
