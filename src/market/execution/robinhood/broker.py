"""Robinhood adapter guards and session stub.

UNOFFICIAL. Violates Robinhood ToS if used against production endpoints.
Live submits require ALL of:
  - mode == live
  - MARKET_RH_LIVE=1
  - allow_live=True on broker
Never default-on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from market.domain.models import (
    Balances,
    Fill,
    Intent,
    Order,
    OrderAck,
    OrderStatus,
    OrderType,
    Position,
    Quote,
    Side,
    utcnow,
)


class RobinhoodAuthError(RuntimeError):
    """Session/login failure."""


class RobinhoodLiveDisabled(RuntimeError):
    """Attempted live submit without unlock."""


def rh_live_unlocked() -> bool:
    return os.environ.get("MARKET_RH_LIVE", "0").strip() in {"1", "true", "TRUE", "yes"}


@dataclass
class RobinhoodSession:
    """Placeholder session. Real HTTP comes later behind this boundary."""

    username: str | None = None
    authenticated: bool = False
    last_error: str | None = None

    def login(self, username: str, password: str, totp: str | None = None) -> None:
        # Intentionally no network in v0 skeleton.
        if not username or not password:
            self.authenticated = False
            self.last_error = "missing_credentials"
            raise RobinhoodAuthError("missing_credentials")
        # skeleton: mark ready only when explicitly forced for tests
        if os.environ.get("MARKET_RH_FAKE_LOGIN") == "1":
            self.username = username
            self.authenticated = True
            self.last_error = None
            return
        self.authenticated = False
        self.last_error = "network_login_not_implemented"
        raise RobinhoodAuthError("network_login_not_implemented")

    def ensure_auth(self) -> None:
        if not self.authenticated:
            raise RobinhoodAuthError(self.last_error or "not_authenticated")


@dataclass
class RobinhoodBroker:
    """BrokerPort-shaped adapter with hard live guards.

    place_order never hits a network in this skeleton. When unlocked it still
    refuses unless a transport callable is injected (tests / future client).
    """

    name: str = "robinhood"
    allow_live: bool = False
    mode_is_live: bool = False
    session: RobinhoodSession = field(default_factory=RobinhoodSession)
    on_auth_error: Callable[[Exception], None] | None = None
    # optional injectable transport for tests: intent -> OrderAck
    transport_place: Callable[[Intent], OrderAck] | None = None
    transport_quote: Callable[[], Quote] | None = None
    transport_balances: Callable[[], Balances] | None = None
    transport_position: Callable[[], Position] | None = None
    _orders: dict[str, Order] = field(default_factory=dict)
    _fills: list[Fill] = field(default_factory=list)
    _by_client: dict[str, str] = field(default_factory=dict)

    def _guard_live_submit(self) -> None:
        if not self.mode_is_live:
            raise RobinhoodLiveDisabled("mode_not_live")
        if not self.allow_live:
            raise RobinhoodLiveDisabled("broker_allow_live_false")
        if not rh_live_unlocked():
            raise RobinhoodLiveDisabled("MARKET_RH_LIVE not set")

    def get_balances(self) -> Balances:
        try:
            self.session.ensure_auth()
            if self.transport_balances:
                return self.transport_balances()
            return Balances(usd=Decimal("0"), btc=Decimal("0"))
        except RobinhoodAuthError as exc:
            if self.on_auth_error:
                self.on_auth_error(exc)
            raise

    def get_btc_position(self) -> Position:
        try:
            self.session.ensure_auth()
            if self.transport_position:
                return self.transport_position()
            return Position()
        except RobinhoodAuthError as exc:
            if self.on_auth_error:
                self.on_auth_error(exc)
            raise

    def get_open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status == OrderStatus.OPEN]

    def get_fills(self) -> list[Fill]:
        return list(self._fills)

    def get_quote(self, symbol: str = "BTC") -> Quote:
        try:
            self.session.ensure_auth()
            if self.transport_quote:
                return self.transport_quote()
            # no silent fake mid in live path
            raise RobinhoodAuthError("quote_transport_missing")
        except RobinhoodAuthError as exc:
            if self.on_auth_error:
                self.on_auth_error(exc)
            raise

    def place_order(self, intent: Intent) -> OrderAck:
        self._guard_live_submit()
        try:
            self.session.ensure_auth()
        except RobinhoodAuthError as exc:
            if self.on_auth_error:
                self.on_auth_error(exc)
            raise

        if intent.client_order_id in self._by_client:
            oid = self._by_client[intent.client_order_id]
            o = self._orders[oid]
            return OrderAck(
                client_order_id=o.client_order_id,
                broker_order_id=o.broker_order_id,
                status=o.status,
                side=o.side,
                qty_btc=o.qty_btc,
                order_type=o.order_type,
                limit_price=o.limit_price,
                ts=o.ts,
                raw={"idempotent": True},
            )

        if self.transport_place is None:
            raise RuntimeError("robinhood transport_place not configured")

        ack = self.transport_place(intent)
        order = Order(
            client_order_id=ack.client_order_id,
            broker_order_id=ack.broker_order_id,
            status=ack.status,
            side=ack.side,
            qty_btc=ack.qty_btc,
            filled_qty_btc=ack.qty_btc if ack.status == OrderStatus.FILLED else Decimal("0"),
            avg_fill_price=None,
            order_type=ack.order_type,
            limit_price=ack.limit_price,
            ts=ack.ts,
            raw=ack.raw,
        )
        self._orders[ack.broker_order_id] = order
        self._by_client[ack.client_order_id] = ack.broker_order_id
        return ack

    def cancel_order(self, broker_order_id: str) -> None:
        self._guard_live_submit()
        o = self._orders.get(broker_order_id)
        if o and o.status == OrderStatus.OPEN:
            self._orders[broker_order_id] = o.model_copy(update={"status": OrderStatus.CANCELED})

    def get_order(self, broker_order_id: str) -> Order:
        if broker_order_id not in self._orders:
            raise KeyError(broker_order_id)
        return self._orders[broker_order_id]


def build_shadow_ack(intent: Intent) -> dict[str, Any]:
    """Record shape for live-dry / paper — never a broker submit."""
    return {
        "type": "shadow_ack",
        "ts": utcnow().isoformat(),
        "client_order_id": intent.client_order_id,
        "side": intent.side.value,
        "qty_btc": str(intent.qty_btc),
        "order_type": intent.order_type.value if isinstance(intent.order_type, OrderType) else intent.order_type,
        "reason": intent.reason,
    }
