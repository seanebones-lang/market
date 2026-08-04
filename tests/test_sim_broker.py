from decimal import Decimal

from market.domain.models import Intent, OrderStatus, Side
from market.execution.sim import SimBroker


def test_buy_and_sell_roundtrip():
    b = SimBroker(usd=Decimal("1000"), btc=Decimal("0"), bid=Decimal("100"), ask=Decimal("101"))
    buy = Intent(side=Side.BUY, qty_btc="1", reason="t")
    ack = b.place_order(buy)
    assert ack.status == OrderStatus.FILLED
    assert b.get_btc_position().qty_btc == Decimal("1")
    assert b.get_balances().usd < Decimal("1000")

    sell = Intent(side=Side.SELL, qty_btc="1", reason="t2")
    ack2 = b.place_order(sell)
    assert ack2.status == OrderStatus.FILLED
    assert b.get_btc_position().qty_btc == Decimal("0")


def test_idempotent_client_order_id():
    b = SimBroker(usd=Decimal("1000"), bid=Decimal("100"), ask=Decimal("101"))
    intent = Intent(side=Side.BUY, qty_btc="1", reason="t", client_order_id="abc")
    a1 = b.place_order(intent)
    a2 = b.place_order(intent)
    assert a1.broker_order_id == a2.broker_order_id
    assert b.get_btc_position().qty_btc == Decimal("1")
    assert len(b.get_fills()) == 1


def test_reject_insufficient_usd():
    b = SimBroker(usd=Decimal("1"), bid=Decimal("100"), ask=Decimal("101"))
    ack = b.place_order(Intent(side=Side.BUY, qty_btc="1", reason="t"))
    assert ack.status == OrderStatus.REJECTED
