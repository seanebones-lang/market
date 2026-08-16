from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from market.data.candles import (
    COINBASE_PRODUCT,
    COINBASE_SOURCE,
    CoinbaseCandleFetch,
    RawCandleBatch,
)
from market.data.dataset import (
    load_research_dataset,
    load_research_segments,
    sha256_path,
    write_research_dataset,
)
from market.data.quality import DatasetQualityError
from market.domain.models import Candle, Timeframe


def _fetch_result() -> CoinbaseCandleFetch:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=4)
    retrieved_at = end + timedelta(days=1)
    candles = tuple(
        Candle(
            ts=start + timedelta(hours=index),
            source=COINBASE_SOURCE,
            open=str(100 + index),
            high=str(101 + index),
            low=str(99 + index),
            close=str(100 + index),
            volume="10",
            received_at=retrieved_at,
            close_confirmed_at=start + timedelta(hours=index + 1),
        )
        for index in range(4)
    )
    rows = tuple(
        (
            int(candle.ts.timestamp()),
            str(candle.low),
            str(candle.high),
            str(candle.open),
            str(candle.close),
            str(candle.volume),
        )
        for candle in candles
    )
    return CoinbaseCandleFetch(
        product=COINBASE_PRODUCT,
        source=COINBASE_SOURCE,
        timeframe=Timeframe.HOUR_1,
        requested_start=start,
        requested_end=end,
        retrieved_at=retrieved_at,
        candles=candles,
        raw_batches=(RawCandleBatch(request_start=start, request_end=end, rows=rows),),
    )


def test_dataset_artifacts_are_content_addressed_and_repeatable(tmp_path: Path):
    result = _fetch_result()
    first = write_research_dataset(tmp_path, result)
    second = write_research_dataset(tmp_path, result)

    assert first.manifest.dataset_id == second.manifest.dataset_id
    assert first.manifest.normalized_sha256 == sha256_path(first.normalized_path)
    assert first.manifest.raw_sha256 == sha256_path(first.raw_path)
    assert first.manifest.quality_report_sha256 == sha256_path(first.quality_report_path)
    assert first.raw_path != first.normalized_path
    assert first.manifest_path.read_bytes() == second.manifest_path.read_bytes()

    candles, manifest = load_research_dataset(first.manifest_path)
    assert len(candles) == 4
    assert manifest.quality_status == "pass"
    assert candles[0].source == COINBASE_SOURCE


def test_dataset_loader_rejects_tampering(tmp_path: Path):
    artifacts = write_research_dataset(tmp_path, _fetch_result())
    artifacts.normalized_path.write_bytes(artifacts.normalized_path.read_bytes() + b"tampered\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_research_dataset(artifacts.manifest_path)


def test_declared_gap_dataset_is_only_admitted_as_rewarmed_segments(tmp_path: Path):
    result = _fetch_result()
    result_with_gap = replace(result, candles=result.candles[:2] + result.candles[3:])
    with pytest.raises(DatasetQualityError):
        write_research_dataset(tmp_path / "reject", result_with_gap)

    artifacts = write_research_dataset(
        tmp_path / "segment",
        result_with_gap,
        allow_declared_gaps=True,
    )
    assert artifacts.manifest.quality_status == "pass_segmented"
    assert artifacts.manifest.missing_bars == 1
    assert artifacts.manifest.strategy_admission == "segments_only"
    with pytest.raises(ValueError, match="load_research_segments"):
        load_research_dataset(artifacts.manifest_path)
    segments, _ = load_research_segments(artifacts.manifest_path)
    assert [len(segment) for segment in segments] == [2, 1]
