"""Offline backtest for pure strategies on historical candles (real or cached)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from market.domain.models import Balances, Candle, Fill, Position, Side
from market.risk.gate import RiskConfig, RiskGate, RiskState
from market.strategy.slow_trend import SlowTrendConfig, SlowTrendV1

SCHEMA_VERSION = 1


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
            "strategy": self.strategy,
            "fast_ema": self.fast_ema,
            "slow_ema": self.slow_ema,
            "qty_btc": str(self.qty_btc),
            "fee_bps": str(self.fee_bps),
            "bars": self.bars,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "fills": len(self.fills),
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
    fee_bps: Decimal = Decimal("5"),
    strategy_cfg: SlowTrendConfig | None = None,
    risk_cfg: RiskConfig | None = None,
    source: str = "",
    record_equity_every: int = 1,
) -> BacktestResult:
    """Long-only slow_trend backtest with cash accounting on closed bars.

    Uses bar.close as fill price (conservative enough for research; not RH book).
    """
    if not candles:
        return BacktestResult(starting_usd=starting_usd, final_usd=starting_usd, source=source)

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
    equity_curve: list[EquityPoint] = []
    intents = allowed = blocked = 0
    fees_total = Decimal("0")
    peak = starting_usd
    max_dd = Decimal("0")

    min_bars = strategy_cfg.slow_ema + 2
    for i in range(min_bars, len(candles) + 1):
        window = candles[:i]
        bar = window[-1]
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
                now=bar.ts,
            )
            if not decision.allow or decision.intent is None:
                blocked += 1
            else:
                allowed += 1
                side = decision.intent.side
                q = decision.intent.qty_btc
                px = bar.close
                fee = (q * px) * (fee_bps / Decimal("10000"))
                traded = False
                if side == Side.BUY:
                    cost = q * px + fee
                    if cost <= usd:
                        usd -= cost
                        btc += q
                        fees_total += fee
                        traded = True
                    else:
                        blocked += 1
                else:
                    q = min(q, btc)
                    if q > 0:
                        usd += q * px - fee
                        btc -= q
                        fees_total += fee
                        traded = True
                    else:
                        blocked += 1
                if traded:
                    fill = Fill(
                        client_order_id=decision.intent.client_order_id,
                        broker_order_id=f"bt-{len(fills) + 1}",
                        side=side,
                        qty_btc=q,
                        price_usd=px,
                        fee_usd=fee,
                        ts=bar.ts,
                        raw={
                            "source": source or "backtest",
                            "bar_close": str(bar.close),
                            "reason": decision.intent.reason,
                            "signal_snapshot": decision.intent.signal_snapshot,
                        },
                    )
                    fills.append(fill)
                    state.last_order_ts = bar.ts
                    state.order_timestamps.append(bar.ts)

        # mark-to-market equity
        equity = usd + btc * bar.close
        peak = max(peak, equity)
        dd = peak - equity
        max_dd = max(max_dd, dd)
        if record_equity_every > 0 and (i % record_equity_every == 0 or i == len(candles)):
            equity_curve.append(
                EquityPoint(
                    ts=bar.ts.isoformat(),
                    equity_usd=equity,
                    usd=usd,
                    btc=btc,
                    mark=bar.close,
                )
            )

    # flatten remaining inventory at last close for comparable cash PnL
    final_btc = btc
    if btc > 0 and candles:
        last = candles[-1].close
        usd += btc * last
        btc = Decimal("0")

    return BacktestResult(
        fills=fills,
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
        fee_bps=fee_bps,
    )


def write_backtest_report(
    result: BacktestResult,
    out_dir: str | Path,
    run_id: str,
) -> dict[str, Path]:
    """Write summary.json, fills.jsonl, equity.jsonl under out_dir/run_id/."""
    out = Path(out_dir) / run_id
    out.mkdir(parents=True, exist_ok=True)
    summary_path = out / "summary.json"
    fills_path = out / "fills.jsonl"
    equity_path = out / "equity.jsonl"

    summary_path.write_text(
        json.dumps(result.summary(), indent=2, default=str) + "\n", encoding="utf-8"
    )
    with fills_path.open("w", encoding="utf-8") as f:
        for fill in result.fills:
            row = {
                "schema_version": SCHEMA_VERSION,
                "type": "fill",
                "run_id": run_id,
                "mode": "backtest",
                "venue": result.source or "historical",
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
    return {"summary": summary_path, "fills": fills_path, "equity": equity_path}
