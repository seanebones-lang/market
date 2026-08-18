import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from market.execution.robinhood.observations import (
    AccountsResponse,
    BestBidAskResponse,
    EstimatedPriceResponse,
    EstimatedPriceResult,
    RobinhoodV2ReadEndpoint,
    TradingPairsResponse,
    load_offline_cost_fixture,
    verify_execution_cost_evidence,
)
from market.execution.robinhood.read_client import (
    CapturedRead,
    RobinhoodReadAuthorizationError,
)
from market.research.cost_collector import (
    CAPTURE_AUTHORIZATION_ID,
    CostCaptureError,
    capture_due_scheduled_cycle,
    capture_readonly_cost_cycle,
    due_sampling_slot,
    verify_control_record_contains_no_sensitive_keys,
)
from market.research.cost_sampling import (
    LoadedCostSamplingProtocol,
    LoadedCostSamplingRunPlan,
    build_cost_sampling_run_plan,
    load_cost_sampling_protocol,
    load_cost_sampling_run_plan,
    write_cost_sampling_run_plan,
)

PROTOCOL = Path("config/research/rh-v2-cost-sampling-v1.json")
FIXTURE = Path(__file__).parent / "fixtures" / "robinhood" / "v2_cost_snapshot.json"
BASE_TIME = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _multi_estimates(
    quantities: tuple[Decimal, ...],
    *,
    estimated_at: datetime,
) -> EstimatedPriceResponse:
    fee_ratio = Decimal("0.0095")
    bid = Decimal("62970")
    ask = Decimal("63030")
    rows: list[EstimatedPriceResult] = []
    for quantity in quantities:
        bid_fee = bid * quantity * fee_ratio
        ask_fee = ask * quantity * fee_ratio
        rows.extend(
            (
                EstimatedPriceResult(
                    symbol="BTC-USD",
                    side="bid",
                    quantity=quantity,
                    timestamp=estimated_at,
                    bid=bid,
                    ask=ask,
                    fee_ratio=fee_ratio,
                    est_fee=bid_fee,
                    est_total_cost=Decimal("0"),
                    est_total_credit=bid * quantity - bid_fee,
                ),
                EstimatedPriceResult(
                    symbol="BTC-USD",
                    side="ask",
                    quantity=quantity,
                    timestamp=estimated_at,
                    bid=bid,
                    ask=ask,
                    fee_ratio=fee_ratio,
                    est_fee=ask_fee,
                    est_total_cost=ask * quantity + ask_fee,
                    est_total_credit=Decimal("0"),
                ),
            )
        )
    return EstimatedPriceResponse(results=tuple(rows))


class FakeReadClient:
    def __init__(
        self,
        loaded_protocol: LoadedCostSamplingProtocol,
        *,
        base_time: datetime = BASE_TIME,
        fail_at: str | None = None,
        estimates: EstimatedPriceResponse | None = None,
    ) -> None:
        fixture = load_offline_cost_fixture(FIXTURE)
        self.protocol = loaded_protocol.protocol
        self.base_time = base_time
        self.fail_at = fail_at
        self.calls: list[str] = []
        self.accounts = fixture.accounts_response
        self.pairs = fixture.trading_pairs_response
        self.best = fixture.best_bid_ask_response
        self.estimates = estimates or _multi_estimates(
            self.protocol.asset_quantities,
            estimated_at=base_time,
        )

    def _fail(self, label: str) -> None:
        self.calls.append(label)
        if self.fail_at == label:
            raise RobinhoodReadAuthorizationError("synthetic_safe_failure")

    def get_accounts(self) -> CapturedRead[AccountsResponse]:
        self._fail("accounts")
        return CapturedRead(
            endpoint=RobinhoodV2ReadEndpoint.ACCOUNTS,
            request_path=RobinhoodV2ReadEndpoint.ACCOUNTS.value,
            received_at=self.base_time + timedelta(seconds=1),
            response=self.accounts,
        )

    def get_trading_pair(self, symbol: str) -> CapturedRead[TradingPairsResponse]:
        self._fail("pairs")
        assert symbol == self.protocol.symbol
        return CapturedRead(
            endpoint=RobinhoodV2ReadEndpoint.TRADING_PAIRS,
            request_path=RobinhoodV2ReadEndpoint.TRADING_PAIRS.value,
            received_at=self.base_time + timedelta(seconds=2),
            response=self.pairs,
        )

    def get_best_bid_ask(self, symbol: str) -> CapturedRead[BestBidAskResponse]:
        self._fail("best")
        assert symbol == self.protocol.symbol
        return CapturedRead(
            endpoint=RobinhoodV2ReadEndpoint.BEST_BID_ASK,
            request_path=RobinhoodV2ReadEndpoint.BEST_BID_ASK.value,
            received_at=self.base_time + timedelta(seconds=3),
            response=self.best,
        )

    def get_estimated_price(
        self,
        symbol: str,
        quantities: tuple[Decimal, ...],
    ) -> CapturedRead[EstimatedPriceResponse]:
        self._fail("estimates")
        assert symbol == self.protocol.symbol
        assert quantities == self.protocol.asset_quantities
        return CapturedRead(
            endpoint=RobinhoodV2ReadEndpoint.ESTIMATED_PRICE,
            request_path=RobinhoodV2ReadEndpoint.ESTIMATED_PRICE.value,
            received_at=self.base_time + timedelta(seconds=4),
            response=self.estimates,
        )


