import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from market.app.cli import main
from market.backtest.costs import VenueCostProfile
from market.backtest.engine import ExecutionModel, run_backtest, write_backtest_report
from market.backtest.reproducibility import (
    REQUIRED_EVIDENCE_ROLES,
    ArtifactIntegrity,
    BacktestArtifactManifest,
    BacktestReproducibilityError,
    BacktestRunProvenance,
    SourceRevision,
    canonical_json_bytes,
    pretty_json_bytes,
    resolve_source_revision,
    sha256_path,
    verify_backtest_report,
)
from market.data.candles import load_candles_csv
from market.domain.models import Candle
from market.strategy.slow_trend import SlowTrendConfig

FUTURE_JUMP_FIXTURE = Path(__file__).parent / "fixtures" / "backtest" / "future_jump.csv"
FIXED_REVISION = SourceRevision(status="clean", commit_sha="a" * 40)


def _run_fixture(*, candles: list[Candle] | None = None, random_seed: int = 7):
    return run_backtest(
        candles or load_candles_csv(FUTURE_JUMP_FIXTURE),
        starting_cash_usd=Decimal("1000"),
        qty_btc=Decimal("1"),
        strategy_cfg=SlowTrendConfig(fast_ema=2, slow_ema=3, order_qty_btc=Decimal("1")),
        source="fixture:future-jump",
        execution_model=ExecutionModel.NEXT_BAR_OPEN_BID_ASK,
        quoted_spread_bps_assumption=Decimal("20"),
        adverse_slippage_bps_assumption=Decimal("10"),
        venue_cost_profile=VenueCostProfile.ROBINHOOD_CRYPTO_API_V2_EXCHANGE_TAKER,
        transaction_fee_bps_per_fill_assumption=Decimal("95"),
        benchmark_dca_interval_bars=2,
        random_seed=random_seed,
    )


def _rebind_artifact(
    manifest_path: Path,
    artifact_name: str,
    *,
    update_record_count: bool = True,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = next(item for item in manifest["artifacts"] if item["name"] == artifact_name)
    artifact_path = manifest_path.parent / artifact["path"]
    artifact["bytes"] = artifact_path.stat().st_size
    artifact["sha256"] = sha256_path(artifact_path)
    if update_record_count and artifact["media_type"] == "application/x-ndjson":
        artifact["records"] = len(artifact_path.read_text(encoding="utf-8").splitlines())
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


def test_identical_inputs_have_identical_run_identity_and_simulation_output():
    first = _run_fixture()
    second = _run_fixture()

    assert first.provenance == second.provenance
    assert first.provenance.input_bar_count == 6
    assert first.provenance.random_seed == 7
    assert first.provenance.randomness_used is False
    assert first.provenance.resolved_config["strategy"] == {
        "name": "slow_trend_v1",
        "timeframe": "1h",
        "fast_ema": 2,
        "slow_ema": 3,
        "order_qty_btc": "1",
    }
    assert first.provenance.resolved_config["venue_cost"]["profile"] == (
        "robinhood_crypto_api_v2_exchange_taker"
    )
    assert first.events == second.events
    assert first.fills == second.fills
    assert first.accounting_journal == second.accounting_journal
    assert first.equity_curve == second.equity_curve
    assert first.fills[0].client_order_id == ("backtest-strategy-00000001-00000005-buy")


def test_input_or_seed_change_updates_the_bound_provenance():
    candles = load_candles_csv(FUTURE_JUMP_FIXTURE)
    changed = list(candles)
    final_payload = changed[-1].model_dump()
    final_payload.update(
        {
            "open": Decimal("21"),
            "high": Decimal("21"),
            "low": Decimal("21"),
            "close": Decimal("21"),
        }
    )
    changed[-1] = Candle.model_validate(final_payload)

    baseline = _run_fixture(candles=candles, random_seed=7)
    changed_data = _run_fixture(candles=changed, random_seed=7)
    changed_seed = _run_fixture(candles=candles, random_seed=8)

    assert baseline.provenance.input_data_sha256 != changed_data.provenance.input_data_sha256
    assert baseline.provenance.resolved_config_sha256 == (
        changed_data.provenance.resolved_config_sha256
    )
    assert baseline.provenance.input_data_sha256 == changed_seed.provenance.input_data_sha256
    assert baseline.provenance.resolved_config_sha256 != (
        changed_seed.provenance.resolved_config_sha256
    )


@pytest.mark.parametrize("random_seed", [-1, True, 1.5])
def test_invalid_random_seed_fails_closed(random_seed: Any):
    with pytest.raises(BacktestReproducibilityError, match="random_seed"):
        _run_fixture(random_seed=random_seed)


@pytest.mark.parametrize("record_equity_every", [-1, True, 1.5])
def test_invalid_equity_sampling_contract_fails_closed(record_equity_every: Any):
    with pytest.raises(ValueError, match="record_equity_every"):
        run_backtest([], record_equity_every=record_equity_every)


def test_nonfinite_json_and_unavailable_source_revision_fail_safely(tmp_path: Path):
    with pytest.raises(BacktestReproducibilityError, match="canonical-JSON"):
        canonical_json_bytes({"invalid": float("nan")})
    with pytest.raises(BacktestReproducibilityError, match="JSON serializable"):
        pretty_json_bytes({"invalid": float("nan")})

    revision = resolve_source_revision(tmp_path)
    assert revision == SourceRevision(status="unavailable", commit_sha=None)
    assert revision.reproducible is False


@pytest.mark.parametrize(
    "values",
    [
        {"status": "unavailable", "commit_sha": "a" * 40},
        {"status": "clean", "commit_sha": None},
        {"status": "dirty", "commit_sha": "invalid"},
    ],
)
def test_invalid_source_revision_contract_is_rejected(values: dict[str, Any]):
    with pytest.raises(ValidationError):
        SourceRevision.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("engine_version", ""),
        ("artifact_schema_version", 0),
        ("input_fingerprint_schema", "unknown"),
        ("input_bar_count", -1),
        ("randomness_used", True),
        ("input_data_sha256", "invalid"),
        ("resolved_config_sha256", "0" * 64),
    ],
)
def test_invalid_run_provenance_contract_is_rejected(field: str, value: Any):
    values = _run_fixture().provenance.model_dump(mode="json")
    values[field] = value
    with pytest.raises((ValidationError, BacktestReproducibilityError)):
        BacktestRunProvenance.model_validate(values)


