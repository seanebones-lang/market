from pathlib import Path

from market.data.dataset import load_research_segments

MANIFEST = (
    Path(__file__).parents[1]
    / "data"
    / "research"
    / "manifests"
    / "coinbase-btc-usd-1h-20210816T000000Z-20260816T000000Z-00c5f0b63bef9236.manifest.json"
)


def test_committed_five_year_dataset_verifies_and_is_segment_only():
    segments, manifest = load_research_segments(MANIFEST)
    assert manifest.normalized_sha256 == (
        "00c5f0b63bef92361fb6dfc1c227fe282ddcda9d1d7f03f5c9a449cb97dd65e2"
    )
    assert manifest.quality_status == "pass_segmented"
    assert manifest.strategy_admission == "segments_only"
    assert manifest.missing_bars == 13
    assert [len(segment) for segment in segments] == [13578, 23179, 4661, 2393]
    assert sum(len(segment) for segment in segments) == 43811
