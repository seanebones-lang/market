"""YAML + env config loading."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from market.domain.models import Mode
from market.risk.gate import RiskConfig
from market.strategy.slow_trend import SlowTrendConfig


class BrokerSettings(BaseModel):
    name: str = "sim"


class AppConfig(BaseModel):
    mode: Mode = Mode.SIM
    symbol: str = "BTC"
    loop_seconds: float = 30.0
    broker: BrokerSettings = Field(default_factory=BrokerSettings)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    strategy: SlowTrendConfig = Field(default_factory=SlowTrendConfig)
    data_dir: Path = Path("data")
    iterations: int | None = None  # None = forever


def _dec(v: Any) -> Any:
    if isinstance(v, (int, str)):
        return Decimal(str(v))
    return v


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    risk_raw = raw.get("risk", {})
    for k in (
        "max_position_btc",
        "max_notional_usd",
        "max_daily_loss_usd",
    ):
        if k in risk_raw:
            risk_raw[k] = _dec(risk_raw[k])
    strat_raw = raw.get("strategy", {})
    if "order_qty_btc" in strat_raw:
        strat_raw["order_qty_btc"] = _dec(strat_raw["order_qty_btc"])
    # map yaml strategy knobs
    st = SlowTrendConfig(
        fast_ema=int(strat_raw.get("fast_ema", 12)),
        slow_ema=int(strat_raw.get("slow_ema", 26)),
        order_qty_btc=_dec(strat_raw.get("order_qty_btc", "0.001")),
    )
    rc = RiskConfig(
        max_position_btc=_dec(risk_raw.get("max_position_btc", "0.002")),
        max_notional_usd=_dec(risk_raw.get("max_notional_usd", "150")),
        max_daily_loss_usd=_dec(risk_raw.get("max_daily_loss_usd", "25")),
        max_orders_per_hour=int(risk_raw.get("max_orders_per_hour", 4)),
        min_seconds_between_orders=int(risk_raw.get("min_seconds_between_orders", 300)),
        allow_entries=bool(risk_raw.get("allow_entries", True)),
    )
    mode = Mode(raw.get("mode", "sim"))
    broker = BrokerSettings(name=str(raw.get("broker", {}).get("name", "sim")))
    return AppConfig(
        mode=mode,
        symbol=str(raw.get("symbol", "BTC")),
        loop_seconds=float(raw.get("loop_seconds", 30)),
        broker=broker,
        risk=rc,
        strategy=st,
        data_dir=Path(raw.get("data_dir", "data")),
        iterations=raw.get("iterations"),
    )
