import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from market.execution.robinhood.observations import (
    ExecutionCostObservationError,
    OfflineCostFixture,
    RobinhoodV2ReadEndpoint,
    derive_execution_cost_observation,
    load_offline_cost_fixture,
    sanitize_sensitive_fields,
    verify_execution_cost_evidence,
    write_execution_cost_evidence,
)

FIXTURE = Path(__file__).parent / "fixtures" / "robinhood" / "v2_cost_snapshot.json"


def _fixture_dict() -> dict[str, object]:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_read_contract_allowlists_only_four_get_cost_resources() -> None:
    endpoints = tuple(RobinhoodV2ReadEndpoint)

    assert {endpoint.name for endpoint in endpoints} == {
        "ACCOUNTS",
        "TRADING_PAIRS",
        "BEST_BID_ASK",
        "ESTIMATED_PRICE",
    }
    assert all(endpoint.method == "GET" for endpoint in endpoints)
    assert all("/orders/" not in endpoint.value for endpoint in endpoints)
    assert all("cancel" not in endpoint.value for endpoint in endpoints)


def test_synthetic_fixture_derives_exact_separated_cost_measures() -> None:
    fixture = load_offline_cost_fixture(FIXTURE)
    observation = derive_execution_cost_observation(fixture)

    assert observation.source_kind == "synthetic_fixture"
    assert observation.measurement_type == "hypothetical_estimate_not_execution"
    assert observation.routing_model == "partner_exchanges"
    assert observation.symbol == "BTC-USD"
    assert observation.asset_quantity == Decimal("0.001")
    assert observation.midpoint_usd == Decimal("63000")
    assert observation.quoted_full_spread_bps == Decimal("20") / Decimal("63000") * Decimal("10000")
    assert observation.account_fee_ratio == Decimal("0.0095")
    assert observation.buy.requested_book_side == "ask"
    assert observation.buy.estimated_price_excluding_fee_usd == Decimal("63030")
    assert observation.buy.estimated_fee_usd == Decimal("0.598785")
    assert observation.buy.effective_price_including_fee_usd == Decimal("63628.785")
    assert observation.sell.requested_book_side == "bid"
    assert observation.sell.estimated_price_excluding_fee_usd == Decimal("62970")
    assert observation.sell.estimated_fee_usd == Decimal("0.598215")
    assert observation.sell.effective_price_including_fee_usd == Decimal("62371.785")
    assert observation.buy.fee_bps == Decimal("95")
    assert observation.sell.fee_bps == Decimal("95")
    assert observation.indicative_round_trip_cost_bps == (
        observation.buy.all_in_one_way_cost_from_mid_bps
        + observation.sell.all_in_one_way_cost_from_mid_bps
    )
    assert observation.indicative_round_trip_cost_bps == Decimal("199.5238095238095238095238070")
    assert observation.maximum_estimate_age_seconds == Decimal("5")
    assert observation.capture_span_seconds == Decimal("4")


def test_evidence_bundle_redacts_account_number_and_verifies(tmp_path: Path) -> None:
    fixture = load_offline_cost_fixture(FIXTURE)
    artifacts = write_execution_cost_evidence(tmp_path, fixture)

    source_text = artifacts.sanitized_source_path.read_text(encoding="utf-8")
    assert "synthetic-account-must-be-redacted" not in source_text
    assert '"account_number": "[REDACTED]"' in source_text
    assert '"buying_power": "[REDACTED]"' in source_text
    assert artifacts.manifest.contains_execution is False
    assert artifacts.manifest.contains_account_identifier is False
    assert artifacts.manifest.contains_account_buying_power is False
    assert artifacts.manifest.network_contact_performed_by_derivation is False
    assert verify_execution_cost_evidence(artifacts.manifest_path).observation == (
        artifacts.observation
    )


def test_evidence_writer_refuses_to_overwrite_same_observation(tmp_path: Path) -> None:
    fixture = load_offline_cost_fixture(FIXTURE)
    write_execution_cost_evidence(tmp_path, fixture)

    with pytest.raises(FileExistsError):
        write_execution_cost_evidence(tmp_path, fixture)


def test_evidence_verifier_rejects_tampered_observation(tmp_path: Path) -> None:
    artifacts = write_execution_cost_evidence(tmp_path, load_offline_cost_fixture(FIXTURE))
    artifacts.observation_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ExecutionCostObservationError, match="byte count mismatch"):
        verify_execution_cost_evidence(artifacts.manifest_path)


def test_fixture_schema_rejects_unknown_fields() -> None:
    value = _fixture_dict()
    value["headers"] = {"x-api-key": "must-not-be-accepted"}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        OfflineCostFixture.model_validate(value)


