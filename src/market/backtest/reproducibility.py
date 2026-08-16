"""Deterministic backtest identity, artifact integrity, and verification."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from market.domain.models import Candle

PROVENANCE_SCHEMA_VERSION = 1
INPUT_FINGERPRINT_SCHEMA = "market.candle-sequence.v1"

REQUIRED_ARTIFACT_NAMES = frozenset(
    {
        "input_data",
        "summary",
        "events",
        "fills",
        "accounting",
        "lifecycle",
        "benchmarks",
        "benchmark_fills",
        "benchmark_equity",
        "performance",
        "performance_observations",
        "equity",
    }
)

REQUIRED_EVIDENCE_ROLES = {
    "input_data": "input_candles.jsonl",
    "resolved_summary": "summary.json",
    "executions": "fills.jsonl",
    "trades": "lifecycle.jsonl",
    "equity_curve": "equity.jsonl",
    "metrics": "performance.jsonl",
}


class BacktestReproducibilityError(ValueError):
    """Raised when run identity or an artifact integrity contract is invalid."""


def canonical_json_bytes(value: object) -> bytes:
    """Return one stable UTF-8 JSON representation for hashing."""
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BacktestReproducibilityError("value is not canonical-JSON serializable") from exc
    return (text + "\n").encode("utf-8")


def pretty_json_bytes(value: object) -> bytes:
    """Return deterministic human-readable JSON bytes."""
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            indent=2,
            default=str,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BacktestReproducibilityError("value is not JSON serializable") from exc
    return (text + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candle_payload(candle: Candle) -> dict[str, Any]:
    return candle.model_dump(mode="json")


def fingerprint_candles(candles: tuple[Candle, ...] | list[Candle]) -> str:
    payload = {
        "fingerprint_schema": INPUT_FINGERPRINT_SCHEMA,
        "candles": [candle_payload(candle) for candle in candles],
    }
    return sha256_bytes(canonical_json_bytes(payload))


def fingerprint_config(resolved_config: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(resolved_config))


def validate_random_seed(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BacktestReproducibilityError("random_seed must be an integer")
    if value < 0:
        raise BacktestReproducibilityError("random_seed must be >= 0")
    return value


def validate_run_id(value: str) -> str:
    if not value or value in {".", ".."} or Path(value).name != value:
        raise BacktestReproducibilityError("run_id must be one nonempty path-safe name")
    return value


def _validate_sha256(value: str, field_name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


class SourceRevision(BaseModel):
    """Git identity captured before an output directory is created."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["clean", "dirty", "unavailable"]
    commit_sha: str | None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "unavailable":
            if self.commit_sha is not None:
                raise ValueError("unavailable source revision cannot carry a commit SHA")
            return self
        if self.commit_sha is None:
            raise ValueError("clean or dirty source revision requires a commit SHA")
        if len(self.commit_sha) != 40 or any(
            character not in "0123456789abcdef" for character in self.commit_sha
        ):
            raise ValueError("commit_sha must be a lowercase 40-character Git SHA")
        return self

    @property
    def reproducible(self) -> bool:
        return self.status == "clean"


def resolve_source_revision(repository_root: str | Path) -> SourceRevision:
    """Resolve Git commit and cleanliness without making a report depend on Git at verification."""
    root = Path(repository_root).resolve()
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return SourceRevision(status="unavailable", commit_sha=None)
    return SourceRevision(status="dirty" if status else "clean", commit_sha=revision)


