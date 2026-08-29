"""Exact portfolio accounting for deterministic backtests."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from market.domain.models import Fill, Side


class PortfolioAccountingError(ValueError):
    """Raised before a fill or mark could violate the accounting contract."""


class PortfolioJournalEntryType(str, Enum):
    OPENING_BALANCE = "opening_balance"
    FILL = "fill"


@dataclass(frozen=True)
class PortfolioJournalEntry:
    """Immutable state transition caused by an opening balance or execution fill."""

    journal_sequence: int
    event_sequence: int | None
    entry_type: PortfolioJournalEntryType
    ts: str | None
    client_order_id: str | None
    broker_order_id: str | None
    side: Side | None
    quantity_btc: Decimal
    price_usd: Decimal
    executed_notional_usd: Decimal
    cash_delta_usd: Decimal
    inventory_delta_btc: Decimal
    inventory_cost_basis_delta_usd: Decimal
    realized_gross_pnl_delta_usd: Decimal
    fee_delta_usd: Decimal
    cash_after_usd: Decimal
    inventory_after_btc: Decimal
    inventory_cost_basis_after_usd: Decimal
    realized_gross_pnl_after_usd: Decimal
    cumulative_fees_after_usd: Decimal
    accounting_identity_residual_usd: Decimal


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Marked portfolio state with a separate estimated net liquidation value."""

    cash_usd: Decimal
    inventory_btc: Decimal
    inventory_cost_basis_usd: Decimal
    average_entry_price_usd: Decimal | None
    mark_price_usd: Decimal
    inventory_market_value_usd: Decimal
    realized_gross_pnl_usd: Decimal
    unrealized_gross_pnl_usd: Decimal
    cumulative_fees_usd: Decimal
    marked_equity_usd: Decimal
    estimated_liquidation_price_usd: Decimal
    estimated_liquidation_fee_usd: Decimal
    net_liquidation_value_usd: Decimal
    marked_net_pnl_after_fees_usd: Decimal
    net_liquidation_pnl_after_fees_usd: Decimal
    accounting_identity_residual_usd: Decimal


