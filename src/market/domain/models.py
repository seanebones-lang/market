"""Core domain types. Money/qty use Decimal only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def D(value: str | int | Decimal) -> Decimal:
    """Construct Decimal from str/int/Decimal. Reject float at the call site."""
    if isinstance(value, float):
        raise TypeError("float is not allowed; pass str or Decimal")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class Mode(str, Enum):
    SIM = "sim"
    PAPER = "paper"
    LIVE_DRY = "live-dry"
    LIVE = "live"


class Timeframe(str, Enum):
    """Supported normalized research-bar intervals."""

    HOUR_1 = "1h"

    @property
    def seconds(self) -> int:
        return 3600

    @classmethod
    def from_seconds(cls, seconds: int) -> Timeframe:
        if seconds != cls.HOUR_1.seconds:
            raise ValueError(f"unsupported candle interval: {seconds} seconds")
        return cls.HOUR_1


class DataQualityFlag(str, Enum):
    """Provider or normalization conditions that make a bar non-tradable."""

    LATE = "late"
    PARTIAL = "partial"
    PROVIDER_WARNING = "provider_warning"


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Side
    qty_btc: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    reason: str
    signal_snapshot: dict[str, Any] = Field(default_factory=dict)
    client_order_id: str = Field(default_factory=lambda: uuid4().hex)
    ts: datetime = Field(default_factory=utcnow)

    @field_validator("qty_btc", "limit_price", mode="before")
    @classmethod
    def _no_float_money(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise TypeError("float not allowed for money/qty fields")
        if v is None:
            return v
        return D(v)

    @field_validator("qty_btc")
    @classmethod
    def _qty_positive(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("qty_btc must be > 0")
        return v


class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qty_btc: Decimal = Decimal("0")
    avg_entry_usd: Decimal | None = None

    @field_validator("qty_btc", "avg_entry_usd", mode="before")
    @classmethod
    def _no_float(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise TypeError("float not allowed")
        if v is None:
            return v
        return D(v)

    @property
    def is_flat(self) -> bool:
        return self.qty_btc == 0


class Balances(BaseModel):
    model_config = ConfigDict(extra="forbid")

    usd: Decimal = Decimal("0")
    btc: Decimal = Decimal("0")

    @field_validator("usd", "btc", mode="before")
    @classmethod
    def _no_float(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise TypeError("float not allowed")
        return D(v)


class Quote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = "BTC"
    bid: Decimal
    ask: Decimal
    ts: datetime = Field(default_factory=utcnow)

    @field_validator("bid", "ask", mode="before")
    @classmethod
    def _no_float(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise TypeError("float not allowed")
        return D(v)

    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / Decimal("2")


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class OrderAck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_order_id: str
    broker_order_id: str
    status: OrderStatus
    side: Side
    qty_btc: Decimal
    order_type: OrderType
    limit_price: Decimal | None = None
    ts: datetime = Field(default_factory=utcnow)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("qty_btc", "limit_price", mode="before")
    @classmethod
    def _no_float(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise TypeError("float not allowed")
        if v is None:
            return v
        return D(v)


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_order_id: str
    broker_order_id: str
    status: OrderStatus
    side: Side
    qty_btc: Decimal
    filled_qty_btc: Decimal = Decimal("0")
    avg_fill_price: Decimal | None = None
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None
    ts: datetime = Field(default_factory=utcnow)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("qty_btc", "filled_qty_btc", "avg_fill_price", "limit_price", mode="before")
    @classmethod
    def _no_float(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise TypeError("float not allowed")
        if v is None:
            return v
        return D(v)


class Fill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_order_id: str
    broker_order_id: str
    side: Side
    qty_btc: Decimal
    price_usd: Decimal
    fee_usd: Decimal = Decimal("0")
    ts: datetime = Field(default_factory=utcnow)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("qty_btc", "price_usd", "fee_usd", mode="before")
    @classmethod
    def _no_float(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise TypeError("float not allowed")
        return D(v)

    @property
    def notional_usd(self) -> Decimal:
        return self.qty_btc * self.price_usd


class RiskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: bool
    intent: Intent | None = None
    violations: list[str] = Field(default_factory=list)


class Candle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    ts: datetime
    timeframe: Timeframe = Timeframe.HOUR_1
    source: str = "synthetic"
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")
    received_at: datetime | None = None
    close_confirmed_at: datetime | None = None
    is_closed: bool = True
    quality_flags: tuple[DataQualityFlag, ...] = ()

    @field_validator("open", "high", "low", "close", "volume", mode="before")
    @classmethod
    def _no_float(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise TypeError("float not allowed")
        return D(v)

    @field_validator("ts", "received_at", "close_confirmed_at")
    @classmethod
    def _utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candle timestamps must be timezone-aware UTC")
        if value.utcoffset() != timedelta(0):
            raise ValueError("candle timestamps must use UTC")
        return value.astimezone(UTC)

    @field_validator("source")
    @classmethod
    def _source_present(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("candle source must not be empty")
        return value

    @field_validator("quality_flags", mode="before")
    @classmethod
    def _canonical_flags(cls, value: Any) -> tuple[DataQualityFlag, ...]:
        if value is None:
            return ()
        if isinstance(value, (str, DataQualityFlag)):
            value = (value,)
        flags = {DataQualityFlag(item) for item in value}
        return tuple(sorted(flags, key=lambda item: item.value))

    @model_validator(mode="after")
    def _validate_market_bar(self) -> Candle:
        close_time = self.ts + timedelta(seconds=self.timeframe.seconds)
        if int(self.ts.timestamp()) % self.timeframe.seconds != 0 or self.ts.microsecond != 0:
            raise ValueError(f"candle ts must align to {self.timeframe.value} UTC boundaries")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("candle prices must be > 0")
        if self.volume < 0:
            raise ValueError("candle volume must be >= 0")
        if self.low > min(self.open, self.close):
            raise ValueError("candle low cannot exceed open or close")
        if self.high < max(self.open, self.close):
            raise ValueError("candle high cannot be below open or close")
        if self.low > self.high:
            raise ValueError("candle low cannot exceed high")

        if self.received_at is None:
            self.received_at = close_time
        if self.received_at < self.ts:
            raise ValueError("received_at cannot precede candle open")

        if self.is_closed:
            if self.close_confirmed_at is None:
                self.close_confirmed_at = close_time
            if self.close_confirmed_at < close_time:
                raise ValueError("close_confirmed_at cannot precede candle close")
            if DataQualityFlag.PARTIAL in self.quality_flags:
                raise ValueError("a closed candle cannot carry the partial flag")
        elif self.close_confirmed_at is not None:
            raise ValueError("an open candle cannot have close_confirmed_at")
        return self

    @property
    def close_time(self) -> datetime:
        return self.ts + timedelta(seconds=self.timeframe.seconds)
