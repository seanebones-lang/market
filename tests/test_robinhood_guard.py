import os
from decimal import Decimal

import pytest

from market.domain.models import Intent, OrderAck, OrderStatus, OrderType, Side, utcnow
from market.execution.robinhood import (
    RobinhoodAuthError,
    RobinhoodBroker,
    RobinhoodLiveDisabled,
    RobinhoodSession,
    rh_live_unlocked,
)


def test_rh_live_unlocked_default_off(monkeypatch):
    monkeypatch.delenv("MARKET_RH_LIVE", raising=False)
    assert rh_live_unlocked() is False


def test_place_order_blocked_without_live_flags(monkeypatch):
    monkeypatch.setenv("MARKET_RH_LIVE", "0")
    b = RobinhoodBroker(allow_live=True, mode_is_live=True)
    b.session.authenticated = True
    with pytest.raises(RobinhoodLiveDisabled):
        b.place_order(Intent(side=Side.BUY, qty_btc="0.001", reason="t"))


def test_place_order_blocked_when_mode_not_live(monkeypatch):
    monkeypatch.setenv("MARKET_RH_LIVE", "1")
    b = RobinhoodBroker(allow_live=True, mode_is_live=False)
    b.session.authenticated = True
    with pytest.raises(RobinhoodLiveDisabled, match="mode_not_live"):
        b.place_order(Intent(side=Side.BUY, qty_btc="0.001", reason="t"))


def test_place_order_blocked_when_allow_live_false(monkeypatch):
    monkeypatch.setenv("MARKET_RH_LIVE", "1")
    b = RobinhoodBroker(allow_live=False, mode_is_live=True)
    b.session.authenticated = True
    with pytest.raises(RobinhoodLiveDisabled, match="allow_live"):
        b.place_order(Intent(side=Side.BUY, qty_btc="0.001", reason="t"))


def test_place_order_requires_auth_even_when_unlocked(monkeypatch):
    monkeypatch.setenv("MARKET_RH_LIVE", "1")
    b = RobinhoodBroker(allow_live=True, mode_is_live=True)
    b.session.authenticated = False
    with pytest.raises(RobinhoodAuthError):
        b.place_order(Intent(side=Side.BUY, qty_btc="0.001", reason="t"))


def test_place_order_ok_with_transport(monkeypatch):
    monkeypatch.setenv("MARKET_RH_LIVE", "1")

    def transport(intent: Intent) -> OrderAck:
        return OrderAck(
            client_order_id=intent.client_order_id,
            broker_order_id="br1",
            status=OrderStatus.FILLED,
            side=intent.side,
            qty_btc=intent.qty_btc,
            order_type=OrderType.MARKET,
            ts=utcnow(),
        )

    b = RobinhoodBroker(
        allow_live=True,
        mode_is_live=True,
        transport_place=transport,
    )
    b.session.authenticated = True
    intent = Intent(side=Side.BUY, qty_btc="0.001", reason="t", client_order_id="cid1")
    ack = b.place_order(intent)
    assert ack.status == OrderStatus.FILLED
    # idempotent
    ack2 = b.place_order(intent)
    assert ack2.broker_order_id == "br1"


def test_auth_error_callback():
    seen: list[str] = []

    def on_err(exc: Exception) -> None:
        seen.append(str(exc))

    b = RobinhoodBroker(on_auth_error=on_err)
    b.session.authenticated = False
    with pytest.raises(RobinhoodAuthError):
        b.get_balances()
    assert seen


def test_session_login_not_implemented(monkeypatch):
    monkeypatch.delenv("MARKET_RH_FAKE_LOGIN", raising=False)
    s = RobinhoodSession()
    with pytest.raises(RobinhoodAuthError, match="not_implemented"):
        s.login("u", "p")


def test_session_fake_login(monkeypatch):
    monkeypatch.setenv("MARKET_RH_FAKE_LOGIN", "1")
    s = RobinhoodSession()
    s.login("u", "p")
    assert s.authenticated