@dataclass
class PortfolioAccount:
    """Long-only, weighted-average portfolio journal using exact decimal arithmetic."""

    starting_cash_usd: Decimal
    cash_usd: Decimal = field(init=False)
    inventory_btc: Decimal = field(init=False, default=Decimal("0"))
    inventory_cost_basis_usd: Decimal = field(init=False, default=Decimal("0"))
    realized_gross_pnl_usd: Decimal = field(init=False, default=Decimal("0"))
    cumulative_fees_usd: Decimal = field(init=False, default=Decimal("0"))
    _journal: list[PortfolioJournalEntry] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self._require_finite(self.starting_cash_usd, "starting_cash_usd")
        if self.starting_cash_usd < 0:
            raise PortfolioAccountingError("starting_cash_usd must be >= 0")
        self.cash_usd = self.starting_cash_usd
        self._journal.append(
            PortfolioJournalEntry(
                journal_sequence=1,
                event_sequence=None,
                entry_type=PortfolioJournalEntryType.OPENING_BALANCE,
                ts=None,
                client_order_id=None,
                broker_order_id=None,
                side=None,
                quantity_btc=Decimal("0"),
                price_usd=Decimal("0"),
                executed_notional_usd=Decimal("0"),
                cash_delta_usd=self.starting_cash_usd,
                inventory_delta_btc=Decimal("0"),
                inventory_cost_basis_delta_usd=Decimal("0"),
                realized_gross_pnl_delta_usd=Decimal("0"),
                fee_delta_usd=Decimal("0"),
                cash_after_usd=self.cash_usd,
                inventory_after_btc=self.inventory_btc,
                inventory_cost_basis_after_usd=self.inventory_cost_basis_usd,
                realized_gross_pnl_after_usd=self.realized_gross_pnl_usd,
                cumulative_fees_after_usd=self.cumulative_fees_usd,
                accounting_identity_residual_usd=Decimal("0"),
            )
        )

    @property
    def journal(self) -> tuple[PortfolioJournalEntry, ...]:
        return tuple(self._journal)

    @staticmethod
    def _require_finite(value: Decimal, name: str) -> None:
        if not value.is_finite():
            raise PortfolioAccountingError(f"{name} must be finite")

    def _identity_residual(self, mark_price_usd: Decimal) -> Decimal:
        market_value = self.inventory_btc * mark_price_usd
        unrealized_gross_pnl = market_value - self.inventory_cost_basis_usd
        marked_equity = self.cash_usd + market_value
        explained_equity = (
            self.starting_cash_usd
            + self.realized_gross_pnl_usd
            + unrealized_gross_pnl
            - self.cumulative_fees_usd
        )
        residual = marked_equity - explained_equity
        # Tolerance for Decimal arithmetic precision (e.g., division in cost basis allocation)
        return Decimal("0") if abs(residual) < Decimal("1E-20") else residual

    def apply_fill(self, fill: Fill, *, event_sequence: int) -> PortfolioJournalEntry:
        """Apply one immutable fill transition or fail without mutating account state."""
        quantity = fill.qty_btc
        price = fill.price_usd
        fee = fill.fee_usd
        for value, name in (
            (quantity, "fill quantity"),
            (price, "fill price"),
            (fee, "fill fee"),
        ):
            self._require_finite(value, name)
        if quantity <= 0:
            raise PortfolioAccountingError("fill quantity must be > 0")
        if price <= 0:
            raise PortfolioAccountingError("fill price must be > 0")
        if fee < 0:
            raise PortfolioAccountingError("fill fee must be >= 0")
        if event_sequence <= 0:
            raise PortfolioAccountingError("event_sequence must be > 0")
        previous_event_sequence = self._journal[-1].event_sequence
        if previous_event_sequence is not None and event_sequence <= previous_event_sequence:
            raise PortfolioAccountingError("fill event_sequence must increase monotonically")

        executed_notional = quantity * price
        if fill.side == Side.BUY:
            total_cash_required = executed_notional + fee
            if total_cash_required > self.cash_usd:
                raise PortfolioAccountingError("insufficient cash for buy fill")
            cash_delta = -total_cash_required
            inventory_delta = quantity
            cost_basis_delta = executed_notional
            realized_delta = Decimal("0")
        else:
            if quantity > self.inventory_btc:
                raise PortfolioAccountingError("sell fill exceeds BTC inventory")
            allocated_cost_basis = (
                self.inventory_cost_basis_usd
                if quantity == self.inventory_btc
                else self.inventory_cost_basis_usd * (quantity / self.inventory_btc)
            )
            cash_delta = executed_notional - fee
            inventory_delta = -quantity
            cost_basis_delta = -allocated_cost_basis
            realized_delta = executed_notional - allocated_cost_basis

        self.cash_usd += cash_delta
        self.inventory_btc += inventory_delta
        self.inventory_cost_basis_usd += cost_basis_delta
        self.realized_gross_pnl_usd += realized_delta
        self.cumulative_fees_usd += fee
        if self.inventory_btc == 0:
            self.inventory_btc = Decimal("0")
            self.inventory_cost_basis_usd = Decimal("0")

        residual = self._identity_residual(price)
        if residual != 0:
            raise RuntimeError(f"portfolio accounting identity violated by {residual}")

        entry = PortfolioJournalEntry(
            journal_sequence=len(self._journal) + 1,
            event_sequence=event_sequence,
            entry_type=PortfolioJournalEntryType.FILL,
            ts=fill.ts.isoformat(),
            client_order_id=fill.client_order_id,
            broker_order_id=fill.broker_order_id,
            side=fill.side,
            quantity_btc=quantity,
            price_usd=price,
            executed_notional_usd=executed_notional,
            cash_delta_usd=cash_delta,
            inventory_delta_btc=inventory_delta,
            inventory_cost_basis_delta_usd=cost_basis_delta,
            realized_gross_pnl_delta_usd=realized_delta,
            fee_delta_usd=fee,
            cash_after_usd=self.cash_usd,
            inventory_after_btc=self.inventory_btc,
            inventory_cost_basis_after_usd=self.inventory_cost_basis_usd,
            realized_gross_pnl_after_usd=self.realized_gross_pnl_usd,
            cumulative_fees_after_usd=self.cumulative_fees_usd,
            accounting_identity_residual_usd=residual,
        )
        self._journal.append(entry)
        return entry

    def snapshot(
        self,
        *,
        mark_price_usd: Decimal,
        estimated_liquidation_price_usd: Decimal,
        estimated_liquidation_fee_usd: Decimal,
    ) -> PortfolioSnapshot:
        """Mark inventory and estimate net liquidation under declared exit costs."""
        for value, name in (
            (mark_price_usd, "mark_price_usd"),
            (estimated_liquidation_price_usd, "estimated_liquidation_price_usd"),
            (estimated_liquidation_fee_usd, "estimated_liquidation_fee_usd"),
        ):
            self._require_finite(value, name)
        if mark_price_usd <= 0:
            raise PortfolioAccountingError("mark_price_usd must be > 0")
        if estimated_liquidation_price_usd <= 0:
            raise PortfolioAccountingError("estimated_liquidation_price_usd must be > 0")
        if estimated_liquidation_fee_usd < 0:
            raise PortfolioAccountingError("estimated_liquidation_fee_usd must be >= 0")
        if self.inventory_btc == 0 and estimated_liquidation_fee_usd != 0:
            raise PortfolioAccountingError("flat inventory requires zero estimated liquidation fee")

        inventory_market_value = self.inventory_btc * mark_price_usd
        unrealized_gross_pnl = inventory_market_value - self.inventory_cost_basis_usd
        marked_equity = self.cash_usd + inventory_market_value
        net_liquidation_value = (
            self.cash_usd
            + self.inventory_btc * estimated_liquidation_price_usd
            - estimated_liquidation_fee_usd
        )
        residual = self._identity_residual(mark_price_usd)
        if residual != 0:
            raise RuntimeError(f"portfolio accounting identity violated by {residual}")

        average_entry_price = (
            self.inventory_cost_basis_usd / self.inventory_btc if self.inventory_btc > 0 else None
        )
        return PortfolioSnapshot(
            cash_usd=self.cash_usd,
            inventory_btc=self.inventory_btc,
            inventory_cost_basis_usd=self.inventory_cost_basis_usd,
            average_entry_price_usd=average_entry_price,
            mark_price_usd=mark_price_usd,
            inventory_market_value_usd=inventory_market_value,
            realized_gross_pnl_usd=self.realized_gross_pnl_usd,
            unrealized_gross_pnl_usd=unrealized_gross_pnl,
            cumulative_fees_usd=self.cumulative_fees_usd,
            marked_equity_usd=marked_equity,
            estimated_liquidation_price_usd=estimated_liquidation_price_usd,
            estimated_liquidation_fee_usd=estimated_liquidation_fee_usd,
            net_liquidation_value_usd=net_liquidation_value,
            marked_net_pnl_after_fees_usd=marked_equity - self.starting_cash_usd,
            net_liquidation_pnl_after_fees_usd=(net_liquidation_value - self.starting_cash_usd),
            accounting_identity_residual_usd=residual,
        )