@pytest.mark.parametrize(
    "changes",
    [
        {"path": "../escape.json"},
        {"name": ""},
        {"bytes": -1},
        {"records": -1},
        {"sha256": "invalid"},
    ],
)
def test_invalid_artifact_integrity_contract_is_rejected(changes: dict[str, Any]):
    values: dict[str, Any] = {
        "name": "summary",
        "path": "summary.json",
        "media_type": "application/json",
        "sha256": "0" * 64,
        "bytes": 1,
        "records": 1,
    }
    values.update(changes)
    with pytest.raises(ValidationError):
        ArtifactIntegrity.model_validate(values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 10),
        ("code_identity_reproducible", False),
        ("evidence_roles", {}),
        ("artifacts", []),
    ],
)
def test_invalid_manifest_contract_is_rejected(tmp_path: Path, field: str, value: Any):
    paths = write_backtest_report(
        _run_fixture(),
        tmp_path,
        f"manifest-{field}",
        source_revision=FIXED_REVISION,
    )
    values = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    values[field] = value
    with pytest.raises(ValidationError):
        BacktestArtifactManifest.model_validate(values)


def test_duplicate_manifest_artifact_name_is_rejected(tmp_path: Path):
    paths = write_backtest_report(
        _run_fixture(), tmp_path, "duplicate-artifact", source_revision=FIXED_REVISION
    )
    values = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    values["artifacts"][-1] = dict(values["artifacts"][0])
    values["evidence_roles"] = dict(REQUIRED_EVIDENCE_ROLES)
    with pytest.raises(ValidationError, match="duplicate artifact"):
        BacktestArtifactManifest.model_validate(values)


def test_report_is_byte_repeatable_self_contained_and_verifiable(tmp_path: Path):
    result = _run_fixture()
    first = write_backtest_report(
        result,
        tmp_path / "first",
        "repeatable-run",
        source_revision=FIXED_REVISION,
    )
    second = write_backtest_report(
        result,
        tmp_path / "second",
        "repeatable-run",
        source_revision=FIXED_REVISION,
    )

    assert first.keys() == second.keys()
    for name in first:
        assert first[name].read_bytes() == second[name].read_bytes(), name

    manifest = verify_backtest_report(first["manifest"])
    assert manifest.code_identity_reproducible is True
    assert manifest.source_revision == FIXED_REVISION
    assert manifest.provenance == result.provenance
    assert manifest.evidence_roles == {
        "input_data": "input_candles.jsonl",
        "resolved_summary": "summary.json",
        "executions": "fills.jsonl",
        "trades": "lifecycle.jsonl",
        "equity_curve": "equity.jsonl",
        "metrics": "performance.jsonl",
    }
    assert len(manifest.artifacts) == 12
    input_rows = first["input_data"].read_text(encoding="utf-8").splitlines()
    assert len(input_rows) == result.bars
    assert json.loads(input_rows[0])["candle"] == result.input_candles[0].model_dump(mode="json")

    with pytest.raises(FileExistsError):
        write_backtest_report(
            result,
            tmp_path / "first",
            "repeatable-run",
            source_revision=FIXED_REVISION,
        )


