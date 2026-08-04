"""Offline backtest for pure strategies on historical candles."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from market.domain.models import Candle, Fill, Position, Side
from market.risk.gate import RiskConfig, RiskGate, RiskState
from market.strategy.slow_trend import SlowTrendConfig, SlowTrendV1


@dataclass
class BacktestResult:
    fills: list[Fill] = field(default_factory=list)
    final_position_btc: Decimal = Decimal("0")
    final_usd: Decimal = Decimal("0")
    starting_usd: Decimal = Decimal("0")
    intents: int = 0
    allowed: int = 0
    blocked: int = 0

    @property
    def equity_usd(self) -> Decimal:
        # mark with last fill price if long else cash only — caller can mark
        return self.final_usd

    @property
    def realized_pnl_usd(self) -> Decimal:
        return self.final_usd - self.starting_usd


def run_backtest(
    candles: list[Candle],
    starting_usd: Decimal = Decimal("1000"),
    qty_btc: Decimal = Decimal("0.001"),
    fee_bps: Decimal = Decimal("5"),
    strategy_cfg: SlowTrendConfig | None = None,
    risk_cfg: RiskConfig | None = None,
) -> BacktestResult:
    """Long-only slow_trend backtest with simple cash accounting."""
    strategy_cfg = strategy_cfg or SlowTrendConfig(order_qty_btc=qty_btc)
    risk_cfg = risk_cfg or RiskConfig(
        max_position_btc=qty_btc,
        max_notional_usd=Decimal("100000"),
        max_daily_loss_usd=Decimal("100000"),
        max_orders_per_hour=1000,
        min_seconds_between_orders=0,
        allow_entries=True,
    )
    strategy = SlowTrendV1(strategy_cfg)
    risk = RiskGate(risk_cfg)
    state = RiskState()

    usd = starting_usd
    btc = Decimal("0")
    fills: list[Fill] = []
    intents = allowed = blocked = 0

    # need history window
    min_bars = strategy_cfg.slow_ema + 2
    for i in range(min_bars, len(candles) + 1):
        window = candles[:i]
        bar = window[-1]
        pos = Position(qty_btc=btc)
        intent = strategy.evaluate(window, pos)
        if intent is None:
            continue
        intents += 1
        from market.domain.models import Balances

        decision = risk.evaluate(
            intent,
            pos,
            Balances(usd=usd, btc=btc),
            state,
            mark_usd=bar.close,
            now=bar.ts,
        )
        if not decision.allow or decision.intent is None:
            blocked += 1
            continue
        allowed += 1
        side = decision.intent.side
        q = decision.intent.qty_btc
        px = bar.close
        fee = (q * px) * (fee_bps / Decimal("10000"))
        if side == Side.BUY:
            cost = q * px + fee
            if cost > usd:
                blocked += 1
                continue
            usd -= cost
            btc += q
        else:
            if q > btc:
                q = btc
            if q <= 0:
                blocked += 1
                continue
            usd += q * px - fee
            btc -= q
        fill = Fill(
            client_order_id=decision.intent.client_order_id,
            broker_order_id=f"bt-{len(fills)+1}",
            side=side,
            qty_btc=q,
            price_usd=px,
            fee_usd=fee,
            ts=bar.ts,
        )
        fills.append(fill)
        state.last_order_ts = bar.ts
        state.order_timestamps.append(bar.ts)

    # mark remaining inventory at last close
    if btc > 0 and candles:
        last = candles[-1].close
        usd += btc * last
        btc = Decimal("0")

    return BacktestResult(
        fills=fills,
        final_position_btc=btc,
        final_usd=usd,
        starting_usd=starting_usd,
        intents=intents,
        allowed=allowed,
        blocked=blocked,
    )
