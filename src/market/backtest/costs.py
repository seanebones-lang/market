"""Venue/routing-specific backtest cost assumptions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from market.domain.models import D

BPS_DIVISOR = Decimal("10000")
DEFAULT_LEGACY_TRANSACTION_FEE_BPS_PER_FILL_ASSUMPTION = Decimal("5")


class VenueCostProfile(str, Enum):
    """A route-specific cost contract, not a claim about an observed execution."""

    LEGACY_UNCLASSIFIED = "legacy_unclassified"
    ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER = "robinhood_crypto_api_v1_market_maker"
    ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER = "robinhood_crypto_api_v2_exchange_taker"


class CostInputClassification(str, Enum):
    LEGACY_UNCLASSIFIED = "legacy_unclassified"
    CONFIGURED_ASSUMPTION = "configured_assumption"


class TransactionFeeTreatment(str, Enum):
    LEGACY_TRANSACTION_FEE_PER_FILL_ASSUMPTION = "legacy_transaction_fee_per_fill_assumption"
    SPREAD_INCLUSIVE_NO_SEPARATE_TRANSACTION_FEE = "spread_inclusive_no_separate_transaction_fee"
    EXCHANGE_TAKER_FEE_PER_FILL_ON_EXECUTED_NOTIONAL_ASSUMPTION = (
        "exchange_taker_fee_per_fill_on_executed_notional_assumption"
    )


@dataclass(frozen=True)
class CostProfileMetadata:
    venue: str
    routing: str
    api_version: str
    input_classification: CostInputClassification
    transaction_fee_treatment: TransactionFeeTreatment


PROFILE_METADATA = {
    VenueCostProfile.LEGACY_UNCLASSIFIED: CostProfileMetadata(
        venue="unclassified",
        routing="unclassified",
        api_version="unclassified",
        input_classification=CostInputClassification.LEGACY_UNCLASSIFIED,
        transaction_fee_treatment=(
            TransactionFeeTreatment.LEGACY_TRANSACTION_FEE_PER_FILL_ASSUMPTION
        ),
    ),
    VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER: CostProfileMetadata(
        venue="robinhood_crypto",
        routing="market_maker",
        api_version="v1",
        input_classification=CostInputClassification.CONFIGURED_ASSUMPTION,
        transaction_fee_treatment=(
            TransactionFeeTreatment.SPREAD_INCLUSIVE_NO_SEPARATE_TRANSACTION_FEE
        ),
    ),
    VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER: CostProfileMetadata(
        venue="robinhood_crypto",
        routing="exchange",
        api_version="v2",
        input_classification=CostInputClassification.CONFIGURED_ASSUMPTION,
        transaction_fee_treatment=(
            TransactionFeeTreatment.EXCHANGE_TAKER_FEE_PER_FILL_ON_EXECUTED_NOTIONAL_ASSUMPTION
        ),
    ),
}


class VenueCostAssumptions(BaseModel):
    """Validated profile inputs whose rates are always labeled assumptions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: VenueCostProfile = VenueCostProfile.LEGACY_UNCLASSIFIED
    transaction_fee_bps_per_fill_assumption: Decimal | None = None

    @field_validator("transaction_fee_bps_per_fill_assumption", mode="before")
    @classmethod
    def _exact_decimal(cls, value: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, float):
            raise TypeError("float not allowed for venue cost assumptions")
        return D(value)

    @model_validator(mode="after")
    def _valid_profile_inputs(self) -> Self:
        per_fill_fee_bps = self.transaction_fee_bps_per_fill_assumption
        if per_fill_fee_bps is not None and per_fill_fee_bps < 0:
            raise ValueError("transaction_fee_bps_per_fill_assumption must be >= 0")
        if per_fill_fee_bps is not None and per_fill_fee_bps >= BPS_DIVISOR:
            raise ValueError("transaction_fee_bps_per_fill_assumption must be < 10000")
        if (
            self.profile == VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER
            and per_fill_fee_bps is not None
        ):
            raise ValueError(
                f"{self.profile.value} does not accept a "
                "transaction_fee_bps_per_fill_assumption; omit it"
            )
        if self.profile == VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER and (
            per_fill_fee_bps is None or per_fill_fee_bps <= 0
        ):
            raise ValueError(
                "robinhood_crypto_api_v2_exchange_taker requires a positive "
                "transaction_fee_bps_per_fill_assumption"
            )
        return self

    @property
    def metadata(self) -> CostProfileMetadata:
        return PROFILE_METADATA[self.profile]


