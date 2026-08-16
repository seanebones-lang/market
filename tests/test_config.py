from pathlib import Path

import pytest
from pydantic import ValidationError

from market.config import load_config
from market.domain.models import Mode


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_config_preserves_declared_timeframe():
    config = load_config(Path("config/paper-live.yaml"))
    assert config.strategy.timeframe == "1h"
    assert config.strategy.fast_ema == 12
    assert config.strategy.slow_ema == 26


@pytest.mark.parametrize(
    "yaml_text",
    [
        "unknown: true\n",
        "broker:\n  name: unsupported\n",
        'risk:\n  allow_entries: "false"\n',
        'risk:\n  max_notional_usd: "-1"\n',
        "risk:\n  max_orders_per_hour: 0\n",
        "risk:\n  min_seconds_between_orders: -1\n",
        "strategy:\n  fast_ema: 26\n  slow_ema: 12\n",
        "strategy:\n  timeframe: 2s\n",
        "mode: live\nbroker:\n  name: sim\n",
        "symbol: btc\n",
    ],
)
def test_config_rejects_unsafe_or_unknown_values(tmp_path: Path, yaml_text: str):
    with pytest.raises(ValidationError):
        load_config(_write(tmp_path, yaml_text))


def test_config_root_must_be_mapping(tmp_path: Path):
    with pytest.raises(TypeError, match="root must be a mapping"):
        load_config(_write(tmp_path, "- not\n- a\n- mapping\n"))


def test_live_config_requires_robinhood(tmp_path: Path):
    config = load_config(_write(tmp_path, "mode: live\nbroker:\n  name: robinhood\n"))
    assert config.mode == Mode.LIVE
    assert config.broker.name == "robinhood"
