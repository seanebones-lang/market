"""Immutable, content-addressed research dataset artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from market.data.candles import CoinbaseCandleFetch, candles_csv_bytes, load_candles_csv
from market.data.quality import (
    DatasetQualityError,
    DatasetQualityReport,
    contains_only_declared_gaps,
    require_clean_candles,
    split_contiguous_candles,
    validate_candles,
)
from market.domain.models import Candle, Timeframe

DATASET_SCHEMA_VERSION = 1


class RegimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: datetime
    end: datetime
    bars: int
    trend: str
    volatility: str
    return_pct: Decimal
    annualized_volatility_pct: Decimal


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = DATASET_SCHEMA_VERSION
    dataset_id: str
    provider: str
    product: str
    timeframe: Timeframe
    requested_start: datetime
    requested_end: datetime
    retrieved_at: datetime
    bars: int
    first_ts: datetime
    last_ts: datetime
    raw_path: str
    raw_sha256: str
    raw_bytes: int
    normalized_path: str
    normalized_sha256: str
    normalized_bytes: int
    quality_report_path: str
    quality_report_sha256: str
    quality_status: str
    strategy_admission: str
    missing_bars: int
    contiguous_segments: int
    regimes: tuple[RegimeWindow, ...]


@dataclass(frozen=True)
class DatasetArtifacts:
    manifest: DatasetManifest
    manifest_path: Path
    raw_path: Path
    normalized_path: Path
    quality_report_path: Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, default=str, allow_nan=False) + "\n"
    ).encode("utf-8")


def _write_immutable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"refusing to overwrite immutable artifact: {path}")
        return
    with path.open("xb") as file:
        file.write(data)


def _quarter_key(value: datetime) -> tuple[int, int]:
    return value.year, (value.month - 1) // 3 + 1


def detect_regimes(candles: tuple[Candle, ...]) -> tuple[RegimeWindow, ...]:
    """Describe complete-enough calendar quarters by trend and realized volatility."""
    grouped: dict[tuple[int, int], list[Candle]] = {}
    for candle in candles:
        grouped.setdefault(_quarter_key(candle.ts), []).append(candle)

    regimes: list[RegimeWindow] = []
    for _, bars in sorted(grouped.items()):
        # Avoid assigning a regime to a thin partial quarter.
        if len(bars) < 24 * 45:
            continue
        first = bars[0]
        last = bars[-1]
        total_return = last.close / first.open - Decimal("1")
        returns = [
            bars[index].close / bars[index - 1].close - Decimal("1")
            for index in range(1, len(bars))
        ]
        mean = sum(returns, Decimal("0")) / Decimal(len(returns))
        variance = sum(((value - mean) ** 2 for value in returns), Decimal("0")) / Decimal(
            len(returns)
        )
        annualized_volatility = variance.sqrt() * Decimal(24 * 365).sqrt()
        if total_return >= Decimal("0.10"):
            trend = "bull"
        elif total_return <= Decimal("-0.10"):
            trend = "bear"
        else:
            trend = "sideways"
        if annualized_volatility >= Decimal("0.70"):
            volatility = "high"
        elif annualized_volatility <= Decimal("0.35"):
            volatility = "low"
        else:
            volatility = "medium"
        regimes.append(
            RegimeWindow(
                start=first.ts,
                end=last.close_time,
                bars=len(bars),
                trend=trend,
                volatility=volatility,
                return_pct=total_return * Decimal("100"),
                annualized_volatility_pct=annualized_volatility * Decimal("100"),
            )
        )
    return tuple(regimes)


def _raw_fetch_bytes(result: CoinbaseCandleFetch) -> bytes:
    raw = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "provider": result.source,
        "product": result.product,
        "timeframe": result.timeframe.value,
        "requested_start": result.requested_start.isoformat(),
        "requested_end": result.requested_end.isoformat(),
        "retrieved_at": result.retrieved_at.isoformat(),
        "batches": [
            {
                "request_start": batch.request_start.isoformat(),
                "request_end": batch.request_end.isoformat(),
                "rows": batch.rows,
            }
            for batch in result.raw_batches
        ],
    }
    return _canonical_json_bytes(raw)


def _quality_bytes(
    report: DatasetQualityReport,
    *,
    dataset_id: str,
    normalized_sha256: str,
    strategy_admission: str,
) -> bytes:
    value = report.model_dump(mode="json")
    value["dataset_id"] = dataset_id
    value["normalized_sha256"] = normalized_sha256
    value["strategy_admission"] = strategy_admission
    return _pretty_json_bytes(value)


def write_research_dataset(
    output_root: str | Path,
    result: CoinbaseCandleFetch,
    *,
    allow_declared_gaps: bool = False,
) -> DatasetArtifacts:
    """Validate and atomically add an immutable raw/normalized/quality/manifest set."""
    candles = list(result.candles)
    if any(candle.source != result.source for candle in candles):
        raise ValueError("fetch metadata source does not match normalized candles")
    if any(candle.timeframe != result.timeframe for candle in candles):
        raise ValueError("fetch metadata timeframe does not match normalized candles")
    report = validate_candles(
        candles,
        as_of=result.retrieved_at,
        expected_start=result.requested_start,
        expected_end=result.requested_end,
    )
    if report.ok:
        quality_status = "pass"
        strategy_admission = "continuous"
    elif allow_declared_gaps and contains_only_declared_gaps(report):
        quality_status = "pass_segmented"
        strategy_admission = "segments_only"
    else:
        raise DatasetQualityError(report)
    normalized_bytes = candles_csv_bytes(result.candles)
    normalized_sha = sha256_bytes(normalized_bytes)
    start_text = result.requested_start.strftime("%Y%m%dT%H%M%SZ")
    end_text = result.requested_end.strftime("%Y%m%dT%H%M%SZ")
    dataset_id = (
        f"coinbase-btc-usd-{result.timeframe.value}-{start_text}-{end_text}-{normalized_sha[:16]}"
    )

    raw_bytes = _raw_fetch_bytes(result)
    raw_sha = sha256_bytes(raw_bytes)
    quality_bytes = _quality_bytes(
        report,
        dataset_id=dataset_id,
        normalized_sha256=normalized_sha,
        strategy_admission=strategy_admission,
    )
    quality_sha = sha256_bytes(quality_bytes)

    root = Path(output_root)
    raw_path = root / "raw" / f"{dataset_id}-{raw_sha[:16]}.json"
    normalized_path = root / "normalized" / f"{dataset_id}.csv"
    quality_path = root / "quality" / f"{dataset_id}.quality.json"
    manifest_path = root / "manifests" / f"{dataset_id}.manifest.json"

    _write_immutable(raw_path, raw_bytes)
    _write_immutable(normalized_path, normalized_bytes)
    _write_immutable(quality_path, quality_bytes)

    regimes = detect_regimes(result.candles)
    manifest = DatasetManifest(
        dataset_id=dataset_id,
        provider=result.source,
        product=result.product,
        timeframe=result.timeframe,
        requested_start=result.requested_start,
        requested_end=result.requested_end,
        retrieved_at=result.retrieved_at,
        bars=len(result.candles),
        first_ts=result.candles[0].ts,
        last_ts=result.candles[-1].ts,
        raw_path=str(raw_path.relative_to(root)),
        raw_sha256=raw_sha,
        raw_bytes=len(raw_bytes),
        normalized_path=str(normalized_path.relative_to(root)),
        normalized_sha256=normalized_sha,
        normalized_bytes=len(normalized_bytes),
        quality_report_path=str(quality_path.relative_to(root)),
        quality_report_sha256=quality_sha,
        quality_status=quality_status,
        strategy_admission=strategy_admission,
        missing_bars=report.missing_bars,
        contiguous_segments=len(report.segments),
        regimes=regimes,
    )
    manifest_bytes = _pretty_json_bytes(manifest.model_dump(mode="json"))
    _write_immutable(manifest_path, manifest_bytes)
    return DatasetArtifacts(
        manifest=manifest,
        manifest_path=manifest_path,
        raw_path=raw_path,
        normalized_path=normalized_path,
        quality_report_path=quality_path,
    )


def _verify_dataset_artifacts(
    manifest_path: str | Path,
) -> tuple[Path, DatasetManifest, Path]:
    manifest_path = Path(manifest_path)
    manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent.parent
    resolved_root = root.resolve()

    def artifact_path(relative_path: str) -> Path:
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise ValueError(f"artifact path escapes dataset root: {relative_path}")
        return candidate

    raw_path = artifact_path(manifest.raw_path)
    normalized_path = artifact_path(manifest.normalized_path)
    quality_path = artifact_path(manifest.quality_report_path)
    checks = (
        (raw_path, manifest.raw_sha256),
        (normalized_path, manifest.normalized_sha256),
        (quality_path, manifest.quality_report_sha256),
    )
    for path, expected in checks:
        actual = sha256_path(path)
        if actual != expected:
            raise ValueError(f"checksum mismatch for {path}: expected {expected}, got {actual}")
    return normalized_path, manifest, quality_path


def load_research_dataset(manifest_path: str | Path) -> tuple[list[Candle], DatasetManifest]:
    """Return only a continuous dataset; segmented datasets require the segment loader."""
    normalized_path, manifest, _ = _verify_dataset_artifacts(manifest_path)
    if manifest.strategy_admission != "continuous" or manifest.quality_status != "pass":
        raise ValueError(
            "dataset is not admitted as a continuous series; use load_research_segments"
        )
    candles = load_candles_csv(normalized_path)
    require_clean_candles(
        candles,
        as_of=manifest.retrieved_at,
        expected_start=manifest.requested_start,
        expected_end=manifest.requested_end,
    )
    return candles, manifest


def load_research_segments(
    manifest_path: str | Path,
) -> tuple[list[list[Candle]], DatasetManifest]:
    """Return gap-delimited segments; each segment must re-warm indicators independently."""
    normalized_path, manifest, _ = _verify_dataset_artifacts(manifest_path)
    candles = load_candles_csv(normalized_path)
    report = validate_candles(
        candles,
        as_of=manifest.retrieved_at,
        expected_start=manifest.requested_start,
        expected_end=manifest.requested_end,
    )
    if report.ok:
        return [candles], manifest
    if manifest.strategy_admission != "segments_only" or not contains_only_declared_gaps(report):
        raise DatasetQualityError(report)
    segments = split_contiguous_candles(candles)
    for segment in segments:
        require_clean_candles(segment, as_of=manifest.retrieved_at)
    return segments, manifest