@dataclass(frozen=True)
class ResolvedVenueCost:
    assumptions: VenueCostAssumptions
    transaction_fee_bps_per_fill_assumption: Decimal
    transaction_fee_bps_per_fill_applied: Decimal

    def calculate_fee_usd(
        self,
        *,
        executed_quantity: Decimal,
        fill_price_usd: Decimal,
    ) -> Decimal:
        """Apply the configured fee once to this fill's executed notional."""
        if executed_quantity <= 0:
            raise ValueError("executed_quantity must be > 0")
        if fill_price_usd <= 0:
            raise ValueError("fill_price_usd must be > 0")
        return (
            executed_quantity
            * fill_price_usd
            * (self.transaction_fee_bps_per_fill_applied / BPS_DIVISOR)
        )

    def artifact_details(self) -> dict[str, str]:
        metadata = self.assumptions.metadata
        return {
            "venue_cost_profile": self.assumptions.profile.value,
            "venue": metadata.venue,
            "routing": metadata.routing,
            "api_version": metadata.api_version,
            "cost_input_classification": metadata.input_classification.value,
            "transaction_fee_treatment": metadata.transaction_fee_treatment.value,
            "fee_calculation_basis": "executed_notional_per_fill",
            "transaction_fee_bps_per_fill_assumption": str(
                self.transaction_fee_bps_per_fill_assumption
            ),
            "transaction_fee_bps_per_fill_applied": str(self.transaction_fee_bps_per_fill_applied),
        }


def resolve_venue_cost(
    assumptions: VenueCostAssumptions,
    *,
    execution_model: str,
    quoted_spread_bps_assumption: Decimal,
) -> ResolvedVenueCost:
    """Resolve one cost profile and reject incompatible execution inputs."""
    if assumptions.profile == VenueCostProfile.LEGACY_UNCLASSIFIED:
        fee_bps_per_fill = (
            DEFAULT_LEGACY_TRANSACTION_FEE_BPS_PER_FILL_ASSUMPTION
            if assumptions.transaction_fee_bps_per_fill_assumption is None
            else assumptions.transaction_fee_bps_per_fill_assumption
        )
        return ResolvedVenueCost(
            assumptions=assumptions,
            transaction_fee_bps_per_fill_assumption=fee_bps_per_fill,
            transaction_fee_bps_per_fill_applied=fee_bps_per_fill,
        )

    if execution_model != "next_bar_open_bid_ask":
        raise ValueError(
            f"{assumptions.profile.value} requires execution_model=next_bar_open_bid_ask"
        )
    if quoted_spread_bps_assumption <= 0:
        raise ValueError(
            f"{assumptions.profile.value} requires a positive quoted_spread_bps_assumption"
        )

    resolved_fee_bps_per_fill: Decimal
    if assumptions.profile == VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER:
        resolved_fee_bps_per_fill = Decimal("0")
    else:
        assert assumptions.transaction_fee_bps_per_fill_assumption is not None
        resolved_fee_bps_per_fill = assumptions.transaction_fee_bps_per_fill_assumption
    return ResolvedVenueCost(
        assumptions=assumptions,
        transaction_fee_bps_per_fill_assumption=resolved_fee_bps_per_fill,
        transaction_fee_bps_per_fill_applied=resolved_fee_bps_per_fill,
    )
