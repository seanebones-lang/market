"""Prospective, offline analysis for Robinhood v2 execution-cost observations.

The module freezes a sampling protocol and run plan before observations are collected, verifies
every G3.2c evidence bundle, measures schedule coverage, summarizes exact-Decimal cost
distributions, and writes an immutable corpus-bound result. It has no broker transport,
authentication, signing, credential, or order capability.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from market.backtest.reproducibility import (
    pretty_json_bytes,
    sha256_bytes,
    sha256_path,
)
from market.execution.robinhood.observations import (
    EvidenceFile,
    ExecutionCostEvidenceArtifacts,
    ExecutionCostObservation,
    verify_execution_cost_evidence,
)

COST_SAMPLING_PROTOCOL_SCHEMA_VERSION: Literal[1] = 1
COST_SAMPLING_RUN_PLAN_SCHEMA_VERSION: Literal[1] = 1
COST_SAMPLING_SUMMARY_SCHEMA_VERSION: Literal[1] = 1
COST_SAMPLING_EVIDENCE_SCHEMA_VERSION: Literal[1] = 1
_SECONDS_PER_DAY = 86_400
_BPS = Decimal("10000")


class CostSamplingError(ValueError):
    """Raised when a protocol, run plan, corpus, or summary fails its contract."""


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value.astimezone(UTC)


def _utc_validator(value: datetime, info: object) -> datetime:
    return _require_utc(value, str(getattr(info, "field_name", "timestamp")))


def _validate_sha256(value: str, field_name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _coverage(observed: int, expected: int) -> Decimal | None:
    if expected == 0:
        return None
    return Decimal(observed) / Decimal(expected)


class CostSamplingProtocol(BaseModel):
    """Invariant study decisions frozen before a dated collection run is planned."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = COST_SAMPLING_PROTOCOL_SCHEMA_VERSION
    protocol_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    protocol_version: str = Field(min_length=1)
    frozen_at: datetime
    venue: Literal["robinhood_crypto"] = "robinhood_crypto"
    api_version: Literal["v2"] = "v2"
    routing_model: Literal["partner_exchanges"] = "partner_exchanges"
    required_source_kind: Literal["captured_read_only_v2", "synthetic_fixture"]
    symbol: Literal["BTC-USD"] = "BTC-USD"
    asset_quantities: tuple[Decimal, ...] = Field(min_length=1, max_length=10)
    primary_asset_quantity: Decimal = Field(gt=0)
    request_side: Literal["both"] = "both"
    official_max_quantities_per_request: Literal[10] = 10
    duration_full_utc_days: int = Field(ge=1, le=60)
    sampling_interval_seconds: int = Field(ge=60, le=_SECONDS_PER_DAY)
    schedule_offset_seconds: int = Field(ge=0)
    maximum_schedule_lateness_seconds: int = Field(ge=0, le=300)
    no_early_capture: Literal[True] = True
    no_window_extension: Literal[True] = True
    quantile_method: Literal["nearest_rank"] = "nearest_rank"
    reported_quantiles: tuple[Decimal, ...] = Field(min_length=1)
    base_profile_quantile: Decimal
    stress_profile_quantile: Decimal
    base_unmeasured_slippage_buffer_bps: Decimal = Field(ge=0)
    stress_unmeasured_slippage_buffer_bps: Decimal = Field(ge=0)
    fee_mapping_method: Literal["maximum_observed"] = "maximum_observed"
    size_impact_mapping_method: Literal["maximum_directional_nonnegative_quantile"] = (
        "maximum_directional_nonnegative_quantile"
    )
    minimum_overall_cycle_coverage: Decimal = Field(gt=0, le=1)
    minimum_utc_hour_cycle_coverage: Decimal = Field(gt=0, le=1)
    minimum_day_of_week_cycle_coverage: Decimal = Field(gt=0, le=1)
    minimum_distinct_utc_dates: int = Field(ge=1)
    block_bootstrap_unit: Literal["utc_day"] = "utc_day"
    block_bootstrap_replicates: int = Field(ge=1)
    block_bootstrap_seed: int = Field(ge=0)
    bootstrap_confidence: Decimal = Field(gt=0, lt=1)

    @field_validator("frozen_at")
    @classmethod
    def _frozen_at(cls, value: datetime, info: object) -> datetime:
        return _utc_validator(value, info)

    @field_validator(
        "base_profile_quantile",
        "stress_profile_quantile",
    )
    @classmethod
    def _profile_probability(cls, value: Decimal) -> Decimal:
        if not Decimal("0") < value <= Decimal("1"):
            raise ValueError("profile quantiles must be in (0, 1]")
        return value

    @field_validator("reported_quantiles")
    @classmethod
    def _reported_probabilities(cls, values: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(not Decimal("0") < value <= Decimal("1") for value in values):
            raise ValueError("reported quantiles must be in (0, 1]")
        if tuple(sorted(set(values))) != values:
            raise ValueError("reported quantiles must be unique and strictly increasing")
        return values

    @model_validator(mode="after")
    def _coherent_protocol(self) -> Self:
        if any(quantity <= 0 for quantity in self.asset_quantities):
            raise ValueError("asset quantities must be positive")
        if tuple(sorted(set(self.asset_quantities))) != self.asset_quantities:
            raise ValueError("asset quantities must be unique and strictly increasing")
        if self.primary_asset_quantity not in self.asset_quantities:
            raise ValueError("primary asset quantity must be in asset_quantities")
        if len(self.asset_quantities) > self.official_max_quantities_per_request:
            raise ValueError("asset quantities exceed the official per-request maximum")
        if _SECONDS_PER_DAY % self.sampling_interval_seconds != 0:
            raise ValueError("sampling interval must divide one UTC day exactly")
        if self.schedule_offset_seconds >= self.sampling_interval_seconds:
            raise ValueError("schedule offset must be less than the sampling interval")
        if self.maximum_schedule_lateness_seconds >= self.sampling_interval_seconds:
            raise ValueError("schedule lateness must be less than the sampling interval")
        if self.base_profile_quantile not in self.reported_quantiles:
            raise ValueError("base profile quantile must be reported")
        if self.stress_profile_quantile not in self.reported_quantiles:
            raise ValueError("stress profile quantile must be reported")
        if self.stress_profile_quantile <= self.base_profile_quantile:
            raise ValueError("stress profile quantile must exceed base profile quantile")
        if self.stress_unmeasured_slippage_buffer_bps < self.base_unmeasured_slippage_buffer_bps:
            raise ValueError("stress slippage buffer must not be below the base buffer")
        if self.minimum_distinct_utc_dates > self.duration_full_utc_days:
            raise ValueError("minimum distinct dates cannot exceed study duration")
        return self

    @property
    def expected_cycles(self) -> int:
        return self.duration_full_utc_days * _SECONDS_PER_DAY // self.sampling_interval_seconds


class CostSamplingRunPlan(BaseModel):
    """Dated window and authorization reference frozen before collection starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = COST_SAMPLING_RUN_PLAN_SCHEMA_VERSION
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$")
    protocol_id: str
    protocol_version: str
    protocol_sha256: str
    frozen_at: datetime
    authorization_reference: str = Field(min_length=1)
    collection_status: Literal["planned_not_started"] = "planned_not_started"
    scheduled_start: datetime
    scheduled_end: datetime
    expected_cycles: int = Field(gt=0)

    @field_validator("frozen_at", "scheduled_start", "scheduled_end")
    @classmethod
    def _utc_times(cls, value: datetime, info: object) -> datetime:
        return _utc_validator(value, info)

    @field_validator("protocol_sha256")
    @classmethod
    def _protocol_hash(cls, value: str) -> str:
        return _validate_sha256(value, "protocol_sha256")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.frozen_at >= self.scheduled_start:
            raise ValueError("run plan must be frozen before scheduled collection")
        if self.scheduled_end <= self.scheduled_start:
            raise ValueError("scheduled end must be after scheduled start")
        return self


@dataclass(frozen=True)
class LoadedCostSamplingProtocol:
    protocol: CostSamplingProtocol
    path: Path
    sha256: str


@dataclass(frozen=True)
class LoadedCostSamplingRunPlan:
    plan: CostSamplingRunPlan
    path: Path
    sha256: str


class QuantileEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    probability: Decimal = Field(gt=0, le=1)
    value: Decimal
    daily_block_bootstrap_lower: Decimal
    daily_block_bootstrap_upper: Decimal

    @model_validator(mode="after")
    def _ordered_interval(self) -> Self:
        if self.daily_block_bootstrap_upper < self.daily_block_bootstrap_lower:
            raise ValueError("bootstrap upper bound must not be below lower bound")
        return self


class MetricDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric: str
    count: int = Field(gt=0)
    minimum: Decimal
    mean: Decimal
    maximum: Decimal
    quantiles: tuple[QuantileEstimate, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ordered_range(self) -> Self:
        if self.maximum < self.minimum:
            raise ValueError("distribution maximum must not be below minimum")
        if not self.minimum <= self.mean <= self.maximum:
            raise ValueError("distribution mean must lie inside its range")
        return self

    def quantile(self, probability: Decimal) -> Decimal:
        matches = [item.value for item in self.quantiles if item.probability == probability]
        if len(matches) != 1:
            raise CostSamplingError(f"metric {self.metric} lacks quantile {probability}")
        return matches[0]


class QuantityCostSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_quantity: Decimal = Field(gt=0)
    complete_cycle_observations: int = Field(gt=0)
    midpoint_usd: MetricDistribution
    quoted_full_spread_bps: MetricDistribution
    fee_bps_per_fill: MetricDistribution
    buy_size_impact_bps: MetricDistribution
    sell_size_impact_bps: MetricDistribution
    buy_all_in_one_way_cost_bps: MetricDistribution
    sell_all_in_one_way_cost_bps: MetricDistribution
    indicative_round_trip_cost_bps: MetricDistribution
    capture_span_seconds: MetricDistribution
    estimate_age_seconds: MetricDistribution


class CoverageCell(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    expected_cycles: int = Field(ge=0)
    observed_complete_cycles: int = Field(ge=0)
    coverage_ratio: Decimal | None
    required_minimum: Decimal | None
    passes: bool | None

    @model_validator(mode="after")
    def _coherent(self) -> Self:
        if self.observed_complete_cycles > self.expected_cycles:
            raise ValueError("observed coverage cannot exceed expected coverage")
        if self.expected_cycles == 0:
            if any(value is not None for value in (self.coverage_ratio, self.required_minimum)):
                raise ValueError("non-applicable coverage cell must not carry ratios")
            if self.passes is not None:
                raise ValueError("non-applicable coverage cell must not pass or fail")
        else:
            if self.coverage_ratio is None or self.required_minimum is None:
                raise ValueError("applicable coverage cell requires ratios")
            if self.passes != (self.coverage_ratio >= self.required_minimum):
                raise ValueError("coverage pass bit mismatches ratio")
        return self


class PartialCycle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scheduled_at: datetime
    present_quantities: tuple[Decimal, ...]
    missing_quantities: tuple[Decimal, ...]

    @field_validator("scheduled_at")
    @classmethod
    def _utc_time(cls, value: datetime, info: object) -> datetime:
        return _utc_validator(value, info)


class MappedResearchCostProfile(BaseModel):
    """Candidate engine inputs derived prospectively from admitted cost observations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile_name: Literal["base", "stress"]
    asset_quantity: Decimal = Field(gt=0)
    source_quantile: Decimal = Field(gt=0, le=1)
    transaction_fee_bps_per_fill_assumption: Decimal = Field(ge=0)
    quoted_spread_bps_assumption: Decimal = Field(ge=0)
    observed_size_impact_bps: Decimal = Field(ge=0)
    unmeasured_execution_slippage_buffer_bps: Decimal = Field(ge=0)
    adverse_slippage_bps_assumption: Decimal = Field(ge=0)
    additive_round_trip_cost_bps: Decimal = Field(ge=0)
    interpretation: Literal["candidate_research_input_not_execution_evidence"] = (
        "candidate_research_input_not_execution_evidence"
    )


class ObservationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str
    scheduled_at: datetime
    received_at: datetime
    asset_quantity: Decimal = Field(gt=0)
    evidence_manifest_sha256: str
    observation_sha256: str

    @field_validator("scheduled_at", "received_at")
    @classmethod
    def _utc_times(cls, value: datetime, info: object) -> datetime:
        return _utc_validator(value, info)

    @field_validator("evidence_manifest_sha256", "observation_sha256")
    @classmethod
    def _hashes(cls, value: str, info: object) -> str:
        return _validate_sha256(value, str(getattr(info, "field_name", "sha256")))


class CostSamplingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = COST_SAMPLING_SUMMARY_SCHEMA_VERSION
    summary_id: str = Field(pattern=r"^rh-v2-cost-study-[a-z0-9._-]+-[0-9a-f]{16}$")
    protocol_id: str
    protocol_version: str
    protocol_sha256: str
    run_id: str
    run_plan_sha256: str
    source_kind: Literal["captured_read_only_v2", "synthetic_fixture"]
    symbol: Literal["BTC-USD"]
    scheduled_start: datetime
    scheduled_end: datetime
    expected_cycles: int = Field(gt=0)
    complete_cycles: int = Field(ge=0)
    partial_cycles: tuple[PartialCycle, ...]
    missing_cycle_times: tuple[datetime, ...]
    overall_cycle_coverage: Decimal = Field(ge=0, le=1)
    observed_distinct_utc_dates: int = Field(ge=0)
    utc_hour_coverage: tuple[CoverageCell, ...] = Field(min_length=24, max_length=24)
    day_of_week_coverage: tuple[CoverageCell, ...] = Field(min_length=7, max_length=7)
    admission_status: Literal["pass", "fail"]
    admission_failures: tuple[str, ...]
    quantity_summaries: tuple[QuantityCostSummary, ...]
    base_profile: MappedResearchCostProfile | None
    stress_profile: MappedResearchCostProfile | None
    contains_execution: Literal[False] = False
    strategy_result_generated: Literal[False] = False
    network_contact_performed_by_analysis: Literal[False] = False

    @field_validator("scheduled_start", "scheduled_end")
    @classmethod
    def _utc_times(cls, value: datetime, info: object) -> datetime:
        return _utc_validator(value, info)

    @field_validator("missing_cycle_times")
    @classmethod
    def _utc_missing_times(cls, values: tuple[datetime, ...], info: object) -> tuple[datetime, ...]:
        return tuple(_utc_validator(value, info) for value in values)

    @field_validator("protocol_sha256", "run_plan_sha256")
    @classmethod
    def _hashes(cls, value: str, info: object) -> str:
        return _validate_sha256(value, str(getattr(info, "field_name", "sha256")))

    @model_validator(mode="after")
    def _admission_consistency(self) -> Self:
        passed = self.admission_status == "pass"
        if passed != (not self.admission_failures):
            raise ValueError("admission status mismatches failure list")
        if passed != (self.base_profile is not None and self.stress_profile is not None):
            raise ValueError("cost profiles may exist only for an admitted corpus")
        return self


class CostSamplingEvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = COST_SAMPLING_EVIDENCE_SCHEMA_VERSION
    summary_id: str
    protocol_id: str
    run_id: str
    contains_execution: Literal[False] = False
    contains_account_identifier: Literal[False] = False
    network_contact_performed_by_analysis: Literal[False] = False
    summary: EvidenceFile
    corpus: EvidenceFile


@dataclass(frozen=True)
class CostSamplingAnalysis:
    summary: CostSamplingSummary
    bindings: tuple[ObservationBinding, ...]


@dataclass(frozen=True)
class CostSamplingEvidenceArtifacts:
    analysis: CostSamplingAnalysis
    manifest: CostSamplingEvidenceManifest
    bundle_dir: Path
    summary_path: Path
    corpus_path: Path
    manifest_path: Path


def load_cost_sampling_protocol(path: str | Path) -> LoadedCostSamplingProtocol:
    resolved = Path(path).resolve()
    protocol = CostSamplingProtocol.model_validate_json(resolved.read_text(encoding="utf-8"))
    return LoadedCostSamplingProtocol(
        protocol=protocol, path=resolved, sha256=sha256_path(resolved)
    )


def _validate_run_plan(
    loaded_protocol: LoadedCostSamplingProtocol,
    plan: CostSamplingRunPlan,
) -> None:
    protocol = loaded_protocol.protocol
    if plan.protocol_id != protocol.protocol_id:
        raise CostSamplingError("run plan protocol ID mismatch")
    if plan.protocol_version != protocol.protocol_version:
        raise CostSamplingError("run plan protocol version mismatch")
    if plan.protocol_sha256 != loaded_protocol.sha256:
        raise CostSamplingError("run plan protocol SHA-256 mismatch")
    if plan.frozen_at < protocol.frozen_at:
        raise CostSamplingError("run plan cannot be frozen before its protocol")
    expected_end = plan.scheduled_start + timedelta(days=protocol.duration_full_utc_days)
    if plan.scheduled_end != expected_end:
        raise CostSamplingError("run plan does not cover the configured full UTC days")
    if plan.expected_cycles != protocol.expected_cycles:
        raise CostSamplingError("run plan expected cycle count mismatch")
    start_midnight = datetime.combine(plan.scheduled_start.date(), time(), tzinfo=UTC)
    if plan.scheduled_start != start_midnight + timedelta(seconds=protocol.schedule_offset_seconds):
        raise CostSamplingError("run plan must start at the first configured slot of a UTC day")


def load_cost_sampling_run_plan(
    path: str | Path,
    loaded_protocol: LoadedCostSamplingProtocol,
) -> LoadedCostSamplingRunPlan:
    resolved = Path(path).resolve()
    plan = CostSamplingRunPlan.model_validate_json(resolved.read_text(encoding="utf-8"))
    _validate_run_plan(loaded_protocol, plan)
    return LoadedCostSamplingRunPlan(plan=plan, path=resolved, sha256=sha256_path(resolved))


def build_cost_sampling_run_plan(
    loaded_protocol: LoadedCostSamplingProtocol,
    *,
    scheduled_start: datetime,
    frozen_at: datetime,
    authorization_reference: str,
    run_id: str | None = None,
) -> CostSamplingRunPlan:
    protocol = loaded_protocol.protocol
    scheduled_start = _require_utc(scheduled_start, "scheduled_start")
    frozen_at = _require_utc(frozen_at, "frozen_at")
    scheduled_end = scheduled_start + timedelta(days=protocol.duration_full_utc_days)
    resolved_run_id = run_id or (
        f"{protocol.protocol_id}-{scheduled_start.strftime('%Y%m%dt%H%M%Sz')}"
    )
    plan = CostSamplingRunPlan(
        run_id=resolved_run_id,
        protocol_id=protocol.protocol_id,
        protocol_version=protocol.protocol_version,
        protocol_sha256=loaded_protocol.sha256,
        frozen_at=frozen_at,
        authorization_reference=authorization_reference,
        scheduled_start=scheduled_start,
        scheduled_end=scheduled_end,
        expected_cycles=protocol.expected_cycles,
    )
    _validate_run_plan(loaded_protocol, plan)
    return plan


def write_cost_sampling_run_plan(path: str | Path, plan: CostSamplingRunPlan) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    data = pretty_json_bytes(plan.model_dump(mode="json"))
    with output.open("xb") as file:
        file.write(data)
    return output


def expected_sampling_slots(
    protocol: CostSamplingProtocol,
    plan: CostSamplingRunPlan,
) -> tuple[datetime, ...]:
    return tuple(
        plan.scheduled_start + timedelta(seconds=index * protocol.sampling_interval_seconds)
        for index in range(plan.expected_cycles)
    )


def _nearest_rank(values: list[Decimal] | tuple[Decimal, ...], probability: Decimal) -> Decimal:
    if not values:
        raise CostSamplingError("nearest-rank quantile requires observations")
    if not Decimal("0") < probability <= Decimal("1"):
        raise CostSamplingError("nearest-rank probability must be in (0, 1]")
    ordered = sorted(values)
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[rank - 1]


def _bootstrap_interval(
    values_by_day: dict[date, list[Decimal]],
    *,
    probability: Decimal,
    protocol: CostSamplingProtocol,
    salt: str,
) -> tuple[Decimal, Decimal]:
    dates = sorted(values_by_day)
    if not dates:
        raise CostSamplingError("daily block bootstrap requires observations")
    salt_digest = hashlib.sha256(salt.encode("utf-8")).digest()
    salt_seed = int.from_bytes(salt_digest[:8], "big")
    generator = random.Random(protocol.block_bootstrap_seed + salt_seed)
    estimates: list[Decimal] = []
    for _ in range(protocol.block_bootstrap_replicates):
        sampled_dates = [generator.choice(dates) for _ in dates]
        sample = [value for sampled_date in sampled_dates for value in values_by_day[sampled_date]]
        estimates.append(_nearest_rank(sample, probability))
    tail = (Decimal("1") - protocol.bootstrap_confidence) / Decimal("2")
    return _nearest_rank(estimates, tail), _nearest_rank(estimates, Decimal("1") - tail)


def _distribution(
    metric: str,
    values_by_slot: list[tuple[datetime, Decimal]],
    protocol: CostSamplingProtocol,
    *,
    salt: str,
) -> MetricDistribution:
    values = [value for _, value in values_by_slot]
    by_day: dict[date, list[Decimal]] = defaultdict(list)
    for slot, value in values_by_slot:
        by_day[slot.date()].append(value)
    quantiles = []
    for probability in protocol.reported_quantiles:
        lower, upper = _bootstrap_interval(
            by_day,
            probability=probability,
            protocol=protocol,
            salt=f"{salt}:{metric}:{probability}",
        )
        quantiles.append(
            QuantileEstimate(
                probability=probability,
                value=_nearest_rank(values, probability),
                daily_block_bootstrap_lower=lower,
                daily_block_bootstrap_upper=upper,
            )
        )
    return MetricDistribution(
        metric=metric,
        count=len(values),
        minimum=min(values),
        mean=sum(values, Decimal("0")) / Decimal(len(values)),
        maximum=max(values),
        quantiles=tuple(quantiles),
    )


def _match_scheduled_slot(
    received_at: datetime,
    protocol: CostSamplingProtocol,
    plan: CostSamplingRunPlan,
) -> datetime:
    if received_at < plan.scheduled_start or received_at >= plan.scheduled_end:
        raise CostSamplingError("observation receive time is outside the scheduled window")
    elapsed = received_at - plan.scheduled_start
    elapsed_seconds = elapsed.days * _SECONDS_PER_DAY + elapsed.seconds
    slot_index = elapsed_seconds // protocol.sampling_interval_seconds
    slot = plan.scheduled_start + timedelta(seconds=slot_index * protocol.sampling_interval_seconds)
    lateness = received_at - slot
    if lateness < timedelta(0):
        raise CostSamplingError("observation precedes its scheduled slot")
    if lateness > timedelta(seconds=protocol.maximum_schedule_lateness_seconds):
        raise CostSamplingError("observation exceeds maximum schedule lateness")
    return slot


def _cycle_signature(observation: ExecutionCostObservation) -> tuple[object, ...]:
    return (
        observation.endpoint_receive_times,
        observation.best_bid_usd,
        observation.best_ask_usd,
        observation.account_status,
        observation.account_fee_ratio,
        observation.account_thirty_day_volume,
        observation.trading_pair_status,
        observation.asset_increment,
        observation.quote_increment,
        observation.maximum_order_size,
        observation.minimum_order_amount_usd,
    )


def _verify_complete_cycle(
    slot: datetime,
    observations: dict[Decimal, ExecutionCostEvidenceArtifacts],
) -> None:
    signatures = {_cycle_signature(artifact.observation) for artifact in observations.values()}
    if len(signatures) != 1:
        raise CostSamplingError(
            f"quantity observations do not share one capture cycle at {slot.isoformat()}"
        )


def _coverage_cells(
    *,
    keys: tuple[str, ...],
    expected_by_key: dict[str, int],
    observed_by_key: dict[str, int],
    required_minimum: Decimal,
) -> tuple[CoverageCell, ...]:
    cells = []
    for key in keys:
        expected = expected_by_key.get(key, 0)
        observed = observed_by_key.get(key, 0)
        ratio = _coverage(observed, expected)
        cells.append(
            CoverageCell(
                key=key,
                expected_cycles=expected,
                observed_complete_cycles=observed,
                coverage_ratio=ratio,
                required_minimum=required_minimum if expected else None,
                passes=(ratio >= required_minimum) if ratio is not None else None,
            )
        )
    return tuple(cells)


def _quantity_summary(
    quantity: Decimal,
    observations: list[tuple[datetime, ExecutionCostObservation]],
    protocol: CostSamplingProtocol,
) -> QuantityCostSummary:
    def values(
        selector: Callable[[ExecutionCostObservation], Decimal],
    ) -> list[tuple[datetime, Decimal]]:
        return [(slot, selector(observation)) for slot, observation in observations]

    salt = f"{protocol.protocol_id}:{quantity}"
    return QuantityCostSummary(
        asset_quantity=quantity,
        complete_cycle_observations=len(observations),
        midpoint_usd=_distribution(
            "midpoint_usd", values(lambda item: item.midpoint_usd), protocol, salt=salt
        ),
        quoted_full_spread_bps=_distribution(
            "quoted_full_spread_bps",
            values(lambda item: item.quoted_full_spread_bps),
            protocol,
            salt=salt,
        ),
        fee_bps_per_fill=_distribution(
            "fee_bps_per_fill",
            values(lambda item: item.account_fee_ratio * _BPS),
            protocol,
            salt=salt,
        ),
        buy_size_impact_bps=_distribution(
            "buy_size_impact_bps",
            values(lambda item: item.buy.size_impact_from_best_touch_bps),
            protocol,
            salt=salt,
        ),
        sell_size_impact_bps=_distribution(
            "sell_size_impact_bps",
            values(lambda item: item.sell.size_impact_from_best_touch_bps),
            protocol,
            salt=salt,
        ),
        buy_all_in_one_way_cost_bps=_distribution(
            "buy_all_in_one_way_cost_bps",
            values(lambda item: item.buy.all_in_one_way_cost_from_mid_bps),
            protocol,
            salt=salt,
        ),
        sell_all_in_one_way_cost_bps=_distribution(
            "sell_all_in_one_way_cost_bps",
            values(lambda item: item.sell.all_in_one_way_cost_from_mid_bps),
            protocol,
            salt=salt,
        ),
        indicative_round_trip_cost_bps=_distribution(
            "indicative_round_trip_cost_bps",
            values(lambda item: item.indicative_round_trip_cost_bps),
            protocol,
            salt=salt,
        ),
        capture_span_seconds=_distribution(
            "capture_span_seconds",
            values(lambda item: item.capture_span_seconds),
            protocol,
            salt=salt,
        ),
        estimate_age_seconds=_distribution(
            "estimate_age_seconds",
            values(lambda item: item.maximum_estimate_age_seconds),
            protocol,
            salt=salt,
        ),
    )


def _mapped_profile(
    profile_name: Literal["base", "stress"],
    quantity_summary: QuantityCostSummary,
    *,
    source_quantile: Decimal,
    slippage_buffer: Decimal,
) -> MappedResearchCostProfile:
    fee = quantity_summary.fee_bps_per_fill.maximum
    spread = quantity_summary.quoted_full_spread_bps.quantile(source_quantile)
    buy_impact = quantity_summary.buy_size_impact_bps.quantile(source_quantile)
    sell_impact = quantity_summary.sell_size_impact_bps.quantile(source_quantile)
    observed_impact = max(Decimal("0"), buy_impact, sell_impact)
    adverse_slippage = observed_impact + slippage_buffer
    return MappedResearchCostProfile(
        profile_name=profile_name,
        asset_quantity=quantity_summary.asset_quantity,
        source_quantile=source_quantile,
        transaction_fee_bps_per_fill_assumption=fee,
        quoted_spread_bps_assumption=spread,
        observed_size_impact_bps=observed_impact,
        unmeasured_execution_slippage_buffer_bps=slippage_buffer,
        adverse_slippage_bps_assumption=adverse_slippage,
        additive_round_trip_cost_bps=(
            Decimal("2") * fee + spread + Decimal("2") * adverse_slippage
        ),
    )


def _summary_fingerprint(
    core: dict[str, object],
    bindings: Sequence[ObservationBinding],
) -> str:
    fingerprint_payload = {
        "summary": core,
        "bindings": [
            item.model_dump(mode="json")
            for item in sorted(bindings, key=lambda item: item.observation_id)
        ],
    }
    return sha256_bytes(pretty_json_bytes(fingerprint_payload))[:16]


def analyze_cost_sampling_corpus(
    loaded_protocol: LoadedCostSamplingProtocol,
    loaded_plan: LoadedCostSamplingRunPlan,
    manifest_paths: Sequence[str | Path],
) -> CostSamplingAnalysis:
    """Verify and summarize a complete or incomplete offline observation corpus."""
    protocol = loaded_protocol.protocol
    plan = loaded_plan.plan
    _validate_run_plan(loaded_protocol, plan)
    if not manifest_paths:
        raise CostSamplingError("cost sampling corpus contains no evidence manifests")

    expected_slots = expected_sampling_slots(protocol, plan)
    expected_set = set(expected_slots)
    cycle_artifacts: dict[datetime, dict[Decimal, ExecutionCostEvidenceArtifacts]] = defaultdict(
        dict
    )
    bindings: list[ObservationBinding] = []
    seen_ids: set[str] = set()

    for input_path in sorted((Path(path).resolve() for path in manifest_paths), key=str):
        artifacts = verify_execution_cost_evidence(input_path)
        observation = artifacts.observation
        if observation.observation_id in seen_ids:
            raise CostSamplingError(f"duplicate observation ID: {observation.observation_id}")
        seen_ids.add(observation.observation_id)
        if observation.source_kind != protocol.required_source_kind:
            raise CostSamplingError("observation source kind mismatches protocol")
        if observation.symbol != protocol.symbol:
            raise CostSamplingError("observation symbol mismatches protocol")
        if observation.asset_quantity not in protocol.asset_quantities:
            raise CostSamplingError("observation quantity is outside the protocol")
        slot = _match_scheduled_slot(observation.received_at, protocol, plan)
        if slot not in expected_set:
            raise CostSamplingError("observation did not map to an expected slot")
        if observation.asset_quantity in cycle_artifacts[slot]:
            raise CostSamplingError(
                f"duplicate quantity {observation.asset_quantity} at {slot.isoformat()}"
            )
        cycle_artifacts[slot][observation.asset_quantity] = artifacts
        bindings.append(
            ObservationBinding(
                observation_id=observation.observation_id,
                scheduled_at=slot,
                received_at=observation.received_at,
                asset_quantity=observation.asset_quantity,
                evidence_manifest_sha256=sha256_path(input_path),
                observation_sha256=artifacts.manifest.observation.sha256,
            )
        )

    required_quantities = set(protocol.asset_quantities)
    complete_slots: list[datetime] = []
    partial_cycles: list[PartialCycle] = []
    for slot in expected_slots:
        present = set(cycle_artifacts.get(slot, {}))
        if present == required_quantities:
            _verify_complete_cycle(slot, cycle_artifacts[slot])
            complete_slots.append(slot)
        elif present:
            partial_cycles.append(
                PartialCycle(
                    scheduled_at=slot,
                    present_quantities=tuple(sorted(present)),
                    missing_quantities=tuple(sorted(required_quantities - present)),
                )
            )

    complete_set = set(complete_slots)
    missing_cycle_times = tuple(slot for slot in expected_slots if slot not in complete_set)
    overall_coverage = Decimal(len(complete_slots)) / Decimal(len(expected_slots))
    observed_dates = {slot.date() for slot in complete_slots}

    expected_by_hour: dict[str, int] = defaultdict(int)
    observed_by_hour: dict[str, int] = defaultdict(int)
    expected_by_weekday: dict[str, int] = defaultdict(int)
    observed_by_weekday: dict[str, int] = defaultdict(int)
    weekday_keys = (
        "0-monday",
        "1-tuesday",
        "2-wednesday",
        "3-thursday",
        "4-friday",
        "5-saturday",
        "6-sunday",
    )
    for slot in expected_slots:
        expected_by_hour[f"{slot.hour:02d}"] += 1
        expected_by_weekday[weekday_keys[slot.weekday()]] += 1
    for slot in complete_slots:
        observed_by_hour[f"{slot.hour:02d}"] += 1
        observed_by_weekday[weekday_keys[slot.weekday()]] += 1

    utc_hour_coverage = _coverage_cells(
        keys=tuple(f"{hour:02d}" for hour in range(24)),
        expected_by_key=expected_by_hour,
        observed_by_key=observed_by_hour,
        required_minimum=protocol.minimum_utc_hour_cycle_coverage,
    )
    weekday_coverage = _coverage_cells(
        keys=weekday_keys,
        expected_by_key=expected_by_weekday,
        observed_by_key=observed_by_weekday,
        required_minimum=protocol.minimum_day_of_week_cycle_coverage,
    )

    failures: list[str] = []
    if overall_coverage < protocol.minimum_overall_cycle_coverage:
        failures.append("overall_cycle_coverage_below_minimum")
    if any(cell.passes is False for cell in utc_hour_coverage):
        failures.append("utc_hour_cycle_coverage_below_minimum")
    if any(cell.passes is False for cell in weekday_coverage):
        failures.append("day_of_week_cycle_coverage_below_minimum")
    if len(observed_dates) < protocol.minimum_distinct_utc_dates:
        failures.append("distinct_utc_dates_below_minimum")

    quantity_summaries: list[QuantityCostSummary] = []
    if complete_slots:
        for quantity in protocol.asset_quantities:
            quantity_observations = [
                (slot, cycle_artifacts[slot][quantity].observation) for slot in complete_slots
            ]
            quantity_summaries.append(_quantity_summary(quantity, quantity_observations, protocol))

    base_profile: MappedResearchCostProfile | None = None
    stress_profile: MappedResearchCostProfile | None = None
    if not failures:
        primary = next(
            summary
            for summary in quantity_summaries
            if summary.asset_quantity == protocol.primary_asset_quantity
        )
        base_profile = _mapped_profile(
            "base",
            primary,
            source_quantile=protocol.base_profile_quantile,
            slippage_buffer=protocol.base_unmeasured_slippage_buffer_bps,
        )
        stress_profile = _mapped_profile(
            "stress",
            primary,
            source_quantile=protocol.stress_profile_quantile,
            slippage_buffer=protocol.stress_unmeasured_slippage_buffer_bps,
        )

    core: dict[str, object] = {
        "schema_version": COST_SAMPLING_SUMMARY_SCHEMA_VERSION,
        "protocol_id": protocol.protocol_id,
        "protocol_version": protocol.protocol_version,
        "protocol_sha256": loaded_protocol.sha256,
        "run_id": plan.run_id,
        "run_plan_sha256": loaded_plan.sha256,
        "source_kind": protocol.required_source_kind,
        "symbol": protocol.symbol,
        "scheduled_start": plan.scheduled_start.isoformat().replace("+00:00", "Z"),
        "scheduled_end": plan.scheduled_end.isoformat().replace("+00:00", "Z"),
        "expected_cycles": plan.expected_cycles,
        "complete_cycles": len(complete_slots),
        "partial_cycles": [item.model_dump(mode="json") for item in partial_cycles],
        "missing_cycle_times": [
            item.isoformat().replace("+00:00", "Z") for item in missing_cycle_times
        ],
        "overall_cycle_coverage": str(overall_coverage),
        "observed_distinct_utc_dates": len(observed_dates),
        "utc_hour_coverage": [item.model_dump(mode="json") for item in utc_hour_coverage],
        "day_of_week_coverage": [item.model_dump(mode="json") for item in weekday_coverage],
        "admission_status": "fail" if failures else "pass",
        "admission_failures": failures,
        "quantity_summaries": [item.model_dump(mode="json") for item in quantity_summaries],
        "base_profile": base_profile.model_dump(mode="json") if base_profile else None,
        "stress_profile": stress_profile.model_dump(mode="json") if stress_profile else None,
        "contains_execution": False,
        "strategy_result_generated": False,
        "network_contact_performed_by_analysis": False,
    }
    fingerprint = _summary_fingerprint(core, bindings)
    summary = CostSamplingSummary(
        summary_id=f"rh-v2-cost-study-{plan.run_id}-{fingerprint}",
        **core,
    )
    return CostSamplingAnalysis(
        summary=summary,
        bindings=tuple(sorted(bindings, key=lambda item: item.observation_id)),
    )


def _evidence_file(path: str, data: bytes) -> EvidenceFile:
    return EvidenceFile(path=path, sha256=sha256_bytes(data), bytes=len(data))


def write_cost_sampling_evidence(
    output_root: str | Path,
    analysis: CostSamplingAnalysis,
) -> CostSamplingEvidenceArtifacts:
    summary_bytes = pretty_json_bytes(analysis.summary.model_dump(mode="json"))
    corpus_bytes = pretty_json_bytes(
        {
            "schema_version": 1,
            "summary_id": analysis.summary.summary_id,
            "observation_count": len(analysis.bindings),
            "observations": [item.model_dump(mode="json") for item in analysis.bindings],
        }
    )
    summary_filename = "summary.json"
    corpus_filename = "corpus.json"
    manifest_filename = "manifest.json"
    manifest = CostSamplingEvidenceManifest(
        summary_id=analysis.summary.summary_id,
        protocol_id=analysis.summary.protocol_id,
        run_id=analysis.summary.run_id,
        summary=_evidence_file(summary_filename, summary_bytes),
        corpus=_evidence_file(corpus_filename, corpus_bytes),
    )
    manifest_bytes = pretty_json_bytes(manifest.model_dump(mode="json"))
    bundle_dir = Path(output_root) / analysis.summary.summary_id
    bundle_dir.mkdir(parents=True, exist_ok=False)
    summary_path = bundle_dir / summary_filename
    corpus_path = bundle_dir / corpus_filename
    manifest_path = bundle_dir / manifest_filename
    for path, data in (
        (summary_path, summary_bytes),
        (corpus_path, corpus_bytes),
        (manifest_path, manifest_bytes),
    ):
        with path.open("xb") as file:
            file.write(data)
    return CostSamplingEvidenceArtifacts(
        analysis=analysis,
        manifest=manifest,
        bundle_dir=bundle_dir,
        summary_path=summary_path,
        corpus_path=corpus_path,
        manifest_path=manifest_path,
    )


def verify_cost_sampling_evidence(
    manifest_path: str | Path,
) -> CostSamplingEvidenceArtifacts:
    resolved_manifest = Path(manifest_path).resolve()
    manifest = CostSamplingEvidenceManifest.model_validate_json(
        resolved_manifest.read_text(encoding="utf-8")
    )
    bundle_dir = resolved_manifest.parent
    summary_path = bundle_dir / manifest.summary.path
    corpus_path = bundle_dir / manifest.corpus.path
    summary_bytes = summary_path.read_bytes()
    corpus_bytes = corpus_path.read_bytes()
    for label, data, expected in (
        ("summary", summary_bytes, manifest.summary),
        ("corpus", corpus_bytes, manifest.corpus),
    ):
        if len(data) != expected.bytes:
            raise CostSamplingError(f"{label} byte count mismatch")
        if sha256_bytes(data) != expected.sha256:
            raise CostSamplingError(f"{label} SHA-256 mismatch")
    summary = CostSamplingSummary.model_validate_json(summary_bytes)
    corpus_value = json.loads(corpus_bytes)
    if not isinstance(corpus_value, dict):
        raise CostSamplingError("corpus file must contain a JSON object")
    if set(corpus_value) != {"schema_version", "summary_id", "observation_count", "observations"}:
        raise CostSamplingError("corpus file has unexpected fields")
    if corpus_value["schema_version"] != 1:
        raise CostSamplingError("unsupported corpus schema")
    if corpus_value["summary_id"] != summary.summary_id:
        raise CostSamplingError("corpus summary ID mismatch")
    bindings = tuple(
        ObservationBinding.model_validate(item) for item in corpus_value["observations"]
    )
    if corpus_value["observation_count"] != len(bindings):
        raise CostSamplingError("corpus observation count mismatch")
    if manifest.summary_id != summary.summary_id:
        raise CostSamplingError("manifest summary ID mismatch")
    if manifest.protocol_id != summary.protocol_id or manifest.run_id != summary.run_id:
        raise CostSamplingError("manifest study identity mismatch")
    summary_core = summary.model_dump(mode="json", exclude={"summary_id"})
    expected_fingerprint = _summary_fingerprint(summary_core, bindings)
    expected_summary_id = f"rh-v2-cost-study-{summary.run_id}-{expected_fingerprint}"
    if summary.summary_id != expected_summary_id:
        raise CostSamplingError("summary content fingerprint mismatch")
    return CostSamplingEvidenceArtifacts(
        analysis=CostSamplingAnalysis(summary=summary, bindings=bindings),
        manifest=manifest,
        bundle_dir=bundle_dir,
        summary_path=summary_path,
        corpus_path=corpus_path,
        manifest_path=resolved_manifest,
    )
