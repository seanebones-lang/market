"""Order, execution, closed-trade, and round-trip lifecycle analysis."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from market.backtest.accounting import PortfolioJournalEntry, PortfolioJournalEntryType
from market.domain.models import Fill, Side


class LifecycleAnalysisError(ValueError):
    """Raised when lifecycle inputs cannot be reconciled exactly."""


class OrderOrigin(str, Enum):
    STRATEGY = "strategy"
    TERMINAL_LIQUIDATION = "terminal_liquidation"


class OrderDisposition(str, Enum):
    EXPIRED = "expired"
    EXECUTION_REJECTED = "execution_rejected"


class OrderLifecycleState(str, Enum):
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    EXPIRED = "expired"
    EXECUTION_REJECTED = "execution_rejected"
    UNFILLED = "unfilled"


class TradeOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    BREAKEVEN = "breakeven"


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    origin: OrderOrigin
    side: Side
    requested_quantity_btc: Decimal


@dataclass(frozen=True)
class OrderLifecycleRecord:
    order_sequence: int
    client_order_id: str
    origin: OrderOrigin
    side: Side
    requested_quantity_btc: Decimal
    executed_quantity_btc: Decimal
    remaining_quantity_btc: Decimal
    execution_count: int
    state: OrderLifecycleState
    unfilled_disposition: OrderDisposition | None


@dataclass(frozen=True)
class ClosedTradeRecord:
    closed_trade_sequence: int
    execution_index: int
    client_order_id: str
    quantity_btc: Decimal
    exit_price_usd: Decimal
    allocated_gross_cost_basis_usd: Decimal
    allocated_entry_fees_usd: Decimal
    exit_fee_usd: Decimal
    realized_gross_pnl_usd: Decimal
    realized_net_pnl_after_fees_usd: Decimal
    outcome: TradeOutcome
    inventory_after_btc: Decimal


@dataclass(frozen=True)
class RoundTripRecord:
    round_trip_sequence: int
    start_execution_index: int
    end_execution_index: int
    entry_execution_count: int
    exit_execution_count: int
    quantity_btc: Decimal
    net_pnl_after_fees_usd: Decimal
    outcome: TradeOutcome


@dataclass(frozen=True)
class LifecycleAnalysis:
    orders: tuple[OrderLifecycleRecord, ...] = ()
    closed_trades: tuple[ClosedTradeRecord, ...] = ()
    round_trips: tuple[RoundTripRecord, ...] = ()
    order_count: int = 0
    strategy_order_count: int = 0
    terminal_liquidation_order_count: int = 0
    filled_order_count: int = 0
    partially_filled_order_count: int = 0
    unfilled_order_count: int = 0
    orders_with_remaining_quantity_count: int = 0
    expired_order_count: int = 0
    execution_rejected_order_count: int = 0
    execution_count: int = 0
    buy_execution_count: int = 0
    sell_execution_count: int = 0
    partial_fill_execution_count: int = 0
    round_trip_count: int = 0
    open_round_trip_count: int = 0
    closed_trade_count: int = 0
    winning_closed_trade_count: int = 0
    losing_closed_trade_count: int = 0
    breakeven_closed_trade_count: int = 0
    realized_closed_trade_net_pnl_after_fees_usd: Decimal = Decimal("0")
    completed_round_trip_net_pnl_after_fees_usd: Decimal = Decimal("0")
    open_inventory_btc: Decimal = Decimal("0")
    open_inventory_cost_basis_usd: Decimal = Decimal("0")
    open_inventory_entry_fees_usd: Decimal = Decimal("0")

    def summary(self) -> dict[str, int | str]:
        return {
            "order_count": self.order_count,
            "strategy_order_count": self.strategy_order_count,
            "terminal_liquidation_order_count": self.terminal_liquidation_order_count,
            "filled_order_count": self.filled_order_count,
            "partially_filled_order_count": self.partially_filled_order_count,
            "unfilled_order_count": self.unfilled_order_count,
            "orders_with_remaining_quantity_count": self.orders_with_remaining_quantity_count,
            "expired_order_count": self.expired_order_count,
            "execution_rejected_order_count": self.execution_rejected_order_count,
            "execution_count": self.execution_count,
            "buy_execution_count": self.buy_execution_count,
            "sell_execution_count": self.sell_execution_count,
            "partial_fill_execution_count": self.partial_fill_execution_count,
            "round_trip_count": self.round_trip_count,
            "open_round_trip_count": self.open_round_trip_count,
            "closed_trade_count": self.closed_trade_count,
            "winning_closed_trade_count": self.winning_closed_trade_count,
            "losing_closed_trade_count": self.losing_closed_trade_count,
            "breakeven_closed_trade_count": self.breakeven_closed_trade_count,
            "realized_closed_trade_net_pnl_after_fees_usd": str(
                self.realized_closed_trade_net_pnl_after_fees_usd
            ),
            "completed_round_trip_net_pnl_after_fees_usd": str(
                self.completed_round_trip_net_pnl_after_fees_usd
            ),
            "open_inventory_btc": str(self.open_inventory_btc),
            "open_inventory_cost_basis_usd": str(self.open_inventory_cost_basis_usd),
            "open_inventory_entry_fees_usd": str(self.open_inventory_entry_fees_usd),
        }


def _outcome(net_pnl_after_fees_usd: Decimal) -> TradeOutcome:
    if net_pnl_after_fees_usd > 0:
        return TradeOutcome.WIN
    if net_pnl_after_fees_usd < 0:
        return TradeOutcome.LOSS
    return TradeOutcome.BREAKEVEN


def _validate_requests(requests: list[OrderRequest]) -> dict[str, OrderRequest]:
    by_id: dict[str, OrderRequest] = {}
    for request in requests:
        if not request.client_order_id:
            raise LifecycleAnalysisError("order request requires a client_order_id")
        if request.client_order_id in by_id:
            raise LifecycleAnalysisError(f"duplicate order request {request.client_order_id}")
        if not request.requested_quantity_btc.is_finite() or request.requested_quantity_btc <= 0:
            raise LifecycleAnalysisError("requested order quantity must be finite and > 0")
        by_id[request.client_order_id] = request
    return by_id


def _validate_fill_journal(
    fills: list[Fill],
    accounting_journal: tuple[PortfolioJournalEntry, ...],
) -> None:
    fill_entries = tuple(
        entry for entry in accounting_journal if entry.entry_type == PortfolioJournalEntryType.FILL
    )
    if len(fill_entries) != len(fills):
        raise LifecycleAnalysisError("fill count does not match accounting journal")
    for fill, entry in zip(fills, fill_entries, strict=True):
        if (
            entry.client_order_id != fill.client_order_id
            or entry.broker_order_id != fill.broker_order_id
            or entry.side != fill.side
            or entry.quantity_btc != fill.qty_btc
            or entry.price_usd != fill.price_usd
            or entry.executed_notional_usd != fill.notional_usd
            or entry.fee_delta_usd != fill.fee_usd
            or entry.accounting_identity_residual_usd != 0
        ):
            raise LifecycleAnalysisError("fill does not reconcile to accounting journal")


def analyze_lifecycle(
    *,
    requests: list[OrderRequest],
    fills: list[Fill],
    accounting_journal: tuple[PortfolioJournalEntry, ...],
    unfilled_dispositions: dict[str, OrderDisposition] | None = None,
) -> LifecycleAnalysis:
    """Reconcile order and execution counts and derive long-only trade lifecycles."""
    requests_by_id = _validate_requests(requests)
    dispositions = unfilled_dispositions or {}
    unknown_dispositions = set(dispositions) - set(requests_by_id)
    if unknown_dispositions:
        raise LifecycleAnalysisError("unfilled disposition references an unknown order")
    _validate_fill_journal(fills, accounting_journal)

    fills_by_order: dict[str, list[Fill]] = {request.client_order_id: [] for request in requests}
    for fill in fills:
        request = requests_by_id.get(fill.client_order_id)
        if request is None:
            raise LifecycleAnalysisError("fill references an unknown order request")
        if fill.side != request.side:
            raise LifecycleAnalysisError("fill side does not match order request")
        fills_by_order[fill.client_order_id].append(fill)

    order_records: list[OrderLifecycleRecord] = []
    partial_fill_execution_count = 0
    for order_sequence, request in enumerate(requests, start=1):
        order_fills = fills_by_order[request.client_order_id]
        executed_quantity = sum((fill.qty_btc for fill in order_fills), Decimal("0"))
        if executed_quantity > request.requested_quantity_btc:
            raise LifecycleAnalysisError("aggregate executions exceed requested order quantity")
        disposition = dispositions.get(request.client_order_id)
        if executed_quantity == request.requested_quantity_btc:
            if disposition is not None:
                raise LifecycleAnalysisError("filled order cannot have an unfilled disposition")
            state = OrderLifecycleState.FILLED
        elif executed_quantity > 0:
            state = OrderLifecycleState.PARTIALLY_FILLED
        elif disposition == OrderDisposition.EXPIRED:
            state = OrderLifecycleState.EXPIRED
        elif disposition == OrderDisposition.EXECUTION_REJECTED:
            state = OrderLifecycleState.EXECUTION_REJECTED
        else:
            state = OrderLifecycleState.UNFILLED
        partial_fill_execution_count += sum(
            1 for fill in order_fills if fill.qty_btc < request.requested_quantity_btc
        )
        order_records.append(
            OrderLifecycleRecord(
                order_sequence=order_sequence,
                client_order_id=request.client_order_id,
                origin=request.origin,
                side=request.side,
                requested_quantity_btc=request.requested_quantity_btc,
                executed_quantity_btc=executed_quantity,
                remaining_quantity_btc=request.requested_quantity_btc - executed_quantity,
                execution_count=len(order_fills),
                state=state,
                unfilled_disposition=disposition,
            )
        )

    inventory = Decimal("0")
    inventory_cost_basis = Decimal("0")
    unallocated_entry_fees = Decimal("0")
    closed_trades: list[ClosedTradeRecord] = []
    round_trips: list[RoundTripRecord] = []
    cycle_start_execution_index: int | None = None
    cycle_entry_execution_count = 0
    cycle_exit_execution_count = 0
    cycle_quantity = Decimal("0")
    cycle_net_pnl = Decimal("0")

    for execution_index, fill in enumerate(fills, start=1):
        if fill.side == Side.BUY:
            if inventory == 0:
                cycle_start_execution_index = execution_index
                cycle_entry_execution_count = 0
                cycle_exit_execution_count = 0
                cycle_quantity = Decimal("0")
                cycle_net_pnl = Decimal("0")
            inventory += fill.qty_btc
            inventory_cost_basis += fill.notional_usd
            unallocated_entry_fees += fill.fee_usd
            cycle_entry_execution_count += 1
            cycle_quantity += fill.qty_btc
            cycle_net_pnl -= fill.notional_usd + fill.fee_usd
            continue

        if fill.qty_btc > inventory:
            raise LifecycleAnalysisError("sell execution exceeds lifecycle inventory")
        allocated_basis = (
            inventory_cost_basis
            if fill.qty_btc == inventory
            else inventory_cost_basis * (fill.qty_btc / inventory)
        )
        allocated_entry_fees = (
            unallocated_entry_fees
            if fill.qty_btc == inventory
            else unallocated_entry_fees * (fill.qty_btc / inventory)
        )
        realized_gross_pnl = fill.notional_usd - allocated_basis
        realized_net_pnl = realized_gross_pnl - allocated_entry_fees - fill.fee_usd
        inventory -= fill.qty_btc
        inventory_cost_basis -= allocated_basis
        unallocated_entry_fees -= allocated_entry_fees
        if inventory == 0:
            inventory = Decimal("0")
            inventory_cost_basis = Decimal("0")
            unallocated_entry_fees = Decimal("0")
        cycle_exit_execution_count += 1
        cycle_net_pnl += fill.notional_usd - fill.fee_usd
        closed_trades.append(
            ClosedTradeRecord(
                closed_trade_sequence=len(closed_trades) + 1,
                execution_index=execution_index,
                client_order_id=fill.client_order_id,
                quantity_btc=fill.qty_btc,
                exit_price_usd=fill.price_usd,
                allocated_gross_cost_basis_usd=allocated_basis,
                allocated_entry_fees_usd=allocated_entry_fees,
                exit_fee_usd=fill.fee_usd,
                realized_gross_pnl_usd=realized_gross_pnl,
                realized_net_pnl_after_fees_usd=realized_net_pnl,
                outcome=_outcome(realized_net_pnl),
                inventory_after_btc=inventory,
            )
        )
        if inventory == 0:
            if cycle_start_execution_index is None:
                raise LifecycleAnalysisError("round trip closed without an entry execution")
            round_trips.append(
                RoundTripRecord(
                    round_trip_sequence=len(round_trips) + 1,
                    start_execution_index=cycle_start_execution_index,
                    end_execution_index=execution_index,
                    entry_execution_count=cycle_entry_execution_count,
                    exit_execution_count=cycle_exit_execution_count,
                    quantity_btc=cycle_quantity,
                    net_pnl_after_fees_usd=cycle_net_pnl,
                    outcome=_outcome(cycle_net_pnl),
                )
            )
            cycle_start_execution_index = None

    final_fill_entries = tuple(
        entry for entry in accounting_journal if entry.entry_type == PortfolioJournalEntryType.FILL
    )
    if final_fill_entries:
        final_entry = final_fill_entries[-1]
        if (
            final_entry.inventory_after_btc != inventory
            or final_entry.inventory_cost_basis_after_usd != inventory_cost_basis
        ):
            raise LifecycleAnalysisError("lifecycle inventory does not match accounting journal")

    outcomes = [trade.outcome for trade in closed_trades]
    return LifecycleAnalysis(
        orders=tuple(order_records),
        closed_trades=tuple(closed_trades),
        round_trips=tuple(round_trips),
        order_count=len(order_records),
        strategy_order_count=sum(record.origin == OrderOrigin.STRATEGY for record in order_records),
        terminal_liquidation_order_count=sum(
            record.origin == OrderOrigin.TERMINAL_LIQUIDATION for record in order_records
        ),
        filled_order_count=sum(
            record.state == OrderLifecycleState.FILLED for record in order_records
        ),
        partially_filled_order_count=sum(
            record.state == OrderLifecycleState.PARTIALLY_FILLED for record in order_records
        ),
        unfilled_order_count=sum(record.executed_quantity_btc == 0 for record in order_records),
        orders_with_remaining_quantity_count=sum(
            record.remaining_quantity_btc > 0 for record in order_records
        ),
        expired_order_count=sum(
            record.unfilled_disposition == OrderDisposition.EXPIRED for record in order_records
        ),
        execution_rejected_order_count=sum(
            record.unfilled_disposition == OrderDisposition.EXECUTION_REJECTED
            for record in order_records
        ),
        execution_count=len(fills),
        buy_execution_count=sum(fill.side == Side.BUY for fill in fills),
        sell_execution_count=sum(fill.side == Side.SELL for fill in fills),
        partial_fill_execution_count=partial_fill_execution_count,
        round_trip_count=len(round_trips),
        open_round_trip_count=int(inventory > 0),
        closed_trade_count=len(closed_trades),
        winning_closed_trade_count=outcomes.count(TradeOutcome.WIN),
        losing_closed_trade_count=outcomes.count(TradeOutcome.LOSS),
        breakeven_closed_trade_count=outcomes.count(TradeOutcome.BREAKEVEN),
        realized_closed_trade_net_pnl_after_fees_usd=sum(
            (trade.realized_net_pnl_after_fees_usd for trade in closed_trades),
            Decimal("0"),
        ),
        completed_round_trip_net_pnl_after_fees_usd=sum(
            (trip.net_pnl_after_fees_usd for trip in round_trips),
            Decimal("0"),
        ),
        open_inventory_btc=inventory,
        open_inventory_cost_basis_usd=inventory_cost_basis,
        open_inventory_entry_fees_usd=unallocated_entry_fees,
    )
