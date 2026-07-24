from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.core.config import ExecutionMode, Settings, get_settings
from dyadtetrade.db.models import (
    AuditEventRecord,
    LedgerQuarantineRecord,
    OmsOrderRecord,
    OmsTradeRecord,
    PaperAccountRecord,
    PositionRecord,
)
from dyadtetrade.db.session import SessionFactory
from dyadtetrade.domain import Order, Trade
from dyadtetrade.domain.precision import api_decimal, quantize_money
from dyadtetrade.integrations.qmt_sim_gateway import QmtSimTradingGateway, QmtSubmission

CHINA_TZ = ZoneInfo("Asia/Shanghai")
ACTIVE_ORDER_STATUSES = {
    "CREATED",
    "SUBMITTING",
    "SUBMITTED",
    "PARTIALLY_FILLED",
    "CANCELLING",
    "UNKNOWN",
}


def validate_broker_trade(trade: Trade) -> None:
    if trade.source not in {"qmt_trade_callback", "qmt_trade_query"}:
        raise ValueError("QMT broker ledger only accepts QMT trade facts")
    identities = (trade.account_id, trade.broker_order_id, trade.broker_trade_id)
    if (
        any(not value or str(value).strip() in {"", "0"} for value in identities)
        or trade.broker_reported_at is None
    ):
        raise ValueError("QMT broker trade is missing mandatory broker identity")
    if trade.qmt_order_id is not None:
        if trade.qmt_order_id <= 0 or trade.broker_order_id != str(trade.qmt_order_id):
            raise ValueError("QMT broker trade has inconsistent numeric order identity")
    elif not trade.order_sysid or trade.broker_order_id != trade.order_sysid:
        raise ValueError("QMT broker trade has inconsistent order_sysid identity")
    if trade.broker_reported_fee is not None and trade.broker_reported_fee < 0:
        raise ValueError("QMT broker trade fee must be non-negative")
    if trade.fee_breakdown_complete:
        raise ValueError("QMT XtTrade aggregate fee cannot claim a complete fee breakdown")


def _merge_persisted_order_fact(
    row: OmsOrderRecord,
    remote: Order,
) -> dict[str, object] | None:
    previous_status = row.status
    previous_filled = row.filled_quantity
    incoming_status = remote.status.value
    incoming_filled = remote.filled_quantity
    terminal_conflict = (
        (previous_status == "FILLED" and incoming_status != "FILLED")
        or (previous_status == "CANCELLED" and incoming_status not in {"CANCELLED", "FILLED"})
        or (
            previous_status == "REJECTED"
            and incoming_status not in {"REJECTED", "PARTIALLY_FILLED", "FILLED", "CANCELLED"}
        )
    )
    fill_regression = incoming_filled < previous_filled
    preserve_status = (
        terminal_conflict
        or (
            previous_status == "CANCELLING"
            and incoming_status in {"SUBMITTING", "SUBMITTED", "PARTIALLY_FILLED", "UNKNOWN"}
        )
        or (
            previous_status == "PARTIALLY_FILLED"
            and incoming_status in {"CREATED", "SUBMITTING", "SUBMITTED", "UNKNOWN"}
        )
        or (
            previous_status == "SUBMITTED"
            and incoming_status in {"CREATED", "SUBMITTING", "UNKNOWN"}
        )
        or (previous_status == "SUBMITTING" and incoming_status in {"CREATED", "UNKNOWN"})
    )

    if not preserve_status:
        row.status = incoming_status
    if incoming_filled > previous_filled or (
        incoming_filled == previous_filled
        and remote.average_fill_price > 0
        and row.average_fill_price == 0
    ):
        row.filled_quantity = incoming_filled
        row.average_fill_price = remote.average_fill_price
    row.qmt_order_id = remote.qmt_order_id
    row.order_sysid = remote.order_sysid
    reported_at = remote.updated_at or remote.submitted_at
    row.broker_reported_at = reported_at.replace(tzinfo=None) if reported_at else None

    if not terminal_conflict and not fill_regression:
        if row.last_error and row.last_error.startswith("broker order state regression"):
            row.last_error = None
        return None
    conflict: dict[str, object] = {
        "order_id": row.order_id,
        "local_status": previous_status,
        "broker_status": incoming_status,
        "local_filled_quantity": previous_filled,
        "broker_filled_quantity": incoming_filled,
        "terminal_conflict": terminal_conflict,
        "fill_regression": fill_regression,
    }
    row.last_error = (
        "broker order state regression: "
        f"{previous_status}/{previous_filled} -> {incoming_status}/{incoming_filled}"
    )
    return conflict


