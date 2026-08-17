"""Offline Robinhood v2 execution-cost observations.

This module deliberately has no HTTP, authentication, signing, or order capability. It accepts
previously captured or synthetic JSON shaped like four official read-only v2 responses, validates
their cross-field contract, derives comparable cost measures, and writes an immutable evidence
bundle with sensitive account identifiers redacted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SOURCE_FIXTURE_SCHEMA_VERSION: Literal[1] = 1
OBSERVATION_SCHEMA_VERSION: Literal[1] = 1
EVIDENCE_MANIFEST_SCHEMA_VERSION: Literal[1] = 1
MAX_ESTIMATE_AGE_SECONDS = Decimal("60")
MAX_FUTURE_SKEW_SECONDS = Decimal("5")
MAX_CAPTURE_SPAN_SECONDS = Decimal("60")
MAX_TOTAL_ARITHMETIC_RESIDUAL_USD = Decimal("0.02")
_BPS = Decimal("10000")


class ExecutionCostObservationError(ValueError):
    """Raised when an offline source bundle is inconsistent or unsafe to interpret."""


class RobinhoodV2ReadEndpoint(StrEnum):
    """The complete endpoint allowlist for this offline research contract.

    The enum is descriptive evidence only; there is intentionally no transport that can execute
    these requests. Order and order-history resources are outside this narrow cost study.
    """

    ACCOUNTS = "/api/v2/crypto/trading/accounts/"
    TRADING_PAIRS = "/api/v2/crypto/trading/trading_pairs/"
    BEST_BID_ASK = "/api/v2/crypto/marketdata/best_bid_ask/"
    ESTIMATED_PRICE = "/api/v2/crypto/trading/estimated_price/"

    @property
    def method(self) -> Literal["GET"]:
        return "GET"


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must use UTC")
    return value.astimezone(UTC)


def _utc_validator(value: datetime, info: object) -> datetime:
    return _require_utc(value, str(getattr(info, "field_name", "timestamp")))


def _validate_symbol(value: str) -> str:
    if value != value.upper() or not value.endswith("-USD"):
        raise ValueError("symbol must be an uppercase USD trading pair")
    asset = value.removesuffix("-USD")
    if not asset or not asset.isalnum():
        raise ValueError("symbol asset code must be alphanumeric")
    return value


class FeeTierStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fee_ratio: Decimal = Field(ge=0)
    thirty_day_volume: Decimal = Field(ge=0)
    next_fee_tier_ratio: Decimal | None = Field(default=None, ge=0)
    next_fee_tier_threshold: Decimal | None = Field(default=None, ge=0)


class AccountResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    account_number: str = Field(min_length=1, repr=False)
    status: str = Field(min_length=1)
    buying_power: Decimal | Literal["[REDACTED]"]
    buying_power_currency: str = Field(min_length=1)
    account_type: str = Field(min_length=1)
    is_api_tradable: bool
    fee_tier_status: FeeTierStatus

    @field_validator("buying_power")
    @classmethod
    def _nonnegative_buying_power(
        cls, value: Decimal | Literal["[REDACTED]"]
    ) -> Decimal | Literal["[REDACTED]"]:
        if isinstance(value, Decimal) and value < 0:
            raise ValueError("buying_power must be nonnegative")
        return value


class AccountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    next: str | None
    previous: str | None
    results: tuple[AccountResult, ...] = Field(min_length=1)


class TradingPairResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    asset_code: str = Field(min_length=1)
    quote_code: str = Field(min_length=1)
    asset_increment: Decimal = Field(gt=0)
    quote_increment: Decimal = Field(gt=0)
    max_order_size: Decimal = Field(gt=0)
    min_order_amount: Decimal = Field(gt=0)
    status: str = Field(min_length=1)
    is_api_tradable: bool

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _validate_symbol(value)


class TradingPairsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    next: str | None
    previous: str | None
    results: tuple[TradingPairResult, ...] = Field(min_length=1)


class BestBidAskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _validate_symbol(value)

    @model_validator(mode="after")
    def _ordered_market(self) -> Self:
        if self.ask < self.bid:
            raise ValueError("best ask must be greater than or equal to best bid")
        return self


class BestBidAskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    results: tuple[BestBidAskResult, ...] = Field(min_length=1)


class EstimatedPriceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    side: Literal["bid", "ask"]
    quantity: Decimal = Field(gt=0)
    timestamp: datetime
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    fee_ratio: Decimal = Field(ge=0)
    est_fee: Decimal = Field(ge=0)
    est_total_cost: Decimal = Field(ge=0)
    est_total_credit: Decimal = Field(ge=0)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _validate_symbol(value)

    @field_validator("timestamp")
    @classmethod
    def _timestamp(cls, value: datetime, info: object) -> datetime:
        return _utc_validator(value, info)

    @model_validator(mode="after")
    def _coherent_estimate(self) -> Self:
        if self.ask < self.bid:
            raise ValueError("estimated ask must be greater than or equal to estimated bid")
        if self.side == "ask" and self.est_total_cost <= 0:
            raise ValueError("ask estimate must provide a positive estimated total cost")
        if self.side == "bid" and self.est_total_credit <= 0:
            raise ValueError("bid estimate must provide a positive estimated total credit")
        return self


class EstimatedPriceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    results: tuple[EstimatedPriceResult, ...] = Field(min_length=2)


class EndpointReceiveTimes(BaseModel):
    """Local UTC receive time for every response participating in one observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accounts: datetime
    trading_pairs: datetime
    best_bid_ask: datetime
    estimated_price: datetime

    @field_validator("accounts", "trading_pairs", "best_bid_ask", "estimated_price")
    @classmethod
    def _utc_times(cls, value: datetime, info: object) -> datetime:
        return _utc_validator(value, info)

    @property
    def earliest(self) -> datetime:
        return min(self.accounts, self.trading_pairs, self.best_bid_ask, self.estimated_price)

    @property
    def latest(self) -> datetime:
        return max(self.accounts, self.trading_pairs, self.best_bid_ask, self.estimated_price)


