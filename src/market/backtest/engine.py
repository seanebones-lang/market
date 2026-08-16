"""Offline backtest for pure strategies on historical candles (real or cached)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from market.backtest.accounting import (
    PortfolioAccount,
    PortfolioJournalEntry,
    PortfolioSnapshot,
)
from market.backtest.costs import (
    BPS_DIVISOR,
    CostInputClassification,
    TransactionFeeTreatment,
    VenueCostAssumptions,
    VenueCostProfile,
    resolve_venue_cost,
)
from market.backtest.lifecycle import (
    LifecycleAnalysis,
    OrderDisposition,
    OrderOrigin,
    OrderRequest,
    analyze_lifecycle,
)
from market.data.quality import require_clean_candles
from market.domain.models import Balances, Candle, D, Fill, Intent, Position, Side
from market.risk.gate import RiskConfig, RiskGate, RiskState
from market.strategy.slow_trend import SlowTrendConfig, SlowTrendV1

SCHEMA_VERSION = 8


class ExecutionModel(str, Enum):
    NEXT_BAR_OPEN = "next_bar_open"
    NEXT_BAR_OPEN_BID_ASK = "next_bar_open_bid_ask"


class TerminalLiquidationModel(str, Enum):
    LAST_BAR_CLOSE = "last_bar_close"
    LAST_BAR_CLOSE_BID_ASK = "last_bar_close_bid_ask"


class ExecutionAssumptions(BaseModel):
    """Declared synthetic execution inputs; these are not observed venue costs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: ExecutionModel = ExecutionModel.NEXT_BAR_OPEN
    quoted_spread_bps_assumption: Decimal = Decimal("0")
    adverse_slippage_bps_assumption: Decimal = Decimal("0")

    @field_validator(
        "quoted_spread_bps_assumption",
        "adverse_slippage_bps_assumption",
        mode="before",
    )
    @classmethod
    def _exact_decimal(cls, value: Any) -> Decimal:
        if isinstance(value, float):
            raise TypeError("float not allowed for execution assumptions")
        return D(value)

    @model_validator(mode="after")
    def _valid_ranges_and_model(self) -> Self:
        if self.quoted_spread_bps_assumption < 0:
            raise ValueError("quoted_spread_bps_assumption must be >= 0")
        if self.quoted_spread_bps_assumption >= Decimal("20000"):
            raise ValueError("quoted_spread_bps_assumption must be < 20000")
        if self.adverse_slippage_bps_assumption < 0:
            raise ValueError("adverse_slippage_bps_assumption must be >= 0")
        if self.adverse_slippage_bps_assumption >= BPS_DIVISOR:
            raise ValueError("adverse_slippage_bps_assumption must be < 10000")
        if self.model == ExecutionModel.NEXT_BAR_OPEN and (
            self.quoted_spread_bps_assumption != 0 or self.adverse_slippage_bps_assumption != 0
        ):
            raise ValueError(
                "next_bar_open requires zero spread and slippage assumptions; "
                "use next_bar_open_bid_ask"
            )
        return self


@dataclass(frozen=True)
class ExecutionPrice:
    """Synthetic touch and simulated fill derived from a next-bar reference open."""

    reference_open_usd: Decimal
    synthetic_bid_usd: Decimal
    synthetic_ask_usd: Decimal
    pre_slippage_touch_usd: Decimal
    fill_price_usd: Decimal


@dataclass(frozen=True)
class TerminalLiquidationPrice:
    """Synthetic terminal sell derived from the final bar's reference close."""

    reference_close_usd: Decimal
    synthetic_bid_usd: Decimal
    synthetic_ask_usd: Decimal
    pre_slippage_touch_usd: Decimal
    fill_price_usd: Decimal


def calculate_execution_price(
    assumptions: ExecutionAssumptions,
    side: Side,
    reference_open_usd: Decimal,
) -> ExecutionPrice:
    """Calculate a deterministic next-open fill under declared assumptions."""
    if reference_open_usd <= 0:
        raise ValueError("reference_open_usd must be > 0")

    if assumptions.model == ExecutionModel.NEXT_BAR_OPEN:
        return ExecutionPrice(
            reference_open_usd=reference_open_usd,
            synthetic_bid_usd=reference_open_usd,
            synthetic_ask_usd=reference_open_usd,
            pre_slippage_touch_usd=reference_open_usd,
            fill_price_usd=reference_open_usd,
        )

    half_spread_rate = assumptions.quoted_spread_bps_assumption / (Decimal("2") * BPS_DIVISOR)
    slippage_rate = assumptions.adverse_slippage_bps_assumption / BPS_DIVISOR
    synthetic_bid = reference_open_usd * (Decimal("1") - half_spread_rate)
    synthetic_ask = reference_open_usd * (Decimal("1") + half_spread_rate)
    if side == Side.BUY:
        touch = synthetic_ask
        fill_price = touch * (Decimal("1") + slippage_rate)
    else:
        touch = synthetic_bid
        fill_price = touch * (Decimal("1") - slippage_rate)

    return ExecutionPrice(
        reference_open_usd=reference_open_usd,
        synthetic_bid_usd=synthetic_bid,
        synthetic_ask_usd=synthetic_ask,
        pre_slippage_touch_usd=touch,
        fill_price_usd=fill_price,
    )


