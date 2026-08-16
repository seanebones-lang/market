from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from market.backtest.accounting import PortfolioAccount
from market.backtest.costs import VenueCostProfile
from market.backtest.engine import ExecutionModel, run_backtest
from market.backtest.lifecycle import (
    LifecycleAnalysisError,
    OrderDisposition,
    OrderLifecycleState,
    OrderOrigin,
    OrderRequest,
    TradeOutcome,
    analyze_lifecycle,
)
from market.data.candles import load_candles_csv
from market.domain.models import Fill, Side
from market.strategy.slow_trend import SlowTrendConfig

FUTURE_JUMP_FIXTURE = Path(__file__).parent / "fixtures" / "backtest" / "future_jump.csv"
FILL_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _fill(
    order_id: str,
    side: Side,
    quantity: str,
    price: str,
    fee: str,
    execution_number: int,
) -> Fill:
    return Fill(
        client_order_id=order_id,
        broker_order_id=f"broker-{order_id}-{execution_number}",
        side=side,
        qty_btc=Decimal(quantity),
        price_usd=Decimal(price),
        fee_usd=Decimal(fee),
        ts=FILL_TS + timedelta(seconds=execution_number),
    )


def _journal_for(fills: list[Fill]):
    account = PortfolioAccount(starting_cash_usd=Decimal("10000"))
    for index, fill in enumerate(fills, start=1):
        account.apply_fill(fill, event_sequence=index * 10)
    return account.journal


def test_multi_execution_orders_distinguish_fills_closed_trades_and_round_trip():
    requests = [
        OrderRequest("buy", OrderOrigin.STRATEGY, Side.BUY, Decimal("2")),
        OrderRequest("sell", OrderOrigin.STRATEGY, Side.SELL, Decimal("2")),
    ]
    fills = [
        _fill("buy", Side.BUY, "1", "100", "1", 1),
        _fill("buy", Side.BUY, "1", "110", "1.1", 2),
        _fill("sell", Side.SELL, "1", "120", "1.2", 3),
        _fill("sell", Side.SELL, "1", "90", "0.9", 4),
    ]

    analysis = analyze_lifecycle(
        requests=requests,
        fills=fills,
        accounting_journal=_journal_for(fills),
    )

    assert analysis.order_count == 2
    assert analysis.filled_order_count == 2
    assert analysis.partially_filled_order_count == 0
    assert analysis.execution_count == 4
    assert analysis.buy_execution_count == 2
    assert analysis.sell_execution_count == 2
    assert analysis.partial_fill_execution_count == 4
    assert [order.execution_count for order in analysis.orders] == [2, 2]
    assert all(order.state == OrderLifecycleState.FILLED for order in analysis.orders)

    assert analysis.closed_trade_count == 2
    first_trade, second_trade = analysis.closed_trades
    assert first_trade.allocated_gross_cost_basis_usd == Decimal("105")
    assert first_trade.allocated_entry_fees_usd == Decimal("1.05")
    assert first_trade.realized_gross_pnl_usd == Decimal("15")
    assert first_trade.realized_net_pnl_after_fees_usd == Decimal("12.75")
    assert first_trade.outcome == TradeOutcome.WIN
    assert first_trade.inventory_after_btc == Decimal("1")
    assert second_trade.realized_gross_pnl_usd == Decimal("-15")
    assert second_trade.realized_net_pnl_after_fees_usd == Decimal("-16.95")
    assert second_trade.outcome == TradeOutcome.LOSS
    assert second_trade.inventory_after_btc == 0
    assert analysis.winning_closed_trade_count == 1
    assert analysis.losing_closed_trade_count == 1
    assert analysis.breakeven_closed_trade_count == 0
    assert analysis.realized_closed_trade_net_pnl_after_fees_usd == Decimal("-4.20")

    assert analysis.round_trip_count == 1
    assert analysis.open_round_trip_count == 0
    trip = analysis.round_trips[0]
    assert trip.entry_execution_count == 2
    assert trip.exit_execution_count == 2
    assert trip.quantity_btc == Decimal("2")
    assert trip.net_pnl_after_fees_usd == Decimal("-4.2")
    assert trip.outcome == TradeOutcome.LOSS
    assert analysis.completed_round_trip_net_pnl_after_fees_usd == Decimal("-4.2")
    assert analysis.open_inventory_btc == 0
    assert analysis.open_inventory_cost_basis_usd == 0
    assert analysis.open_inventory_entry_fees_usd == 0


