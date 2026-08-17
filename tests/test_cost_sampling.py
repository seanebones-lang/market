import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from market.app.cli import main
from market.backtest.reproducibility import pretty_json_bytes, sha256_bytes
from market.execution.robinhood.observations import (
    OfflineCostFixture,
    write_execution_cost_evidence,
)
from market.research.cost_sampling import (
    CostSamplingError,
    CostSamplingProtocol,
    analyze_cost_sampling_corpus,
    build_cost_sampling_run_plan,
    expected_sampling_slots,
    load_cost_sampling_protocol,
    load_cost_sampling_run_plan,
    verify_cost_sampling_evidence,
    write_cost_sampling_evidence,
    write_cost_sampling_run_plan,
)

ROOT = Path(__file__).parents[1]
PRODUCTION_PROTOCOL = ROOT / "config" / "research" / "rh-v2-cost-sampling-v1.json"
BASE_FIXTURE = Path(__file__).parent / "fixtures" / "robinhood" / "v2_cost_snapshot.json"


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_test_protocol(tmp_path: Path, **updates: object):
    production = load_cost_sampling_protocol(PRODUCTION_PROTOCOL).protocol
    value = production.model_dump(mode="json")
    value.update(
        {
            "protocol_id": "rh-v2-cost-sampling-test",
            "required_source_kind": "synthetic_fixture",
            "asset_quantities": ["0.001", "0.002"],
            "primary_asset_quantity": "0.001",
            "duration_full_utc_days": 2,
            "sampling_interval_seconds": 43_200,
            "schedule_offset_seconds": 420,
            "maximum_schedule_lateness_seconds": 60,
            "reported_quantiles": ["0.50", "0.75", "0.95"],
            "base_profile_quantile": "0.75",
            "stress_profile_quantile": "0.95",
            "minimum_overall_cycle_coverage": "1",
            "minimum_utc_hour_cycle_coverage": "1",
            "minimum_day_of_week_cycle_coverage": "1",
            "minimum_distinct_utc_dates": 2,
            "block_bootstrap_replicates": 19,
        }
    )
    value.update(updates)
    protocol = CostSamplingProtocol.model_validate(value)
    path = tmp_path / "protocol.json"
    path.write_bytes(pretty_json_bytes(protocol.model_dump(mode="json")))
    return load_cost_sampling_protocol(path)


def _write_test_plan(tmp_path: Path, loaded_protocol, *, start: datetime | None = None):
    scheduled_start = start or datetime(2026, 8, 18, 0, 7, tzinfo=UTC)
    plan = build_cost_sampling_run_plan(
        loaded_protocol,
        scheduled_start=scheduled_start,
        frozen_at=datetime(2026, 8, 17, 23, 50, tzinfo=UTC),
        authorization_reference="synthetic-test-only",
        run_id="rh-v2-cost-test-run",
    )
    path = tmp_path / "run-plan.json"
    write_cost_sampling_run_plan(path, plan)
    return load_cost_sampling_run_plan(path, loaded_protocol)


def _fixture_for(
    slot: datetime,
    quantity: Decimal,
    *,
    cycle_index: int,
    midpoint_adjustment: Decimal = Decimal("0"),
) -> OfflineCostFixture:
    value = json.loads(BASE_FIXTURE.read_text(encoding="utf-8"))
    midpoint = Decimal("63000") + Decimal(cycle_index * 100) + midpoint_adjustment
    best_bid = midpoint - Decimal("10")
    best_ask = midpoint + Decimal("10")
    quantity_rank = Decimal("1") if quantity == Decimal("0.001") else Decimal("3")
    buy_price = best_ask + quantity_rank + Decimal(cycle_index)
    sell_price = best_bid - quantity_rank - Decimal(cycle_index)
    fee_ratio = Decimal("0.0095")
    buy_fee = buy_price * quantity * fee_ratio
    sell_fee = sell_price * quantity * fee_ratio

    value["source_kind"] = "synthetic_fixture"
    value["requested_quantity"] = str(quantity)
    value["endpoint_receive_times"] = {
        "accounts": _utc_text(slot + timedelta(seconds=1)),
        "trading_pairs": _utc_text(slot + timedelta(seconds=2)),
        "best_bid_ask": _utc_text(slot + timedelta(seconds=3)),
        "estimated_price": _utc_text(slot + timedelta(seconds=5)),
    }
    value["best_bid_ask_response"]["results"][0]["bid"] = str(best_bid)
    value["best_bid_ask_response"]["results"][0]["ask"] = str(best_ask)
    value["estimated_price_response"] = {
        "results": [
            {
                "symbol": "BTC-USD",
                "side": "bid",
                "quantity": str(quantity),
                "timestamp": _utc_text(slot + timedelta(seconds=4)),
                "bid": str(sell_price),
                "ask": str(buy_price),
                "fee_ratio": str(fee_ratio),
                "est_fee": str(sell_fee),
                "est_total_cost": "0",
                "est_total_credit": str(sell_price * quantity - sell_fee),
            },
            {
                "symbol": "BTC-USD",
                "side": "ask",
                "quantity": str(quantity),
                "timestamp": _utc_text(slot + timedelta(seconds=4)),
                "bid": str(sell_price),
                "ask": str(buy_price),
                "fee_ratio": str(fee_ratio),
                "est_fee": str(buy_fee),
                "est_total_cost": str(buy_price * quantity + buy_fee),
                "est_total_credit": "0",
            },
        ]
    }
    return OfflineCostFixture.model_validate(value)