def calculate_terminal_liquidation_price(
    assumptions: ExecutionAssumptions,
    reference_close_usd: Decimal,
) -> TerminalLiquidationPrice:
    """Calculate a deterministic end-of-data sell under the run's execution assumptions."""
    price = calculate_execution_price(assumptions, Side.SELL, reference_close_usd)
    return TerminalLiquidationPrice(
        reference_close_usd=reference_close_usd,
        synthetic_bid_usd=price.synthetic_bid_usd,
        synthetic_ask_usd=price.synthetic_ask_usd,
        pre_slippage_touch_usd=price.pre_slippage_touch_usd,
        fill_price_usd=price.fill_price_usd,
    )


def terminal_liquidation_model_for(
    execution_model: ExecutionModel,
) -> TerminalLiquidationModel:
    if execution_model == ExecutionModel.NEXT_BAR_OPEN:
        return TerminalLiquidationModel.LAST_BAR_CLOSE
    return TerminalLiquidationModel.LAST_BAR_CLOSE_BID_ASK


class BacktestEventType(str, Enum):
    BAR_OPEN = "bar_open"
    ORDER_ELIGIBLE = "order_eligible"
    FILL = "fill"
    EXECUTION_REJECTED = "execution_rejected"
    BAR_CLOSE = "bar_close"
    DECISION_ACCEPTED = "decision_accepted"
    DECISION_BLOCKED = "decision_blocked"
    ORDER_EXPIRED = "order_expired"
    TERMINAL_LIQUIDATION_REQUESTED = "terminal_liquidation_requested"


class EquityPointStage(str, Enum):
    BAR_CLOSE_MARK = "bar_close_mark"
    POST_TERMINAL_LIQUIDATION = "post_terminal_liquidation"


@dataclass(frozen=True)
class BacktestEvent:
    sequence: int
    event_type: BacktestEventType
    ts: str
    bar_ts: str
    client_order_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PendingOrder:
    intent: Intent
    signal_bar_ts: str
    signal_bar_close: Decimal
    decision_ts: str
    eligible_bar_ts: str


@dataclass
class EquityPoint:
    ts: str
    marked_equity_usd: Decimal
    cash_usd: Decimal
    inventory_btc: Decimal
    mark_price_usd: Decimal
    inventory_market_value_usd: Decimal
    inventory_cost_basis_usd: Decimal
    average_entry_price_usd: Decimal | None
    realized_gross_pnl_usd: Decimal
    unrealized_gross_pnl_usd: Decimal
    cumulative_fees_usd: Decimal
    net_liquidation_value_usd: Decimal
    estimated_liquidation_price_usd: Decimal
    estimated_liquidation_fee_usd: Decimal
    marked_net_pnl_after_fees_usd: Decimal
    net_liquidation_pnl_after_fees_usd: Decimal
    accounting_identity_residual_usd: Decimal
    stage: EquityPointStage = EquityPointStage.BAR_CLOSE_MARK

    @classmethod
    def from_snapshot(
        cls,
        *,
        ts: str,
        stage: EquityPointStage,
        snapshot: PortfolioSnapshot,
    ) -> EquityPoint:
        return cls(
            ts=ts,
            marked_equity_usd=snapshot.marked_equity_usd,
            cash_usd=snapshot.cash_usd,
            inventory_btc=snapshot.inventory_btc,
            mark_price_usd=snapshot.mark_price_usd,
            inventory_market_value_usd=snapshot.inventory_market_value_usd,
            inventory_cost_basis_usd=snapshot.inventory_cost_basis_usd,
            average_entry_price_usd=snapshot.average_entry_price_usd,
            realized_gross_pnl_usd=snapshot.realized_gross_pnl_usd,
            unrealized_gross_pnl_usd=snapshot.unrealized_gross_pnl_usd,
            cumulative_fees_usd=snapshot.cumulative_fees_usd,
            net_liquidation_value_usd=snapshot.net_liquidation_value_usd,
            estimated_liquidation_price_usd=snapshot.estimated_liquidation_price_usd,
            estimated_liquidation_fee_usd=snapshot.estimated_liquidation_fee_usd,
            marked_net_pnl_after_fees_usd=snapshot.marked_net_pnl_after_fees_usd,
            net_liquidation_pnl_after_fees_usd=(snapshot.net_liquidation_pnl_after_fees_usd),
            accounting_identity_residual_usd=snapshot.accounting_identity_residual_usd,
            stage=stage,
        )


