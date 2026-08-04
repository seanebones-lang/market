from datetime import datetime, timedelta, timezone
from decimal import Decimal

from market.domain.models import Balances, Intent, Position, Side
from market.risk.gate import RiskConfig, RiskGate, RiskState


def _intent(side=Side.BUY, qty="0.001"):
    return Intent(side=side, qty_btc=qty, reason="t")


def test_halt_blocks():
    g = RiskGate(RiskConfig())
    st = RiskState(halt=True)
    d = g.evaluate(_intent(), Position(), Balances(usd="1000", btc="0"), st, Decimal("100000"))
    assert not d.allow
    assert "halt" in d.violations


def test_max_daily_loss_blocks():
    g = RiskGate(RiskConfig(max_daily_loss_usd=Decimal("25")))
    st = RiskState(daily_pnl_usd=Decimal("-25"))
    d = g.evaluate(_intent(), Position(), Balances(usd="1000", btc="0"), st, Decimal("100000"))
    assert not d.allow
    assert "max_daily_loss" in d.violations


def test_freeze_entries_blocks_buy():
    g = RiskGate(RiskConfig())
    st = RiskState(freeze_entries=True)
    d = g.evaluate(_intent(), Position(), Balances(usd="1000", btc="0"), st, Decimal("100000"))
    assert not d.allow
    assert "freeze_entries" in d.violations


def test_freeze_allows_sell_exit():
    g = RiskGate(RiskConfig())
    st = RiskState(freeze_entries=True)
    d = g.evaluate(
        _intent(Side.SELL, "0.001"),
        Position(qty_btc="0.001"),
        Balances(usd="0", btc="0.001"),
        st,
        Decimal("100000"),
    )
    assert d.allow
    assert d.intent is not None


def test_min_spacing():
    g = RiskGate(RiskConfig(min_seconds_between_orders=300))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    st = RiskState(last_order_ts=now)
    d = g.evaluate(
        _intent(),
        Position(),
        Balances(usd="1000", btc="0"),
        st,
        Decimal("100000"),
        now=now + timedelta(seconds=10),
    )
    assert not d.allow
    assert "min_order_spacing" in d.violations


def test_max_orders_per_hour():
    g = RiskGate(RiskConfig(max_orders_per_hour=2))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    st = RiskState(order_timestamps=[now, now + timedelta(minutes=1)])
    d = g.evaluate(
        _intent(),
        Position(),
        Balances(usd="1000", btc="0"),
        st,
        Decimal("100000"),
        now=now + timedelta(minutes=2),
    )
    assert not d.allow
    assert "max_orders_per_hour" in d.violations


def test_resizes_to_max_position():
    g = RiskGate(
        RiskConfig(
            max_position_btc=Decimal("0.002"),
            max_notional_usd=Decimal("10000"),
        )
    )
    d = g.evaluate(
        _intent(qty="0.005"),
        Position(qty_btc="0"),
        Balances(usd="10000", btc="0"),
        RiskState(),
        Decimal("100000"),
    )
    assert d.allow
    assert d.intent is not None
    assert d.intent.qty_btc == Decimal("0.002")


def test_sell_no_position_blocked():
    g = RiskGate(RiskConfig())
    d = g.evaluate(
        _intent(Side.SELL),
        Position(),
        Balances(usd="1000", btc="0"),
        RiskState(),
        Decimal("100000"),
    )
    assert not d.allow
    assert "no_position_to_sell" in d.violations