def _write_corpus(
    tmp_path: Path,
    loaded_protocol,
    loaded_plan,
    *,
    omit: tuple[int, Decimal] | None = None,
    inconsistent: tuple[int, Decimal] | None = None,
) -> list[Path]:
    evidence_root = tmp_path / "observations"
    manifests: list[Path] = []
    for cycle_index, slot in enumerate(
        expected_sampling_slots(loaded_protocol.protocol, loaded_plan.plan)
    ):
        for quantity in loaded_protocol.protocol.asset_quantities:
            if omit == (cycle_index, quantity):
                continue
            midpoint_adjustment = (
                Decimal("100") if inconsistent == (cycle_index, quantity) else Decimal("0")
            )
            fixture = _fixture_for(
                slot,
                quantity,
                cycle_index=cycle_index,
                midpoint_adjustment=midpoint_adjustment,
            )
            artifacts = write_execution_cost_evidence(evidence_root, fixture)
            manifests.append(artifacts.manifest_path)
    return manifests


def test_production_protocol_freezes_expected_30_day_design() -> None:
    loaded = load_cost_sampling_protocol(PRODUCTION_PROTOCOL)
    protocol = loaded.protocol

    assert protocol.required_source_kind == "captured_read_only_v2"
    assert protocol.duration_full_utc_days == 30
    assert protocol.sampling_interval_seconds == 900
    assert protocol.schedule_offset_seconds == 420
    assert protocol.expected_cycles == 2880
    assert protocol.asset_quantities == (
        Decimal("0.00025"),
        Decimal("0.0005"),
        Decimal("0.001"),
        Decimal("0.002"),
    )
    assert protocol.primary_asset_quantity == Decimal("0.001")
    assert protocol.base_profile_quantile == Decimal("0.75")
    assert protocol.stress_profile_quantile == Decimal("0.95")
    assert protocol.no_window_extension is True


def test_run_plan_binds_protocol_and_full_utc_days(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)
    loaded_plan = _write_test_plan(tmp_path, protocol)
    slots = expected_sampling_slots(protocol.protocol, loaded_plan.plan)

    assert loaded_plan.plan.expected_cycles == 4
    assert loaded_plan.plan.protocol_sha256 == protocol.sha256
    assert slots == (
        datetime(2026, 8, 18, 0, 7, tzinfo=UTC),
        datetime(2026, 8, 18, 12, 7, tzinfo=UTC),
        datetime(2026, 8, 19, 0, 7, tzinfo=UTC),
        datetime(2026, 8, 19, 12, 7, tzinfo=UTC),
    )
    with pytest.raises(FileExistsError):
        write_cost_sampling_run_plan(loaded_plan.path, loaded_plan.plan)


def test_run_plan_rejects_nonfirst_slot_start(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)

    with pytest.raises(CostSamplingError, match="first configured slot"):
        _write_test_plan(
            tmp_path,
            protocol,
            start=datetime(2026, 8, 18, 12, 7, tzinfo=UTC),
        )


def test_run_plan_rejects_tampered_protocol_hash(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)
    loaded_plan = _write_test_plan(tmp_path, protocol)
    value = loaded_plan.plan.model_dump(mode="json")
    value["protocol_sha256"] = "0" * 64
    loaded_plan.path.unlink()
    loaded_plan.path.write_bytes(pretty_json_bytes(value))

    with pytest.raises(CostSamplingError, match="SHA-256 mismatch"):
        load_cost_sampling_run_plan(loaded_plan.path, protocol)


