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
        transaction_fee_bps_per_fill_assumption=Decimal("95"),
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
        TransactionFeeTreatment.EXCHANGE_TAKER_FEE_PER_FILL_ON_EXECUTED_NOTIONAL_ASSUMPTION
    )
    assert v2.metadata.input_classification == (CostInputClassification.CONFIGURED_ASSUMPTION)


@pytest.mark.parametrize(
    ("per_fill_assumption", "expected"),
    [(None, Decimal("5")), (Decimal("0"), Decimal("0"))],
)
def test_legacy_profile_has_explicit_per_fill_semantics(
    per_fill_assumption: Decimal | None,
    expected: Decimal,
):
    resolved = resolve_venue_cost(
        VenueCostAssumptions(transaction_fee_bps_per_fill_assumption=per_fill_assumption),
        execution_model="next_bar_open",
        quoted_spread_bps_assumption=Decimal("0"),
    )

    assert resolved.transaction_fee_bps_per_fill_assumption == expected
    assert resolved.transaction_fee_bps_per_fill_applied == expected
    details = resolved.artifact_details()
    assert details["fee_calculation_basis"] == "executed_notional_per_fill"
    assert details["transaction_fee_treatment"] == ("legacy_transaction_fee_per_fill_assumption")
    assert "fee_bps" not in details
    assert "transaction_fee_bps_assumption" not in details


def test_robinhood_v1_cost_is_spread_inclusive_without_separate_fee():
    resolved = resolve_venue_cost(
        VenueCostAssumptions(profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER),
        execution_model="next_bar_open_bid_ask",
        quoted_spread_bps_assumption=Decimal("192"),
    )

    assert resolved.transaction_fee_bps_per_fill_applied == 0
    assert resolved.artifact_details()["transaction_fee_treatment"] == (
        "spread_inclusive_no_separate_transaction_fee"
    )


def test_robinhood_v2_cost_applies_configured_taker_fee_per_fill():
    resolved = resolve_venue_cost(
        VenueCostAssumptions(
            profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
            transaction_fee_bps_per_fill_assumption=Decimal("95"),
        ),
        execution_model="next_bar_open_bid_ask",
        quoted_spread_bps_assumption=Decimal("20"),
    )

    assert resolved.transaction_fee_bps_per_fill_applied == Decimal("95")
    assert resolved.artifact_details()["transaction_fee_bps_per_fill_assumption"] == "95"
    assert resolved.artifact_details()["cost_input_classification"] == ("configured_assumption")


def test_equal_buy_and_sell_notional_each_pay_the_full_per_fill_rate():
    resolved = resolve_venue_cost(
        VenueCostAssumptions(
            profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
            transaction_fee_bps_per_fill_assumption=Decimal("95"),
        ),
        execution_model="next_bar_open_bid_ask",
        quoted_spread_bps_assumption=Decimal("20"),
    )

    buy_fee = resolved.calculate_fee_usd(
        executed_quantity=Decimal("1"),
        fill_price_usd=Decimal("100"),
    )
    sell_fee = resolved.calculate_fee_usd(
        executed_quantity=Decimal("1"),
        fill_price_usd=Decimal("100"),
    )

    assert buy_fee == Decimal("0.95")
    assert sell_fee == Decimal("0.95")
    assert buy_fee + sell_fee == Decimal("1.90")


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (
            {"transaction_fee_bps_per_fill_assumption": Decimal("-1")},
            "must be >= 0",
        ),
        (
            {"transaction_fee_bps_per_fill_assumption": Decimal("10000")},
            "must be < 10000",
        ),
        (
            {"transaction_fee_bps_per_fill_assumption": Decimal("NaN")},
            "finite number",
        ),
        (
            {"transaction_fee_bps_per_fill_assumption": Decimal("Infinity")},
            "finite number",
        ),
        (
            {
                "profile": VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER,
                "transaction_fee_bps_per_fill_assumption": Decimal("0"),
            },
            "does not accept",
        ),
        (
            {"profile": VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER},
            "requires a positive transaction_fee_bps_per_fill_assumption",
        ),
        (
            {
                "profile": VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
                "transaction_fee_bps_per_fill_assumption": Decimal("0"),
            },
            "requires a positive transaction_fee_bps_per_fill_assumption",
        ),
    ],
)
def test_venue_cost_assumptions_reject_invalid_values(
    values: dict[str, object],
    message: str,
):
    with pytest.raises(ValidationError, match=message):
        VenueCostAssumptions.model_validate(values)


def test_venue_cost_assumptions_reject_float():
    with pytest.raises((TypeError, ValidationError), match="float not allowed"):
        VenueCostAssumptions(
            profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
            transaction_fee_bps_per_fill_assumption=95.0,  # type: ignore[arg-type]
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
    execution_model: str,
    spread_bps: Decimal,
    message: str,
):
    assumptions = VenueCostAssumptions(
        profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V1_MARKET_MAKER
    )
    with pytest.raises(ValueError, match=message):
        resolve_venue_cost(
            assumptions,
            execution_model=execution_model,
            quoted_spread_bps_assumption=spread_bps,
        )


@pytest.mark.parametrize(
    ("quantity", "price", "message"),
    [
        (Decimal("0"), Decimal("100"), "executed_quantity must be > 0"),
        (Decimal("1"), Decimal("0"), "fill_price_usd must be > 0"),
    ],
)
def test_fee_calculation_rejects_nonpositive_fill_inputs(
    quantity: Decimal,
    price: Decimal,
    message: str,
):
    resolved = resolve_venue_cost(
        VenueCostAssumptions(),
        execution_model="next_bar_open",
        quoted_spread_bps_assumption=Decimal("0"),
    )
    with pytest.raises(ValueError, match=message):
        resolved.calculate_fee_usd(
            executed_quantity=quantity,
            fill_price_usd=price,
        )
