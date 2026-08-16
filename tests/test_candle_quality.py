from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from market.data.candles import load_candles_csv
from market.data.quality import (
    DatasetQualityError,
    QualityIssueCode,
    require_clean_candles,
    split_contiguous_candles,
    validate_candles,
)
from market.domain.models import Candle, Position
from market.strategy.slow_trend import SlowTrendConfig, SlowTrendV1

FIXTURES = Path(__file__).parent / "fixtures" / "candles"


@pytest.mark.parametrize(
    ("name", "expected_code"),
    [
        ("gap.csv", QualityIssueCode.GAP),
        ("duplicate.csv", QualityIssueCode.DUPLICATE),
        ("out_of_order.csv", QualityIssueCode.OUT_OF_ORDER),
        ("late.csv", QualityIssueCode.QUALITY_FLAG),
        ("partial_final.csv", QualityIssueCode.UNCLOSED),
        ("extreme_move.csv", QualityIssueCode.EXTREME_MOVE),
    ],
)
def test_corrupt_fixture_fails_closed(name: str, expected_code: QualityIssueCode):
    candles = load_candles_csv(FIXTURES / name)
    report = validate_candles(candles, as_of=datetime(2024, 1, 3, tzinfo=UTC))
    assert report.status == "fail"
    assert expected_code in {issue.code for issue in report.issues}
    with pytest.raises(DatasetQualityError):
        require_clean_candles(candles, as_of=datetime(2024, 1, 3, tzinfo=UTC))


def test_gap_splits_segments_and_requires_indicator_rewarm():
    candles = load_candles_csv(FIXTURES / "gap.csv")
    segments = split_contiguous_candles(candles)
    assert [len(segment) for segment in segments] == [2, 1]


def test_strategy_rejects_unresolved_gap():
    candles = load_candles_csv(FIXTURES / "gap.csv")
    strategy = SlowTrendV1(SlowTrendConfig(fast_ema=2, slow_ema=3))
    with pytest.raises(DatasetQualityError):
        strategy.evaluate(candles, Position())


def test_current_hour_is_not_strategy_eligible():
    ts = datetime(2024, 1, 1, 10, tzinfo=UTC)
    candle = Candle(ts=ts, open="100", high="100", low="100", close="100")
    report = validate_candles([candle], as_of=ts + timedelta(minutes=30))
    codes = {issue.code for issue in report.issues}
    assert QualityIssueCode.UNCLOSED in codes
    assert QualityIssueCode.FUTURE_CONFIRMATION in codes