def test_partial_order_can_expire_with_open_inventory_and_open_round_trip():
    requests = [OrderRequest("buy", OrderOrigin.STRATEGY, Side.BUY, Decimal("2"))]
    fills = [_fill("buy", Side.BUY, "1", "100", "1", 1)]

    analysis = analyze_lifecycle(
        requests=requests,
        fills=fills,
        accounting_journal=_journal_for(fills),
        unfilled_dispositions={"buy": OrderDisposition.EXPIRED},
    )

    order = analysis.orders[0]
    assert order.state == OrderLifecycleState.PARTIALLY_FILLED
    assert order.unfilled_disposition == OrderDisposition.EXPIRED
    assert order.executed_quantity_btc == Decimal("1")
    assert order.remaining_quantity_btc == Decimal("1")
    assert analysis.partially_filled_order_count == 1
    assert analysis.partial_fill_execution_count == 1
    assert analysis.expired_order_count == 1
    assert analysis.unfilled_order_count == 0
    assert analysis.orders_with_remaining_quantity_count == 1
    assert analysis.round_trip_count == 0
    assert analysis.open_round_trip_count == 1
    assert analysis.closed_trade_count == 0
    assert analysis.open_inventory_btc == Decimal("1")
    assert analysis.open_inventory_cost_basis_usd == Decimal("100")
    assert analysis.open_inventory_entry_fees_usd == Decimal("1")


def test_expired_and_execution_rejected_orders_have_no_executions():
    requests = [
        OrderRequest("expired", OrderOrigin.STRATEGY, Side.BUY, Decimal("1")),
        OrderRequest("rejected", OrderOrigin.STRATEGY, Side.BUY, Decimal("1")),
    ]
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))

    analysis = analyze_lifecycle(
        requests=requests,
        fills=[],
        accounting_journal=account.journal,
        unfilled_dispositions={
            "expired": OrderDisposition.EXPIRED,
            "rejected": OrderDisposition.EXECUTION_REJECTED,
        },
    )

    assert [order.state for order in analysis.orders] == [
        OrderLifecycleState.EXPIRED,
        OrderLifecycleState.EXECUTION_REJECTED,
    ]
    assert analysis.order_count == 2
    assert analysis.unfilled_order_count == 2
    assert analysis.orders_with_remaining_quantity_count == 2
    assert analysis.expired_order_count == 1
    assert analysis.execution_rejected_order_count == 1
    assert analysis.execution_count == 0
    assert analysis.closed_trade_count == 0
    assert analysis.round_trip_count == 0


def test_closed_trade_can_be_breakeven_only_after_both_sides_fees():
    requests = [
        OrderRequest("buy", OrderOrigin.STRATEGY, Side.BUY, Decimal("1")),
        OrderRequest("sell", OrderOrigin.STRATEGY, Side.SELL, Decimal("1")),
    ]
    fills = [
        _fill("buy", Side.BUY, "1", "100", "1", 1),
        _fill("sell", Side.SELL, "1", "102", "1", 2),
    ]

    analysis = analyze_lifecycle(
        requests=requests,
        fills=fills,
        accounting_journal=_journal_for(fills),
    )

    trade = analysis.closed_trades[0]
    assert trade.realized_gross_pnl_usd == Decimal("2")
    assert trade.allocated_entry_fees_usd == Decimal("1")
    assert trade.exit_fee_usd == Decimal("1")
    assert trade.realized_net_pnl_after_fees_usd == 0
    assert trade.outcome == TradeOutcome.BREAKEVEN
    assert analysis.breakeven_closed_trade_count == 1
    assert analysis.round_trips[0].outcome == TradeOutcome.BREAKEVEN