async def _record_order_state_regression(
    session: AsyncSession,
    row: OmsOrderRecord,
    conflict: dict[str, object],
) -> None:
    payload = json.dumps(conflict, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    trace_id = f"qmt-order-state-regression-{digest}"
    existing = await session.scalar(
        select(AuditEventRecord.id).where(AuditEventRecord.trace_id == trace_id)
    )
    if existing is None:
        session.add(
            AuditEventRecord(
                trace_id=trace_id,
                event_type="qmt_order_state_regression",
                actor="reconciliation",
                object_id=row.order_id,
                payload_json=json.dumps(conflict, ensure_ascii=False),
            )
        )


class QmtSimRuntime:
    def __init__(
        self,
        settings: Settings | None = None,
        gateway: QmtSimTradingGateway | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.gateway = gateway
        self._monitor: asyncio.Task[None] | None = None
        self.last_reconciled_at: datetime | None = None
        self.last_reconciliation_attempt_at: datetime | None = None
        self.last_reconciliation: dict[str, object] | None = None
        self._last_persistence_conflicts: list[dict[str, object]] = []
        self._active_mismatch_identity: str | None = None
        self._active_mismatch_trace_id: str | None = None
        self.last_error: str | None = None

    async def start(self) -> None:
        self.settings.validate_execution_safety()
        if self.settings.execution_mode not in {ExecutionMode.QMT_SIM, ExecutionMode.QMT_LIVE}:
            return
        if self.gateway is not None and self.gateway.connected and self.gateway.subscribed:
            return
        if self.gateway is None:
            self.gateway = QmtSimTradingGateway.create(self.settings)
        await asyncio.to_thread(self.gateway.start)
        await self.reconcile()
        await self.restore_safety_usage()
        self._monitor = asyncio.create_task(self._monitor_loop(), name="qmt-sim-monitor")

    async def stop(self) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            try:
                await self._monitor
            except asyncio.CancelledError:
                pass
            self._monitor = None
        if self.gateway is not None:
            await asyncio.to_thread(self.gateway.stop)

    async def reconcile(self) -> dict[str, object]:
        gateway = self.require_gateway()
        snapshot = await asyncio.to_thread(gateway.reconcile)
        known_order_ids = await self._active_qmt_order_ids()
        refreshed_known_orders = await asyncio.to_thread(
            gateway.refresh_known_orders, known_order_ids
        )
        snapshot["refreshed_known_orders"] = refreshed_known_orders
        await self.persist_snapshot()
        attempted_at = datetime.now(UTC)
        consistency = await self._audit_reconciliation_consistency(attempted_at)
        self.last_reconciliation_attempt_at = attempted_at
        self.last_reconciliation = consistency
        if bool(consistency["consistent"]):
            self.last_reconciled_at = attempted_at
            gateway.recovery_required = False
            gateway.recovery_reason = None
            gateway.last_error = None
            self.last_error = None
        else:
            self.last_error = (
                "reconciliation mismatch: "
                f"{consistency['unmatched_local_active_count']} local active, "
                f"{consistency['unmatched_broker_active_count']} broker active unmatched, "
                f"{consistency['order_state_conflict_count']} order state conflicts, "
                f"gateway health consistent={consistency['gateway_health_consistent']}"
            )
        return {**snapshot, "ledger_consistency": consistency}

    async def _active_qmt_order_ids(self) -> set[int]:
        async with SessionFactory() as session:
            values = (
                await session.scalars(
                    select(OmsOrderRecord.qmt_order_id).where(
                        OmsOrderRecord.account_id == self.settings.qmt_account_id,
                        OmsOrderRecord.status.in_(ACTIVE_ORDER_STATUSES),
                        OmsOrderRecord.qmt_order_id.is_not(None),
                    )
                )
            ).all()
        return {int(value) for value in values if value is not None and value > 0}

    async def _audit_reconciliation_consistency(self, attempted_at: datetime) -> dict[str, object]:
        gateway = self.require_gateway()
        broker_active = [
            gateway.orders[order_id]
            for order_id in gateway.last_queried_order_ids
            if order_id in gateway.orders
            and gateway.orders[order_id].status.value in ACTIVE_ORDER_STATUSES
        ]
        async with SessionFactory() as session:
            local_active = (
                await session.scalars(
                    select(OmsOrderRecord).where(
                        OmsOrderRecord.account_id == self.settings.qmt_account_id,
                        OmsOrderRecord.status.in_(ACTIVE_ORDER_STATUSES),
                    )
                )
            ).all()

            def order_matches(row: OmsOrderRecord, remote: object) -> bool:
                broker = remote
                qmt_order_id = getattr(broker, "qmt_order_id", None)
                order_sysid = getattr(broker, "order_sysid", None)
                request = getattr(broker, "request", None)
                client_order_id = getattr(request, "client_order_id", None)
                return bool(
                    (row.qmt_order_id is not None and row.qmt_order_id == qmt_order_id)
                    or (row.order_sysid and row.order_sysid == order_sysid)
                    or (row.client_order_id and row.client_order_id == client_order_id)
                )

            unmatched_local = [
                row.order_id
                for row in local_active
                if not any(order_matches(row, remote) for remote in broker_active)
            ]
            unmatched_broker = [
                str(remote.broker_order_id or remote.order_id)
                for remote in broker_active
                if not any(order_matches(row, remote) for row in local_active)
            ]
            state_conflicts = list(self._last_persistence_conflicts)
            gateway_health_consistent = (
                gateway.connected and gateway.subscribed and gateway.account_healthy
            )
            consistent = (
                not unmatched_local
                and not unmatched_broker
                and not state_conflicts
                and gateway_health_consistent
            )
            result: dict[str, object] = {
                "consistent": consistent,
                "attempted_at": attempted_at.isoformat(),
                "local_active_count": len(local_active),
                "broker_active_count": len(broker_active),
                "matched_local_active_count": len(local_active) - len(unmatched_local),
                "unmatched_local_active_count": len(unmatched_local),
                "unmatched_broker_active_count": len(unmatched_broker),
                "unmatched_local_active_order_ids": sorted(unmatched_local),
                "unmatched_broker_active_order_ids": sorted(unmatched_broker),
                "order_state_conflict_count": len(state_conflicts),
                "order_state_conflicts": state_conflicts,
                "gateway_health_consistent": gateway_health_consistent,
                "connected": gateway.connected,
                "subscribed": gateway.subscribed,
                "account_healthy": gateway.account_healthy,
            }
            if not consistent:
                mismatch_identity = {
                    "account_digest": hashlib.sha256(
                        self.settings.qmt_account_id.encode("utf-8")
                    ).hexdigest()[:12],
                    "execution_mode": self.settings.execution_mode.value,
                    "unmatched_local_active_order_ids": sorted(unmatched_local),
                    "unmatched_broker_active_order_ids": sorted(unmatched_broker),
                    "order_state_conflicts": state_conflicts,
                    "gateway_health_consistent": gateway_health_consistent,
                }
                mismatch_payload = json.dumps(mismatch_identity, ensure_ascii=False, sort_keys=True)
                mismatch_digest = hashlib.sha256(mismatch_payload.encode("utf-8")).hexdigest()[:16]
                if self._active_mismatch_identity != mismatch_digest:
                    incident_digest = hashlib.sha256(
                        attempted_at.isoformat().encode("utf-8")
                    ).hexdigest()[:12]
                    self._active_mismatch_identity = mismatch_digest
                    self._active_mismatch_trace_id = (
                        f"qmt-reconcile-mismatch-{mismatch_digest}-{incident_digest}"
                    )
                trace_id = self._active_mismatch_trace_id
                assert trace_id is not None
                existing = await session.scalar(
                    select(AuditEventRecord.id).where(AuditEventRecord.trace_id == trace_id)
                )
                if existing is None:
                    session.add(
                        AuditEventRecord(
                            trace_id=trace_id,
                            event_type="qmt_reconciliation_mismatch",
                            actor="reconciliation",
                            object_id=self.settings.execution_mode.value,
                            payload_json=json.dumps(result, ensure_ascii=False),
                        )
                    )
                    await session.commit()
            else:
                self._active_mismatch_identity = None
                self._active_mismatch_trace_id = None
            return result

    async def persist_submission(self, submission: QmtSubmission) -> None:
        await self._retry_locked(lambda: self._persist_submission_once(submission))

    async def _persist_submission_once(self, submission: QmtSubmission) -> None:
        order = submission.order
        mapping = submission.mapping
        async with SessionFactory() as session:
            existing = await session.scalar(
                select(OmsOrderRecord).where(
                    OmsOrderRecord.client_order_id == mapping.client_order_id
                )
            )
            if existing is None:
                session.add(
                    OmsOrderRecord(
                        order_id=order.order_id,
                        client_order_id=mapping.client_order_id,
                        idempotency_key=mapping.idempotency_key,
                        account_id=mapping.account_id,
                        strategy_id=mapping.strategy_id,
                        run_id=None,
                        strategy_release_id=order.request.strategy_release_id,
                        signal_id=order.request.signal_id,
                        parent_order_id=order.request.parent_order_id,
                        symbol=order.request.symbol,
                        direction=order.request.direction.value,
                        quantity=order.request.quantity,
                        filled_quantity=order.filled_quantity,
                        limit_price=order.request.limit_price,
                        average_fill_price=order.average_fill_price,
                        status=order.status.value,
                        execution_mode=self.settings.execution_mode.value,
                        qmt_seq=mapping.qmt_seq,
                        qmt_order_id=mapping.qmt_order_id,
                        order_sysid=mapping.order_sysid,
                        order_remark=mapping.order_remark,
                        last_error=mapping.last_error,
                        source="qmt_order_submit",
                        data_origin="dyadtetrade",
                        is_simulated=(
                            self.settings.execution_mode is not ExecutionMode.QMT_LIVE
                            or self.settings.qmt_account_is_simulated
                        ),
                        payload_json=json.dumps(
                            {
                                "limit_price": str(order.request.limit_price),
                                "strategy_release_id": order.request.strategy_release_id,
                                "signal_id": order.request.signal_id,
                            },
                            ensure_ascii=False,
                        ),
                        submitted_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                )
                session.add(
                    AuditEventRecord(
                        trace_id=order.request.idempotency_key,
                        event_type=f"{self.settings.execution_mode.value}_order_submitted",
                        actor=order.request.strategy_id or "manual",
                        object_id=order.order_id,
                        payload_json=json.dumps(
                            {
                                "symbol": order.request.symbol,
                                "quantity": order.request.quantity,
                                "direction": order.request.direction.value,
                                "qmt_seq": mapping.qmt_seq,
                                "strategy_release_id": order.request.strategy_release_id,
                                "signal_id": order.request.signal_id,
                            },
                            ensure_ascii=False,
                        ),
                    )
                )
            await session.commit()

    async def restore_safety_usage(self) -> None:
        gateway = self.require_gateway()
        trading_day = datetime.now(CHINA_TZ).date()
        day_start = datetime.combine(trading_day, time.min)
        async with SessionFactory() as session:
            rows = (
                await session.scalars(
                    select(OmsOrderRecord).where(
                        OmsOrderRecord.execution_mode == self.settings.execution_mode.value,
                        OmsOrderRecord.created_at >= day_start,
                    )
                )
            ).all()
        submitted_notional = Decimal("0")
        idempotency_keys: set[str] = set()
        order_timestamps: list[datetime] = []
        strategy_notional: dict[str, Decimal] = {}
        for row in rows:
            payload = json.loads(row.payload_json or "{}")
            price = Decimal(str(payload.get("limit_price") or 0))
            notional = price * row.quantity
            submitted_notional += notional
            strategy_notional[row.strategy_id] = (
                strategy_notional.get(row.strategy_id, Decimal("0")) + notional
            )
            idempotency_keys.add(row.idempotency_key)
            order_timestamps.append(row.created_at.replace(tzinfo=CHINA_TZ).astimezone(UTC))
        traded_notional = sum(
            (
                trade.notional
                for trade in gateway.trades.values()
                if trade.timestamp.astimezone(CHINA_TZ).date() == trading_day
            ),
            Decimal("0"),
        )
        gateway.safety.restore_daily_usage(
            trading_day=trading_day,
            notional=max(submitted_notional, traded_notional),
            idempotency_keys=idempotency_keys,
            order_timestamps=order_timestamps,
            strategy_notional=strategy_notional,
        )

    async def persist_snapshot(self) -> None:
        await self._retry_locked(self._persist_snapshot_once)

    async def _persist_snapshot_once(self) -> None:
        gateway = self.require_gateway()
        state_conflicts: dict[str, dict[str, object]] = {}
        async with SessionFactory() as session:
            if gateway.account is not None:
                account = await session.scalar(
                    select(PaperAccountRecord).where(
                        PaperAccountRecord.account_id == self.settings.qmt_account_id
                    )
                )
                if account is None:
                    account = PaperAccountRecord(
                        account_id=self.settings.qmt_account_id,
                        cash=gateway.account.cash,
                        available_cash=gateway.account.available_cash,
                        frozen_cash=gateway.account.frozen_cash,
                        status=self.settings.execution_mode.value,
                        last_reconciled_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                    session.add(account)
                else:
                    account.cash = gateway.account.cash
                    account.available_cash = gateway.account.available_cash
                    account.frozen_cash = gateway.account.frozen_cash
                    account.status = self.settings.execution_mode.value
                    account.last_reconciled_at = datetime.now(UTC).replace(tzinfo=None)
            for position in gateway.positions.values():
                row = await session.scalar(
                    select(PositionRecord).where(
                        PositionRecord.account_id == self.settings.qmt_account_id,
                        PositionRecord.symbol == position.symbol,
                    )
                )
                if row is None:
                    row = PositionRecord(
                        account_id=self.settings.qmt_account_id,
                        symbol=position.symbol,
                        quantity=position.quantity,
                        available_quantity=position.available_quantity,
                        frozen_quantity=position.frozen_quantity,
                        average_cost=position.average_cost,
                        last_price=position.last_price,
                    )
                    session.add(row)
                else:
                    row.quantity = position.quantity
                    row.available_quantity = position.available_quantity
                    row.frozen_quantity = position.frozen_quantity
                    row.average_cost = position.average_cost
                    row.last_price = position.last_price
            for mapping in gateway.mappings_by_seq.values():
                mapped_order = await session.scalar(
                    select(OmsOrderRecord).where(
                        OmsOrderRecord.client_order_id == mapping.client_order_id
                    )
                )
                if mapped_order is not None:
                    mapped_order.qmt_order_id = mapping.qmt_order_id
                    mapped_order.order_sysid = mapping.order_sysid
                    mapped_order.last_error = mapping.last_error
                    if mapping.qmt_order_id is not None:
                        remote = gateway.orders.get(str(mapping.qmt_order_id))
                        if remote is None and mapping.order_sysid:
                            remote = gateway.orders.get(mapping.order_sysid)
                        if remote is None:
                            remote = gateway.orders.get(f"qmt-seq-{mapping.qmt_seq}")
                        if remote is not None:
                            conflict = _merge_persisted_order_fact(mapped_order, remote)
                            if conflict is not None:
                                state_conflicts[mapped_order.order_id] = conflict
                                await _record_order_state_regression(
                                    session,
                                    mapped_order,
                                    conflict,
                                )
            invalid_orders = (
                await session.scalars(
                    select(OmsOrderRecord).where(
                        OmsOrderRecord.account_id == self.settings.qmt_account_id,
                        OmsOrderRecord.execution_mode == self.settings.execution_mode.value,
                        OmsOrderRecord.qmt_order_id == 0,
                        OmsOrderRecord.source == "qmt_order_query",
                    )
                )
            ).all()
            for invalid_order in invalid_orders:
                invalid_order.status = "QUARANTINED"
                invalid_order.source = "qmt_order_query_invalid"
                invalid_order.last_error = "broker order_id sentinel 0 is not an identity"
                quarantined = await session.scalar(
                    select(LedgerQuarantineRecord.id).where(
                        LedgerQuarantineRecord.original_table == "orders",
                        LedgerQuarantineRecord.original_id == str(invalid_order.id),
                    )
                )
                if quarantined is None:
                    session.add(
                        LedgerQuarantineRecord(
                            entity_type="order",
                            original_table="orders",
                            original_id=str(invalid_order.id),
                            reason="invalid broker order identity sentinel",
                            payload_json=json.dumps(
                                {"qmt_order_id": 0, "status": "QUARANTINED"},
                                ensure_ascii=False,
                            ),
                        )
                    )
            for remote in gateway.orders.values():
                broker_identity = remote.broker_order_id or remote.order_id
                if not broker_identity or broker_identity == "0":
                    continue
                qmt_order_id = remote.qmt_order_id
                if qmt_order_id is not None and remote.order_sysid:
                    identity_condition = or_(
                        OmsOrderRecord.qmt_order_id == qmt_order_id,
                        OmsOrderRecord.order_sysid == remote.order_sysid,
                    )
                elif qmt_order_id is not None:
                    identity_condition = OmsOrderRecord.qmt_order_id == qmt_order_id
                else:
                    assert remote.order_sysid
                    identity_condition = OmsOrderRecord.order_sysid == remote.order_sysid
                order_lookup = select(OmsOrderRecord).where(
                    OmsOrderRecord.account_id == self.settings.qmt_account_id,
                    identity_condition,
                )
                broker_order_row = await session.scalar(order_lookup)
                reported_at = remote.updated_at or remote.submitted_at
                broker_reported_at = reported_at.replace(tzinfo=None) if reported_at else None
                if broker_order_row is None:
                    client_order_id = remote.request.client_order_id
                    if client_order_id and client_order_id != broker_identity:
                        broker_order_row = await session.scalar(
                            select(OmsOrderRecord).where(
                                OmsOrderRecord.account_id == self.settings.qmt_account_id,
                                OmsOrderRecord.client_order_id == client_order_id,
                            )
                        )
                if broker_order_row is None:
                    account_digest = hashlib.sha256(
                        self.settings.qmt_account_id.encode("utf-8")
                    ).hexdigest()[:12]
                    broker_digest = hashlib.sha256(broker_identity.encode("utf-8")).hexdigest()[:16]
                    identity = f"{account_digest}-{broker_digest}"
                    broker_order_row = OmsOrderRecord(
                        order_id=f"qmt-broker-{identity}",
                        client_order_id=f"qmt-query-{identity}",
                        idempotency_key=f"qmt-order-query-{identity}",
                        account_id=self.settings.qmt_account_id,
                        strategy_id=remote.request.strategy_id or "external-broker",
                        run_id=None,
                        strategy_release_id=None,
                        signal_id=None,
                        parent_order_id=None,
                        symbol=remote.request.symbol,
                        direction=remote.request.direction.value,
                        quantity=remote.request.quantity,
                        filled_quantity=remote.filled_quantity,
                        limit_price=remote.request.limit_price,
                        average_fill_price=remote.average_fill_price,
                        status=remote.status.value,
                        execution_mode=self.settings.execution_mode.value,
                        qmt_seq=None,
                        qmt_order_id=qmt_order_id,
                        order_sysid=remote.order_sysid,
                        order_remark=(
                            remote.request.client_order_id
                            if remote.request.client_order_id != remote.order_id
                            else None
                        ),
                        last_error=None,
                        source="qmt_order_query",
                        data_origin="qmt_broker",
                        is_simulated=(
                            self.settings.execution_mode is not ExecutionMode.QMT_LIVE
                            or self.settings.qmt_account_is_simulated
                        ),
                        payload_json=json.dumps(
                            {"external_order": True, "data_origin": "qmt_order_query"},
                            ensure_ascii=False,
                        ),
                        created_at=(
                            remote.submitted_at.replace(tzinfo=None)
                            if remote.submitted_at
                            else datetime.now(UTC).replace(tzinfo=None)
                        ),
                        submitted_at=(
                            remote.submitted_at.replace(tzinfo=None)
                            if remote.submitted_at
                            else None
                        ),
                        broker_reported_at=broker_reported_at,
                    )
                    session.add(broker_order_row)
                    await session.flush()
                    session.add(
                        AuditEventRecord(
                            trace_id=f"qmt-order-query-{identity}",
                            event_type="qmt_external_order_observed",
                            actor="reconciliation",
                            object_id=broker_order_row.order_id,
                            payload_json=json.dumps(
                                {
                                    "broker_identity_kind": (
                                        "qmt_order_id"
                                        if qmt_order_id is not None
                                        else "order_sysid"
                                    ),
                                    "symbol": remote.request.symbol,
                                },
                                ensure_ascii=False,
                            ),
                        )
                    )
                else:
                    conflict = _merge_persisted_order_fact(broker_order_row, remote)
                    if conflict is not None:
                        state_conflicts[broker_order_row.order_id] = conflict
                        await _record_order_state_regression(
                            session,
                            broker_order_row,
                            conflict,
                        )
            for trade in gateway.trades.values():
                validate_broker_trade(trade)
                broker_identity = str(trade.broker_order_id)
                order_lookup = select(OmsOrderRecord).where(
                    OmsOrderRecord.account_id == trade.account_id,
                )
                if trade.qmt_order_id is not None:
                    order_lookup = order_lookup.where(
                        OmsOrderRecord.qmt_order_id == trade.qmt_order_id
                    )
                else:
                    order_lookup = order_lookup.where(
                        OmsOrderRecord.order_sysid == trade.order_sysid
                    )
                broker_order = await session.scalar(order_lookup)
                local_order_id = (
                    broker_order.order_id if broker_order is not None else trade.order_id
                )
                existing_trade = await session.scalar(
                    select(OmsTradeRecord).where(
                        OmsTradeRecord.account_id == trade.account_id,
                        OmsTradeRecord.broker_trade_id == trade.broker_trade_id,
                    )
                )
                if existing_trade is None:
                    session.add(
                        OmsTradeRecord(
                            trade_id=trade.trade_id,
                            order_id=local_order_id,
                            account_id=self.settings.qmt_account_id,
                            symbol=trade.symbol,
                            quantity=trade.quantity,
                            direction=trade.direction.value,
                            price=trade.price,
                            fees=trade.total_fees,
                            commission=trade.commission,
                            tax=trade.tax,
                            transfer_fee=trade.transfer_fee,
                            broker_reported_fee=trade.broker_reported_fee,
                            fee_source=trade.fee_source,
                            fee_breakdown_complete=trade.fee_breakdown_complete,
                            execution_mode=trade.execution_mode,
                            strategy_id=trade.strategy_id or None,
                            run_id=trade.run_id,
                            source=trade.source,
                            data_origin="qmt_broker",
                            is_simulated=trade.is_simulated,
                            broker_order_id=trade.broker_order_id,
                            qmt_order_id=trade.qmt_order_id,
                            order_sysid=trade.order_sysid,
                            broker_trade_id=trade.broker_trade_id,
                            broker_reported_at=(
                                trade.broker_reported_at.replace(tzinfo=None)
                                if trade.broker_reported_at is not None
                                else None
                            ),
                            timestamp=trade.timestamp.replace(tzinfo=None),
                        )
                    )
                else:
                    previous_order_id = existing_trade.order_id
                    previous_broker_order_id = existing_trade.broker_order_id
                    existing_trade.order_id = local_order_id
                    existing_trade.broker_order_id = trade.broker_order_id
                    existing_trade.qmt_order_id = trade.qmt_order_id
                    existing_trade.order_sysid = trade.order_sysid
                    existing_trade.data_origin = "qmt_broker"
                    if (
                        previous_order_id != local_order_id
                        or previous_broker_order_id != trade.broker_order_id
                    ):
                        session.add(
                            AuditEventRecord(
                                trace_id=f"qmt-trade-link-{trade.broker_trade_id}",
                                event_type="qmt_trade_order_link_repaired",
                                actor="reconciliation",
                                object_id=trade.trade_id,
                                payload_json=json.dumps(
                                    {
                                        "previous_order_id": previous_order_id,
                                        "previous_broker_order_id": previous_broker_order_id,
                                        "linked_order_id": local_order_id,
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                        )
                    if trade.broker_reported_fee is not None and (
                        existing_trade.broker_reported_fee != trade.broker_reported_fee
                        or existing_trade.fee_source != trade.fee_source
                        or existing_trade.fee_breakdown_complete
                    ):
                        previous_fee = existing_trade.broker_reported_fee
                        existing_trade.broker_reported_fee = trade.broker_reported_fee
                        existing_trade.fees = trade.total_fees
                        existing_trade.fee_source = trade.fee_source
                        existing_trade.fee_breakdown_complete = False
                        session.add(
                            AuditEventRecord(
                                trace_id=f"qmt-trade-fee-{trade.broker_trade_id}",
                                event_type="qmt_trade_fee_refreshed",
                                actor="reconciliation",
                                object_id=trade.trade_id,
                                payload_json=json.dumps(
                                    {
                                        "previous_broker_reported_fee": (
                                            str(previous_fee) if previous_fee is not None else None
                                        ),
                                        "broker_reported_fee": str(trade.broker_reported_fee),
                                        "fee_source": trade.fee_source,
                                        "fee_breakdown_complete": False,
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                        )
            await session.commit()
        self._last_persistence_conflicts = list(state_conflicts.values())

    async def _retry_locked(self, operation: Callable[[], Awaitable[None]]) -> None:
        for attempt in range(3):
            try:
                await operation()
                return
            except OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 2:
                    raise
                await asyncio.sleep(0.05 * (attempt + 1))

    def snapshot(self) -> dict[str, object]:
        if self.gateway is None:
            return {
                "execution_mode": self.settings.execution_mode.value,
                "connected": False,
                "subscribed": False,
                "account_healthy": False,
                "account": None,
                "positions": [],
                "orders": [],
                "trades": [],
                "last_error": self.last_error,
            }
        gateway = self.gateway
        account = gateway.account
        return {
            **gateway.status(),
            "assets": (
                {
                    "cash": api_decimal(quantize_money(account.cash)),
                    "available_cash": api_decimal(quantize_money(account.available_cash)),
                    "frozen_cash": api_decimal(quantize_money(account.frozen_cash)),
                    "positions_value": api_decimal(quantize_money(account.positions_value)),
                    "total_assets": api_decimal(quantize_money(account.total_assets)),
                }
                if account is not None
                else None
            ),
            "positions": [
                {
                    "symbol": item.symbol,
                    "quantity": item.quantity,
                    "available_quantity": item.available_quantity,
                    "average_cost": api_decimal(item.average_cost),
                    "last_price": api_decimal(item.last_price),
                    "market_value": api_decimal(quantize_money(item.market_value)),
                }
                for item in gateway.positions.values()
            ],
            "orders": [
                {
                    "order_id": item.order_id,
                    "broker_order_id": item.broker_order_id,
                    "qmt_order_id": item.qmt_order_id,
                    "order_sysid": item.order_sysid,
                    "client_order_id": item.request.client_order_id,
                    "symbol": item.request.symbol,
                    "direction": item.request.direction.value,
                    "quantity": item.request.quantity,
                    "filled_quantity": item.filled_quantity,
                    "status": item.status.value,
                }
                for item in gateway.orders.values()
            ],
            "trades": [
                {
                    "trade_id": item.trade_id,
                    "order_id": item.order_id,
                    "broker_order_id": item.broker_order_id,
                    "qmt_order_id": item.qmt_order_id,
                    "order_sysid": item.order_sysid,
                    "symbol": item.symbol,
                    "quantity": item.quantity,
                    "price": str(item.price),
                    "fees": api_decimal(item.total_fees),
                    "broker_reported_fee": (
                        api_decimal(item.broker_reported_fee)
                        if item.broker_reported_fee is not None
                        else None
                    ),
                    "fee_source": item.fee_source,
                    "fee_breakdown_complete": item.fee_breakdown_complete,
                    "timestamp": item.timestamp.isoformat(),
                }
                for item in gateway.trades.values()
            ],
            "last_reconciled_at": (
                self.last_reconciled_at.isoformat() if self.last_reconciled_at else None
            ),
            "last_reconciliation_attempt_at": (
                self.last_reconciliation_attempt_at.isoformat()
                if self.last_reconciliation_attempt_at
                else None
            ),
            "reconciliation": self.last_reconciliation,
            "last_error": self.last_error or gateway.last_error,
        }

    def require_gateway(self) -> QmtSimTradingGateway:
        if self.gateway is None:
            raise RuntimeError("QMT simulation gateway is not initialized")
        return self.gateway

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(2)
            try:
                gateway = self.require_gateway()
                if not gateway.connected or not gateway.subscribed:
                    reconnected = await asyncio.to_thread(gateway.reconnect)
                    if not reconnected:
                        self.last_error = f"{self.settings.execution_mode.value} reconnect failed"
                        continue
                await self.persist_snapshot()
            except Exception as exc:
                self.last_error = type(exc).__name__