class OfflineCostFixture(BaseModel):
    """One locally supplied set of official-response-shaped cost inputs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = SOURCE_FIXTURE_SCHEMA_VERSION
    source_kind: Literal["synthetic_fixture", "captured_read_only_v2"]
    endpoint_receive_times: EndpointReceiveTimes
    requested_symbol: str
    requested_quantity: Decimal = Field(gt=0)
    accounts_response: AccountsResponse
    trading_pairs_response: TradingPairsResponse
    best_bid_ask_response: BestBidAskResponse
    estimated_price_response: EstimatedPriceResponse

    @field_validator("requested_symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _validate_symbol(value)


class SideCostObservation(BaseModel):
    """One hypothetical direction, preserving spread, depth, fee, and total separately."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    order_side: Literal["buy", "sell"]
    requested_book_side: Literal["ask", "bid"]
    estimated_at: datetime
    asset_quantity: Decimal = Field(gt=0)
    best_touch_price_usd: Decimal = Field(gt=0)
    estimated_price_excluding_fee_usd: Decimal = Field(gt=0)
    gross_estimated_notional_usd: Decimal = Field(gt=0)
    fee_ratio: Decimal = Field(ge=0)
    fee_bps: Decimal = Field(ge=0)
    estimated_fee_usd: Decimal = Field(ge=0)
    estimated_total_usd: Decimal = Field(gt=0)
    effective_price_including_fee_usd: Decimal = Field(gt=0)
    quoted_half_spread_cost_bps: Decimal
    size_impact_from_best_touch_bps: Decimal
    all_in_one_way_cost_from_mid_bps: Decimal
    total_arithmetic_residual_usd: Decimal

    @field_validator("estimated_at")
    @classmethod
    def _estimated_at(cls, value: datetime, info: object) -> datetime:
        return _utc_validator(value, info)


