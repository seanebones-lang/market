import re

import pytest

from market.app.cli import main


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