def test_lifecycle_rejects_unknown_fill_and_overfill():
    unknown_fill = _fill("unknown", Side.BUY, "1", "100", "0", 1)
    with pytest.raises(LifecycleAnalysisError, match="unknown order request"):
        analyze_lifecycle(
            requests=[],
            fills=[unknown_fill],
            accounting_journal=_journal_for([unknown_fill]),
        )

    overfill = _fill("buy", Side.BUY, "2", "100", "0", 1)
    with pytest.raises(LifecycleAnalysisError, match="exceed requested"):
        analyze_lifecycle(
            requests=[OrderRequest("buy", OrderOrigin.STRATEGY, Side.BUY, Decimal("1"))],
            fills=[overfill],
            accounting_journal=_journal_for([overfill]),
        )


def test_lifecycle_rejects_duplicate_requests_unknown_disposition_and_journal_mismatch():
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))
    duplicate = OrderRequest("same", OrderOrigin.STRATEGY, Side.BUY, Decimal("1"))
    with pytest.raises(LifecycleAnalysisError, match="duplicate order request"):
        analyze_lifecycle(
            requests=[duplicate, duplicate],
            fills=[],
            accounting_journal=account.journal,
        )
    with pytest.raises(LifecycleAnalysisError, match="unknown order"):
        analyze_lifecycle(
            requests=[],
            fills=[],
            accounting_journal=account.journal,
            unfilled_dispositions={"missing": OrderDisposition.EXPIRED},
        )

    fill = _fill("buy", Side.BUY, "1", "100", "0", 1)
    with pytest.raises(LifecycleAnalysisError, match="fill count"):
        analyze_lifecycle(
            requests=[OrderRequest("buy", OrderOrigin.STRATEGY, Side.BUY, Decimal("1"))],
            fills=[fill],
            accounting_journal=account.journal,
        )


def test_v2_backtest_reports_one_losing_closed_trade_and_one_round_trip():
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

    lifecycle = result.lifecycle
    assert lifecycle.order_count == 2
    assert lifecycle.strategy_order_count == 1
    assert lifecycle.terminal_liquidation_order_count == 1
    assert lifecycle.filled_order_count == 2
    assert lifecycle.partially_filled_order_count == 0
    assert lifecycle.execution_count == 2
    assert lifecycle.partial_fill_execution_count == 0
    assert lifecycle.closed_trade_count == 1
    assert lifecycle.winning_closed_trade_count == 0
    assert lifecycle.losing_closed_trade_count == 1
    assert lifecycle.breakeven_closed_trade_count == 0
    assert lifecycle.round_trip_count == 1
    assert lifecycle.open_round_trip_count == 0
    assert lifecycle.open_inventory_btc == 0
    assert lifecycle.realized_closed_trade_net_pnl_after_fees_usd == Decimal("-0.4600003800")
    assert lifecycle.completed_round_trip_net_pnl_after_fees_usd == Decimal("-0.4600003800")
    assert lifecycle.closed_trades[0].realized_gross_pnl_usd == Decimal("-0.080000")
    assert lifecycle.closed_trades[0].outcome == TradeOutcome.LOSS


def test_end_of_data_expiration_counts_an_order_but_no_execution_or_trade():
    candles = load_candles_csv(FUTURE_JUMP_FIXTURE)[:-1]
    result = run_backtest(
        candles,
        starting_cash_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        source="fixture:future-jump",
    )

    lifecycle = result.lifecycle
    assert lifecycle.order_count == 1
    assert lifecycle.strategy_order_count == 1
    assert lifecycle.expired_order_count == 1
    assert lifecycle.unfilled_order_count == 1
    assert lifecycle.execution_count == 0
    assert lifecycle.closed_trade_count == 0
    assert lifecycle.round_trip_count == 0