class ExecutionCostObservation(BaseModel):
    """A route-specific, account-tier-aware hypothetical round-trip cost observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = OBSERVATION_SCHEMA_VERSION
    observation_id: str = Field(pattern=r"^rh-v2-cost-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}$")
    source_kind: Literal["synthetic_fixture", "captured_read_only_v2"]
    venue: Literal["robinhood_crypto"] = "robinhood_crypto"
    api_version: Literal["v2"] = "v2"
    routing_model: Literal["partner_exchanges"] = "partner_exchanges"
    measurement_type: Literal["hypothetical_estimate_not_execution"] = (
        "hypothetical_estimate_not_execution"
    )
    received_at: datetime
    symbol: str
    asset_quantity: Decimal = Field(gt=0)
    account_status: str
    account_is_api_tradable: bool
    account_buying_power_currency: str
    account_fee_ratio: Decimal = Field(ge=0)
    account_thirty_day_volume: Decimal = Field(ge=0)
    trading_pair_status: str
    trading_pair_is_api_tradable: bool
    asset_increment: Decimal = Field(gt=0)
    quote_increment: Decimal = Field(gt=0)
    maximum_order_size: Decimal = Field(gt=0)
    minimum_order_amount_usd: Decimal = Field(gt=0)
    endpoint_receive_times: EndpointReceiveTimes
    capture_span_seconds: Decimal = Field(ge=0)
    best_bid_usd: Decimal = Field(gt=0)
    best_ask_usd: Decimal = Field(gt=0)
    midpoint_usd: Decimal = Field(gt=0)
    quoted_full_spread_bps: Decimal = Field(ge=0)
    maximum_estimate_age_seconds: Decimal = Field(ge=0)
    buy: SideCostObservation
    sell: SideCostObservation
    indicative_round_trip_cost_bps: Decimal

    @field_validator("received_at")
    @classmethod
    def _received_at(cls, value: datetime, info: object) -> datetime:
        return _utc_validator(value, info)

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _validate_symbol(value)


class EvidenceFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(gt=0)

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts or len(path.parts) != 1:
            raise ValueError("evidence file path must be a safe bundle-local filename")
        return path.as_posix()


class ExecutionCostEvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = EVIDENCE_MANIFEST_SCHEMA_VERSION
    observation_id: str
    source_kind: Literal["synthetic_fixture", "captured_read_only_v2"]
    contains_execution: Literal[False] = False
    contains_account_identifier: Literal[False] = False
    contains_account_buying_power: Literal[False] = False
    network_contact_performed_by_derivation: Literal[False] = False
    observation: EvidenceFile
    sanitized_source: EvidenceFile


@dataclass(frozen=True)
class ExecutionCostEvidenceArtifacts:
    observation: ExecutionCostObservation
    manifest: ExecutionCostEvidenceManifest
    bundle_dir: Path
    observation_path: Path
    sanitized_source_path: Path
    manifest_path: Path


def _single_matching(items: tuple[object, ...], predicate: object, label: str) -> object:
    if not callable(predicate):  # defensive internal contract
        raise TypeError("predicate must be callable")
    matches = [item for item in items if predicate(item)]
    if len(matches) != 1:
        raise ExecutionCostObservationError(f"expected exactly one {label}; found {len(matches)}")
    return matches[0]


def _seconds(value: timedelta) -> Decimal:
    return Decimal(value.days * 86400 + value.seconds) + (
        Decimal(value.microseconds) / Decimal("1000000")
    )


def _canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_SENSITIVE_KEYS = frozenset(
    {
        "account_number",
        "api_key",
        "authorization",
        "buying_power",
        "private_key",
        "private_key_path",
        "x_api_key",
        "x_signature",
    }
)


def sanitize_sensitive_fields(value: object) -> object:
    """Recursively redact recognized broker authentication and account fields."""
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = str(key).lower().replace("-", "_")
            if normalized_key in _SENSITIVE_KEYS:
                sanitized[str(key)] = "[REDACTED]"
            else:
                sanitized[str(key)] = sanitize_sensitive_fields(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_sensitive_fields(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_sensitive_fields(item) for item in value]
    return value


def _derive_side(
    *,
    order_side: Literal["buy", "sell"],
    estimate: EstimatedPriceResult,
    quantity: Decimal,
    midpoint: Decimal,
    best_bid: Decimal,
    best_ask: Decimal,
) -> SideCostObservation:
    if order_side == "buy":
        estimated_price = estimate.ask
        best_touch = best_ask
        gross_notional = estimated_price * quantity
        estimated_total = estimate.est_total_cost
        effective_price = estimated_total / quantity
        quoted_half_spread_cost = (best_touch / midpoint - Decimal("1")) * _BPS
        size_impact = (estimated_price / best_touch - Decimal("1")) * _BPS
        all_in_cost = (effective_price / midpoint - Decimal("1")) * _BPS
        arithmetic_residual = estimated_total - (gross_notional + estimate.est_fee)
        requested_book_side: Literal["ask", "bid"] = "ask"
    else:
        estimated_price = estimate.bid
        best_touch = best_bid
        gross_notional = estimated_price * quantity
        estimated_total = estimate.est_total_credit
        effective_price = estimated_total / quantity
        quoted_half_spread_cost = (Decimal("1") - best_touch / midpoint) * _BPS
        size_impact = (Decimal("1") - estimated_price / best_touch) * _BPS
        all_in_cost = (Decimal("1") - effective_price / midpoint) * _BPS
        arithmetic_residual = estimated_total - (gross_notional - estimate.est_fee)
        requested_book_side = "bid"

    if abs(arithmetic_residual) > MAX_TOTAL_ARITHMETIC_RESIDUAL_USD:
        raise ExecutionCostObservationError(
            f"{order_side} estimated total arithmetic residual exceeds "
            f"{MAX_TOTAL_ARITHMETIC_RESIDUAL_USD} USD"
        )

    return SideCostObservation(
        order_side=order_side,
        requested_book_side=requested_book_side,
        estimated_at=estimate.timestamp,
        asset_quantity=quantity,
        best_touch_price_usd=best_touch,
        estimated_price_excluding_fee_usd=estimated_price,
        gross_estimated_notional_usd=gross_notional,
        fee_ratio=estimate.fee_ratio,
        fee_bps=estimate.fee_ratio * _BPS,
        estimated_fee_usd=estimate.est_fee,
        estimated_total_usd=estimated_total,
        effective_price_including_fee_usd=effective_price,
        quoted_half_spread_cost_bps=quoted_half_spread_cost,
        size_impact_from_best_touch_bps=size_impact,
        all_in_one_way_cost_from_mid_bps=all_in_cost,
        total_arithmetic_residual_usd=arithmetic_residual,
    )


def derive_execution_cost_observation(
    fixture: OfflineCostFixture,
) -> ExecutionCostObservation:
    """Validate one offline fixture and derive a content-addressed cost observation."""
    symbol = fixture.requested_symbol
    quantity = fixture.requested_quantity

    account = _single_matching(
        fixture.accounts_response.results,
        lambda item: (
            isinstance(item, AccountResult) and item.status == "active" and item.is_api_tradable
        ),
        "active API-tradable account",
    )
    pair = _single_matching(
        fixture.trading_pairs_response.results,
        lambda item: isinstance(item, TradingPairResult) and item.symbol == symbol,
        f"trading pair for {symbol}",
    )
    best = _single_matching(
        fixture.best_bid_ask_response.results,
        lambda item: isinstance(item, BestBidAskResult) and item.symbol == symbol,
        f"best bid/ask row for {symbol}",
    )
    bid_estimate = _single_matching(
        fixture.estimated_price_response.results,
        lambda item: (
            isinstance(item, EstimatedPriceResult)
            and item.symbol == symbol
            and item.side == "bid"
            and item.quantity == quantity
        ),
        f"bid estimate for {symbol} quantity {quantity}",
    )
    ask_estimate = _single_matching(
        fixture.estimated_price_response.results,
        lambda item: (
            isinstance(item, EstimatedPriceResult)
            and item.symbol == symbol
            and item.side == "ask"
            and item.quantity == quantity
        ),
        f"ask estimate for {symbol} quantity {quantity}",
    )
    assert isinstance(account, AccountResult)
    assert isinstance(pair, TradingPairResult)
    assert isinstance(best, BestBidAskResult)
    assert isinstance(bid_estimate, EstimatedPriceResult)
    assert isinstance(ask_estimate, EstimatedPriceResult)

    if pair.asset_code != symbol.removesuffix("-USD") or pair.quote_code != "USD":
        raise ExecutionCostObservationError("trading-pair asset or quote code mismatches symbol")
    if not pair.is_api_tradable:
        raise ExecutionCostObservationError("requested trading pair is not API-tradable")
    if quantity >= pair.max_order_size:
        raise ExecutionCostObservationError("requested quantity must be below maximum order size")
    if quantity % pair.asset_increment != 0:
        raise ExecutionCostObservationError("requested quantity violates asset increment")

    midpoint = (best.bid + best.ask) / Decimal("2")
    if midpoint * quantity < pair.min_order_amount:
        raise ExecutionCostObservationError("requested quantity is below minimum order amount")

    capture_span = _seconds(
        fixture.endpoint_receive_times.latest - fixture.endpoint_receive_times.earliest
    )
    if capture_span > MAX_CAPTURE_SPAN_SECONDS:
        raise ExecutionCostObservationError("endpoint capture span exceeds 60 seconds")

    fee_ratios = {
        account.fee_tier_status.fee_ratio,
        bid_estimate.fee_ratio,
        ask_estimate.fee_ratio,
    }
    if len(fee_ratios) != 1:
        raise ExecutionCostObservationError("account and estimated-price fee ratios disagree")

    ages: list[Decimal] = []
    for estimate in (bid_estimate, ask_estimate):
        age = _seconds(fixture.endpoint_receive_times.estimated_price - estimate.timestamp)
        if age < -MAX_FUTURE_SKEW_SECONDS:
            raise ExecutionCostObservationError(
                "estimated-price timestamp is too far in the future"
            )
        if age > MAX_ESTIMATE_AGE_SECONDS:
            raise ExecutionCostObservationError("estimated-price response is stale")
        ages.append(max(age, Decimal("0")))

    buy = _derive_side(
        order_side="buy",
        estimate=ask_estimate,
        quantity=quantity,
        midpoint=midpoint,
        best_bid=best.bid,
        best_ask=best.ask,
    )
    sell = _derive_side(
        order_side="sell",
        estimate=bid_estimate,
        quantity=quantity,
        midpoint=midpoint,
        best_bid=best.bid,
        best_ask=best.ask,
    )

    core: dict[str, object] = {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "source_kind": fixture.source_kind,
        "venue": "robinhood_crypto",
        "api_version": "v2",
        "routing_model": "partner_exchanges",
        "measurement_type": "hypothetical_estimate_not_execution",
        "received_at": fixture.endpoint_receive_times.latest.isoformat().replace("+00:00", "Z"),
        "symbol": symbol,
        "asset_quantity": str(quantity),
        "account_status": account.status,
        "account_is_api_tradable": account.is_api_tradable,
        "account_buying_power_currency": account.buying_power_currency,
        "account_fee_ratio": str(account.fee_tier_status.fee_ratio),
        "account_thirty_day_volume": str(account.fee_tier_status.thirty_day_volume),
        "trading_pair_status": pair.status,
        "trading_pair_is_api_tradable": pair.is_api_tradable,
        "asset_increment": str(pair.asset_increment),
        "quote_increment": str(pair.quote_increment),
        "maximum_order_size": str(pair.max_order_size),
        "minimum_order_amount_usd": str(pair.min_order_amount),
        "endpoint_receive_times": fixture.endpoint_receive_times.model_dump(mode="json"),
        "capture_span_seconds": str(capture_span),
        "best_bid_usd": str(best.bid),
        "best_ask_usd": str(best.ask),
        "midpoint_usd": str(midpoint),
        "quoted_full_spread_bps": str((best.ask - best.bid) / midpoint * _BPS),
        "maximum_estimate_age_seconds": str(max(ages)),
        "buy": buy.model_dump(mode="json"),
        "sell": sell.model_dump(mode="json"),
        "indicative_round_trip_cost_bps": str(
            buy.all_in_one_way_cost_from_mid_bps + sell.all_in_one_way_cost_from_mid_bps
        ),
    }
    fingerprint = _sha256(_canonical_json_bytes(core))[:16]
    timestamp = fixture.endpoint_receive_times.latest.strftime("%Y%m%dT%H%M%SZ")
    return ExecutionCostObservation(
        observation_id=f"rh-v2-cost-{timestamp}-{fingerprint}",
        **core,
    )


def load_offline_cost_fixture(path: str | Path) -> OfflineCostFixture:
    return OfflineCostFixture.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _evidence_file(path: str, data: bytes) -> EvidenceFile:
    return EvidenceFile(path=path, sha256=_sha256(data), bytes=len(data))


def write_execution_cost_evidence(
    output_root: str | Path,
    fixture: OfflineCostFixture,
) -> ExecutionCostEvidenceArtifacts:
    """Write a new immutable, sanitized cost-observation evidence directory."""
    observation = derive_execution_cost_observation(fixture)
    sanitized_source_value = sanitize_sensitive_fields(fixture.model_dump(mode="json"))
    observation_bytes = _pretty_json_bytes(observation.model_dump(mode="json"))
    sanitized_source_bytes = _pretty_json_bytes(sanitized_source_value)
    observation_filename = "observation.json"
    source_filename = "source.sanitized.json"
    manifest_filename = "manifest.json"
    manifest = ExecutionCostEvidenceManifest(
        observation_id=observation.observation_id,
        source_kind=observation.source_kind,
        observation=_evidence_file(observation_filename, observation_bytes),
        sanitized_source=_evidence_file(source_filename, sanitized_source_bytes),
    )
    manifest_bytes = _pretty_json_bytes(manifest.model_dump(mode="json"))

    bundle_dir = Path(output_root) / observation.observation_id
    bundle_dir.mkdir(parents=True, exist_ok=False)
    observation_path = bundle_dir / observation_filename
    sanitized_source_path = bundle_dir / source_filename
    manifest_path = bundle_dir / manifest_filename
    for path, data in (
        (observation_path, observation_bytes),
        (sanitized_source_path, sanitized_source_bytes),
        (manifest_path, manifest_bytes),
    ):
        with path.open("xb") as file:
            file.write(data)

    return ExecutionCostEvidenceArtifacts(
        observation=observation,
        manifest=manifest,
        bundle_dir=bundle_dir,
        observation_path=observation_path,
        sanitized_source_path=sanitized_source_path,
        manifest_path=manifest_path,
    )


def verify_execution_cost_evidence(
    manifest_path: str | Path,
) -> ExecutionCostEvidenceArtifacts:
    """Verify hashes, schemas, redaction, and deterministic derivation for one bundle."""
    resolved_manifest = Path(manifest_path).resolve()
    manifest = ExecutionCostEvidenceManifest.model_validate_json(
        resolved_manifest.read_text(encoding="utf-8")
    )
    bundle_dir = resolved_manifest.parent
    observation_path = bundle_dir / manifest.observation.path
    sanitized_source_path = bundle_dir / manifest.sanitized_source.path

    observation_bytes = observation_path.read_bytes()
    sanitized_source_bytes = sanitized_source_path.read_bytes()
    if len(observation_bytes) != manifest.observation.bytes:
        raise ExecutionCostObservationError("observation byte count mismatch")
    if _sha256(observation_bytes) != manifest.observation.sha256:
        raise ExecutionCostObservationError("observation SHA-256 mismatch")
    if len(sanitized_source_bytes) != manifest.sanitized_source.bytes:
        raise ExecutionCostObservationError("sanitized source byte count mismatch")
    if _sha256(sanitized_source_bytes) != manifest.sanitized_source.sha256:
        raise ExecutionCostObservationError("sanitized source SHA-256 mismatch")
    lowered_source = sanitized_source_bytes.lower()
    for forbidden in (b"x-api-key", b"x-signature", b"private_key", b"authorization"):
        if forbidden in lowered_source:
            raise ExecutionCostObservationError("sanitized source contains a forbidden key")

    observation = ExecutionCostObservation.model_validate_json(observation_bytes)
    sanitized_fixture = OfflineCostFixture.model_validate_json(sanitized_source_bytes)
    if any(
        account.account_number != "[REDACTED]"
        for account in sanitized_fixture.accounts_response.results
    ):
        raise ExecutionCostObservationError("sanitized source contains an account identifier")
    if any(
        account.buying_power != "[REDACTED]"
        for account in sanitized_fixture.accounts_response.results
    ):
        raise ExecutionCostObservationError("sanitized source contains account buying power")
    rederived = derive_execution_cost_observation(sanitized_fixture)
    if rederived != observation:
        raise ExecutionCostObservationError(
            "observation does not match sanitized source derivation"
        )
    if manifest.observation_id != observation.observation_id:
        raise ExecutionCostObservationError("manifest observation ID mismatch")

    return ExecutionCostEvidenceArtifacts(
        observation=observation,
        manifest=manifest,
        bundle_dir=bundle_dir,
        observation_path=observation_path,
        sanitized_source_path=sanitized_source_path,
        manifest_path=resolved_manifest,
    )
