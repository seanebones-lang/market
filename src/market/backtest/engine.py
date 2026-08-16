"""Offline backtest for pure strategies on historical candles (real or cached)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from market.backtest.costs import (
    BPS_DIVISOR,
    CostInputClassification,
    TransactionFeeTreatment,
    VenueCostAssumptions,
    VenueCostProfile,
    resolve_venue_cost,
)
from market.data.quality import require_clean_candles
from market.domain.models import Balances, Candle, D, Fill, Intent, Position, Side
from market.risk.gate import RiskConfig, RiskGate, RiskState
from market.strategy.slow_trend import SlowTrendConfig, SlowTrendV1

SCHEMA_VERSION = 4


class ExecutionModel(str, Enum):
    NEXT_BAR_OPEN = "next_bar_open"
    NEXT_BAR_OPEN_BID_ASK = "next_bar_open_bid_ask"


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


class BacktestEventType(str, Enum):
    BAR_OPEN = "bar_open"
    ORDER_ELIGIBLE = "order_eligible"
    FILL = "fill"
    EXECUTION_REJECTED = "execution_rejected"
    BAR_CLOSE = "bar_close"
    DECISION_ACCEPTED = "decision_accepted"
    DECISION_BLOCKED = "decision_blocked"
    ORDER_EXPIRED = "order_expired"


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
    equity_usd: Decimal
    usd: Decimal
    btc: Decimal
    mark: Decimal


@dataclass
class BacktestResult:
    fills: list[Fill] = field(default_factory=list)
    events: list[BacktestEvent] = field(default_factory=list)
    equity_curve: list[EquityPoint] = field(default_factory=list)
    final_position_btc: Decimal = Decimal("0")
    final_usd: Decimal = Decimal("0")
    starting_usd: Decimal = Decimal("0")
    intents: int = 0
    allowed: int = 0
    blocked: int = 0
    bars: int = 0
    first_ts: str | None = None
    last_ts: str | None = None
    max_drawdown_usd: Decimal = Decimal("0")
    max_equity_usd: Decimal = Decimal("0")
    fees_usd: Decimal = Decimal("0")
    source: str = ""
    strategy: str = "slow_trend_v1"
    fast_ema: int = 12
    slow_ema: int = 26
    qty_btc: Decimal = Decimal("0.001")
    fee_bps: Decimal = Decimal("5")
    venue_cost_profile: VenueCostProfile = VenueCostProfile.LEGACY_UNCLASSIFIED
    venue: str = "unclassified"
    routing: str = "unclassified"
    api_version: str = "unclassified"
    cost_input_classification: CostInputClassification = CostInputClassification.LEGACY_UNCLASSIFIED
    transaction_fee_treatment: TransactionFeeTreatment = TransactionFeeTreatment.LEGACY_UNCLASSIFIED
    transaction_fee_bps_assumption: Decimal = Decimal("0")
    execution_model: ExecutionModel = ExecutionModel.NEXT_BAR_OPEN
    quoted_spread_bps_assumption: Decimal = Decimal("0")
    adverse_slippage_bps_assumption: Decimal = Decimal("0")
    end_of_data_orders: int = 0

    @property
    def equity_usd(self) -> Decimal:
        return self.final_usd

    @property
    def realized_pnl_usd(self) -> Decimal:
        return self.final_usd - self.starting_usd

    @property
    def return_pct(self) -> Decimal:
        if self.starting_usd == 0:
            return Decimal("0")
        return (self.realized_pnl_usd / self.starting_usd) * Decimal("100")

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "source": self.source,
            "market_data_source": self.source,
            "strategy": self.strategy,
            "fast_ema": self.fast_ema,
            "slow_ema": self.slow_ema,
            "qty_btc": str(self.qty_btc),
            "fee_bps": str(self.fee_bps),
            "venue_cost_profile": self.venue_cost_profile.value,
            "venue": self.venue,
            "routing": self.routing,
            "api_version": self.api_version,
            "cost_input_classification": self.cost_input_classification.value,
            "transaction_fee_treatment": self.transaction_fee_treatment.value,
            "transaction_fee_bps_assumption": str(self.transaction_fee_bps_assumption),
            "transaction_fee_bps_applied": str(self.fee_bps),
            "execution_model": self.execution_model.value,
            "quoted_spread_bps_assumption": str(self.quoted_spread_bps_assumption),
            "adverse_slippage_bps_assumption": str(self.adverse_slippage_bps_assumption),
            "bars": self.bars,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "fills": len(self.fills),
            "events": len(self.events),
            "end_of_data_orders": self.end_of_data_orders,
            "intents": self.intents,
            "allowed": self.allowed,
            "blocked": self.blocked,
            "starting_usd": str(self.starting_usd),
            "final_usd": str(self.final_usd),
            "pnl_usd": str(self.realized_pnl_usd),
            "return_pct": str(self.return_pct),
            "fees_usd": str(self.fees_usd),
            "max_equity_usd": str(self.max_equity_usd),
            "max_drawdown_usd": str(self.max_drawdown_usd),
            "final_position_btc": str(self.final_position_btc),
        }


def run_backtest(
    candles: list[Candle],
    starting_usd: Decimal = Decimal("1000"),
    qty_btc: Decimal = Decimal("0.001"),
    fee_bps: Decimal | None = None,
    strategy_cfg: SlowTrendConfig | None = None,
    risk_cfg: RiskConfig | None = None,
    source: str = "",
    record_equity_every: int = 1,
    execution_model: ExecutionModel = ExecutionModel.NEXT_BAR_OPEN,
    quoted_spread_bps_assumption: Decimal = Decimal("0"),
    adverse_slippage_bps_assumption: Decimal = Decimal("0"),
    venue_cost_profile: VenueCostProfile = VenueCostProfile.LEGACY_UNCLASSIFIED,
    transaction_fee_bps_assumption: Decimal = Decimal("0"),
) -> BacktestResult:
    """Long-only event replay with decisions after close and fills at the next bar open."""
    execution_assumptions = ExecutionAssumptions(
        model=execution_model,
        quoted_spread_bps_assumption=quoted_spread_bps_assumption,
        adverse_slippage_bps_assumption=adverse_slippage_bps_assumption,
    )
    execution_model = execution_assumptions.model
    venue_cost_assumptions = VenueCostAssumptions(
        profile=venue_cost_profile,
        transaction_fee_bps_assumption=transaction_fee_bps_assumption,
    )
    venue_cost = resolve_venue_cost(
        venue_cost_assumptions,
        execution_model=execution_model.value,
        quoted_spread_bps_assumption=(execution_assumptions.quoted_spread_bps_assumption),
        legacy_fee_bps=fee_bps,
    )
    fee_bps_applied = venue_cost.transaction_fee_bps_applied
    cost_details = venue_cost.artifact_details()
    cost_metadata = venue_cost_assumptions.metadata
    if not candles:
        return BacktestResult(
            starting_usd=starting_usd,
            final_usd=starting_usd,
            source=source,
            fee_bps=fee_bps_applied,
            venue_cost_profile=venue_cost_assumptions.profile,
            venue=cost_metadata.venue,
            routing=cost_metadata.routing,
            api_version=cost_metadata.api_version,
            cost_input_classification=cost_metadata.input_classification,
            transaction_fee_treatment=cost_metadata.transaction_fee_treatment,
            transaction_fee_bps_assumption=(venue_cost_assumptions.transaction_fee_bps_assumption),
            execution_model=execution_model,
            quoted_spread_bps_assumption=(execution_assumptions.quoted_spread_bps_assumption),
            adverse_slippage_bps_assumption=(execution_assumptions.adverse_slippage_bps_assumption),
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

    usd = starting_usd
    btc = Decimal("0")
    fills: list[Fill] = []
    events: list[BacktestEvent] = []
    equity_curve: list[EquityPoint] = []
    intents = allowed = blocked = 0
    fees_total = Decimal("0")
    peak = starting_usd
    max_dd = Decimal("0")
    pending: PendingOrder | None = None

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
            fee = (quantity * price) * (fee_bps_applied / BPS_DIVISOR)
            traded = False
            reject_reason: str | None = None
            if side == Side.BUY:
                cost = quantity * price + fee
                if cost <= usd:
                    usd -= cost
                    btc += quantity
                    fees_total += fee
                    traded = True
                else:
                    blocked += 1
                    reject_reason = "insufficient_cash_at_execution"
            else:
                quantity = min(quantity, btc)
                if quantity > 0:
                    usd += quantity * price - fee
                    btc -= quantity
                    fees_total += fee
                    traded = True
                else:
                    blocked += 1
                    reject_reason = "no_inventory_at_execution"

            if traded:
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
                        "reason": pending.intent.reason,
                        "signal_snapshot": pending.intent.signal_snapshot,
                    },
                )
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
                        **cost_details,
                        **price_details,
                    },
                )
                state.last_order_ts = bar.ts
                state.order_timestamps.append(bar.ts)
            else:
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

        # Mark-to-market after the bar closes and any next-open fill has already occurred.
        equity = usd + btc * bar.close
        peak = max(peak, equity)
        drawdown = peak - equity
        max_dd = max(max_dd, drawdown)
        if record_equity_every > 0 and (
            (index + 1) % record_equity_every == 0 or index == len(candles) - 1
        ):
            equity_curve.append(
                EquityPoint(
                    ts=bar.close_time.isoformat(),
                    equity_usd=equity,
                    usd=usd,
                    btc=btc,
                    mark=bar.close,
                )
            )

        if index + 1 < min_bars:
            continue
        window = candles[: index + 1]
        pos = Position(qty_btc=btc)
        intent = strategy.evaluate(window, pos)
        if intent is not None:
            intents += 1
            decision = risk.evaluate(
                intent,
                pos,
                Balances(usd=usd, btc=btc),
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

    # G2.4 will replace this legacy comparison-only terminal cash adjustment with a costed fill.
    final_btc = btc
    if btc > 0:
        usd += btc * candles[-1].close
        btc = Decimal("0")

    return BacktestResult(
        fills=fills,
        events=events,
        equity_curve=equity_curve,
        final_position_btc=final_btc,  # pre-flatten inventory (informational)
        final_usd=usd,
        starting_usd=starting_usd,
        intents=intents,
        allowed=allowed,
        blocked=blocked,
        bars=len(candles),
        first_ts=candles[0].ts.isoformat(),
        last_ts=candles[-1].ts.isoformat(),
        max_drawdown_usd=max_dd,
        max_equity_usd=peak,
        fees_usd=fees_total,
        source=source,
        strategy="slow_trend_v1",
        fast_ema=strategy_cfg.fast_ema,
        slow_ema=strategy_cfg.slow_ema,
        qty_btc=strategy_cfg.order_qty_btc,
        fee_bps=fee_bps_applied,
        venue_cost_profile=venue_cost_assumptions.profile,
        venue=cost_metadata.venue,
        routing=cost_metadata.routing,
        api_version=cost_metadata.api_version,
        cost_input_classification=cost_metadata.input_classification,
        transaction_fee_treatment=cost_metadata.transaction_fee_treatment,
        transaction_fee_bps_assumption=(venue_cost_assumptions.transaction_fee_bps_assumption),
        execution_model=execution_model,
        quoted_spread_bps_assumption=(execution_assumptions.quoted_spread_bps_assumption),
        adverse_slippage_bps_assumption=(execution_assumptions.adverse_slippage_bps_assumption),
        end_of_data_orders=end_of_data_orders,
    )


def write_backtest_report(
    result: BacktestResult,
    out_dir: str | Path,
    run_id: str,
) -> dict[str, Path]:
    """Write summary, ordered events, fills, and equity under ``out_dir/run_id``."""
    out = Path(out_dir) / run_id
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    events_path = out / "events.jsonl"
    fills_path = out / "fills.jsonl"
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
    with equity_path.open("w", encoding="utf-8") as f:
        for pt in result.equity_curve:
            row = {
                "schema_version": SCHEMA_VERSION,
                "type": "equity",
                "run_id": run_id,
                "ts": pt.ts,
                "equity_usd": str(pt.equity_usd),
                "usd": str(pt.usd),
                "btc": str(pt.btc),
                "mark": str(pt.mark),
            }
            f.write(json.dumps(row, default=str) + "\n")
    return {
        "summary": summary_path,
        "events": events_path,
        "fills": fills_path,
        "equity": equity_path,
    }
