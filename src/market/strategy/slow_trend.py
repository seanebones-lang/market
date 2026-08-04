"""Slow trend v1 — dual EMA cross on closed candles. Pure, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from market.domain.models import Candle, Intent, Position, Side


@dataclass
class SlowTrendConfig:
    fast_ema: int = 12
    slow_ema: int = 26
    order_qty_btc: Decimal = Decimal("0.001")


def ema_series(values: list[Decimal], period: int) -> list[Decimal | None]:
    if period <= 0:
        raise ValueError("period must be > 0")
    out: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return out
    # seed SMA
    seed = sum(values[:period], Decimal("0")) / Decimal(period)
    out[period - 1] = seed
    mult = Decimal("2") / (Decimal(period) + Decimal("1"))
    prev = seed
    for i in range(period, len(values)):
        prev = (values[i] - prev) * mult + prev
        out[i] = prev
    return out


class SlowTrendV1:
    def __init__(self, config: SlowTrendConfig | None = None) -> None:
        self.config = config or SlowTrendConfig()

    def evaluate(self, candles: list[Candle], position: Position) -> Intent | None:
        need = self.config.slow_ema + 2
        if len(candles) < need:
            return None

        closes = [c.close for c in candles]
        fast = ema_series(closes, self.config.fast_ema)
        slow = ema_series(closes, self.config.slow_ema)
        i = len(closes) - 1
        j = i - 1
        if fast[i] is None or slow[i] is None or fast[j] is None or slow[j] is None:
            return None

        f0, s0 = fast[j], slow[j]
        f1, s1 = fast[i], slow[i]
        assert f0 is not None and s0 is not None and f1 is not None and s1 is not None

        bullish_cross = f0 <= s0 and f1 > s1
        bearish_cross = f0 >= s0 and f1 < s1
        snap = {
            "fast_ema": str(f1),
            "slow_ema": str(s1),
            "close": str(closes[i]),
            "bar_ts": candles[i].ts.isoformat(),
        }

        if bullish_cross and position.is_flat:
            return Intent(
                side=Side.BUY,
                qty_btc=self.config.order_qty_btc,
                reason="slow_trend_v1_bullish_cross",
                signal_snapshot=snap,
            )
        if bearish_cross and not position.is_flat:
            return Intent(
                side=Side.SELL,
                qty_btc=min(position.qty_btc, self.config.order_qty_btc)
                if position.qty_btc < self.config.order_qty_btc
                else position.qty_btc,
                reason="slow_trend_v1_bearish_cross",
                signal_snapshot=snap,
            )
        return None
