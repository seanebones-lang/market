import re
from pathlib import Path

import pytest

from market.app.cli import main

ROBINHOOD_COST_FIXTURE = Path(__file__).parent / "fixtures" / "robinhood" / "v2_cost_snapshot.json"


def test_backtest_help_exposes_only_explicit_per_fill_fee_input(
    capsys: pytest.CaptureFixture[str],
):
    with pytest.raises(SystemExit) as raised:
        main(["backtest", "--help"])

    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    normalized_help = " ".join(help_text.split())
    assert "--transaction-fee-bps-per-fill-assumption" in help_text
    assert "--benchmark-dca-interval-bars" in help_text
    assert "every execution fill's notional" in normalized_help
    assert re.search(r"^\s*--fee-bps(?:[ =,]|$)", help_text, re.MULTILINE) is None
    assert (
        re.search(
            r"^\s*--transaction-fee-bps-assumption(?:[ =,]|$)",
            help_text,
            re.MULTILINE,
        )
        is None
    )


def test_backtest_cli_rejects_nonpositive_benchmark_interval(
    capsys: pytest.CaptureFixture[str],
):
    with pytest.raises(SystemExit) as raised:
        main(["backtest", "--benchmark-dca-interval-bars", "0"])

    assert raised.value.code == 2
    assert "must be a positive integer" in capsys.readouterr().err


def test_robinhood_cost_cli_is_explicitly_offline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["derive-rh-v2-cost", "--help"])

    assert raised.value.code == 0
    help_text = capsys.readouterr().out.lower()
    assert "offline" in help_text
    assert "--fixture" in help_text
    assert "credential" not in help_text


def test_robinhood_credential_cli_never_accepts_secret_arguments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for command in (
        "prepare-rh-readonly-key",
        "show-rh-readonly-public-key",
        "complete-rh-readonly-credential",
        "check-rh-readonly-credential",
    ):
        with pytest.raises(SystemExit) as raised:
            main([command, "--help"])
        assert raised.value.code == 0
        help_text = capsys.readouterr().out.lower()
        assert "--credential-label" in help_text
        assert "--api-key" not in help_text
        assert "--private-key" not in help_text
        assert "--secret" not in help_text


def test_robinhood_network_commands_are_explicitly_read_only_and_scoped(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["preflight-rh-v2-cost", "--help"])
    assert raised.value.code == 0
    preflight_help = capsys.readouterr().out.lower()
    assert "network reads" in preflight_help
    assert "four g3.2c get resources" in preflight_help
    assert "no orders" in preflight_help
    assert "--authorization-reference" in preflight_help
    assert "--account-number" not in preflight_help

    with pytest.raises(SystemExit) as raised:
        main(["capture-rh-v2-cost-cycle", "--help"])
    assert raised.value.code == 0
    capture_help = capsys.readouterr().out.lower()
    assert "network reads" in capture_help
    assert "one currently due" in capture_help
    assert "no order endpoint" in capture_help
    assert "--account-number" not in capture_help


def test_robinhood_cost_cli_derives_and_verifies_bundle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(
        [
            "derive-rh-v2-cost",
            "--fixture",
            str(ROBINHOOD_COST_FIXTURE),
            "--out-dir",
            str(tmp_path),
        ]
    )
    assert rc == 0
    derive_output = capsys.readouterr().out
    assert "derived offline" in derive_output
    assert "network_contact=false" in derive_output
    assert "execution=false" in derive_output
    manifests = list(tmp_path.glob("*/manifest.json"))
    assert len(manifests) == 1

    rc = main(["verify-rh-v2-cost", "--manifest", str(manifests[0])])
    assert rc == 0
    verify_output = capsys.readouterr().out
    assert "verified offline" in verify_output
    assert "account_identifier_persisted=false" in verify_output
