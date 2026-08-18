"""Authorized read-only capture path for the prospective G3.2d cost study.

The collector reuses the frozen protocol and G3.2c evidence writer. It performs one four-resource
GET cycle, derives every prespecified quantity from the same market snapshot, and persists only
sanitized evidence. Scheduled attempts are claimed before network contact and are never retried or
backfilled after a partial failure.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from market.backtest.reproducibility import pretty_json_bytes
from market.execution.robinhood.observations import (
    AccountsResponse,
    BestBidAskResponse,
    EndpointReceiveTimes,
    EstimatedPriceResponse,
    ExecutionCostEvidenceArtifacts,
    ExecutionCostObservationError,
    OfflineCostFixture,
    TradingPairsResponse,
    derive_execution_cost_observation,
    write_execution_cost_evidence,
)
from market.execution.robinhood.read_client import (
    CapturedRead,
    RobinhoodReadAuthorizationError,
    RobinhoodReadError,
    RobinhoodReadRateLimitError,
    RobinhoodReadSchemaError,
    RobinhoodReadTransportError,
)
from market.research.cost_sampling import (
    CostSamplingError,
    LoadedCostSamplingProtocol,
    LoadedCostSamplingRunPlan,
    expected_sampling_slots,
)

CAPTURE_AUTHORIZATION_ID = "AUTH-G3.2E-RH-READONLY-2026-08-17"


class CostCaptureError(RuntimeError):
    """A capture was unsafe, outside schedule, already claimed, or structurally invalid."""


class ReadOnlyCostClient(Protocol):
    def get_accounts(self) -> CapturedRead[AccountsResponse]: ...

    def get_trading_pair(self, symbol: str) -> CapturedRead[TradingPairsResponse]: ...

    def get_best_bid_ask(self, symbol: str) -> CapturedRead[BestBidAskResponse]: ...

    def get_estimated_price(
        self,
        symbol: str,
        quantities: Sequence[Decimal],
    ) -> CapturedRead[EstimatedPriceResponse]: ...


@dataclass(frozen=True)
class CapturedCostCycle:
    symbol: str
    quantities: tuple[Decimal, ...]
    received_at: datetime
    observation_ids: tuple[str, ...]
    manifest_paths: tuple[Path, ...]
    network_contact_performed: bool = True
    execution_performed: bool = False
    account_identifier_persisted: bool = False
    credential_persisted: bool = False


@dataclass(frozen=True)
class ScheduledCaptureResult:
    scheduled_slot: datetime
    attempt_dir: Path
    started_path: Path
    completed_path: Path
    cycle: CapturedCostCycle


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CostCaptureError(f"{field_name}_must_be_timezone_aware")
    if value.utcoffset() != timedelta(0):
        raise CostCaptureError(f"{field_name}_must_be_utc")
    return value.astimezone(UTC)


def _write_new_json(path: Path, value: object) -> None:
    data = pretty_json_bytes(value)
    with path.open("xb") as file:
        file.write(data)


def _assert_complete_response_shape(
    *,
    accounts: AccountsResponse,
    pairs: TradingPairsResponse,
    best: BestBidAskResponse,
    estimates: EstimatedPriceResponse,
    symbol: str,
    quantities: tuple[Decimal, ...],
) -> None:
    if accounts.next is not None or accounts.previous is not None:
        raise CostCaptureError("account_pagination_not_admitted")
    if pairs.next is not None or pairs.previous is not None:
        raise CostCaptureError("trading_pair_pagination_not_admitted")
    active_accounts = [
        item for item in accounts.results if item.status == "active" and item.is_api_tradable
    ]
    if len(active_accounts) != 1:
        raise CostCaptureError("expected_one_active_api_tradable_account")
    matching_pairs = [item for item in pairs.results if item.symbol == symbol]
    if len(matching_pairs) != 1:
        raise CostCaptureError("expected_one_matching_trading_pair")
    matching_best = [item for item in best.results if item.symbol == symbol]
    if len(matching_best) != 1 or len(best.results) != 1:
        raise CostCaptureError("expected_one_matching_best_price")
    expected_estimates = {(side, quantity) for quantity in quantities for side in ("bid", "ask")}
    observed_estimates = {
        (item.side, item.quantity) for item in estimates.results if item.symbol == symbol
    }
    if observed_estimates != expected_estimates or len(estimates.results) != len(
        expected_estimates
    ):
        raise CostCaptureError("estimated_price_rows_do_not_match_frozen_quantities")


def capture_readonly_cost_cycle(
    client: ReadOnlyCostClient,
    loaded_protocol: LoadedCostSamplingProtocol,
    output_root: str | Path,
) -> CapturedCostCycle:
    """Capture one shared four-resource snapshot and write one bundle per frozen quantity."""
    protocol = loaded_protocol.protocol
    if protocol.required_source_kind != "captured_read_only_v2":
        raise CostCaptureError("protocol_does_not_admit_captured_readonly_source")
    if protocol.protocol_id != "rh-v2-cost-sampling-v1":
        raise CostCaptureError("collector_protocol_not_allowlisted")

    accounts = client.get_accounts()
    pairs = client.get_trading_pair(protocol.symbol)
    best = client.get_best_bid_ask(protocol.symbol)
    estimates = client.get_estimated_price(protocol.symbol, protocol.asset_quantities)
    _assert_complete_response_shape(
        accounts=accounts.response,
        pairs=pairs.response,
        best=best.response,
        estimates=estimates.response,
        symbol=protocol.symbol,
        quantities=protocol.asset_quantities,
    )

    receive_times = EndpointReceiveTimes(
        accounts=accounts.received_at,
        trading_pairs=pairs.received_at,
        best_bid_ask=best.received_at,
        estimated_price=estimates.received_at,
    )
    try:
        fixtures = tuple(
            OfflineCostFixture(
                source_kind="captured_read_only_v2",
                endpoint_receive_times=receive_times,
                requested_symbol=protocol.symbol,
                requested_quantity=quantity,
                accounts_response=accounts.response,
                trading_pairs_response=pairs.response,
                best_bid_ask_response=best.response,
                estimated_price_response=estimates.response,
            )
            for quantity in protocol.asset_quantities
        )
    except ValidationError:
        raise CostCaptureError("captured_fixture_schema_rejected") from None

    # Validate every quantity before the first persistent write. A process crash can still produce
    # an explicitly partial cycle; ordinary schema/contract failures cannot.
    for fixture in fixtures:
        derive_execution_cost_observation(fixture)

    artifacts: list[ExecutionCostEvidenceArtifacts] = []
    for fixture in fixtures:
        artifacts.append(write_execution_cost_evidence(output_root, fixture))
    return CapturedCostCycle(
        symbol=protocol.symbol,
        quantities=protocol.asset_quantities,
        received_at=receive_times.latest,
        observation_ids=tuple(item.observation.observation_id for item in artifacts),
        manifest_paths=tuple(item.manifest_path for item in artifacts),
    )


def due_sampling_slot(
    loaded_protocol: LoadedCostSamplingProtocol,
    loaded_plan: LoadedCostSamplingRunPlan,
    *,
    now: datetime,
) -> datetime:
    """Return the one currently due slot; never select an early, expired, or future slot."""
    current = _require_utc(now, "now")
    plan = loaded_plan.plan
    if plan.authorization_reference != CAPTURE_AUTHORIZATION_ID:
        raise CostCaptureError("run_plan_authorization_reference_not_approved")
    candidates = [
        slot
        for slot in expected_sampling_slots(loaded_protocol.protocol, loaded_plan.plan)
        if slot
        <= current
        <= slot + timedelta(seconds=loaded_protocol.protocol.maximum_schedule_lateness_seconds)
    ]
    if len(candidates) != 1:
        raise CostCaptureError("no_single_sampling_slot_currently_due")
    return candidates[0]


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, RobinhoodReadAuthorizationError):
        return "authorization_rejected"
    if isinstance(exc, RobinhoodReadRateLimitError):
        return "rate_limit_exhausted"
    if isinstance(exc, RobinhoodReadSchemaError):
        return "response_schema_rejected"
    if isinstance(exc, RobinhoodReadTransportError):
        return "transport_attempts_exhausted"
    if isinstance(exc, RobinhoodReadError):
        return "read_client_rejected"
    if isinstance(exc, ExecutionCostObservationError):
        return "observation_contract_rejected"
    if isinstance(exc, (CostCaptureError, CostSamplingError)):
        return "collector_contract_rejected"
    return "unexpected_capture_failure"


def capture_due_scheduled_cycle(
    client: ReadOnlyCostClient,
    loaded_protocol: LoadedCostSamplingProtocol,
    loaded_plan: LoadedCostSamplingRunPlan,
    output_root: str | Path,
    *,
    now: datetime,
) -> ScheduledCaptureResult:
    """Claim and attempt exactly one due slot, retaining sanitized failure evidence."""
    current = _require_utc(now, "now")
    slot = due_sampling_slot(loaded_protocol, loaded_plan, now=current)
    root = Path(output_root)
    slot_name = slot.strftime("%Y%m%dT%H%M%SZ")
    attempt_dir = root / ".collector" / loaded_plan.plan.run_id / slot_name
    try:
        attempt_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise CostCaptureError("scheduled_slot_already_claimed") from exc

    started_path = attempt_dir / "started.json"
    _write_new_json(
        started_path,
        {
            "schema_version": 1,
            "run_id": loaded_plan.plan.run_id,
            "scheduled_slot": slot,
            "attempted_at": current,
            "authorization_reference": loaded_plan.plan.authorization_reference,
            "network_contact_planned": True,
            "execution_planned": False,
        },
    )
    completed_path = attempt_dir / "completed.json"
    try:
        cycle = capture_readonly_cost_cycle(client, loaded_protocol, root)
    except Exception as exc:
        _write_new_json(
            attempt_dir / "failed.json",
            {
                "schema_version": 1,
                "run_id": loaded_plan.plan.run_id,
                "scheduled_slot": slot,
                "failure_code": _failure_code(exc),
                "retry_same_slot": False,
                "contains_response_body": False,
                "contains_account_identifier": False,
                "contains_credential": False,
                "execution_performed": False,
            },
        )
        raise
    _write_new_json(
        completed_path,
        {
            "schema_version": 1,
            "run_id": loaded_plan.plan.run_id,
            "scheduled_slot": slot,
            "received_at": cycle.received_at,
            "observation_ids": cycle.observation_ids,
            "network_contact_performed": True,
            "execution_performed": False,
            "account_identifier_persisted": False,
            "credential_persisted": False,
        },
    )
    return ScheduledCaptureResult(
        scheduled_slot=slot,
        attempt_dir=attempt_dir,
        started_path=started_path,
        completed_path=completed_path,
        cycle=cycle,
    )


def verify_control_record_contains_no_sensitive_keys(path: str | Path) -> None:
    """Fail closed if a collector control record gains a sensitive field name."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    encoded = json.dumps(value, sort_keys=True).lower().replace("-", "_")
    for forbidden in (
        "account_number",
        "api_key",
        "private_key",
        "x_api_key",
        "x_signature",
        "buying_power",
    ):
        if forbidden in encoded:
            raise CostCaptureError("collector_control_record_contains_sensitive_key")