def test_run_plan_cannot_predate_protocol(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)

    with pytest.raises(CostSamplingError, match="before its protocol"):
        build_cost_sampling_run_plan(
            protocol,
            scheduled_start=datetime(2026, 8, 18, 0, 7, tzinfo=UTC),
            frozen_at=datetime(2026, 8, 17, 23, 40, tzinfo=UTC),
            authorization_reference="synthetic-test-only",
        )


def test_protocol_rejects_unknown_and_incoherent_inputs() -> None:
    value = json.loads(PRODUCTION_PROTOCOL.read_text(encoding="utf-8"))
    value["unregistered_choice"] = True
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CostSamplingProtocol.model_validate(value)

    value = json.loads(PRODUCTION_PROTOCOL.read_text(encoding="utf-8"))
    value["sampling_interval_seconds"] = 1000
    with pytest.raises(ValidationError, match="divide one UTC day"):
        CostSamplingProtocol.model_validate(value)


def test_complete_synthetic_corpus_admits_and_maps_profiles(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)
    plan = _write_test_plan(tmp_path, protocol)
    manifests = _write_corpus(tmp_path, protocol, plan)

    first = analyze_cost_sampling_corpus(protocol, plan, manifests)
    second = analyze_cost_sampling_corpus(protocol, plan, list(reversed(manifests)))

    assert first == second
    summary = first.summary
    assert summary.admission_status == "pass"
    assert summary.admission_failures == ()
    assert summary.expected_cycles == 4
    assert summary.complete_cycles == 4
    assert summary.overall_cycle_coverage == Decimal("1")
    assert summary.observed_distinct_utc_dates == 2
    assert summary.partial_cycles == ()
    assert summary.missing_cycle_times == ()
    assert len(first.bindings) == 8
    assert len(summary.quantity_summaries) == 2
    assert summary.base_profile is not None
    assert summary.stress_profile is not None
    assert summary.base_profile.asset_quantity == Decimal("0.001")
    assert summary.base_profile.transaction_fee_bps_per_fill_assumption == Decimal("95")
    assert summary.base_profile.source_quantile == Decimal("0.75")
    assert summary.stress_profile.source_quantile == Decimal("0.95")
    assert (
        summary.stress_profile.adverse_slippage_bps_assumption
        >= summary.base_profile.adverse_slippage_bps_assumption
    )
    primary = next(
        item for item in summary.quantity_summaries if item.asset_quantity == Decimal("0.001")
    )
    p75 = next(
        item
        for item in primary.quoted_full_spread_bps.quantiles
        if item.probability == Decimal("0.75")
    )
    assert p75.daily_block_bootstrap_lower <= p75.value <= p75.daily_block_bootstrap_upper

    evidence = write_cost_sampling_evidence(tmp_path / "summaries", first)
    verified = verify_cost_sampling_evidence(evidence.manifest_path)
    assert verified.analysis == first
    with pytest.raises(FileExistsError):
        write_cost_sampling_evidence(tmp_path / "summaries", first)


def test_partial_cycle_fails_admission_without_cost_profiles(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)
    plan = _write_test_plan(tmp_path, protocol)
    manifests = _write_corpus(
        tmp_path,
        protocol,
        plan,
        omit=(0, Decimal("0.002")),
    )

    analysis = analyze_cost_sampling_corpus(protocol, plan, manifests)

    assert analysis.summary.admission_status == "fail"
    assert "overall_cycle_coverage_below_minimum" in analysis.summary.admission_failures
    assert analysis.summary.complete_cycles == 3
    assert analysis.summary.overall_cycle_coverage == Decimal("0.75")
    assert len(analysis.summary.partial_cycles) == 1
    assert analysis.summary.partial_cycles[0].missing_quantities == (Decimal("0.002"),)
    assert analysis.summary.base_profile is None
    assert analysis.summary.stress_profile is None


def test_corpus_rejects_source_kind_mismatch(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path, required_source_kind="captured_read_only_v2")
    plan = _write_test_plan(tmp_path, protocol)
    manifests = _write_corpus(tmp_path, protocol, plan)

    with pytest.raises(CostSamplingError, match="source kind mismatches"):
        analyze_cost_sampling_corpus(protocol, plan, manifests)


def test_corpus_rejects_duplicate_observation(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)
    plan = _write_test_plan(tmp_path, protocol)
    manifests = _write_corpus(tmp_path, protocol, plan)

    with pytest.raises(CostSamplingError, match="duplicate observation ID"):
        analyze_cost_sampling_corpus(protocol, plan, [*manifests, manifests[0]])


