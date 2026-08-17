"""Frozen chronological split contracts for preregistered research studies."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from market.backtest.reproducibility import fingerprint_candles, sha256_path
from market.data.dataset import DatasetManifest, load_research_segments
from market.data.quality import split_contiguous_candles
from market.domain.models import Candle

SPLIT_PLAN_SCHEMA_VERSION = 1


class ResearchSplitError(ValueError):
    """Raised when a split artifact or its bound dataset fails verification."""


def _require_utc_hour(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    value = value.astimezone(UTC)
    if value.minute or value.second or value.microsecond:
        raise ValueError(f"{field_name} must align to an hourly boundary")
    return value


def _validate_sha256(value: str, field_name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


class BoundResearchWindow(BaseModel):
    """One immutable, half-open UTC scoring window and its expected candle identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime
    expected_bars: int = Field(gt=0)
    expected_segment_count: int = Field(gt=0)
    candle_fingerprint: str

    @field_validator("start", "end")
    @classmethod
    def _utc_hour(cls, value: datetime, info: object) -> datetime:
        field_name = getattr(info, "field_name", "window boundary")
        return _require_utc_hour(value, str(field_name))

    @field_validator("candle_fingerprint")
    @classmethod
    def _fingerprint(cls, value: str) -> str:
        return _validate_sha256(value, "candle_fingerprint")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.end <= self.start:
            raise ValueError("research window end must be after start")
        return self


