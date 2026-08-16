from decimal import Decimal

import pytest
from pydantic import ValidationError

from market.backtest.costs import (
    CostInputClassification,
    TransactionFeeTreatment,
    VenueCostAssumptions,
    VenueCostProfile,
    resolve_venue_cost,
)


def test_robinhood_profiles_have_distinct_route_metadata():
    v1 = VenueCostAssumptions(profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER)
    v2 = VenueCostAssumptions(
        profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
        transaction_fee_bps_assumption=Decimal("95"),
    )

    assert v1.metadata.venue == v2.metadata.venue == "robinhood_crypto"
    assert v1.metadata.routing == "market_maker"
    assert v1.metadata.api_version == "v1"
    assert v1.metadata.transaction_fee_treatment == (
        TransactionFeeTreatment.SPREAD_INCLUSIVE_NO_SEPARATE_TRANSACTION_FEE
    )
    assert v2.metadata.routing == "exchange"
    assert v2.metadata.api_version == "v2"
    assert v2.metadata.transaction_fee_treatment == (
        TransactionFeeTreatment.EXCHANGE_TAKER_FEE_ON_EXECUTED_NOTIONAL_ASSUMPTION
    )
    assert v2.metadata.input_classification == CostInputClassification.CONFIGURED_ASSUMPTION


@pytest.mark.parametrize(
    ("legacy_fee_bps", "expected"),
    [(None, Decimal("5")), (Decimal("0"), Decimal("0"))],
)
def test_legacy_profile_preserves_existing_fee_behavior(
    legacy_fee_bps: Decimal | None, expected: Decimal
):
    resolved = resolve_venue_cost(
        VenueCostAssumptions(),
        execution_model="next_bar_open",
        quoted_spread_bps_assumption=Decimal("0"),
        legacy_fee_bps=legacy_fee_bps,
    )

    assert resolved.transaction_fee_bps_applied == expected
    assert resolved.artifact_details()["cost_input_classification"] == ("legacy_unclassified")


def test_robinhood_v1_cost_is_spread_inclusive_without_separate_fee():
    resolved = resolve_venue_cost(
        VenueCostAssumptions(profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER),
        execution_model="next_bar_open_bid_ask",
        quoted_spread_bps_assumption=Decimal("192"),
        legacy_fee_bps=None,
    )

    assert resolved.transaction_fee_bps_applied == 0
    assert resolved.artifact_details()["transaction_fee_treatment"] == (
        "spread_inclusive_no_separate_transaction_fee"
    )


def test_robinhood_v2_cost_applies_configured_taker_fee_assumption():
    resolved = resolve_venue_cost(
        VenueCostAssumptions(
            profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
            transaction_fee_bps_assumption=Decimal("95"),
        ),
        execution_model="next_bar_open_bid_ask",
        quoted_spread_bps_assumption=Decimal("20"),
        legacy_fee_bps=None,
    )

    assert resolved.transaction_fee_bps_applied == Decimal("95")
    assert resolved.artifact_details()["transaction_fee_bps_assumption"] == "95"
    assert resolved.artifact_details()["cost_input_classification"] == ("configured_assumption")


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"transaction_fee_bps_assumption": Decimal("-1")}, "must be >= 0"),
        ({"transaction_fee_bps_assumption": Decimal("10000")}, "must be < 10000"),
        ({"transaction_fee_bps_assumption": Decimal("NaN")}, "finite number"),
        ({"transaction_fee_bps_assumption": Decimal("Infinity")}, "finite number"),
        (
            {
                "profile": VenueCostProfile.LEGACY_UNCLASSIFIED,
                "transaction_fee_bps_assumption": Decimal("1"),
            },
            "requires transaction_fee_bps_assumption=0",
        ),
        (
            {
                "profile": VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER,
                "transaction_fee_bps_assumption": Decimal("1"),
            },
            "requires transaction_fee_bps_assumption=0",
        ),
        (
            {
                "profile": VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
                "transaction_fee_bps_assumption": Decimal("0"),
            },
            "requires a positive transaction_fee_bps_assumption",
        ),
    ],
)
def test_venue_cost_assumptions_reject_invalid_values(values: dict[str, object], message: str):
    with pytest.raises(ValidationError, match=message):
        VenueCostAssumptions.model_validate(values)


def test_venue_cost_assumptions_reject_float():
    with pytest.raises((TypeError, ValidationError), match="float not allowed"):
        VenueCostAssumptions(
            profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
            transaction_fee_bps_assumption=95.0,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("execution_model", "spread_bps", "message"),
    [
        ("next_bar_open", Decimal("20"), "requires execution_model"),
        (
            "next_bar_open_bid_ask",
            Decimal("0"),
            "requires a positive quoted_spread_bps_assumption",
        ),
    ],
)
def test_robinhood_profiles_require_bid_ask_execution_with_positive_spread(
    execution_model: str, spread_bps: Decimal, message: str
):
    assumptions = VenueCostAssumptions(
        profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER
    )
    with pytest.raises(ValueError, match=message):
        resolve_venue_cost(
            assumptions,
            execution_model=execution_model,
            quoted_spread_bps_assumption=spread_bps,
            legacy_fee_bps=None,
        )


def test_robinhood_profiles_reject_legacy_fee_input():
    assumptions = VenueCostAssumptions(
        profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER
    )
    with pytest.raises(ValueError, match="cannot be combined"):
        resolve_venue_cost(
            assumptions,
            execution_model="next_bar_open_bid_ask",
            quoted_spread_bps_assumption=Decimal("20"),
            legacy_fee_bps=Decimal("5"),
        )


@pytest.mark.parametrize(
    ("legacy_fee_bps", "message"),
    [
        (Decimal("NaN"), "must be finite"),
        (Decimal("-1"), "must be >= 0"),
        (Decimal("10000"), "must be < 10000"),
    ],
)
def test_legacy_fee_input_is_validated(legacy_fee_bps: Decimal, message: str):
    with pytest.raises(ValueError, match=message):
        resolve_venue_cost(
            VenueCostAssumptions(),
            execution_model="next_bar_open",
            quoted_spread_bps_assumption=Decimal("0"),
            legacy_fee_bps=legacy_fee_bps,
        )
