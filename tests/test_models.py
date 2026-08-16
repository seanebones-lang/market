from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from market.domain.models import Balances, Candle, D, Intent, Position, Quote, Side, Timeframe


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
    q = Quote(bid="100", ask="102", ts=datetime.now(UTC))
    assert q.mid == Decimal("101")


def _valid_candle(**changes: object) -> Candle:
    values: dict[str, object] = {
        "ts": datetime(2024, 1, 1, tzinfo=UTC),
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
        "volume": "1",
    }
    values.update(changes)
    return Candle.model_validate(values)


@pytest.mark.parametrize(
    "changes",
    [
        {"ts": datetime(2024, 1, 1)},  # noqa: DTZ001 - deliberately exercise naive input
        {"ts": datetime(2024, 1, 1, tzinfo=timezone(timedelta(hours=-6)))},
        {"ts": datetime(2024, 1, 1, 0, 1, tzinfo=UTC)},
        {"open": "0"},
        {"volume": "-1"},
        {"low": "101"},
        {"high": "104"},
    ],
)
def test_candle_contract_rejects_invalid_rows(changes: dict[str, object]):
    with pytest.raises(ValidationError):
        _valid_candle(**changes)


def test_candle_contract_derives_deterministic_close_metadata():
    candle = _valid_candle()
    assert candle.timeframe == Timeframe.HOUR_1
    assert candle.close_time == datetime(2024, 1, 1, 1, tzinfo=UTC)
    assert candle.received_at == candle.close_time
    assert candle.close_confirmed_at == candle.close_time
    assert candle.is_closed is True


def test_unsupported_candle_interval_is_rejected():
    with pytest.raises(ValueError, match="unsupported candle interval"):
        Timeframe.from_seconds(300)
