"""Pure risk gate — no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from market.domain.models import Balances, Intent, Position, RiskDecision, Side, utcnow


@dataclass
class RiskConfig:
    max_position_btc: Decimal = Decimal("0.002")
    max_notional_usd: Decimal = Decimal("150")
    max_daily_loss_usd: Decimal = Decimal("25")
    max_orders_per_hour: int = 4
    min_seconds_between_orders: int = 300
    allow_entries: bool = True


@dataclass
class RiskState:
    daily_pnl_usd: Decimal = Decimal("0")
    order_timestamps: list[datetime] = field(default_factory=list)
    freeze_entries: bool = False
    halt: bool = False
    last_order_ts: datetime | None = None


class RiskGate:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config

    def evaluate(
        self,
        intent: Intent | None,
        position: Position,
        balances: Balances,
        state: RiskState,
        mark_usd: Decimal,
        now: datetime | None = None,
    ) -> RiskDecision:
        now = now or utcnow()
        if intent is None:
            return RiskDecision(allow=False, intent=None, violations=["no_intent"])

        violations: list[str] = []

        if state.halt:
            violations.append("halt")

        if state.daily_pnl_usd <= -abs(self.config.max_daily_loss_usd):
            violations.append("max_daily_loss")

        # entry vs exit
        is_entry = self._is_entry(intent, position)
        if is_entry and not self.config.allow_entries:
            violations.append("entries_disabled")
        if is_entry and state.freeze_entries:
            violations.append("freeze_entries")

        if state.last_order_ts is not None:
            delta = (now - state.last_order_ts).total_seconds()
            if delta < self.config.min_seconds_between_orders:
                violations.append("min_order_spacing")

        recent = [t for t in state.order_timestamps if now - t <= timedelta(hours=1)]
        if len(recent) >= self.config.max_orders_per_hour:
            violations.append("max_orders_per_hour")

        qty = intent.qty_btc
        # resize / block position cap for buys that increase inventory
        if intent.side == Side.BUY:
            projected = position.qty_btc + qty
            if projected > self.config.max_position_btc:
                room = self.config.max_position_btc - position.qty_btc
                if room <= 0:
                    violations.append("max_position_btc")
                else:
                    qty = room
            notional = qty * mark_usd
            if notional > self.config.max_notional_usd:
                # resize to notional cap if possible
                max_qty = self.config.max_notional_usd / mark_usd if mark_usd > 0 else Decimal("0")
                if max_qty <= 0:
                    violations.append("max_notional_usd")
                else:
                    qty = min(qty, max_qty)
            if balances.usd < qty * mark_usd:
                # affordability — resize if partial cash
                afford = balances.usd / mark_usd if mark_usd > 0 else Decimal("0")
                if afford <= 0:
                    violations.append("insufficient_usd")
                else:
                    qty = min(qty, afford)
        else:  # sell
            if position.qty_btc <= 0:
                violations.append("no_position_to_sell")
            elif qty > position.qty_btc:
                qty = position.qty_btc

        hard = {
            "halt",
            "max_daily_loss",
            "entries_disabled",
            "freeze_entries",
            "min_order_spacing",
            "max_orders_per_hour",
            "max_position_btc",
            "max_notional_usd",
            "insufficient_usd",
            "no_position_to_sell",
        }
        blocking = [v for v in violations if v in hard]
        if blocking:
            return RiskDecision(allow=False, intent=None, violations=violations)

        if qty <= 0:
            return RiskDecision(allow=False, intent=None, violations=violations + ["qty_zero"])

        out = intent.model_copy(update={"qty_btc": qty})
        return RiskDecision(allow=True, intent=out, violations=violations)

    @staticmethod
    def _is_entry(intent: Intent, position: Position) -> bool:
        if intent.side == Side.BUY and position.qty_btc >= 0:
            # long-only book: buy is entry/add
            return True
        if intent.side == Side.SELL and position.qty_btc <= 0:
            # shorting would be entry; we don't support shorts in v1
            return True
        return False
