from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from market.backtest.accounting import (
    PortfolioAccount,
    PortfolioAccountingError,
    PortfolioJournalEntryType,
)
from market.backtest.costs import VenueCostProfile
from market.backtest.engine import ExecutionModel, run_backtest
from market.data.candles import load_candles_csv
from market.domain.models import Fill, Side
from market.strategy.slow_trend import SlowTrendConfig

FUTURE_JUMP_FIXTURE = Path(__file__).parent / "fixtures" / "backtest" / "future_jump.csv"
FILL_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _fill(
    *,
    side: Side,
    quantity: str,
    price: str,
    fee: str,
    order_id: str,
) -> Fill:
    return Fill(
        client_order_id=order_id,
        broker_order_id=f"broker-{order_id}",
        side=side,
        qty_btc=Decimal(quantity),
        price_usd=Decimal(price),
        fee_usd=Decimal(fee),
        ts=FILL_TS,
    )


def test_weighted_average_journal_reconciles_partial_sale_and_liquidation_mark():
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))
    buy = account.apply_fill(
        _fill(side=Side.BUY, quantity="2", price="100", fee="1", order_id="buy-1"),
        event_sequence=10,
    )

    assert buy.entry_type == PortfolioJournalEntryType.FILL
    assert buy.cash_delta_usd == Decimal("-201")
    assert buy.inventory_delta_btc == Decimal("2")
    assert buy.inventory_cost_basis_delta_usd == Decimal("200")
    assert buy.realized_gross_pnl_delta_usd == 0
    assert account.cash_usd == Decimal("799")
    assert account.inventory_btc == Decimal("2")
    assert account.inventory_cost_basis_usd == Decimal("200")
    assert account.cumulative_fees_usd == Decimal("1")

    marked = account.snapshot(
        mark_price_usd=Decimal("110"),
        estimated_liquidation_price_usd=Decimal("109"),
        estimated_liquidation_fee_usd=Decimal("2"),
    )
    assert marked.average_entry_price_usd == Decimal("100")
    assert marked.unrealized_gross_pnl_usd == Decimal("20")
    assert marked.marked_equity_usd == Decimal("1019")
    assert marked.net_liquidation_value_usd == Decimal("1015")
    assert marked.marked_net_pnl_after_fees_usd == Decimal("19")
    assert marked.net_liquidation_pnl_after_fees_usd == Decimal("15")
    assert marked.accounting_identity_residual_usd == 0

    sell = account.apply_fill(
        _fill(side=Side.SELL, quantity="1", price="120", fee="1.2", order_id="sell-1"),
        event_sequence=20,
    )
    assert sell.cash_delta_usd == Decimal("118.8")
    assert sell.inventory_delta_btc == Decimal("-1")
    assert sell.inventory_cost_basis_delta_usd == Decimal("-100")
    assert sell.realized_gross_pnl_delta_usd == Decimal("20")
    assert account.cash_usd == Decimal("917.8")
    assert account.inventory_btc == Decimal("1")
    assert account.inventory_cost_basis_usd == Decimal("100")
    assert account.realized_gross_pnl_usd == Decimal("20")
    assert account.cumulative_fees_usd == Decimal("2.2")

    after_partial = account.snapshot(
        mark_price_usd=Decimal("115"),
        estimated_liquidation_price_usd=Decimal("114"),
        estimated_liquidation_fee_usd=Decimal("1.14"),
    )
    assert after_partial.unrealized_gross_pnl_usd == Decimal("15")
    assert after_partial.marked_equity_usd == Decimal("1032.8")
    assert after_partial.net_liquidation_value_usd == Decimal("1030.66")
    assert after_partial.accounting_identity_residual_usd == 0