def test_derivation_rejects_fee_tier_mismatch() -> None:
    value = _fixture_dict()
    estimates = value["estimated_price_response"]
    assert isinstance(estimates, dict)
    rows = estimates["results"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["fee_ratio"] = "0.0080"

    with pytest.raises(ExecutionCostObservationError, match="fee ratios disagree"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_derivation_rejects_stale_estimate() -> None:
    value = _fixture_dict()
    receive_times = value["endpoint_receive_times"]
    assert isinstance(receive_times, dict)
    receive_times["estimated_price"] = "2026-08-17T12:02:00Z"

    with pytest.raises(ExecutionCostObservationError, match="capture span exceeds"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_derivation_rejects_stale_server_estimate_within_tight_capture() -> None:
    value = _fixture_dict()
    estimates = value["estimated_price_response"]
    assert isinstance(estimates, dict)
    rows = estimates["results"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        row["timestamp"] = "2026-08-17T11:58:00Z"

    with pytest.raises(ExecutionCostObservationError, match="response is stale"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_derivation_rejects_untradable_pair() -> None:
    value = _fixture_dict()
    pairs = value["trading_pairs_response"]
    assert isinstance(pairs, dict)
    rows = pairs["results"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["is_api_tradable"] = False

    with pytest.raises(ExecutionCostObservationError, match="not API-tradable"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_derivation_rejects_inactive_account() -> None:
    value = _fixture_dict()
    accounts = value["accounts_response"]
    assert isinstance(accounts, dict)
    rows = accounts["results"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["status"] = "restricted"

    with pytest.raises(ExecutionCostObservationError, match="active API-tradable account"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_fixture_schema_rejects_crossed_best_market() -> None:
    value = _fixture_dict()
    best = value["best_bid_ask_response"]
    assert isinstance(best, dict)
    rows = best["results"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["ask"] = "62980"

    with pytest.raises(ValidationError, match="best ask must be greater"):
        OfflineCostFixture.model_validate(value)


def test_fixture_schema_rejects_crossed_estimated_market() -> None:
    value = _fixture_dict()
    estimates = value["estimated_price_response"]
    assert isinstance(estimates, dict)
    rows = estimates["results"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["ask"] = "62960"

    with pytest.raises(ValidationError, match="estimated ask must be greater"):
        OfflineCostFixture.model_validate(value)


def test_derivation_rejects_quantity_at_maximum() -> None:
    value = _fixture_dict()
    pairs = value["trading_pairs_response"]
    assert isinstance(pairs, dict)
    rows = pairs["results"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["max_order_size"] = "0.001"

    with pytest.raises(ExecutionCostObservationError, match="below maximum"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_derivation_rejects_quantity_below_quote_minimum() -> None:
    value = _fixture_dict()
    pairs = value["trading_pairs_response"]
    assert isinstance(pairs, dict)
    rows = pairs["results"]
    assert isinstance(rows, list)
    assert isinstance(rows[0], dict)
    rows[0]["min_order_amount"] = "100"

    with pytest.raises(ExecutionCostObservationError, match="below minimum"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_derivation_rejects_quantity_increment_violation() -> None:
    value = _fixture_dict()
    value["requested_quantity"] = "0.001000005"
    estimates = value["estimated_price_response"]
    assert isinstance(estimates, dict)
    rows = estimates["results"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        row["quantity"] = "0.001000005"

    with pytest.raises(ExecutionCostObservationError, match="violates asset increment"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_derivation_rejects_excessive_future_clock_skew() -> None:
    value = _fixture_dict()
    estimates = value["estimated_price_response"]
    assert isinstance(estimates, dict)
    rows = estimates["results"]
    assert isinstance(rows, list)
    for row in rows:
        assert isinstance(row, dict)
        row["timestamp"] = "2026-08-17T12:00:11Z"

    with pytest.raises(ExecutionCostObservationError, match="too far in the future"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_derivation_rejects_incoherent_estimated_total() -> None:
    value = _fixture_dict()
    estimates = value["estimated_price_response"]
    assert isinstance(estimates, dict)
    rows = estimates["results"]
    assert isinstance(rows, list)
    assert isinstance(rows[1], dict)
    rows[1]["est_total_cost"] = "70"

    with pytest.raises(ExecutionCostObservationError, match="arithmetic residual"):
        derive_execution_cost_observation(OfflineCostFixture.model_validate(value))


def test_sanitizer_redacts_supported_sensitive_key_spellings() -> None:
    value = {
        "x-api-key": "secret-1",
        "nested": {
            "x_signature": "secret-2",
            "private_key_path": "/secret/key",
            "safe": "keep-me",
        },
    }

    assert sanitize_sensitive_fields(value) == {
        "x-api-key": "[REDACTED]",
        "nested": {
            "x_signature": "[REDACTED]",
            "private_key_path": "[REDACTED]",
            "safe": "keep-me",
        },
    }