def test_report_verifier_detects_same_size_artifact_tampering(tmp_path: Path):
    paths = write_backtest_report(
        _run_fixture(),
        tmp_path,
        "tamper-test",
        source_revision=FIXED_REVISION,
    )
    original = paths["fills"].read_bytes()
    tampered = bytearray(original)
    index = next(index for index, value in enumerate(tampered) if value not in {10, 13, 32})
    tampered[index] = ord("X") if tampered[index] != ord("X") else ord("Y")
    paths["fills"].write_bytes(bytes(tampered))

    with pytest.raises(BacktestReproducibilityError, match="checksum mismatch"):
        verify_backtest_report(paths["manifest"])


def test_report_verifier_rejects_invalid_manifest(tmp_path: Path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(BacktestReproducibilityError, match="invalid backtest manifest"):
        verify_backtest_report(manifest_path)


def test_report_verifier_rejects_missing_and_size_changed_artifacts(tmp_path: Path):
    missing = write_backtest_report(
        _run_fixture(), tmp_path, "missing", source_revision=FIXED_REVISION
    )
    missing["events"].unlink()
    with pytest.raises(BacktestReproducibilityError, match="missing backtest artifact"):
        verify_backtest_report(missing["manifest"])

    changed = write_backtest_report(
        _run_fixture(), tmp_path, "changed-size", source_revision=FIXED_REVISION
    )
    changed["events"].write_bytes(changed["events"].read_bytes() + b"\n")
    with pytest.raises(BacktestReproducibilityError, match="byte-size mismatch"):
        verify_backtest_report(changed["manifest"])


def test_report_verifier_rejects_record_count_drift(tmp_path: Path):
    paths = write_backtest_report(
        _run_fixture(), tmp_path, "record-count", source_revision=FIXED_REVISION
    )
    paths["events"].write_bytes(paths["events"].read_bytes() + b"\n")
    _rebind_artifact(paths["manifest"], "events", update_record_count=False)
    with pytest.raises(BacktestReproducibilityError, match="record-count mismatch"):
        verify_backtest_report(paths["manifest"])


def test_report_verifier_rejects_invalid_or_drifted_summary(tmp_path: Path):
    invalid = write_backtest_report(
        _run_fixture(), tmp_path, "invalid-summary", source_revision=FIXED_REVISION
    )
    invalid["summary"].write_text("not-json\n", encoding="utf-8")
    _rebind_artifact(invalid["manifest"], "summary")
    with pytest.raises(BacktestReproducibilityError, match="summary is not valid JSON"):
        verify_backtest_report(invalid["manifest"])

    identity = write_backtest_report(
        _run_fixture(), tmp_path, "identity-summary", source_revision=FIXED_REVISION
    )
    summary = json.loads(identity["summary"].read_text(encoding="utf-8"))
    summary["random_seed"] = 999
    identity["summary"].write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    _rebind_artifact(identity["manifest"], "summary")
    with pytest.raises(BacktestReproducibilityError, match="summary identity mismatch"):
        verify_backtest_report(identity["manifest"])

    config = write_backtest_report(
        _run_fixture(), tmp_path, "config-summary", source_revision=FIXED_REVISION
    )
    summary = json.loads(config["summary"].read_text(encoding="utf-8"))
    summary["resolved_config"]["source"] = "drifted"
    config["summary"].write_text(
        json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    _rebind_artifact(config["manifest"], "summary")
    with pytest.raises(BacktestReproducibilityError, match="resolved config"):
        verify_backtest_report(config["manifest"])


def test_report_verifier_rejects_input_row_identity_and_payload_drift(tmp_path: Path):
    identity = write_backtest_report(
        _run_fixture(), tmp_path, "input-identity", source_revision=FIXED_REVISION
    )
    rows = identity["input_data"].read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["sequence"] = 2
    rows[0] = json.dumps(first, sort_keys=True)
    identity["input_data"].write_text("\n".join(rows) + "\n", encoding="utf-8")
    _rebind_artifact(identity["manifest"], "input_data")
    with pytest.raises(BacktestReproducibilityError, match="row identity mismatch"):
        verify_backtest_report(identity["manifest"])

    payload = write_backtest_report(
        _run_fixture(), tmp_path, "input-payload", source_revision=FIXED_REVISION
    )
    rows = payload["input_data"].read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["candle"] = {"invalid": True}
    rows[0] = json.dumps(first, sort_keys=True)
    payload["input_data"].write_text("\n".join(rows) + "\n", encoding="utf-8")
    _rebind_artifact(payload["manifest"], "input_data")
    with pytest.raises(BacktestReproducibilityError, match="invalid input candle"):
        verify_backtest_report(payload["manifest"])


def test_report_verifier_rejects_input_count_and_fingerprint_drift(tmp_path: Path):
    count = write_backtest_report(
        _run_fixture(), tmp_path, "input-count", source_revision=FIXED_REVISION
    )
    rows = count["input_data"].read_text(encoding="utf-8").splitlines()
    count["input_data"].write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
    _rebind_artifact(count["manifest"], "input_data")
    with pytest.raises(BacktestReproducibilityError, match="count does not match"):
        verify_backtest_report(count["manifest"])

    fingerprint = write_backtest_report(
        _run_fixture(), tmp_path, "input-fingerprint", source_revision=FIXED_REVISION
    )
    rows = fingerprint["input_data"].read_text(encoding="utf-8").splitlines()
    first = json.loads(rows[0])
    first["candle"]["source"] = "changed-but-valid"
    rows[0] = json.dumps(first, sort_keys=True)
    fingerprint["input_data"].write_text("\n".join(rows) + "\n", encoding="utf-8")
    _rebind_artifact(fingerprint["manifest"], "input_data")
    with pytest.raises(BacktestReproducibilityError, match="fingerprint mismatch"):
        verify_backtest_report(fingerprint["manifest"])


def test_report_writer_rejects_post_run_input_mutation(tmp_path: Path):
    result = _run_fixture()
    result.input_candles[0].source = "mutated-after-run"

    with pytest.raises(BacktestReproducibilityError, match="no longer match provenance"):
        write_backtest_report(
            result,
            tmp_path,
            "mutated-input",
            source_revision=FIXED_REVISION,
        )
    assert not (tmp_path / "mutated-input").exists()


def test_report_writer_rechecks_schema_count_and_config_before_output(tmp_path: Path):
    schema = _run_fixture()
    schema.provenance = schema.provenance.model_copy(update={"artifact_schema_version": 10})
    with pytest.raises(BacktestReproducibilityError, match="schema version"):
        write_backtest_report(schema, tmp_path, "stale-schema", source_revision=FIXED_REVISION)

    count = _run_fixture()
    count.bars -= 1
    with pytest.raises(BacktestReproducibilityError, match="candle count"):
        write_backtest_report(count, tmp_path, "wrong-count", source_revision=FIXED_REVISION)

    config = _run_fixture()
    config.provenance.resolved_config["source"] = "mutated-after-run"
    with pytest.raises(BacktestReproducibilityError, match="config no longer matches"):
        write_backtest_report(config, tmp_path, "wrong-config", source_revision=FIXED_REVISION)


@pytest.mark.parametrize("run_id", ["", ".", "..", "nested/run", "../escape"])
def test_report_writer_rejects_unsafe_run_ids(tmp_path: Path, run_id: str):
    with pytest.raises(BacktestReproducibilityError, match="run_id"):
        write_backtest_report(
            _run_fixture(),
            tmp_path,
            run_id,
            source_revision=FIXED_REVISION,
        )


def test_verify_backtest_cli_reports_bound_identity(tmp_path: Path, capsys: Any):
    paths = write_backtest_report(
        _run_fixture(),
        tmp_path,
        "cli-verify",
        source_revision=FIXED_REVISION,
    )

    assert main(["verify-backtest", "--manifest", str(paths["manifest"])]) == 0
    output = capsys.readouterr().out
    assert "verified" in output
    assert "run_id=cli-verify" in output
    assert "artifacts=12" in output
    assert "code_status=clean" in output