class BacktestRunProvenance(BaseModel):
    """Pure run inputs required to reproduce deterministic simulation output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = PROVENANCE_SCHEMA_VERSION
    engine_version: str
    artifact_schema_version: int
    input_fingerprint_schema: str = INPUT_FINGERPRINT_SCHEMA
    input_data_sha256: str
    input_bar_count: int
    resolved_config: dict[str, Any]
    resolved_config_sha256: str
    random_seed: int
    randomness_used: bool = False

    @field_validator("input_data_sha256", "resolved_config_sha256")
    @classmethod
    def _sha256(cls, value: str, info: Any) -> str:
        return _validate_sha256(value, info.field_name)

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if not self.engine_version.strip():
            raise ValueError("engine_version must not be empty")
        if self.artifact_schema_version <= 0:
            raise ValueError("artifact_schema_version must be > 0")
        if self.input_fingerprint_schema != INPUT_FINGERPRINT_SCHEMA:
            raise ValueError("unsupported input fingerprint schema")
        if self.input_bar_count < 0:
            raise ValueError("input_bar_count must be >= 0")
        validate_random_seed(self.random_seed)
        if self.randomness_used:
            raise ValueError("the current deterministic engine must not report randomness_used")
        if fingerprint_config(self.resolved_config) != self.resolved_config_sha256:
            raise ValueError("resolved config checksum mismatch")
        return self

    def summary(self) -> dict[str, Any]:
        return {
            "provenance_schema_version": self.schema_version,
            "engine_version": self.engine_version,
            "input_fingerprint_schema": self.input_fingerprint_schema,
            "input_data_sha256": self.input_data_sha256,
            "input_bar_count": self.input_bar_count,
            "resolved_config": self.resolved_config,
            "resolved_config_sha256": self.resolved_config_sha256,
            "random_seed": self.random_seed,
            "randomness_used": self.randomness_used,
        }


def build_run_provenance(
    *,
    engine_version: str,
    artifact_schema_version: int,
    candles: tuple[Candle, ...] | list[Candle],
    resolved_config: dict[str, Any],
    random_seed: int,
) -> BacktestRunProvenance:
    seed = validate_random_seed(random_seed)
    return BacktestRunProvenance(
        engine_version=engine_version,
        artifact_schema_version=artifact_schema_version,
        input_data_sha256=fingerprint_candles(candles),
        input_bar_count=len(candles),
        resolved_config=resolved_config,
        resolved_config_sha256=fingerprint_config(resolved_config),
        random_seed=seed,
        randomness_used=False,
    )


class ArtifactIntegrity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    path: str
    media_type: Literal["application/json", "application/x-ndjson"]
    sha256: str
    bytes: int
    records: int

    @field_validator("sha256")
    @classmethod
    def _sha256(cls, value: str) -> str:
        return _validate_sha256(value, "sha256")

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if not self.name or not self.path or Path(self.path).name != self.path:
            raise ValueError("artifact name and path must be nonempty, local names")
        if self.bytes < 0 or self.records < 0:
            raise ValueError("artifact bytes and records must be >= 0")
        return self


def build_artifact_integrity(name: str, path: Path) -> ArtifactIntegrity:
    is_jsonl = path.suffix == ".jsonl"
    records = len(path.read_text(encoding="utf-8").splitlines()) if is_jsonl else 1
    return ArtifactIntegrity(
        name=name,
        path=path.name,
        media_type="application/x-ndjson" if is_jsonl else "application/json",
        sha256=sha256_path(path),
        bytes=path.stat().st_size,
        records=records,
    )


class BacktestArtifactManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    type: Literal["backtest_artifact_manifest"] = "backtest_artifact_manifest"
    run_id: str
    provenance: BacktestRunProvenance
    source_revision: SourceRevision
    code_identity_reproducible: bool
    evidence_roles: dict[str, str]
    artifacts: tuple[ArtifactIntegrity, ...]

    @model_validator(mode="after")
    def _valid(self) -> Self:
        validate_run_id(self.run_id)
        if self.schema_version != self.provenance.artifact_schema_version:
            raise ValueError("manifest and provenance artifact schema versions differ")
        if self.code_identity_reproducible != self.source_revision.reproducible:
            raise ValueError("code_identity_reproducible contradicts source revision status")
        names = [artifact.name for artifact in self.artifacts]
        if len(names) != len(set(names)):
            raise ValueError("manifest contains duplicate artifact names")
        if set(names) != REQUIRED_ARTIFACT_NAMES:
            raise ValueError("manifest artifact set does not match the required contract")
        if self.evidence_roles != REQUIRED_EVIDENCE_ROLES:
            raise ValueError("manifest evidence roles do not match the required contract")
        return self


def verify_backtest_report(manifest_path: str | Path) -> BacktestArtifactManifest:
    """Verify report integrity, identity consistency, and preserved input-candle checksum."""
    path = Path(manifest_path)
    try:
        manifest = BacktestArtifactManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BacktestReproducibilityError(f"invalid backtest manifest: {path}") from exc

    root = path.parent.resolve()
    artifacts = {artifact.name: artifact for artifact in manifest.artifacts}
    for artifact in manifest.artifacts:
        candidate = (root / artifact.path).resolve()
        if not candidate.is_relative_to(root):
            raise BacktestReproducibilityError(
                f"artifact path escapes report root: {artifact.path}"
            )
        if not candidate.is_file():
            raise BacktestReproducibilityError(f"missing backtest artifact: {artifact.path}")
        if candidate.stat().st_size != artifact.bytes:
            raise BacktestReproducibilityError(f"artifact byte-size mismatch: {artifact.path}")
        if sha256_path(candidate) != artifact.sha256:
            raise BacktestReproducibilityError(f"artifact checksum mismatch: {artifact.path}")
        records = (
            len(candidate.read_text(encoding="utf-8").splitlines())
            if artifact.media_type == "application/x-ndjson"
            else 1
        )
        if records != artifact.records:
            raise BacktestReproducibilityError(f"artifact record-count mismatch: {artifact.path}")

    summary_path = root / artifacts["summary"].path
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BacktestReproducibilityError("summary is not valid JSON") from exc
    expected_summary = {
        "schema_version": manifest.schema_version,
        "run_id": manifest.run_id,
        "engine_version": manifest.provenance.engine_version,
        "input_data_sha256": manifest.provenance.input_data_sha256,
        "input_bar_count": manifest.provenance.input_bar_count,
        "resolved_config_sha256": manifest.provenance.resolved_config_sha256,
        "random_seed": manifest.provenance.random_seed,
        "randomness_used": manifest.provenance.randomness_used,
        "source_revision": manifest.source_revision.model_dump(mode="json"),
        "code_identity_reproducible": manifest.code_identity_reproducible,
    }
    for key, expected in expected_summary.items():
        if summary.get(key) != expected:
            raise BacktestReproducibilityError(f"summary identity mismatch for {key}")
    if summary.get("resolved_config") != manifest.provenance.resolved_config:
        raise BacktestReproducibilityError("summary resolved config does not match manifest")
    if fingerprint_config(manifest.provenance.resolved_config) != (
        manifest.provenance.resolved_config_sha256
    ):
        raise BacktestReproducibilityError("manifest resolved config checksum mismatch")

    input_path = root / artifacts["input_data"].path
    candles: list[Candle] = []
    for expected_sequence, line in enumerate(
        input_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        try:
            row = json.loads(line)
            if (
                row["schema_version"] != manifest.schema_version
                or row["type"] != "input_candle"
                or row["run_id"] != manifest.run_id
                or row["sequence"] != expected_sequence
            ):
                raise BacktestReproducibilityError("input candle row identity mismatch")
            candles.append(Candle.model_validate(row["candle"]))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            if isinstance(exc, BacktestReproducibilityError):
                raise
            raise BacktestReproducibilityError("invalid input candle artifact row") from exc
    if len(candles) != manifest.provenance.input_bar_count:
        raise BacktestReproducibilityError("input candle count does not match provenance")
    if fingerprint_candles(candles) != manifest.provenance.input_data_sha256:
        raise BacktestReproducibilityError("input candle fingerprint mismatch")
    return manifest
