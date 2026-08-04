from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from market.domain.models import Balances, D, Intent, Position, Quote, Side


def test_d_rejects_float():
    with pytest.raises(TypeError):
        D(1.5)  # type: ignore[arg-type]


def test_d_from_str():
    assert D("0.001") == Decimal("0.001")


def test_intent_rejects_float_qty():
    with pytest.raises((TypeError, ValidationError)):
        Intent(side=Side.BUY, qty_btc=0.001, reason="x")  # type: ignore[arg-type]


def test_intent_ok_decimal_str():
    intent = Intent(side=Side.BUY, qty_btc="0.001", reason="cross")
    assert intent.qty_btc == Decimal("0.001")
    assert intent.client_order_id
    assert intent.ts.tzinfo is not None


def test_intent_qty_must_be_positive():
    with pytest.raises(ValidationError):
        Intent(side=Side.BUY, qty_btc="0", reason="x")


def test_position_flat():
    assert Position().is_flat
    assert not Position(qty_btc="0.01").is_flat


def test_balances_no_float():
    with pytest.raises((TypeError, ValidationError)):
        Balances(usd=100.0, btc=0.0)  # type: ignore[arg-type]


def test_quote_mid():
    q = Quote(bid="100", ask="102", ts=datetime.now(timezone.utc))
    assert q.mid == Decimal("101")
