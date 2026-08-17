from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from market.app.cli import main
from market.backtest.reproducibility import fingerprint_candles, pretty_json_bytes
from market.data.candles import (
    COINBASE_PRODUCT,
    COINBASE_SOURCE,
    CoinbaseCandleFetch,
    RawCandleBatch,
)
from market.data.dataset import write_research_dataset
from market.domain.models import Candle, Timeframe
from market.research.splits import (
    ResearchSplitError,
    ResearchSplitPlan,
    WalkForwardFold,
    bind_research_window,
    verify_research_split_plan,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMMITTED_PLAN = REPOSITORY_ROOT / "config/research/g3-ema-v1-splits.json"
COMMITTED_PLAN_SHA256 = "8527aa172a35ff1990c919bd6f7bebe60d73c14284507d82f8901fafb0dd2a9e"


def _fetch_result(*, hours: int = 72) -> CoinbaseCandleFetch:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = start + timedelta(hours=hours)
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
        for index in range(hours)
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


def _write_plan(root: Path, *, with_gap: bool = False) -> Path:
    result = _fetch_result()
    if with_gap:
        result = replace(result, candles=result.candles[:10] + result.candles[11:])
    artifacts = write_research_dataset(
        root / "data/research",
        result,
        allow_declared_gaps=with_gap,
    )
    candles = list(result.candles)
    start = result.requested_start
    development_end = start + timedelta(hours=60)
    dataset_end = result.requested_end

    def window(start_hour: int, end_hour: int):
        return bind_research_window(
            candles,
            start=start + timedelta(hours=start_hour),
            end=start + timedelta(hours=end_hour),
        )

    plan = ResearchSplitPlan(
        plan_id="fixture-splits-v1",
        study_id="fixture-study-v1",
        protocol_version="1.0",
        frozen_at=datetime(2024, 2, 1, tzinfo=UTC),
        dataset_manifest_path=str(artifacts.manifest_path.relative_to(root)),
        dataset_id=artifacts.manifest.dataset_id,
        dataset_normalized_sha256=artifacts.manifest.normalized_sha256,
        dataset_candle_fingerprint=fingerprint_candles(candles),
        dataset_bars=len(candles),
        dataset_start=start,
        dataset_end=dataset_end,
        maximum_ema_period_bars=2,
        indicator_warmup_bars=4,
        development=bind_research_window(candles, start=start, end=development_end),
        final_holdout=bind_research_window(
            candles,
            start=development_end,
            end=dataset_end,
        ),
        folds=(
            WalkForwardFold(
                fold_id="wf-01",
                train=window(0, 24),
                validation=window(24, 36),
                test=window(36, 48),
            ),
            WalkForwardFold(
                fold_id="wf-02",
                train=window(0, 36),
                validation=window(36, 48),
                test=window(48, 60),
            ),
        ),
    )
    plan_path = root / "config/research/fixture-splits.json"
    plan_path.parent.mkdir(parents=True)
    plan_path.write_bytes(pretty_json_bytes(plan.model_dump(mode="json")))
    return plan_path


def test_frozen_split_plan_verifies_dataset_windows_and_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    plan_path = _write_plan(tmp_path)

    verified = verify_research_split_plan(plan_path, repository_root=tmp_path)
    assert verified.fold_count == 2
    assert verified.stitched_test_bars == 24
    assert verified.plan.final_holdout.expected_bars == 12

    assert main(["verify-research-splits", "--plan", str(plan_path), "--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "folds=2 stitched_test_bars=24" in output
    assert "holdout_access=locked_until_g3_8" in output


def test_split_plan_preserves_declared_gaps_as_multiple_window_segments(tmp_path: Path):
    plan_path = _write_plan(tmp_path, with_gap=True)

    verified = verify_research_split_plan(plan_path, repository_root=tmp_path)
    assert verified.plan.development.expected_segment_count == 2
    assert verified.plan.folds[0].train.expected_segment_count == 2


def test_split_plan_rejects_chronology_that_leaks_or_skips_test_time(tmp_path: Path):
    plan_path = _write_plan(tmp_path)
    plan = ResearchSplitPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    second = plan.folds[1]
    leaked = second.model_copy(
        update={"test": second.test.model_copy(update={"end": plan.final_holdout.end})}
    )

    with pytest.raises(ValidationError, match="final holdout|development boundary"):
        ResearchSplitPlan.model_validate(
            plan.model_dump(mode="python") | {"folds": (plan.folds[0], leaked)}
        )


def test_split_verifier_rejects_window_fingerprint_drift(tmp_path: Path):
    plan_path = _write_plan(tmp_path)
    payload = ResearchSplitPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
    first = payload.folds[0]
    changed_train = first.train.model_copy(update={"candle_fingerprint": "0" * 64})
    changed_first = first.model_copy(update={"train": changed_train})
    changed = payload.model_copy(update={"folds": (changed_first, payload.folds[1])})
    plan_path.write_bytes(pretty_json_bytes(changed.model_dump(mode="json")))

    with pytest.raises(ResearchSplitError, match=r"wf-01\.train binding mismatch"):
        verify_research_split_plan(plan_path, repository_root=tmp_path)


def test_split_plan_rejects_unsafe_manifest_path(tmp_path: Path):
    plan_path = _write_plan(tmp_path)
    plan = ResearchSplitPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

    with pytest.raises(ValidationError, match="safe repository-relative"):
        ResearchSplitPlan.model_validate(
            plan.model_dump(mode="python") | {"dataset_manifest_path": "../outside.json"}
        )


def test_committed_g3_split_plan_is_frozen_and_verifiable():
    verified = verify_research_split_plan(COMMITTED_PLAN, repository_root=REPOSITORY_ROOT)

    assert verified.plan_sha256 == COMMITTED_PLAN_SHA256
    assert verified.fold_count == 9
    assert verified.stitched_test_bars == 19_752
    assert verified.plan.development.expected_bars == 35_061
    assert verified.plan.final_holdout.expected_bars == 8_750
    assert verified.plan.final_holdout.expected_segment_count == 3
