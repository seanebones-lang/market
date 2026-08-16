"""Deterministic candle quality checks and strategy-data admission control."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict

from market.domain.models import Candle, Timeframe


class QualityIssueCode(str, Enum):
    EMPTY = "empty_dataset"
    DUPLICATE = "duplicate_timestamp"
    OUT_OF_ORDER = "out_of_order_timestamp"
    GAP = "missing_bar_gap"
    MIXED_SOURCE = "mixed_source"
    MIXED_TIMEFRAME = "mixed_timeframe"
    UNCLOSED = "unclosed_bar"
    FUTURE = "future_timestamp"
    FUTURE_CONFIRMATION = "future_close_confirmation"
    QUALITY_FLAG = "data_quality_flag"
    EXTREME_MOVE = "extreme_hourly_move"
    RANGE_START = "unexpected_range_start"
    RANGE_END = "unexpected_range_end"


class QualityIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: QualityIssueCode
    index: int | None = None
    ts: datetime | None = None
    message: str
    missing_bars: int = 0


class ContiguousSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime
    bars: int


class DatasetQualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    status: str
    bars: int
    timeframe: Timeframe | None = None
    source: str | None = None
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    expected_start: datetime | None = None
    expected_end: datetime | None = None
    as_of: datetime | None = None
    missing_bars: int = 0
    segments: tuple[ContiguousSegment, ...] = ()
    issues: tuple[QualityIssue, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "pass"


class DatasetQualityError(ValueError):
    def __init__(self, report: DatasetQualityReport) -> None:
        self.report = report
        details = "; ".join(
            f"{issue.code.value}@{issue.ts.isoformat() if issue.ts else 'dataset'}: {issue.message}"
            for issue in report.issues[:10]
        )
        if len(report.issues) > 10:
            details += f"; plus {len(report.issues) - 10} more issue(s)"
        super().__init__(f"candle quality check failed: {details or 'unknown error'}")


def contains_only_declared_gaps(report: DatasetQualityReport) -> bool:
    """True when the sole defects are interior missing-bar gaps."""
    return bool(report.issues) and all(
        issue.code == QualityIssueCode.GAP for issue in report.issues
    )


def split_contiguous_candles(candles: list[Candle]) -> list[list[Candle]]:
    """Split on gaps/order defects; callers must re-warm indicators per segment."""
    if not candles:
        return []
    segments: list[list[Candle]] = [[candles[0]]]
    for candle in candles[1:]:
        previous = segments[-1][-1]
        expected = previous.ts.timestamp() + previous.timeframe.seconds
        if candle.ts.timestamp() != expected or candle.timeframe != previous.timeframe:
            segments.append([candle])
        else:
            segments[-1].append(candle)
    return segments


def validate_candles(
    candles: list[Candle],
    *,
    as_of: datetime | None = None,
    expected_start: datetime | None = None,
    expected_end: datetime | None = None,
    max_hourly_move: Decimal = Decimal("1.00"),
) -> DatasetQualityReport:
    """Return a machine-readable report without sorting, deduplicating, or filling bars."""
    if not candles:
        issue = QualityIssue(code=QualityIssueCode.EMPTY, message="dataset contains no candles")
        return DatasetQualityReport(
            status="fail",
            bars=0,
            expected_start=expected_start,
            expected_end=expected_end,
            as_of=as_of,
            issues=(issue,),
        )

    issues: list[QualityIssue] = []
    first = candles[0]
    source = first.source
    timeframe = first.timeframe
    if expected_start is not None and first.ts != expected_start:
        issues.append(
            QualityIssue(
                code=QualityIssueCode.RANGE_START,
                index=0,
                ts=first.ts,
                message=f"first candle is {first.ts.isoformat()}, expected {expected_start.isoformat()}",
            )
        )

    missing_bars = 0
    for index, candle in enumerate(candles):
        if candle.source != source:
            issues.append(
                QualityIssue(
                    code=QualityIssueCode.MIXED_SOURCE,
                    index=index,
                    ts=candle.ts,
                    message=f"source {candle.source!r} does not match {source!r}",
                )
            )
        if candle.timeframe != timeframe:
            issues.append(
                QualityIssue(
                    code=QualityIssueCode.MIXED_TIMEFRAME,
                    index=index,
                    ts=candle.ts,
                    message=(
                        f"timeframe {candle.timeframe.value!r} does not match {timeframe.value!r}"
                    ),
                )
            )
        if not candle.is_closed:
            issues.append(
                QualityIssue(
                    code=QualityIssueCode.UNCLOSED,
                    index=index,
                    ts=candle.ts,
                    message="currently forming or otherwise unconfirmed bar",
                )
            )
        if candle.quality_flags:
            flags = ",".join(flag.value for flag in candle.quality_flags)
            issues.append(
                QualityIssue(
                    code=QualityIssueCode.QUALITY_FLAG,
                    index=index,
                    ts=candle.ts,
                    message=f"bar carries non-tradable quality flags: {flags}",
                )
            )
        if as_of is not None:
            if candle.ts >= as_of:
                issues.append(
                    QualityIssue(
                        code=QualityIssueCode.FUTURE,
                        index=index,
                        ts=candle.ts,
                        message=f"bar opens at or after as_of {as_of.isoformat()}",
                    )
                )
            if candle.close_time > as_of:
                issues.append(
                    QualityIssue(
                        code=QualityIssueCode.UNCLOSED,
                        index=index,
                        ts=candle.ts,
                        message=f"bar closes after as_of {as_of.isoformat()}",
                    )
                )
            if candle.close_confirmed_at is not None and candle.close_confirmed_at > as_of:
                issues.append(
                    QualityIssue(
                        code=QualityIssueCode.FUTURE_CONFIRMATION,
                        index=index,
                        ts=candle.ts,
                        message=(
                            "close confirmation occurs after the dataset's "
                            f"as_of time {as_of.isoformat()}"
                        ),
                    )
                )

        if index == 0:
            continue
        previous = candles[index - 1]
        delta_seconds = int((candle.ts - previous.ts).total_seconds())
        expected_seconds = previous.timeframe.seconds
        if delta_seconds == 0:
            issues.append(
                QualityIssue(
                    code=QualityIssueCode.DUPLICATE,
                    index=index,
                    ts=candle.ts,
                    message="duplicate candle open timestamp",
                )
            )
        elif delta_seconds < 0:
            issues.append(
                QualityIssue(
                    code=QualityIssueCode.OUT_OF_ORDER,
                    index=index,
                    ts=candle.ts,
                    message="candle timestamps are not strictly ascending",
                )
            )
        elif delta_seconds != expected_seconds:
            count = max(delta_seconds // expected_seconds - 1, 0)
            missing_bars += count
            issues.append(
                QualityIssue(
                    code=QualityIssueCode.GAP,
                    index=index,
                    ts=candle.ts,
                    missing_bars=count,
                    message=(
                        f"expected next bar at {previous.close_time.isoformat()}, "
                        f"received {candle.ts.isoformat()}"
                    ),
                )
            )

        if previous.close > 0:
            absolute_move = abs(candle.close / previous.close - Decimal("1"))
            if absolute_move > max_hourly_move:
                issues.append(
                    QualityIssue(
                        code=QualityIssueCode.EXTREME_MOVE,
                        index=index,
                        ts=candle.ts,
                        message=(
                            f"absolute close-to-close move {absolute_move} exceeds "
                            f"limit {max_hourly_move}"
                        ),
                    )
                )

    last = candles[-1]
    if expected_end is not None and last.close_time != expected_end:
        issues.append(
            QualityIssue(
                code=QualityIssueCode.RANGE_END,
                index=len(candles) - 1,
                ts=last.ts,
                message=(
                    f"last candle closes at {last.close_time.isoformat()}, "
                    f"expected {expected_end.isoformat()}"
                ),
            )
        )

    segments = tuple(
        ContiguousSegment(start=segment[0].ts, end=segment[-1].close_time, bars=len(segment))
        for segment in split_contiguous_candles(candles)
    )
    return DatasetQualityReport(
        status="fail" if issues else "pass",
        bars=len(candles),
        timeframe=timeframe,
        source=source,
        first_ts=first.ts,
        last_ts=last.ts,
        expected_start=expected_start,
        expected_end=expected_end,
        as_of=as_of,
        missing_bars=missing_bars,
        segments=segments,
        issues=tuple(issues),
    )


def require_clean_candles(
    candles: list[Candle],
    *,
    as_of: datetime | None = None,
    expected_start: datetime | None = None,
    expected_end: datetime | None = None,
    max_hourly_move: Decimal = Decimal("1.00"),
) -> DatasetQualityReport:
    report = validate_candles(
        candles,
        as_of=as_of,
        expected_start=expected_start,
        expected_end=expected_end,
        max_hourly_move=max_hourly_move,
    )
    if not report.ok:
        raise DatasetQualityError(report)
    return report
