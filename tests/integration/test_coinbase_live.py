"""Explicit network integration checks; excluded from the default test suite."""

import pytest

from market.data.candles import fetch_coinbase_ticker

pytestmark = pytest.mark.integration


def test_live_coinbase_ticker_shape():
    quote = fetch_coinbase_ticker()
    assert quote.bid > 0
    assert quote.ask >= quote.bid
    assert quote.mid > 0
