from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dyadtetrade.domain import CancelRequest, Order, OrderRequest, OrderStatus, Trade
from dyadtetrade.domain.orders import FINAL_ORDER_STATUSES


@dataclass(frozen=True, slots=True)
class AuditEntry:
    timestamp: datetime
    action: str
    order_id: str
    status: OrderStatus
    message: str = ""


class OrderManagementService:
    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}
        self.by_client_order_id: dict[str, str] = {}
        self.by_idempotency_key: dict[str, str] = {}
        self.trades: dict[str, Trade] = {}
        self.audit: list[AuditEntry] = []

    def register(self, order: Order, at: datetime) -> Order:
        existing_id = self.by_idempotency_key.get(order.request.idempotency_key)
        if existing_id is not None:
            return self.orders[existing_id]
        if order.request.client_order_id in self.by_client_order_id:
            return self.orders[self.by_client_order_id[order.request.client_order_id]]
        self.orders[order.order_id] = order
        self.by_client_order_id[order.request.client_order_id] = order.order_id
        self.by_idempotency_key[order.request.idempotency_key] = order.order_id
        self.audit.append(AuditEntry(at, "register", order.order_id, order.status))
        return order

    def record_trade(self, trade: Trade) -> None:
        self.trades.setdefault(trade.trade_id, trade)

    def request_cancel(self, request: CancelRequest, at: datetime) -> Order | None:
        order = self.orders.get(request.order_id)
        if order is None or order.status in FINAL_ORDER_STATUSES:
            return order
        if order.status in {OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED}:
            order.transition(OrderStatus.CANCELLING, at)
            self.audit.append(AuditEntry(at, "cancel_requested", order.order_id, order.status))
        return order

    def mark_unknown(self, order_id: str, at: datetime, message: str) -> Order:
        order = self.orders[order_id]
        if order.status not in FINAL_ORDER_STATUSES:
            order.transition(OrderStatus.UNKNOWN, at)
        self.audit.append(AuditEntry(at, "unknown", order.order_id, order.status, message))
        return order

    def recover(self, snapshots: list[Order], at: datetime) -> None:
        for snapshot in snapshots:
            existing = self.orders.get(snapshot.order_id)
            if existing is None:
                self.register(snapshot, at)
            else:
                self.orders[snapshot.order_id] = snapshot
                self.audit.append(AuditEntry(at, "recovered", snapshot.order_id, snapshot.status))

    def active_orders(self) -> list[Order]:
        return [order for order in self.orders.values() if order.status not in FINAL_ORDER_STATUSES]

    @staticmethod
    def new_order(request: OrderRequest, order_id: str) -> Order:
        return Order(order_id, request)
