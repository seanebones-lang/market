"""Reconcile local journal client_order_ids vs broker fills/orders."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from market.domain.models import Fill, Order, OrderStatus


@dataclass
class ReconcileReport:
    ok: bool
    missing_on_broker: list[str] = field(default_factory=list)
    extra_on_broker: list[str] = field(default_factory=list)
    status_mismatches: list[str] = field(default_factory=list)

    @property
    def messages(self) -> list[str]:
        out: list[str] = []
        for x in self.missing_on_broker:
            out.append(f"missing_on_broker:{x}")
        for x in self.extra_on_broker:
            out.append(f"extra_on_broker:{x}")
        for x in self.status_mismatches:
            out.append(f"status_mismatch:{x}")
        return out


def reconcile(
    local_client_ids: Iterable[str],
    broker_orders: Iterable[Order],
    broker_fills: Iterable[Fill],
    expect_filled_ids: Iterable[str] | None = None,
) -> ReconcileReport:
    """Compare local submitted client ids to broker state.

    missing_on_broker: local id with no broker order/fill
    extra_on_broker: broker fill/order client id not in local set
    """
    local = set(local_client_ids)
    broker_ids: set[str] = set()
    orders_by_client: dict[str, Order] = {}
    for o in broker_orders:
        broker_ids.add(o.client_order_id)
        orders_by_client[o.client_order_id] = o
    for f in broker_fills:
        broker_ids.add(f.client_order_id)

    missing = sorted(local - broker_ids)
    extra = sorted(broker_ids - local)

    mismatches: list[str] = []
    if expect_filled_ids is not None:
        for cid in expect_filled_ids:
            expected_order = orders_by_client.get(cid)
            if expected_order is not None and expected_order.status not in {
                OrderStatus.FILLED,
                OrderStatus.REJECTED,
            }:
                mismatches.append(cid)

    ok = not missing and not extra and not mismatches
    return ReconcileReport(
        ok=ok,
        missing_on_broker=missing,
        extra_on_broker=extra,
        status_mismatches=mismatches,
    )
