"""BrokerPort protocol."""

from __future__ import annotations

from typing import Protocol

from market.domain.models import Balances, Intent, Order, OrderAck, Position, Quote


class BrokerPort(Protocol):
    name: str

    def get_balances(self) -> Balances: ...

    def get_btc_position(self) -> Position: ...

    def get_open_orders(self) -> list[Order]: ...

    def place_order(self, intent: Intent) -> OrderAck: ...

    def cancel_order(self, broker_order_id: str) -> None: ...

    def get_order(self, broker_order_id: str) -> Order: ...

    def get_quote(self, symbol: str = "BTC") -> Quote: ...

    def get_fills(self) -> list: ...