def test_full_sale_clears_basis_and_separates_gross_pnl_from_fees():
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))
    account.apply_fill(
        _fill(side=Side.BUY, quantity="2", price="100", fee="1", order_id="buy-1"),
        event_sequence=10,
    )
    account.apply_fill(
        _fill(side=Side.SELL, quantity="2", price="120", fee="2", order_id="sell-1"),
        event_sequence=20,
    )

    snapshot = account.snapshot(
        mark_price_usd=Decimal("120"),
        estimated_liquidation_price_usd=Decimal("120"),
        estimated_liquidation_fee_usd=Decimal("0"),
    )
    assert snapshot.cash_usd == Decimal("1037")
    assert snapshot.inventory_btc == 0
    assert snapshot.inventory_cost_basis_usd == 0
    assert snapshot.average_entry_price_usd is None
    assert snapshot.realized_gross_pnl_usd == Decimal("40")
    assert snapshot.unrealized_gross_pnl_usd == 0
    assert snapshot.cumulative_fees_usd == Decimal("3")
    assert snapshot.marked_equity_usd == Decimal("1037")
    assert snapshot.net_liquidation_value_usd == Decimal("1037")
    assert snapshot.marked_net_pnl_after_fees_usd == Decimal("37")
    assert snapshot.net_liquidation_pnl_after_fees_usd == Decimal("37")
    assert snapshot.accounting_identity_residual_usd == 0


@pytest.mark.parametrize(
    "invalid_fill",
    [
        _fill(side=Side.BUY, quantity="11", price="100", fee="0", order_id="overbuy"),
        _fill(side=Side.SELL, quantity="1", price="100", fee="0", order_id="oversell"),
        _fill(side=Side.BUY, quantity="1", price="100", fee="-1", order_id="negative-fee"),
        _fill(side=Side.BUY, quantity="0", price="100", fee="0", order_id="zero-quantity"),
        _fill(side=Side.BUY, quantity="1", price="0", fee="0", order_id="zero-price"),
    ],
)
def test_rejected_fill_does_not_mutate_account(invalid_fill: Fill):
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))

    with pytest.raises(PortfolioAccountingError):
        account.apply_fill(invalid_fill, event_sequence=1)

    assert account.cash_usd == Decimal("1000")
    assert account.inventory_btc == 0
    assert account.inventory_cost_basis_usd == 0
    assert account.realized_gross_pnl_usd == 0
    assert account.cumulative_fees_usd == 0
    assert len(account.journal) == 1


@pytest.mark.parametrize("starting_cash", [Decimal("-1"), Decimal("NaN")])
def test_invalid_opening_balance_is_rejected(starting_cash: Decimal):
    with pytest.raises(PortfolioAccountingError):
        PortfolioAccount(starting_cash_usd=starting_cash)


def test_flat_snapshot_rejects_nonzero_estimated_liquidation_fee():
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))

    with pytest.raises(PortfolioAccountingError, match="flat inventory requires zero"):
        account.snapshot(
            mark_price_usd=Decimal("100"),
            estimated_liquidation_price_usd=Decimal("99"),
            estimated_liquidation_fee_usd=Decimal("1"),
        )


@pytest.mark.parametrize(
    ("mark", "liquidation_price", "liquidation_fee"),
    [
        (Decimal("0"), Decimal("99"), Decimal("0")),
        (Decimal("100"), Decimal("0"), Decimal("0")),
        (Decimal("100"), Decimal("99"), Decimal("-1")),
        (Decimal("NaN"), Decimal("99"), Decimal("0")),
    ],
)
def test_invalid_mark_inputs_are_rejected(
    mark: Decimal,
    liquidation_price: Decimal,
    liquidation_fee: Decimal,
):
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))

    with pytest.raises(PortfolioAccountingError):
        account.snapshot(
            mark_price_usd=mark,
            estimated_liquidation_price_usd=liquidation_price,
            estimated_liquidation_fee_usd=liquidation_fee,
        )


def test_fill_event_sequence_must_increase_without_mutating_state():
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))
    account.apply_fill(
        _fill(side=Side.BUY, quantity="1", price="100", fee="1", order_id="buy-1"),
        event_sequence=10,
    )
    before = (
        account.cash_usd,
        account.inventory_btc,
        account.inventory_cost_basis_usd,
        account.cumulative_fees_usd,
        account.journal,
    )

    with pytest.raises(PortfolioAccountingError, match="increase monotonically"):
        account.apply_fill(
            _fill(side=Side.BUY, quantity="1", price="100", fee="1", order_id="buy-2"),
            event_sequence=10,
        )

    assert (
        account.cash_usd,
        account.inventory_btc,
        account.inventory_cost_basis_usd,
        account.cumulative_fees_usd,
        account.journal,
    ) == before


