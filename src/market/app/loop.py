"""Main trading loop."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable

from market.config import AppConfig
from market.domain.models import Candle, Intent, Mode, Side, utcnow
from market.execution.reconcile import reconcile
from market.execution.sim import SimBroker
from market.ledger.jsonl import JsonlLedger
from market.ops.freeze import FreezeControl
from market.ops.heartbeat import Heartbeat
from market.risk.gate import RiskGate, RiskState
from market.strategy.slow_trend import SlowTrendV1


@dataclass
class LoopStats:
    ticks: int = 0
    intents: int = 0
    allowed: int = 0
    blocked: int = 0
    submits: int = 0
    fills: int = 0


@dataclass
class TradingLoop:
    config: AppConfig
    broker: SimBroker
    strategy: SlowTrendV1
    risk: RiskGate
    intents_ledger: JsonlLedger
    acks_ledger: JsonlLedger
    fills_ledger: JsonlLedger
    freeze: FreezeControl
    heartbeat: Heartbeat
    risk_state: RiskState = field(default_factory=RiskState)
    candles: list[Candle] = field(default_factory=list)
    submitted_client_ids: list[str] = field(default_factory=list)
    stats: LoopStats = field(default_factory=LoopStats)
    # submit hook — replaced/asserted in live-dry tests
    submit_enabled: bool = True

    def tick(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        self.stats.ticks += 1
        self.heartbeat.beat(now)

        if self.freeze.frozen:
            self.risk_state.freeze_entries = True
        else:
            # only clear freeze_entries from file if halt not set externally
            if not self.risk_state.halt:
                self.risk_state.freeze_entries = False

        # advance synthetic candle from quote for sim
        quote = self.broker.get_quote(self.config.symbol)
        self._append_candle_from_quote(quote.mid, now)

        position = self.broker.get_btc_position()
        balances = self.broker.get_balances()
        intent = self.strategy.evaluate(self.candles, position)
        if intent:
            self.stats.intents += 1

        decision = self.risk.evaluate(
            intent,
            position,
            balances,
            self.risk_state,
            mark_usd=quote.mid,
            now=now,
        )

        result: dict = {
            "ts": now.isoformat(),
            "mode": self.config.mode.value,
            "intent": intent.model_dump(mode="json") if intent else None,
            "allow": decision.allow,
            "violations": decision.violations,
            "submitted": False,
        }

        if intent:
            self.intents_ledger.append(
                {
                    "type": "intent",
                    "ts": now.isoformat(),
                    "intent": intent.model_dump(mode="json"),
                    "allow": decision.allow,
                    "violations": decision.violations,
                }
            )

        if not decision.allow or decision.intent is None:
            self.stats.blocked += 1
            self._reconcile_and_log(now)
            return result

        self.stats.allowed += 1
        out_intent = decision.intent

        if self.config.mode in {Mode.LIVE_DRY, Mode.PAPER}:
            # shadow only
            self.acks_ledger.append(
                {
                    "type": "shadow_ack",
                    "ts": now.isoformat(),
                    "intent": out_intent.model_dump(mode="json"),
                    "mode": self.config.mode.value,
                }
            )
            result["submitted"] = False
            self._reconcile_and_log(now)
            return result

        if self.config.mode in {Mode.SIM, Mode.LIVE} and self.submit_enabled:
            ack = self.broker.place_order(out_intent)
            self.stats.submits += 1
            self.submitted_client_ids.append(out_intent.client_order_id)
            self.risk_state.last_order_ts = now
            self.risk_state.order_timestamps.append(now)
            self.acks_ledger.append(
                {
                    "type": "ack",
                    "ts": now.isoformat(),
                    "ack": ack.model_dump(mode="json"),
                }
            )
            result["submitted"] = True
            result["ack_status"] = ack.status.value
            # journal new fills
            for f in self.broker.get_fills():
                if f.client_order_id == out_intent.client_order_id:
                    self.stats.fills += 1
                    self.fills_ledger.append(
                        {"type": "fill", "ts": now.isoformat(), "fill": f.model_dump(mode="json")}
                    )
        else:
            result["submitted"] = False

        self._reconcile_and_log(now)
        return result

    def run(
        self,
        iterations: int | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        now_fn: Callable[[], datetime] | None = None,
        demo_prices: bool = True,
    ) -> LoopStats:
        sleep_fn = sleep_fn or time.sleep
        now_fn = now_fn or utcnow
        n = iterations if iterations is not None else self.config.iterations
        i = 0
        base_now = now_fn()
        while n is None or i < n:
            if demo_prices and hasattr(self.broker, "set_quote"):
                # synthetic path: grind up then dump so slow_trend can fire
                half = 15 if n is None else max(n // 2, 1)
                if i < half:
                    mid = Decimal("100000") + Decimal(i) * Decimal("250")
                else:
                    mid = Decimal("100000") + Decimal(half) * Decimal("250") - Decimal(
                        i - half
                    ) * Decimal("400")
                self.broker.set_quote(mid - Decimal("5"), mid + Decimal("5"))
            # hour-step clock so candle timestamps are unique
            tick_now = base_now + timedelta(hours=i)
            self.tick(now=tick_now)
            i += 1
            if n is None or i < n:
                sleep_fn(self.config.loop_seconds)
        return self.stats

    def _append_candle_from_quote(self, mid: Decimal, now: datetime) -> None:
        # 1 candle per tick for sim simplicity (not wall-clock hour)
        if self.candles and self.candles[-1].ts == now:
            return
        c = Candle(ts=now, open=mid, high=mid, low=mid, close=mid, volume=Decimal("1"))
        self.candles.append(c)
        # keep memory bounded
        max_len = max(self.config.strategy.slow_ema * 5, 200)
        if len(self.candles) > max_len:
            self.candles = self.candles[-max_len:]

    def _reconcile_and_log(self, now: datetime) -> None:
        all_orders = list(getattr(self.broker, "_orders", {}).values())
        report = reconcile(self.submitted_client_ids, all_orders, self.broker.get_fills())
        if not report.ok:
            self.acks_ledger.append(
                {
                    "type": "reconcile_fail",
                    "ts": now.isoformat(),
                    "messages": report.messages,
                }
            )


def seed_trending_candles(
    n: int = 80,
    start: Decimal = Decimal("100000"),
    step: Decimal = Decimal("50"),
    start_ts: datetime | None = None,
) -> list[Candle]:
    """Generate synthetic uptrend then downtrend for strategy tests / demos."""
    start_ts = start_ts or datetime(2026, 1, 1, tzinfo=timezone.utc)
    out: list[Candle] = []
    px = start
    half = n // 2
    for i in range(n):
        if i < half:
            px = px + step
        else:
            px = px - step
        ts = start_ts + timedelta(hours=i)
        out.append(Candle(ts=ts, open=px, high=px, low=px, close=px, volume=Decimal("10")))
    return out


def build_sim_loop(config: AppConfig, root: Path | None = None) -> TradingLoop:
    root = root or Path.cwd()
    data = root / config.data_dir
    broker = SimBroker(
        usd=Decimal("1000"),
        btc=Decimal("0"),
        bid=Decimal("99995"),
        ask=Decimal("100005"),
    )
    # warm-up: flat history so first demo moves can create a clean EMA cross
    warm = max(config.strategy.slow_ema + 5, 30)
    flat = seed_trending_candles(warm, start=Decimal("100000"), step=Decimal("0"))
    return TradingLoop(
        config=config,
        broker=broker,
        strategy=SlowTrendV1(config.strategy),
        risk=RiskGate(config.risk),
        intents_ledger=JsonlLedger(data / "ledger" / "intents.jsonl"),
        acks_ledger=JsonlLedger(data / "ledger" / "acks.jsonl"),
        fills_ledger=JsonlLedger(data / "ledger" / "fills.jsonl"),
        freeze=FreezeControl(data / "state" / "FREEZE"),
        heartbeat=Heartbeat(data / "state" / "heartbeat.json", max_age_seconds=120),
        candles=flat,
    )
