from datetime import UTC, datetime, timedelta

import httpx
import pytest

from market.data.candles import (
    fetch_coinbase_candle_range,
    latest_closed_boundary,
)


def _row(ts: datetime, price: int) -> list[object]:
    return [int(ts.timestamp()), price - 1, price + 1, price, price, 10]


def test_range_fetch_preserves_raw_and_normalizes_exact_half_open_range():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=2)

    def handler(request: httpx.Request) -> httpx.Response:
        rows = [
            _row(end, 102),
            _row(start + timedelta(hours=1), 101),
            _row(start, 100),
            _row(start - timedelta(hours=1), 99),
        ]
        return httpx.Response(200, json=rows, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_coinbase_candle_range(
            start,
            end,
            as_of=datetime(2024, 1, 2, tzinfo=UTC),
            client=client,
        )
    assert [candle.ts for candle in result.candles] == [start, start + timedelta(hours=1)]
    assert len(result.raw_batches) == 1
    assert len(result.raw_batches[0].rows) == 4


def test_range_fetch_pages_at_documented_300_candle_limit():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=301)
    requests: list[tuple[datetime, datetime]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_start = datetime.fromisoformat(request.url.params["start"])
        request_end = datetime.fromisoformat(request.url.params["end"])
        requests.append((request_start, request_end))
        rows = []
        cursor = request_start
        while cursor < request_end:
            rows.append(_row(cursor, 100))
            cursor += timedelta(hours=1)
        rows.reverse()
        return httpx.Response(200, json=rows, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_coinbase_candle_range(
            start,
            end,
            as_of=end + timedelta(days=1),
            client=client,
        )
    assert len(result.candles) == 301
    assert len(requests) == 2
    request_widths = [int((right - left).total_seconds() // 3600) for left, right in requests]
    assert max(request_widths) == 299
    assert max(request_widths) <= 300


def test_range_fetch_refuses_current_bar():
    start = datetime(2024, 1, 1, 9, tzinfo=UTC)
    transport = httpx.MockTransport(lambda request: httpx.Response(500, request=request))
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(ValueError, match="unclosed"),
    ):
        fetch_coinbase_candle_range(
            start,
            datetime(2024, 1, 1, 11, tzinfo=UTC),
            as_of=datetime(2024, 1, 1, 11, 0, 10, tzinfo=UTC),
            client=client,
        )


def test_latest_closed_boundary_applies_confirmation_grace():
    just_after = datetime(2024, 1, 1, 11, 0, 10, tzinfo=UTC)
    confirmed = datetime(2024, 1, 1, 11, 0, 31, tzinfo=UTC)
    assert latest_closed_boundary(just_after) == datetime(2024, 1, 1, 10, tzinfo=UTC)
    assert latest_closed_boundary(confirmed) == datetime(2024, 1, 1, 11, tzinfo=UTC)