def test_fill_event_sequence_must_be_positive():
    account = PortfolioAccount(starting_cash_usd=Decimal("1000"))

    with pytest.raises(PortfolioAccountingError, match="event_sequence must be > 0"):
        account.apply_fill(
            _fill(side=Side.BUY, quantity="1", price="100", fee="1", order_id="buy-1"),
            event_sequence=0,
        )

    assert len(account.journal) == 1


def test_empty_backtest_preserves_opening_cash_and_accounting_identity():
    result = run_backtest([], starting_cash_usd=Decimal("123.45"))

    assert result.starting_cash_usd == Decimal("123.45")
    assert result.final_cash_usd == Decimal("123.45")
    assert result.final_inventory_btc == 0
    assert result.marked_equity_usd == Decimal("123.45")
    assert result.net_liquidation_value_usd == Decimal("123.45")
    assert result.max_marked_equity_usd == Decimal("123.45")
    assert result.max_net_liquidation_drawdown_usd == 0
    assert result.net_liquidation_pnl_after_fees_usd == 0
    assert result.net_liquidation_return_pct == 0
    assert result.accounting_identity_residual_usd == 0
    assert len(result.accounting_journal) == 1
    assert result.accounting_journal[0].entry_type == PortfolioJournalEntryType.OPENING_BALANCE


def test_v2_backtest_reconciles_entry_terminal_exit_and_every_mark():
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

    assert len(result.accounting_journal) == 3
    opening, buy, terminal_sell = result.accounting_journal
    assert opening.entry_type == PortfolioJournalEntryType.OPENING_BALANCE
    assert buy.event_sequence == next(
        event.sequence
        for event in result.events
        if event.client_order_id == buy.client_order_id and event.event_type.value == "fill"
    )
    assert terminal_sell.event_sequence == result.events[-1].sequence
    assert result.fills[0].raw["accounting_journal_sequence"] == buy.journal_sequence
    assert result.fills[-1].raw["accounting_journal_sequence"] == (terminal_sell.journal_sequence)
    assert buy.executed_notional_usd == Decimal("20.040020")
    assert buy.fee_delta_usd == Decimal("0.1903801900")
    assert terminal_sell.executed_notional_usd == Decimal("19.960020")
    assert terminal_sell.inventory_cost_basis_delta_usd == Decimal("-20.040020")
    assert terminal_sell.realized_gross_pnl_delta_usd == Decimal("-0.080000")
    assert terminal_sell.fee_delta_usd == Decimal("0.1896201900")

    assert result.final_cash_usd == Decimal("999.5399996200")
    assert result.final_inventory_btc == 0
    assert result.final_inventory_cost_basis_usd == 0
    assert result.final_average_entry_price_usd is None
    assert result.realized_gross_pnl_usd == Decimal("-0.080000")
    assert result.unrealized_gross_pnl_usd == 0
    assert result.cumulative_fees_usd == Decimal("0.3800003800")
    assert result.marked_equity_usd == Decimal("999.5399996200")
    assert result.net_liquidation_value_usd == Decimal("999.5399996200")
    assert result.marked_net_pnl_after_fees_usd == Decimal("-0.4600003800")
    assert result.net_liquidation_pnl_after_fees_usd == Decimal("-0.4600003800")
    assert result.accounting_identity_residual_usd == 0
    assert all(point.accounting_identity_residual_usd == 0 for point in result.equity_curve)

    pre_liquidation = result.equity_curve[-2]
    assert pre_liquidation.inventory_cost_basis_usd == Decimal("20.040020")
    assert pre_liquidation.unrealized_gross_pnl_usd == Decimal("-0.040020")
    assert pre_liquidation.cumulative_fees_usd == Decimal("0.1903801900")
    assert pre_liquidation.marked_equity_usd == Decimal("999.7695998100")
    assert pre_liquidation.net_liquidation_value_usd == Decimal("999.5399996200")