class WalkForwardFold(BaseModel):
    """One expanding-train, validation, and future test decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fold_id: str = Field(pattern=r"^wf-[0-9]{2}$")
    train: BoundResearchWindow
    validation: BoundResearchWindow
    test: BoundResearchWindow

    @model_validator(mode="after")
    def _contiguous_roles(self) -> Self:
        if self.train.end != self.validation.start:
            raise ValueError("train end must equal validation start")
        if self.validation.end != self.test.start:
            raise ValueError("validation end must equal test start")
        return self


class ResearchSplitPlan(BaseModel):
    """A complete, immutable boundary plan parsed with no ignored fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    plan_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    study_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    protocol_version: str = Field(min_length=1)
    frozen_at: datetime
    dataset_manifest_path: str
    dataset_id: str = Field(min_length=1)
    dataset_normalized_sha256: str
    dataset_candle_fingerprint: str
    dataset_bars: int = Field(gt=0)
    dataset_start: datetime
    dataset_end: datetime
    timeframe: Literal["1h"] = "1h"
    split_method: Literal["anchored_expanding_train"] = "anchored_expanding_train"
    boundary_semantics: Literal["half_open_utc"] = "half_open_utc"
    gap_policy: Literal["preserve_split_rewarm"] = "preserve_split_rewarm"
    indicator_context_policy: Literal["prior_admitted_bars_indicators_only"] = (
        "prior_admitted_bars_indicators_only"
    )
    scoring_start_state: Literal["flat_cash_reset"] = "flat_cash_reset"
    scoring_end_state: Literal["costed_terminal_liquidation"] = "costed_terminal_liquidation"
    holdout_access: Literal["locked_until_g3_8"] = "locked_until_g3_8"
    maximum_ema_period_bars: int = Field(gt=0)
    indicator_warmup_bars: int = Field(gt=0)
    development: BoundResearchWindow
    final_holdout: BoundResearchWindow
    folds: tuple[WalkForwardFold, ...] = Field(min_length=2)

    @field_validator("frozen_at", "dataset_start", "dataset_end")
    @classmethod
    def _utc_hour(cls, value: datetime, info: object) -> datetime:
        field_name = getattr(info, "field_name", "plan timestamp")
        return _require_utc_hour(value, str(field_name))

    @field_validator("dataset_manifest_path")
    @classmethod
    def _safe_relative_manifest_path(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise ValueError("dataset_manifest_path must be a safe repository-relative path")
        return path.as_posix()

    @field_validator("dataset_normalized_sha256", "dataset_candle_fingerprint")
    @classmethod
    def _sha256(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "sha256")
        return _validate_sha256(value, str(field_name))

    @model_validator(mode="after")
    def _complete_chronology(self) -> Self:
        if self.dataset_end <= self.dataset_start:
            raise ValueError("dataset_end must be after dataset_start")
        if self.indicator_warmup_bars != self.maximum_ema_period_bars + 2:
            raise ValueError("indicator_warmup_bars must equal maximum_ema_period_bars + 2")
        if self.development.start != self.dataset_start:
            raise ValueError("development must start at dataset_start")
        if self.development.end != self.final_holdout.start:
            raise ValueError("development end must equal final holdout start")
        if self.final_holdout.end != self.dataset_end:
            raise ValueError("final holdout must end at dataset_end")
        if self.development.expected_bars + self.final_holdout.expected_bars != self.dataset_bars:
            raise ValueError("development and holdout bar counts must partition dataset_bars")

        fold_ids = [fold.fold_id for fold in self.folds]
        if len(set(fold_ids)) != len(fold_ids):
            raise ValueError("walk-forward fold IDs must be unique")
        if fold_ids != [f"wf-{index:02d}" for index in range(1, len(self.folds) + 1)]:
            raise ValueError("walk-forward fold IDs must be sequential from wf-01")

        previous: WalkForwardFold | None = None
        for fold in self.folds:
            if fold.train.start != self.development.start:
                raise ValueError("every anchored training window must start at development start")
            if fold.test.end > self.development.end:
                raise ValueError("walk-forward test window enters the final holdout")
            if previous is not None:
                if fold.train.end != previous.validation.end:
                    raise ValueError("anchored training end must advance to prior validation end")
                if fold.validation.end != previous.test.end:
                    raise ValueError("validation must advance over the prior test window")
                if fold.test.start != previous.test.end:
                    raise ValueError("walk-forward test windows must be contiguous and disjoint")
            previous = fold
        if self.folds[-1].test.end != self.development.end:
            raise ValueError("last walk-forward test must end at development boundary")
        return self


@dataclass(frozen=True)
class SplitPlanVerification:
    plan: ResearchSplitPlan
    dataset_manifest: DatasetManifest
    plan_sha256: str
    fold_count: int
    stitched_test_bars: int


def bind_research_window(
    candles: list[Candle],
    *,
    start: datetime,
    end: datetime,
) -> BoundResearchWindow:
    """Bind one half-open window to its exact ordered candle sequence."""
    start = _require_utc_hour(start, "start")
    end = _require_utc_hour(end, "end")
    if end <= start:
        raise ResearchSplitError("research window end must be after start")
    selected = [candle for candle in candles if start <= candle.ts < end]
    if not selected:
        raise ResearchSplitError("research window contains no admitted candles")
    return BoundResearchWindow(
        start=start,
        end=end,
        expected_bars=len(selected),
        expected_segment_count=len(split_contiguous_candles(selected)),
        candle_fingerprint=fingerprint_candles(selected),
    )


def _verify_window(candles: list[Candle], expected: BoundResearchWindow, label: str) -> None:
    actual = bind_research_window(candles, start=expected.start, end=expected.end)
    if actual != expected:
        mismatches = [
            field
            for field in ("expected_bars", "expected_segment_count", "candle_fingerprint")
            if getattr(actual, field) != getattr(expected, field)
        ]
        raise ResearchSplitError(f"{label} binding mismatch: {', '.join(mismatches)}")


def verify_research_split_plan(
    plan_path: str | Path,
    *,
    repository_root: str | Path,
) -> SplitPlanVerification:
    """Verify plan structure, dataset identity, every window hash, and holdout isolation."""
    root = Path(repository_root).resolve()
    candidate_plan_path = Path(plan_path)
    if not candidate_plan_path.is_absolute():
        candidate_plan_path = root / candidate_plan_path
    candidate_plan_path = candidate_plan_path.resolve()
    if not candidate_plan_path.is_relative_to(root):
        raise ResearchSplitError("split plan path escapes repository root")
    plan = ResearchSplitPlan.model_validate_json(candidate_plan_path.read_text(encoding="utf-8"))

    manifest_path = (root / plan.dataset_manifest_path).resolve()
    if not manifest_path.is_relative_to(root):
        raise ResearchSplitError("dataset manifest path escapes repository root")
    segments, manifest = load_research_segments(manifest_path)
    candles = [candle for segment in segments for candle in segment]

    manifest_end = manifest.last_ts + timedelta(seconds=manifest.timeframe.seconds)
    dataset_checks = {
        "dataset_id": (plan.dataset_id, manifest.dataset_id),
        "dataset_normalized_sha256": (
            plan.dataset_normalized_sha256,
            manifest.normalized_sha256,
        ),
        "dataset_bars": (plan.dataset_bars, len(candles)),
        "dataset_start": (plan.dataset_start, manifest.first_ts),
        "dataset_end": (plan.dataset_end, manifest_end),
        "timeframe": (plan.timeframe, manifest.timeframe.value),
        "dataset_candle_fingerprint": (
            plan.dataset_candle_fingerprint,
            fingerprint_candles(candles),
        ),
    }
    mismatches = [name for name, (expected, actual) in dataset_checks.items() if expected != actual]
    if mismatches:
        raise ResearchSplitError(f"split plan dataset binding mismatch: {', '.join(mismatches)}")

    _verify_window(candles, plan.development, "development")
    _verify_window(candles, plan.final_holdout, "final_holdout")
    stitched_test_bars = 0
    for fold in plan.folds:
        _verify_window(candles, fold.train, f"{fold.fold_id}.train")
        _verify_window(candles, fold.validation, f"{fold.fold_id}.validation")
        _verify_window(candles, fold.test, f"{fold.fold_id}.test")
        stitched_test_bars += fold.test.expected_bars

    return SplitPlanVerification(
        plan=plan,
        dataset_manifest=manifest,
        plan_sha256=sha256_path(candidate_plan_path),
        fold_count=len(plan.folds),
        stitched_test_bars=stitched_test_bars,
    )
