"""Simulated broker with deterministic market fills."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

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


class SimBroker:
    name = "sim"

    def __init__(
        self,
        usd: Decimal = Decimal("1000"),
        btc: Decimal = Decimal("0"),
        bid: Decimal = Decimal("100000"),
        ask: Decimal = Decimal("100010"),
        fee_bps: Decimal = Decimal("5"),  # 5 bps
        slippage_bps: Decimal = Decimal("2"),
    ) -> None:
        self._usd = usd
        self._btc = btc
        self._bid = bid
        self._ask = ask
        self.fee_bps = fee_bps
        self.slippage_bps = slippage_bps
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._by_client: dict[str, str] = {}

    def set_quote(self, bid: Decimal, ask: Decimal) -> None:
        self._bid = bid
        self._ask = ask

    def get_balances(self) -> Balances:
        return Balances(usd=self._usd, btc=self._btc)

    def get_btc_position(self) -> Position:
        avg = None
        if self._btc > 0 and self._fills:
            # rough avg from buys
            buy_qty = Decimal("0")
            buy_notional = Decimal("0")
            for f in self._fills:
                if f.side == Side.BUY:
                    buy_qty += f.qty_btc
                    buy_notional += f.qty_btc * f.price_usd
            if buy_qty > 0:
                avg = buy_notional / buy_qty
        return Position(qty_btc=self._btc, avg_entry_usd=avg)

    def get_open_orders(self) -> list[Order]:
        return [o for o in self._orders.values() if o.status == OrderStatus.OPEN]

    def get_fills(self) -> list[Fill]:
        return list(self._fills)

    def get_quote(self, symbol: str = "BTC") -> Quote:
        return Quote(symbol=symbol, bid=self._bid, ask=self._ask, ts=utcnow())

    def place_order(self, intent: Intent) -> OrderAck:
        # idempotency
        if intent.client_order_id in self._by_client:
            oid = self._by_client[intent.client_order_id]
            existing = self._orders[oid]
            return OrderAck(
                client_order_id=existing.client_order_id,
                broker_order_id=existing.broker_order_id,
                status=existing.status,
                side=existing.side,
                qty_btc=existing.qty_btc,
                order_type=existing.order_type,
                limit_price=existing.limit_price,
                ts=existing.ts,
                raw={"idempotent": True},
            )

        broker_id = uuid4().hex
        px = self._fill_price(intent.side)
        fee = (intent.qty_btc * px) * (self.fee_bps / Decimal("10000"))
        notional = intent.qty_btc * px

        if intent.side == Side.BUY:
            if self._usd < notional + fee:
                order = Order(
                    client_order_id=intent.client_order_id,
                    broker_order_id=broker_id,
                    status=OrderStatus.REJECTED,
                    side=intent.side,
                    qty_btc=intent.qty_btc,
                    order_type=intent.order_type,
                    limit_price=intent.limit_price,
                    raw={"reason": "insufficient_usd"},
                )
                self._orders[broker_id] = order
                self._by_client[intent.client_order_id] = broker_id
                return self._ack(order)
            self._usd -= notional + fee
            self._btc += intent.qty_btc
        else:
            if self._btc < intent.qty_btc:
                order = Order(
                    client_order_id=intent.client_order_id,
                    broker_order_id=broker_id,
                    status=OrderStatus.REJECTED,
                    side=intent.side,
                    qty_btc=intent.qty_btc,
                    order_type=intent.order_type,
                    limit_price=intent.limit_price,
                    raw={"reason": "insufficient_btc"},
                )
                self._orders[broker_id] = order
                self._by_client[intent.client_order_id] = broker_id
                return self._ack(order)
            self._btc -= intent.qty_btc
            self._usd += notional - fee

        fill = Fill(
            client_order_id=intent.client_order_id,
            broker_order_id=broker_id,
            side=intent.side,
            qty_btc=intent.qty_btc,
            price_usd=px,
            fee_usd=fee,
            ts=utcnow(),
        )
        self._fills.append(fill)
        order = Order(
            client_order_id=intent.client_order_id,
            broker_order_id=broker_id,
            status=OrderStatus.FILLED,
            side=intent.side,
            qty_btc=intent.qty_btc,
            filled_qty_btc=intent.qty_btc,
            avg_fill_price=px,
            order_type=intent.order_type or OrderType.MARKET,
            limit_price=intent.limit_price,
            raw={"sim": True},
        )
        self._orders[broker_id] = order
        self._by_client[intent.client_order_id] = broker_id
        return self._ack(order)

    def cancel_order(self, broker_order_id: str) -> None:
        o = self._orders.get(broker_order_id)
        if o and o.status == OrderStatus.OPEN:
            self._orders[broker_order_id] = o.model_copy(update={"status": OrderStatus.CANCELED})

    def get_order(self, broker_order_id: str) -> Order:
        if broker_order_id not in self._orders:
            raise KeyError(broker_order_id)
        return self._orders[broker_order_id]

    def _fill_price(self, side: Side) -> Decimal:
        slip = self.slippage_bps / Decimal("10000")
        if side == Side.BUY:
            return self._ask * (Decimal("1") + slip)
        return self._bid * (Decimal("1") - slip)

    @staticmethod
    def _ack(order: Order) -> OrderAck:
        return OrderAck(
            client_order_id=order.client_order_id,
            broker_order_id=order.broker_order_id,
            status=order.status,
            side=order.side,
            qty_btc=order.qty_btc,
            order_type=order.order_type,
            limit_price=order.limit_price,
            ts=order.ts,
            raw=order.raw,
        )