def _protocol() -> LoadedCostSamplingProtocol:
    return load_cost_sampling_protocol(PROTOCOL)


def _plan(
    tmp_path: Path,
    protocol: LoadedCostSamplingProtocol,
    *,
    authorization: str = CAPTURE_AUTHORIZATION_ID,
) -> LoadedCostSamplingRunPlan:
    plan = build_cost_sampling_run_plan(
        protocol,
        scheduled_start=datetime(2026, 8, 18, 0, 7, tzinfo=UTC),
        frozen_at=datetime(2026, 8, 17, 23, 50, tzinfo=UTC),
        authorization_reference=authorization,
        run_id="g3-cost-test",
    )
    path = tmp_path / f"plan-{authorization}.json"
    write_cost_sampling_run_plan(path, plan)
    return load_cost_sampling_run_plan(path, protocol)


def test_capture_one_shared_cycle_writes_four_sanitized_verified_bundles(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    client = FakeReadClient(protocol)

    cycle = capture_readonly_cost_cycle(client, protocol, tmp_path)

    assert client.calls == ["accounts", "pairs", "best", "estimates"]
    assert cycle.quantities == protocol.protocol.asset_quantities
    assert len(cycle.observation_ids) == 4
    assert len(set(cycle.observation_ids)) == 4
    assert cycle.network_contact_performed is True
    assert cycle.execution_performed is False
    assert cycle.account_identifier_persisted is False
    assert cycle.credential_persisted is False
    assert len(cycle.manifest_paths) == 4
    for quantity, manifest in zip(cycle.quantities, cycle.manifest_paths, strict=True):
        verified = verify_execution_cost_evidence(manifest)
        assert verified.observation.source_kind == "captured_read_only_v2"
        assert verified.observation.asset_quantity == quantity
        source = verified.sanitized_source_path.read_text(encoding="utf-8")
        assert "synthetic-account-must-be-redacted" not in source
        assert '"account_number": "[REDACTED]"' in source
        assert '"buying_power": "[REDACTED]"' in source
        assert "x-api-key" not in source.lower()
        assert "x-signature" not in source.lower()


def test_incomplete_multi_quantity_response_fails_before_any_bundle_write(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    incomplete = _multi_estimates(
        cast(tuple[Decimal, ...], protocol.protocol.asset_quantities[:-1]),
        estimated_at=BASE_TIME,
    )
    client = FakeReadClient(protocol, estimates=incomplete)

    with pytest.raises(CostCaptureError, match="frozen_quantities"):
        capture_readonly_cost_cycle(client, protocol, tmp_path)

    assert list(tmp_path.glob("*/manifest.json")) == []


def test_due_slot_is_inclusive_at_slot_and_lateness_boundary(tmp_path: Path) -> None:
    protocol = _protocol()
    plan = _plan(tmp_path, protocol)
    slot = plan.plan.scheduled_start

    assert due_sampling_slot(protocol, plan, now=slot) == slot
    assert due_sampling_slot(protocol, plan, now=slot + timedelta(seconds=60)) == slot
    with pytest.raises(CostCaptureError, match="no_single_sampling_slot"):
        due_sampling_slot(protocol, plan, now=slot - timedelta(microseconds=1))
    with pytest.raises(CostCaptureError, match="no_single_sampling_slot"):
        due_sampling_slot(protocol, plan, now=slot + timedelta(seconds=61))


def test_scheduled_capture_claims_before_network_and_refuses_same_slot_retry(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    plan = _plan(tmp_path, protocol)
    slot = plan.plan.scheduled_start
    client = FakeReadClient(protocol, base_time=slot, fail_at="best")
    output = tmp_path / "observations"

    with pytest.raises(RobinhoodReadAuthorizationError):
        capture_due_scheduled_cycle(client, protocol, plan, output, now=slot)

    attempt_dir = output / ".collector" / plan.plan.run_id / slot.strftime("%Y%m%dT%H%M%SZ")
    started = attempt_dir / "started.json"
    failed = attempt_dir / "failed.json"
    assert started.exists()
    assert failed.exists()
    assert not (attempt_dir / "completed.json").exists()
    assert json.loads(failed.read_text(encoding="utf-8"))["failure_code"] == (
        "authorization_rejected"
    )
    verify_control_record_contains_no_sensitive_keys(started)
    verify_control_record_contains_no_sensitive_keys(failed)

    retry_client = FakeReadClient(protocol, base_time=slot)
    with pytest.raises(CostCaptureError, match="already_claimed"):
        capture_due_scheduled_cycle(retry_client, protocol, plan, output, now=slot)
    assert retry_client.calls == []


def test_successful_scheduled_capture_writes_sanitized_completion_control(
    tmp_path: Path,
) -> None:
    protocol = _protocol()
    plan = _plan(tmp_path, protocol)
    slot = plan.plan.scheduled_start
    client = FakeReadClient(protocol, base_time=slot)

    captured = capture_due_scheduled_cycle(
        client,
        protocol,
        plan,
        tmp_path / "observations",
        now=slot,
    )

    assert captured.scheduled_slot == slot
    assert captured.completed_path.exists()
    completion_text = captured.completed_path.read_text(encoding="utf-8")
    assert "account_number" not in completion_text
    assert "buying_power" not in completion_text
    assert "api_key" not in completion_text
    assert "private_key" not in completion_text
    assert '"execution_performed": false' in completion_text
    assert '"credential_persisted": false' in completion_text
    verify_control_record_contains_no_sensitive_keys(captured.started_path)
    verify_control_record_contains_no_sensitive_keys(captured.completed_path)


def test_unapproved_run_plan_authorization_stops_before_network(tmp_path: Path) -> None:
    protocol = _protocol()
    plan = _plan(tmp_path, protocol, authorization="unapproved-reference")
    client = FakeReadClient(protocol, base_time=plan.plan.scheduled_start)

    with pytest.raises(CostCaptureError, match="authorization_reference_not_approved"):
        capture_due_scheduled_cycle(
            client,
            protocol,
            plan,
            tmp_path / "observations",
            now=plan.plan.scheduled_start,
        )
    assert client.calls == []


def test_nonproduction_or_wrong_protocol_cannot_reach_client(tmp_path: Path) -> None:
    loaded = _protocol()
    changed_model = loaded.protocol.model_copy(update={"protocol_id": "other-protocol"})
    changed = LoadedCostSamplingProtocol(
        protocol=changed_model,
        path=loaded.path,
        sha256=loaded.sha256,
    )
    client = FakeReadClient(changed)

    with pytest.raises(CostCaptureError, match="not_allowlisted"):
        capture_readonly_cost_cycle(client, changed, tmp_path)
    assert client.calls == []
