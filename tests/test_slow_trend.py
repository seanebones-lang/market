from datetime import datetime, timedelta, timezone
from decimal import Decimal

from market.domain.models import Candle, Position, Side
from market.strategy.slow_trend import SlowTrendConfig, SlowTrendV1, ema_series


def _candles_up_then_flat(n_up=40, start=Decimal("100")) -> list[Candle]:
    out = []
    ts0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    px = start
    for i in range(n_up):
        px = px + Decimal("1")
        out.append(
            Candle(
                ts=ts0 + timedelta(hours=i),
                open=px,
                high=px,
                low=px,
                close=px,
            )
        )
    return out


def test_ema_length():
    vals = [Decimal(i) for i in range(1, 21)]
    out = ema_series(vals, 5)
    assert out[3] is None
    assert out[4] is not None
    assert out[-1] is not None


def test_bullish_cross_buys_when_flat():
    # strong steady uptrend should eventually be fast>slow; craft explicit cross
    cfg = SlowTrendConfig(fast_ema=3, slow_ema=5, order_qty_btc=Decimal("0.001"))
    s = SlowTrendV1(cfg)
    ts0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # down / flat then sharp up to force cross
    prices = [10, 10, 10, 10, 10, 10, 10, 9, 9, 9, 15, 16, 17, 18]
    candles = [
        Candle(
            ts=ts0 + timedelta(hours=i),
            open=Decimal(p),
            high=Decimal(p),
            low=Decimal(p),
            close=Decimal(p),
        )
        for i, p in enumerate(prices)
    ]
    # find first bar that yields buy
    got = None
    for end in range(cfg.slow_ema + 2, len(candles) + 1):
        intent = s.evaluate(candles[:end], Position())
        if intent is not None:
            got = intent
            break
    assert got is not None
    assert got.side == Side.BUY
    assert got.reason == "slow_trend_v1_bullish_cross"


def test_no_pyramid_when_long():
    cfg = SlowTrendConfig(fast_ema=3, slow_ema=5, order_qty_btc=Decimal("0.001"))
    s = SlowTrendV1(cfg)
    ts0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prices = [10, 10, 10, 10, 10, 10, 10, 9, 9, 9, 15, 16, 17, 18]
    candles = [
        Candle(
            ts=ts0 + timedelta(hours=i),
            open=Decimal(p),
            high=Decimal(p),
            low=Decimal(p),
            close=Decimal(p),
        )
        for i, p in enumerate(prices)
    ]
    long_pos = Position(qty_btc="0.001")
    # even if cross conditions exist historically, while long we only sell on bearish
    # feed continued uptrend — should be None (no pyramid)
    intent = s.evaluate(candles, long_pos)
    assert intent is None or intent.side == Side.SELL


def test_bearish_cross_sells_when_long():
    cfg = SlowTrendConfig(fast_ema=3, slow_ema=5, order_qty_btc=Decimal("0.001"))
    s = SlowTrendV1(cfg)
    ts0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # up then sharp down
    prices = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 5, 4, 3, 2]
    candles = [
        Candle(
            ts=ts0 + timedelta(hours=i),
            open=Decimal(p),
            high=Decimal(p),
            low=Decimal(p),
            close=Decimal(p),
        )
        for i, p in enumerate(prices)
    ]
    got = None
    for end in range(cfg.slow_ema + 2, len(candles) + 1):
        intent = s.evaluate(candles[:end], Position(qty_btc="0.001"))
        if intent is not None and intent.side == Side.SELL:
            got = intent
            break
    assert got is not None
    assert got.reason == "slow_trend_v1_bearish_cross"
