"""Core domain types. Money/qty use Decimal only."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal("0")

    @field_validator("open", "high", "low", "close", "volume", mode="before")
    @classmethod
    def _no_float(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise TypeError("float not allowed")
        return D(v)