@dataclass
class BacktestResult:
    fills: list[Fill] = field(default_factory=list)
    events: list[BacktestEvent] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    accounting_journal: list[PortfolioJournalEntry] = field(default_factory=list)
    lifecycle: LifecycleAnalysis = field(default_factory=LifecycleAnalysis)
    final_inventory_btc: Decimal = Decimal("0")
    final_cash_usd: Decimal = Decimal("0")
    starting_cash_usd: Decimal = Decimal("0")
    final_inventory_cost_basis_usd: Decimal = Decimal("0")
    final_average_entry_price_usd: Decimal | None = None
    realized_gross_pnl_usd: Decimal = Decimal("0")
    unrealized_gross_pnl_usd: Decimal = Decimal("0")
    marked_equity_usd: Decimal = Decimal("0")
    net_liquidation_value_usd: Decimal = Decimal("0")
    marked_net_pnl_after_fees_usd: Decimal = Decimal("0")
    net_liquidation_pnl_after_fees_usd: Decimal = Decimal("0")
    accounting_identity_residual_usd: Decimal = Decimal("0")
    intents: int = 0
    allowed: int = 0
    blocked: int = 0
    bars: int = 0
    first_ts: str | None = None
    last_ts: str | None = None
    max_net_liquidation_drawdown_usd: Decimal = Decimal("0")
    max_marked_equity_usd: Decimal = Decimal("0")
    cumulative_fees_usd: Decimal = Decimal("0")
    source: str = ""
    strategy: str = "slow_trend_v1"
    fast_ema: int = 12
    slow_ema: int = 26
    qty_btc: Decimal = Decimal("0.001")
    venue_cost_profile: VenueCostProfile = VenueCostProfile.LEGACY_UNCLASSIFIED
    venue: str = "unclassified"
    routing: str = "unclassified"
    api_version: str = "unclassified"
    cost_input_classification: CostInputClassification = CostInputClassification.LEGACY_UNCLASSIFIED
    transaction_fee_treatment: TransactionFeeTreatment = (
        TransactionFeeTreatment.LEGACY_TRANSACTION_FEE_PER_FILL_ASSUMPTION
    )
    transaction_fee_bps_per_fill_assumption: Decimal = Decimal("5")
    transaction_fee_bps_per_fill_applied: Decimal = Decimal("5")
    execution_model: ExecutionModel = ExecutionModel.NEXT_BAR_OPEN
    quoted_spread_bps_assumption: Decimal = Decimal("0")
    adverse_slippage_bps_assumption: Decimal = Decimal("0")
    end_of_data_orders: int = 0
    terminal_liquidation_model: TerminalLiquidationModel = TerminalLiquidationModel.LAST_BAR_CLOSE
    position_before_terminal_liquidation_btc: Decimal = Decimal("0")
    terminal_liquidation_fills: int = 0
    terminal_liquidation_qty_btc: Decimal = Decimal("0")
    terminal_liquidation_fee_usd: Decimal = Decimal("0")

    @property
    def net_liquidation_return_pct(self) -> Decimal:
        if self.starting_cash_usd == 0:
            return Decimal("0")
        return (self.net_liquidation_pnl_after_fees_usd / self.starting_cash_usd) * Decimal("100")

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source": self.source,
            "market_data_source": self.source,
            "strategy": self.strategy,
            "fast_ema": self.fast_ema,
            "slow_ema": self.slow_ema,
            "qty_btc": str(self.qty_btc),
            "venue_cost_profile": self.venue_cost_profile.value,
            "venue": self.venue,
            "routing": self.routing,
            "api_version": self.api_version,
            "cost_input_classification": self.cost_input_classification.value,
            "transaction_fee_treatment": self.transaction_fee_treatment.value,
            "fee_calculation_basis": "executed_notional_per_fill",
            "transaction_fee_bps_per_fill_assumption": str(
                self.transaction_fee_bps_per_fill_assumption
            ),
            "transaction_fee_bps_per_fill_applied": str(self.transaction_fee_bps_per_fill_applied),
            "execution_model": self.execution_model.value,
            "quoted_spread_bps_assumption": str(self.quoted_spread_bps_assumption),
            "adverse_slippage_bps_assumption": str(self.adverse_slippage_bps_assumption),
            "terminal_liquidation_model": self.terminal_liquidation_model.value,
            "bars": self.bars,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "fills": len(self.fills),
            "events": len(self.events),
            "end_of_data_orders": self.end_of_data_orders,
            "terminal_liquidation_fills": self.terminal_liquidation_fills,
            "terminal_liquidation_qty_btc": str(self.terminal_liquidation_qty_btc),
            "terminal_liquidation_fee_usd": str(self.terminal_liquidation_fee_usd),
            "intents": self.intents,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "accounting_method": "weighted_average_gross_cost_basis_fees_separate",
            "accounting_journal_entries": len(self.accounting_journal),
            "starting_cash_usd": str(self.starting_cash_usd),
            "final_cash_usd": str(self.final_cash_usd),
            "final_inventory_btc": str(self.final_inventory_btc),
            "final_inventory_cost_basis_usd": str(self.final_inventory_cost_basis_usd),
            "final_average_entry_price_usd": (
                str(self.final_average_entry_price_usd)
                if self.final_average_entry_price_usd is not None
                else None
            ),
            "realized_gross_pnl_usd": str(self.realized_gross_pnl_usd),
            "unrealized_gross_pnl_usd": str(self.unrealized_gross_pnl_usd),
            "cumulative_fees_usd": str(self.cumulative_fees_usd),
            "marked_equity_usd": str(self.marked_equity_usd),
            "net_liquidation_value_usd": str(self.net_liquidation_value_usd),
            "marked_net_pnl_after_fees_usd": str(self.marked_net_pnl_after_fees_usd),
            "net_liquidation_pnl_after_fees_usd": str(self.net_liquidation_pnl_after_fees_usd),
            "net_liquidation_return_pct": str(self.net_liquidation_return_pct),
            "accounting_identity_residual_usd": str(self.accounting_identity_residual_usd),
            "max_marked_equity_usd": str(self.max_marked_equity_usd),
            "max_net_liquidation_drawdown_usd": str(self.max_net_liquidation_drawdown_usd),
            "position_before_terminal_liquidation_btc": str(
                self.position_before_terminal_liquidation_btc
            ),
            **self.lifecycle.summary(),
        }


