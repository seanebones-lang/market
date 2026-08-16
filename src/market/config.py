"""YAML + env config loading."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from market.domain.models import Mode
from market.risk.gate import RiskConfig
from market.strategy.slow_trend import SlowTrendConfig


class BrokerSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["sim", "robinhood"] = "sim"


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode = Mode.SIM
    symbol: str = "BTC"
    loop_seconds: float = Field(default=30.0, ge=0)
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: SlowTrendConfig = Field(default_factory=SlowTrendConfig)
    data_dir: Path = Path("data")
    iterations: int | None = Field(default=None, ge=0)  # None = forever

    @model_validator(mode="after")
    def _mode_broker_compatible(self) -> Self:
        if self.mode == Mode.LIVE and self.broker.name != "robinhood":
            raise ValueError("live mode requires broker.name=robinhood")
        if not self.symbol or not self.symbol.isascii() or not self.symbol.isupper():
            raise ValueError("symbol must be nonempty uppercase ASCII")
        return self


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TypeError("configuration root must be a mapping")
    return AppConfig.model_validate(raw)