def test_corpus_rejects_cross_quantity_capture_mismatch(tmp_path: Path) -> None:
    protocol = _write_test_protocol(
        tmp_path,
        duration_full_utc_days=1,
        sampling_interval_seconds=86_400,
        minimum_distinct_utc_dates=1,
    )
    plan = _write_test_plan(tmp_path, protocol)
    manifests = _write_corpus(
        tmp_path,
        protocol,
        plan,
        inconsistent=(0, Decimal("0.002")),
    )

    with pytest.raises(CostSamplingError, match="do not share one capture cycle"):
        analyze_cost_sampling_corpus(protocol, plan, manifests)


def test_corpus_rejects_observation_outside_run_window(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)
    plan = _write_test_plan(tmp_path, protocol)
    outside_slot = plan.plan.scheduled_end + timedelta(seconds=420)
    fixture = _fixture_for(outside_slot, Decimal("0.001"), cycle_index=0)
    evidence = write_execution_cost_evidence(tmp_path / "outside", fixture)

    with pytest.raises(CostSamplingError, match="outside the scheduled window"):
        analyze_cost_sampling_corpus(protocol, plan, [evidence.manifest_path])


def test_summary_verifier_rejects_tampering(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)
    plan = _write_test_plan(tmp_path, protocol)
    manifests = _write_corpus(tmp_path, protocol, plan)
    analysis = analyze_cost_sampling_corpus(protocol, plan, manifests)
    evidence = write_cost_sampling_evidence(tmp_path / "summaries", analysis)
    evidence.summary_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(CostSamplingError, match="summary byte count mismatch"):
        verify_cost_sampling_evidence(evidence.manifest_path)


def test_summary_verifier_recomputes_content_addressed_identity(tmp_path: Path) -> None:
    protocol = _write_test_protocol(tmp_path)
    plan = _write_test_plan(tmp_path, protocol)
    manifests = _write_corpus(tmp_path, protocol, plan)
    analysis = analyze_cost_sampling_corpus(protocol, plan, manifests)
    evidence = write_cost_sampling_evidence(tmp_path / "summaries", analysis)

    summary_value = json.loads(evidence.summary_path.read_text(encoding="utf-8"))
    summary_value["observed_distinct_utc_dates"] = 1
    summary_bytes = pretty_json_bytes(summary_value)
    evidence.summary_path.write_bytes(summary_bytes)
    manifest_value = json.loads(evidence.manifest_path.read_text(encoding="utf-8"))
    manifest_value["summary"]["bytes"] = len(summary_bytes)
    manifest_value["summary"]["sha256"] = sha256_bytes(summary_bytes)
    evidence.manifest_path.write_bytes(pretty_json_bytes(manifest_value))

    with pytest.raises(CostSamplingError, match="content fingerprint mismatch"):
        verify_cost_sampling_evidence(evidence.manifest_path)


def test_cost_sampling_cli_freezes_plan_without_collection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    protocol = _write_test_protocol(tmp_path)
    output = tmp_path / "cli-plan.json"

    rc = main(
        [
            "freeze-rh-v2-cost-run",
            "--protocol",
            str(protocol.path),
            "--start",
            "2026-08-18T00:07:00Z",
            "--frozen-at",
            "2026-08-17T23:50:00Z",
            "--authorization-reference",
            "synthetic-test-only",
            "--run-id",
            "cli-cost-test-run",
            "--out",
            str(output),
        ]
    )

    assert rc == 0
    assert output.exists()
    assert load_cost_sampling_run_plan(output, protocol).plan.expected_cycles == 4
    console = capsys.readouterr().out
    assert "frozen offline" in console
    assert "network_contact=false" in console
    assert "collection_started=false" in console


def test_cost_sampling_cli_summarizes_and_verifies_local_corpus(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    protocol = _write_test_protocol(tmp_path)
    plan = _write_test_plan(tmp_path, protocol)
    _write_corpus(tmp_path, protocol, plan)
    summaries = tmp_path / "cli-summaries"

    rc = main(
        [
            "summarize-rh-v2-cost-study",
            "--protocol",
            str(protocol.path),
            "--run-plan",
            str(plan.path),
            "--observations-root",
            str(tmp_path / "observations"),
            "--out-dir",
            str(summaries),
        ]
    )

    assert rc == 0
    console = capsys.readouterr().out
    assert "study PASS" in console
    assert "network_contact=false" in console
    manifests = list(summaries.glob("*/manifest.json"))
    assert len(manifests) == 1

    rc = main(["verify-rh-v2-cost-study", "--manifest", str(manifests[0])])
    assert rc == 0
    console = capsys.readouterr().out
    assert "verified offline" in console
    assert "strategy_result=false" in console