def run_backtest(
    candles: list[Candle],
    starting_cash_usd: Decimal = Decimal("1000"),
    qty_btc: Decimal = Decimal("0.001"),
    strategy_cfg: SlowTrendConfig | None = None,
    risk_cfg: RiskConfig | None = None,
    source: str = "",
    record_equity_every: int = 1,
    execution_model: ExecutionModel = ExecutionModel.NEXT_BAR_OPEN,
    quoted_spread_bps_assumption: Decimal = Decimal("0"),
    adverse_slippage_bps_assumption: Decimal = Decimal("0"),
    venue_cost_profile: VenueCostProfile = VenueCostProfile.LEGACY_UNCLASSIFIED,
    transaction_fee_bps_per_fill_assumption: Decimal | None = None,
) -> BacktestResult:
    """Long-only event replay with decisions after close and fills at the next bar open."""
    execution_assumptions = ExecutionAssumptions(
        model=execution_model,
        quoted_spread_bps_assumption=quoted_spread_bps_assumption,
        adverse_slippage_bps_assumption=adverse_slippage_bps_assumption,
    )
    execution_model = execution_assumptions.model
    terminal_liquidation_model = terminal_liquidation_model_for(execution_model)
    venue_cost_assumptions = VenueCostAssumptions(
        profile=venue_cost_profile,
        transaction_fee_bps_per_fill_assumption=(transaction_fee_bps_per_fill_assumption),
    )
    venue_cost = resolve_venue_cost(
        venue_cost_assumptions,
        execution_model=execution_model.value,
        quoted_spread_bps_assumption=(execution_assumptions.quoted_spread_bps_assumption),
    )
    cost_details = venue_cost.artifact_details()
    cost_metadata = venue_cost_assumptions.metadata
    account = PortfolioAccount(starting_cash_usd=starting_cash_usd)
    if not candles:
        return BacktestResult(
            starting_cash_usd=starting_cash_usd,
            final_cash_usd=starting_cash_usd,
            marked_equity_usd=starting_cash_usd,
            net_liquidation_value_usd=starting_cash_usd,
            max_marked_equity_usd=starting_cash_usd,
            accounting_journal=list(account.journal),
            lifecycle=analyze_lifecycle(
                requests=[],
                fills=[],
                accounting_journal=account.journal,
            ),
            source=source,
            venue_cost_profile=venue_cost_assumptions.profile,
            venue=cost_metadata.venue,
            routing=cost_metadata.routing,
            api_version=cost_metadata.api_version,
            cost_input_classification=cost_metadata.input_classification,
            transaction_fee_treatment=cost_metadata.transaction_fee_treatment,
            transaction_fee_bps_per_fill_assumption=(
                venue_cost.transaction_fee_bps_per_fill_assumption
            ),
            transaction_fee_bps_per_fill_applied=(venue_cost.transaction_fee_bps_per_fill_applied),
            execution_model=execution_model,
            quoted_spread_bps_assumption=(execution_assumptions.quoted_spread_bps_assumption),
            adverse_slippage_bps_assumption=(execution_assumptions.adverse_slippage_bps_assumption),
            terminal_liquidation_model=terminal_liquidation_model,
        )

    require_clean_candles(candles)

    strategy_cfg = strategy_cfg or SlowTrendConfig(order_qty_btc=qty_btc)
    risk_cfg = risk_cfg or RiskConfig(
        max_position_btc=qty_btc,
        max_notional_usd=Decimal("100000"),
        max_daily_loss_usd=Decimal("100000"),
        max_orders_per_hour=10000,
        min_seconds_between_orders=0,
        allow_entries=True,
    )
    strategy = SlowTrendV1(strategy_cfg)
    risk = RiskGate(risk_cfg)
    state = RiskState()

    fills: list[Fill] = []
    events: list[BacktestEvent] = []
    equity_curve: list[EquityPoint] = []
    intents = allowed = blocked = 0
    peak = starting_cash_usd
    net_liquidation_peak = starting_cash_usd
    max_dd = Decimal("0")
    pending: PendingOrder | None = None
    final_snapshot: PortfolioSnapshot | None = None
    lifecycle_requests: list[OrderRequest] = []
    unfilled_dispositions: dict[str, OrderDisposition] = {}

    def emit(
        event_type: BacktestEventType,
        *,
        ts: str,
        bar_ts: str,
        client_order_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        events.append(
            BacktestEvent(
                sequence=len(events) + 1,
                event_type=event_type,
                ts=ts,
                bar_ts=bar_ts,
                client_order_id=client_order_id,
                details=details or {},
            )
        )

    min_bars = strategy_cfg.slow_ema + 2
    for index, bar in enumerate(candles):
        bar_ts = bar.ts.isoformat()
        emit(
            BacktestEventType.BAR_OPEN,
            ts=bar_ts,
            bar_ts=bar_ts,
            details={"open": str(bar.open)},
        )

        # Orders accepted after bar t closes become eligible only at bar t+1 open.
        if pending is not None:
            if pending.eligible_bar_ts != bar_ts:
                raise RuntimeError(
                    "pending order eligibility does not match the next contiguous bar open"
                )
            side = pending.intent.side
            execution_price = calculate_execution_price(
                execution_assumptions,
                side,
                bar.open,
            )
            price_details = {
                "reference_open_usd": str(execution_price.reference_open_usd),
                "synthetic_bid_usd": str(execution_price.synthetic_bid_usd),
                "synthetic_ask_usd": str(execution_price.synthetic_ask_usd),
                "pre_slippage_touch_usd": str(execution_price.pre_slippage_touch_usd),
                "fill_price_usd": str(execution_price.fill_price_usd),
                "quoted_spread_bps_assumption": str(
                    execution_assumptions.quoted_spread_bps_assumption
                ),
                "adverse_slippage_bps_assumption": str(
                    execution_assumptions.adverse_slippage_bps_assumption
                ),
            }
            emit(
                BacktestEventType.ORDER_ELIGIBLE,
                ts=bar_ts,
                bar_ts=bar_ts,
                client_order_id=pending.intent.client_order_id,
                details={
                    "execution_model": execution_model.value,
                    "signal_bar_ts": pending.signal_bar_ts,
                    "decision_ts": pending.decision_ts,
                    **cost_details,
                    **price_details,
                },
            )
            quantity = pending.intent.qty_btc
            price = execution_price.fill_price_usd
            fee = Decimal("0")
            traded = False
            reject_reason: str | None = None
            if side == Side.BUY:
                fee = venue_cost.calculate_fee_usd(
                    executed_quantity=quantity,
                    fill_price_usd=price,
                )
                cost = quantity * price + fee
                if cost <= account.cash_usd:
                    traded = True
                else:
                    blocked += 1
                    reject_reason = "insufficient_cash_at_execution"
            else:
                quantity = min(quantity, account.inventory_btc)
                if quantity > 0:
                    fee = venue_cost.calculate_fee_usd(
                        executed_quantity=quantity,
                        fill_price_usd=price,
                    )
                    traded = True
                else:
                    blocked += 1
                    reject_reason = "no_inventory_at_execution"

            if traded:
                fill_event_sequence = len(events) + 1
                accounting_journal_sequence = len(account.journal) + 1
                fill = Fill(
                    client_order_id=pending.intent.client_order_id,
                    broker_order_id=f"bt-{len(fills) + 1}",
                    side=side,
                    qty_btc=quantity,
                    price_usd=price,
                    fee_usd=fee,
                    ts=bar.ts,
                    raw={
                        "source": source or "backtest",
                        "execution_model": execution_model.value,
                        "signal_bar_ts": pending.signal_bar_ts,
                        "signal_bar_close": str(pending.signal_bar_close),
                        "decision_ts": pending.decision_ts,
                        "eligible_bar_ts": pending.eligible_bar_ts,
                        "fill_bar_ts": bar_ts,
                        "fill_bar_open": str(bar.open),
                        **cost_details,
                        **price_details,
                        "accounting_journal_sequence": accounting_journal_sequence,
                        "reason": pending.intent.reason,
                        "signal_snapshot": pending.intent.signal_snapshot,
                    },
                )
                account.apply_fill(fill, event_sequence=fill_event_sequence)
                fills.append(fill)
                emit(
                    BacktestEventType.FILL,
                    ts=bar_ts,
                    bar_ts=bar_ts,
                    client_order_id=pending.intent.client_order_id,
                    details={
                        "side": side.value,
                        "qty_btc": str(quantity),
                        "price_usd": str(price),
                        "fee_usd": str(fee),
                        "execution_model": execution_model.value,
                        "signal_bar_ts": pending.signal_bar_ts,
                        "accounting_journal_sequence": accounting_journal_sequence,
                        **cost_details,
                        **price_details,
                    },
                )
                state.last_order_ts = bar.ts
                state.order_timestamps.append(bar.ts)
            else:
                unfilled_dispositions[pending.intent.client_order_id] = (
                    OrderDisposition.EXECUTION_REJECTED
                )
                emit(
                    BacktestEventType.EXECUTION_REJECTED,
                    ts=bar_ts,
                    bar_ts=bar_ts,
                    client_order_id=pending.intent.client_order_id,
                    details={
                        "reason": reject_reason,
                        "execution_model": execution_model.value,
                        **cost_details,
                        **price_details,
                    },
                )
            pending = None

        emit(
            BacktestEventType.BAR_CLOSE,
            ts=bar.close_time.isoformat(),
            bar_ts=bar_ts,
            details={"close": str(bar.close)},
        )

        # Mark inventory at mid and separately estimate the costed net liquidation value.
        liquidation_price = calculate_terminal_liquidation_price(
            execution_assumptions,
            bar.close,
        )
        estimated_liquidation_fee = (
            venue_cost.calculate_fee_usd(
                executed_quantity=account.inventory_btc,
                fill_price_usd=liquidation_price.fill_price_usd,
            )
            if account.inventory_btc > 0
            else Decimal("0")
        )
        final_snapshot = account.snapshot(
            mark_price_usd=bar.close,
            estimated_liquidation_price_usd=liquidation_price.fill_price_usd,
            estimated_liquidation_fee_usd=estimated_liquidation_fee,
        )
        peak = max(peak, final_snapshot.marked_equity_usd)
        net_liquidation_peak = max(
            net_liquidation_peak,
            final_snapshot.net_liquidation_value_usd,
        )
        drawdown = net_liquidation_peak - final_snapshot.net_liquidation_value_usd
        max_dd = max(max_dd, drawdown)
        if record_equity_every > 0 and (
            (index + 1) % record_equity_every == 0 or index == len(candles) - 1
        ):
            equity_curve.append(
                EquityPoint.from_snapshot(
                    ts=bar.close_time.isoformat(),
                    stage=EquityPointStage.BAR_CLOSE_MARK,
                    snapshot=final_snapshot,
                )
            )

        if index + 1 < min_bars:
            continue
        window = candles[: index + 1]
        pos = Position(
            qty_btc=account.inventory_btc,
            avg_entry_usd=(
                account.inventory_cost_basis_usd / account.inventory_btc
                if account.inventory_btc > 0
                else None
            ),
        )
        intent = strategy.evaluate(window, pos)
        if intent is not None:
            intents += 1
            decision = risk.evaluate(
                intent,
                pos,
                Balances(usd=account.cash_usd, btc=account.inventory_btc),
                state,
                mark_usd=bar.close,
                now=bar.close_time,
            )
            if not decision.allow or decision.intent is None:
                blocked += 1
                emit(
                    BacktestEventType.DECISION_BLOCKED,
                    ts=bar.close_time.isoformat(),
                    bar_ts=bar_ts,
                    client_order_id=intent.client_order_id,
                    details={
                        "reason": intent.reason,
                        "violations": decision.violations,
                    },
                )
            else:
                allowed += 1
                eligible_bar_ts = bar.close_time.isoformat()
                lifecycle_requests.append(
                    OrderRequest(
                        client_order_id=decision.intent.client_order_id,
                        origin=OrderOrigin.STRATEGY,
                        side=decision.intent.side,
                        requested_quantity_btc=decision.intent.qty_btc,
                    )
                )
                pending = PendingOrder(
                    intent=decision.intent,
                    signal_bar_ts=bar_ts,
                    signal_bar_close=bar.close,
                    decision_ts=bar.close_time.isoformat(),
                    eligible_bar_ts=eligible_bar_ts,
                )
                emit(
                    BacktestEventType.DECISION_ACCEPTED,
                    ts=bar.close_time.isoformat(),
                    bar_ts=bar_ts,
                    client_order_id=decision.intent.client_order_id,
                    details={
                        "reason": decision.intent.reason,
                        "side": decision.intent.side.value,
                        "qty_btc": str(decision.intent.qty_btc),
                        "eligible_bar_ts": eligible_bar_ts,
                        "execution_model": execution_model.value,
                        "venue_cost_profile": venue_cost_assumptions.profile.value,
                    },
                )

    end_of_data_orders = 0
    if pending is not None:
        end_of_data_orders = 1
        unfilled_dispositions[pending.intent.client_order_id] = OrderDisposition.EXPIRED
        emit(
            BacktestEventType.ORDER_EXPIRED,
            ts=candles[-1].close_time.isoformat(),
            bar_ts=candles[-1].ts.isoformat(),
            client_order_id=pending.intent.client_order_id,
            details={
                "reason": "end_of_data_before_eligible_bar",
                "eligible_bar_ts": pending.eligible_bar_ts,
                "signal_bar_ts": pending.signal_bar_ts,
            },
        )

    position_before_terminal_liquidation = account.inventory_btc
    terminal_liquidation_fills = 0
    terminal_liquidation_qty = Decimal("0")
    terminal_liquidation_fee = Decimal("0")
    if position_before_terminal_liquidation > 0:
        terminal_bar = candles[-1]
        terminal_ts = terminal_bar.close_time.isoformat()
        terminal_bar_ts = terminal_bar.ts.isoformat()
        terminal_client_order_id = f"terminal-liquidation-{terminal_ts}"
        lifecycle_requests.append(
            OrderRequest(
                client_order_id=terminal_client_order_id,
                origin=OrderOrigin.TERMINAL_LIQUIDATION,
                side=Side.SELL,
                requested_quantity_btc=position_before_terminal_liquidation,
            )
        )
        terminal_price = calculate_terminal_liquidation_price(
            execution_assumptions,
            terminal_bar.close,
        )
        terminal_liquidation_qty = position_before_terminal_liquidation
        terminal_liquidation_fee = venue_cost.calculate_fee_usd(
            executed_quantity=terminal_liquidation_qty,
            fill_price_usd=terminal_price.fill_price_usd,
        )
        price_details = {
            "reference_close_usd": str(terminal_price.reference_close_usd),
            "synthetic_bid_usd": str(terminal_price.synthetic_bid_usd),
            "synthetic_ask_usd": str(terminal_price.synthetic_ask_usd),
            "pre_slippage_touch_usd": str(terminal_price.pre_slippage_touch_usd),
            "fill_price_usd": str(terminal_price.fill_price_usd),
            "quoted_spread_bps_assumption": str(execution_assumptions.quoted_spread_bps_assumption),
            "adverse_slippage_bps_assumption": str(
                execution_assumptions.adverse_slippage_bps_assumption
            ),
        }
        emit(
            BacktestEventType.TERMINAL_LIQUIDATION_REQUESTED,
            ts=terminal_ts,
            bar_ts=terminal_bar_ts,
            client_order_id=terminal_client_order_id,
            details={
                "reason": "terminal_liquidation_end_of_data",
                "side": Side.SELL.value,
                "qty_btc": str(terminal_liquidation_qty),
                "terminal_liquidation_model": terminal_liquidation_model.value,
                **cost_details,
                **price_details,
            },
        )
        fill_event_sequence = len(events) + 1
        accounting_journal_sequence = len(account.journal) + 1
        terminal_fill = Fill(
            client_order_id=terminal_client_order_id,
            broker_order_id=f"bt-{len(fills) + 1}",
            side=Side.SELL,
            qty_btc=terminal_liquidation_qty,
            price_usd=terminal_price.fill_price_usd,
            fee_usd=terminal_liquidation_fee,
            ts=terminal_bar.close_time,
            raw={
                "source": source or "backtest",
                "terminal_liquidation": True,
                "reason": "terminal_liquidation_end_of_data",
                "terminal_liquidation_model": terminal_liquidation_model.value,
                "reference_bar_ts": terminal_bar_ts,
                "accounting_journal_sequence": accounting_journal_sequence,
                **cost_details,
                **price_details,
            },
        )
        account.apply_fill(terminal_fill, event_sequence=fill_event_sequence)
        fills.append(terminal_fill)
        emit(
            BacktestEventType.FILL,
            ts=terminal_ts,
            bar_ts=terminal_bar_ts,
            client_order_id=terminal_client_order_id,
            details={
                "side": Side.SELL.value,
                "qty_btc": str(terminal_liquidation_qty),
                "price_usd": str(terminal_price.fill_price_usd),
                "fee_usd": str(terminal_liquidation_fee),
                "terminal_liquidation": True,
                "reason": "terminal_liquidation_end_of_data",
                "terminal_liquidation_model": terminal_liquidation_model.value,
                "accounting_journal_sequence": accounting_journal_sequence,
                **cost_details,
                **price_details,
            },
        )
        terminal_liquidation_fills = 1

        final_snapshot = account.snapshot(
            mark_price_usd=terminal_bar.close,
            estimated_liquidation_price_usd=terminal_price.fill_price_usd,
            estimated_liquidation_fee_usd=Decimal("0"),
        )
        peak = max(peak, final_snapshot.marked_equity_usd)
        net_liquidation_peak = max(
            net_liquidation_peak,
            final_snapshot.net_liquidation_value_usd,
        )
        max_dd = max(
            max_dd,
            net_liquidation_peak - final_snapshot.net_liquidation_value_usd,
        )
        equity_curve.append(
            EquityPoint.from_snapshot(
                ts=terminal_ts,
                stage=EquityPointStage.POST_TERMINAL_LIQUIDATION,
                snapshot=final_snapshot,
            )
        )

    if final_snapshot is None:
        raise RuntimeError("nonempty backtest must produce a final accounting snapshot")
    lifecycle = analyze_lifecycle(
        requests=lifecycle_requests,
        fills=fills,
        accounting_journal=account.journal,
        unfilled_dispositions=unfilled_dispositions,
    )

    return BacktestResult(
        fills=fills,
        events=events,
        equity_curve=equity_curve,
        accounting_journal=list(account.journal),
        lifecycle=lifecycle,
        final_inventory_btc=account.inventory_btc,
        final_cash_usd=account.cash_usd,
        starting_cash_usd=starting_cash_usd,
        final_inventory_cost_basis_usd=account.inventory_cost_basis_usd,
        final_average_entry_price_usd=final_snapshot.average_entry_price_usd,
        realized_gross_pnl_usd=final_snapshot.realized_gross_pnl_usd,
        unrealized_gross_pnl_usd=final_snapshot.unrealized_gross_pnl_usd,
        marked_equity_usd=final_snapshot.marked_equity_usd,
        net_liquidation_value_usd=final_snapshot.net_liquidation_value_usd,
        marked_net_pnl_after_fees_usd=(final_snapshot.marked_net_pnl_after_fees_usd),
        net_liquidation_pnl_after_fees_usd=(final_snapshot.net_liquidation_pnl_after_fees_usd),
        accounting_identity_residual_usd=(final_snapshot.accounting_identity_residual_usd),
        intents=intents,
        allowed=allowed,
        blocked=blocked,
        bars=len(candles),
        first_ts=candles[0].ts.isoformat(),
        last_ts=candles[-1].ts.isoformat(),
        max_net_liquidation_drawdown_usd=max_dd,
        max_marked_equity_usd=peak,
        cumulative_fees_usd=account.cumulative_fees_usd,
        source=source,
        strategy="slow_trend_v1",
        fast_ema=strategy_cfg.fast_ema,
        slow_ema=strategy_cfg.slow_ema,
        qty_btc=strategy_cfg.order_qty_btc,
        venue_cost_profile=venue_cost_assumptions.profile,
        venue=cost_metadata.venue,
        routing=cost_metadata.routing,
        api_version=cost_metadata.api_version,
        cost_input_classification=cost_metadata.input_classification,
        transaction_fee_treatment=cost_metadata.transaction_fee_treatment,
        transaction_fee_bps_per_fill_assumption=(
            venue_cost.transaction_fee_bps_per_fill_assumption
        ),
        transaction_fee_bps_per_fill_applied=(venue_cost.transaction_fee_bps_per_fill_applied),
        execution_model=execution_model,
        quoted_spread_bps_assumption=(execution_assumptions.quoted_spread_bps_assumption),
        adverse_slippage_bps_assumption=(execution_assumptions.adverse_slippage_bps_assumption),
        end_of_data_orders=end_of_data_orders,
        terminal_liquidation_model=terminal_liquidation_model,
        position_before_terminal_liquidation_btc=(position_before_terminal_liquidation),
        terminal_liquidation_fills=terminal_liquidation_fills,
        terminal_liquidation_qty_btc=terminal_liquidation_qty,
        terminal_liquidation_fee_usd=terminal_liquidation_fee,
    )


def write_backtest_report(
    result: BacktestResult,
    out_dir: str | Path,
    run_id: str,
) -> dict[str, Path]:
    """Write summary, events, fills, accounting, lifecycle, and marks under a run directory."""
    out = Path(out_dir) / run_id
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    events_path = out / "events.jsonl"
    fills_path = out / "fills.jsonl"
    accounting_path = out / "accounting.jsonl"
    lifecycle_path = out / "lifecycle.jsonl"
    equity_path = out / "equity.jsonl"

    summary_path.write_text(
        json.dumps(result.summary(), indent=2, default=str) + "\n", encoding="utf-8"
    )
    with events_path.open("w", encoding="utf-8") as file:
        for event in result.events:
            row = {
                "schema_version": SCHEMA_VERSION,
                "type": "backtest_event",
                "run_id": run_id,
                "sequence": event.sequence,
                "event_type": event.event_type.value,
                "ts": event.ts,
                "bar_ts": event.bar_ts,
                "client_order_id": event.client_order_id,
                "details": event.details,
            }
            file.write(json.dumps(row, default=str) + "\n")
    with fills_path.open("w", encoding="utf-8") as f:
        for fill in result.fills:
            row = {
                "schema_version": SCHEMA_VERSION,
                "type": "fill",
                "run_id": run_id,
                "mode": "backtest",
                "venue": result.venue,
                "market_data_source": result.source,
                "fill": fill.model_dump(mode="json"),
            }
            f.write(json.dumps(row, default=str) + "\n")
    with accounting_path.open("w", encoding="utf-8") as f:
        for entry in result.accounting_journal:
            payload = asdict(entry)
            payload["entry_type"] = entry.entry_type.value
            payload["side"] = entry.side.value if entry.side is not None else None
            row = {
                "schema_version": SCHEMA_VERSION,
                "type": "portfolio_journal_entry",
                "run_id": run_id,
                **payload,
            }
            f.write(json.dumps(row, default=str) + "\n")
    with lifecycle_path.open("w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "type": "lifecycle_summary",
                    "run_id": run_id,
                    **result.lifecycle.summary(),
                }
            )
            + "\n"
        )
        for order in result.lifecycle.orders:
            payload = asdict(order)
            payload["origin"] = order.origin.value
            payload["side"] = order.side.value
            payload["state"] = order.state.value
            payload["unfilled_disposition"] = (
                order.unfilled_disposition.value if order.unfilled_disposition is not None else None
            )
            f.write(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "type": "order_lifecycle",
                        "run_id": run_id,
                        **payload,
                    },
                    default=str,
                )
                + "\n"
            )
        for trade in result.lifecycle.closed_trades:
            payload = asdict(trade)
            payload["outcome"] = trade.outcome.value
            f.write(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "type": "closed_trade",
                        "run_id": run_id,
                        **payload,
                    },
                    default=str,
                )
                + "\n"
            )
        for trip in result.lifecycle.round_trips:
            payload = asdict(trip)
            payload["outcome"] = trip.outcome.value
            f.write(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "type": "round_trip",
                        "run_id": run_id,
                        **payload,
                    },
                    default=str,
                )
                + "\n"
            )
    with equity_path.open("w", encoding="utf-8") as f:
        for pt in result.equity_curve:
            row = {
                "schema_version": SCHEMA_VERSION,
                "type": "equity",
                "run_id": run_id,
                "ts": pt.ts,
                "marked_equity_usd": str(pt.marked_equity_usd),
                "cash_usd": str(pt.cash_usd),
                "inventory_btc": str(pt.inventory_btc),
                "mark_price_usd": str(pt.mark_price_usd),
                "inventory_market_value_usd": str(pt.inventory_market_value_usd),
                "inventory_cost_basis_usd": str(pt.inventory_cost_basis_usd),
                "average_entry_price_usd": (
                    str(pt.average_entry_price_usd)
                    if pt.average_entry_price_usd is not None
                    else None
                ),
                "realized_gross_pnl_usd": str(pt.realized_gross_pnl_usd),
                "unrealized_gross_pnl_usd": str(pt.unrealized_gross_pnl_usd),
                "cumulative_fees_usd": str(pt.cumulative_fees_usd),
                "estimated_liquidation_price_usd": str(pt.estimated_liquidation_price_usd),
                "estimated_liquidation_fee_usd": str(pt.estimated_liquidation_fee_usd),
                "net_liquidation_value_usd": str(pt.net_liquidation_value_usd),
                "marked_net_pnl_after_fees_usd": str(pt.marked_net_pnl_after_fees_usd),
                "net_liquidation_pnl_after_fees_usd": str(pt.net_liquidation_pnl_after_fees_usd),
                "accounting_identity_residual_usd": str(pt.accounting_identity_residual_usd),
                "stage": pt.stage.value,
            }
            f.write(json.dumps(row, default=str) + "\n")
    return {
        "summary": summary_path,
        "events": events_path,
        "fills": fills_path,
        "accounting": accounting_path,
        "lifecycle": lifecycle_path,
        "equity": equity_path,
    }
