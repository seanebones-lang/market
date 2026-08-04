from market.domain.models import Fill, Order, OrderStatus, Side
from market.execution.reconcile import reconcile


def test_ok_when_matched():
    orders = [
        Order(
            client_order_id="a",
            broker_order_id="1",
            status=OrderStatus.FILLED,
            side=Side.BUY,
            qty_btc="0.001",
            filled_qty_btc="0.001",
        )
    ]
    fills = [
        Fill(
            client_order_id="a",
            broker_order_id="1",
            side=Side.BUY,
            qty_btc="0.001",
            price_usd="100",
        )
    ]
    r = reconcile(["a"], orders, fills)
    assert r.ok


def test_missing_and_extra():
    r = reconcile(["a"], [], [])
    assert not r.ok
    assert r.missing_on_broker == ["a"]

    fills = [
        Fill(
            client_order_id="b",
            broker_order_id="1",
            side=Side.BUY,
            qty_btc="0.001",
            price_usd="100",
        )
    ]
    r2 = reconcile([], [], fills)
    assert r2.extra_on_broker == ["b"]
