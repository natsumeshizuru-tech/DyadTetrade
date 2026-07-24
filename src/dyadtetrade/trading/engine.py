from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select

from dyadtetrade.core.config import ExecutionMode, Settings, get_settings
from dyadtetrade.core.risk_policy import (
    ExecutionRiskPolicy,
    load_risk_policy,
    risk_policy_path,
    risk_policy_snapshot,
    save_risk_policy,
)
from dyadtetrade.db.models import (
    AuditEventRecord,
    Instrument,
    OmsOrderRecord,
    StrategyExecutionReleaseRecord,
    TradingRiskStateRecord,
)
from dyadtetrade.db.session import SessionFactory, initialize_database
from dyadtetrade.domain import Direction, OrderRequest, OrderType, PriceType, RiskOperatingState
from dyadtetrade.domain.precision import quantize_price
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.integrations.xtquant_converters import tick_from_xtdata
from dyadtetrade.modules.execution.service import QmtSimRuntime
from dyadtetrade.modules.execution.universe import ExecutionUniverseService
from dyadtetrade.modules.risk import RiskStateMachine

CHINA_TZ = ZoneInfo("Asia/Shanghai")


class TradingEngineService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.runtime = QmtSimRuntime(self.settings)
        self.risk_policy_ceiling = ExecutionRiskPolicy.from_settings(self.settings)
        self.risk_policy = self.risk_policy_ceiling.model_copy()
        self.risk = RiskStateMachine()
        self.started_at: datetime | None = None
        self.execution_universe: dict[str, object] | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._resume_state = RiskOperatingState.NORMAL

    async def start(self) -> None:
        persisted_policy = load_risk_policy(risk_policy_path(self.settings))
        if persisted_policy is not None:
            persisted_policy.ensure_within(self.risk_policy_ceiling)
            persisted_policy.apply(self.settings)
            self.risk_policy = persisted_policy
        self.settings.validate_execution_safety()
        await initialize_database()
        await self._load_execution_universe()
        await self._load_risk_state()
        self._resume_state = self.risk.state
        self.risk.transition(RiskOperatingState.RECOVERY, "trading engine startup reconciliation")
        await self._persist_risk_state("system")
        try:
            await self.runtime.start()
            if self.settings.execution_mode in {ExecutionMode.QMT_SIM, ExecutionMode.QMT_LIVE}:
                runtime_status = self.runtime.snapshot()
                if not all(
                    bool(runtime_status.get(key))
                    for key in ("connected", "subscribed", "account_healthy")
                ):
                    raise RuntimeError(
                        "QMT startup reconciliation did not produce a healthy account"
                    )
                await self._finish_reconciliation(
                    self._resume_state,
                    completed_reason="startup reconciliation completed",
                    actor="system",
                )
        except BaseException:
            await self._restore_restricted_state_after_failure(
                self._resume_state,
                reason="startup reconciliation failed",
                actor="system",
            )
            await self.runtime.stop()
            raise
        self.started_at = datetime.now(UTC)
        self._watchdog = asyncio.create_task(self._watchdog_loop(), name="trading-watchdog")

    async def stop(self) -> None:
        if self._watchdog is not None:
            self._watchdog.cancel()
            try:
                await self._watchdog
            except asyncio.CancelledError:
                pass
            self._watchdog = None
        if self.settings.execution_mode in {ExecutionMode.QMT_SIM, ExecutionMode.QMT_LIVE}:
            if self.risk.state is RiskOperatingState.NORMAL:
                self.risk.transition(
                    RiskOperatingState.RECOVERY, "trading engine graceful shutdown"
                )
                self._apply_risk_state()
                await self._persist_risk_state("system")
        await self.runtime.stop()

    def status(self) -> dict[str, object]:
        return {
            **self.runtime.snapshot(),
            "risk_state": self.risk.snapshot(),
            "engine_online": True,
            "engine_pid": os.getpid(),
            "engine_role": "single-qmt-gateway",
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "execution_universe": self.execution_universe,
            "risk_policy": risk_policy_snapshot(
                self.risk_policy,
                self.risk_policy_ceiling,
                editable=self.risk.state
                in {RiskOperatingState.HALTED, RiskOperatingState.RECOVERY},
            ),
        }

    async def update_risk_policy(
        self, policy: ExecutionRiskPolicy, actor: str
    ) -> dict[str, object]:
        if self.risk.state not in {RiskOperatingState.HALTED, RiskOperatingState.RECOVERY}:
            raise PermissionError(
                "risk limits can only change while risk state is HALTED or RECOVERY"
            )
        policy.ensure_within(self.risk_policy_ceiling)
        policy.apply(self.settings)
        save_risk_policy(risk_policy_path(self.settings), policy, actor=actor)
        self.risk_policy = policy
        async with SessionFactory() as session:
            session.add(
                AuditEventRecord(
                    trace_id=f"risk-policy-{datetime.now(UTC).timestamp()}",
                    event_type="execution_risk_policy_changed",
                    actor=actor,
                    object_id=self._account_key,
                    payload_json=json.dumps(policy.api_dict(), ensure_ascii=False),
                )
            )
            await session.commit()
        return self.status()

    async def reconcile(self) -> dict[str, object]:
        resume_state = self.risk.state
        self.risk.transition(RiskOperatingState.RECOVERY, "manual reconciliation")
        self._apply_risk_state()
        await self._persist_risk_state("operator")
        try:
            await self.runtime.reconcile()
        except BaseException:
            await self._restore_restricted_state_after_failure(
                resume_state,
                reason="manual reconciliation failed",
                actor="system",
            )
            raise
        await self._finish_reconciliation(
            resume_state,
            completed_reason="manual reconciliation completed",
            actor="system",
        )
        return self.status()

    async def transition_risk(
        self, target: RiskOperatingState, reason: str, actor: str
    ) -> dict[str, object]:
        self.risk.transition(
            target,
            reason,
            reconciled=(
                target is not RiskOperatingState.RECOVERY and self.risk.reconciled_at is not None
            ),
        )
        self._apply_risk_state()
        await self._persist_risk_state(actor)
        return self.status()

    async def submit_order(self, payload: dict[str, object]) -> dict[str, object]:
        settings = self.settings
        mode = settings.execution_mode
        automated = bool(payload.get("automated"))
        strategy_release: StrategyExecutionReleaseRecord | None = None
        if automated:
            strategy_release = await self._validate_automated_strategy_release(payload)
        else:
            if mode is ExecutionMode.QMT_SIM and not bool(payload.get("confirm_simulated_order")):
                raise PermissionError("explicit simulated-order confirmation is required")
            if mode is ExecutionMode.QMT_LIVE and (
                not bool(payload.get("confirm_live_order"))
                or payload.get("live_confirmation") != "I_CONFIRM_QMT_LIVE_ORDER"
            ):
                raise PermissionError(
                    "daily release plus explicit live-order confirmation is required"
                )
        client_order_id = str(payload["client_order_id"]).strip()
        idempotency_key = str(payload["idempotency_key"]).strip()
        if not 1 <= len(client_order_id) <= 48:
            raise ValueError("client_order_id must contain 1 to 48 characters")
        if not 1 <= len(idempotency_key) <= 64:
            raise ValueError("idempotency_key must contain 1 to 64 characters")
        direction = Direction(str(payload["direction"]))
        gateway = self.runtime.require_gateway()
        symbol = str(payload["symbol"])
        quantity_value = payload["quantity"]
        if not isinstance(quantity_value, (int, str)):
            raise ValueError("quantity must be an integer")
        quantity = int(quantity_value)
        async with SessionFactory() as session:
            instrument = await session.get(Instrument, symbol)
        price_tick = (
            instrument.price_tick
            if instrument is not None and instrument.price_tick is not None
            else Decimal("0.01")
        )
        lot_size = (
            100
            if instrument is not None and instrument.asset_type in {"stock", "etf"}
            else instrument.lot_size
            if instrument is not None and instrument.lot_size is not None
            else 100
        )
        limit_price = quantize_price(payload["limit_price"], price_tick)
        notional = limit_price * quantity
        if strategy_release is not None:
            if notional > strategy_release.max_order_notional:
                raise PermissionError("automated strategy order notional exceeds release limit")
            await self._check_strategy_release_daily_notional(strategy_release, notional)
        position = gateway.positions.get(symbol)
        is_full_odd_lot_exit = (
            direction is Direction.SELL
            and position is not None
            and quantity == position.available_quantity
        )
        if quantity % lot_size != 0 and not is_full_odd_lot_exit:
            raise ValueError(f"quantity must be an integer multiple of lot size {lot_size}")
        reduces = (
            direction is Direction.SELL and position is not None and quantity <= position.quantity
        )
        if not self.risk.allows_order(direction, reduces):
            raise PermissionError(f"risk state {self.risk.state.value} rejects new orders")
        quote = XtQuantGateway().full_tick([symbol]).get(symbol)
        if not isinstance(quote, dict):
            raise RuntimeError("real-time market snapshot is unavailable")
        tick = tick_from_xtdata(symbol, quote, received_at=datetime.now(UTC))
        gateway.update_market(tick)
        request = OrderRequest(
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            order_type=OrderType.LIMIT,
            price_type=PriceType.LIMIT,
            limit_price=limit_price,
            strategy_id=str(payload.get("strategy_id") or "manual"),
            account_id=settings.qmt_account_id,
            client_order_id=client_order_id,
            idempotency_key=idempotency_key,
            created_at=datetime.now(UTC),
            strategy_release_id=str(payload.get("strategy_release_id") or "") or None,
            signal_id=str(payload.get("signal_id") or "") or None,
        )
        submission = gateway.submit_async(request)
        await self.runtime.persist_submission(submission)
        return {
            "client_order_id": submission.mapping.client_order_id,
            "qmt_seq": submission.mapping.qmt_seq,
            "status": submission.order.status.value,
            "execution_mode": settings.execution_mode.value,
        }

    async def cancel_order(self, qmt_order_id: int, confirmed: bool) -> dict[str, object]:
        if not confirmed:
            raise PermissionError("explicit cancel confirmation is required")
        result = self.runtime.require_gateway().cancel(qmt_order_id)
        await self.runtime.persist_snapshot()
        return {"qmt_order_id": qmt_order_id, "cancel_result": result}

    def release_gate(self) -> dict[str, object]:
        settings = self.settings
        checks = {
            "execution_mode_qmt_live": settings.execution_mode is ExecutionMode.QMT_LIVE,
            "enable_live_trading": settings.enable_live_trading,
            "enable_qmt_live_trading": settings.enable_qmt_live_trading,
            "account_whitelisted": bool(settings.qmt_account_id)
            and settings.qmt_account_id in settings.qmt_live_account_whitelist,
            "userdata_exists": settings.qmt_userdata_path is not None
            and settings.qmt_userdata_path.exists(),
            "symbol_whitelist": bool(settings.allowed_symbols),
            "daily_confirmation": settings.qmt_live_confirmation == "I_CONFIRM_QMT_LIVE",
            "daily_release_date": settings.qmt_live_release_date
            == datetime.now(CHINA_TZ).date().isoformat(),
            "risk_state_normal": self.risk.state is RiskOperatingState.NORMAL,
            "dynamic_universe_loaded": not settings.enable_dynamic_execution_universe
            or self.execution_universe is not None,
            "full_market_daily_release": not settings.enable_dynamic_execution_universe
            or (
                settings.enable_full_market_live_trading
                and settings.full_market_live_confirmation == "I_CONFIRM_FULL_MARKET_LIVE_UNIVERSE"
                and settings.full_market_live_release_date
                == datetime.now(CHINA_TZ).date().isoformat()
            ),
            "automated_live_daily_release": not settings.enable_automated_strategy_execution
            or (
                settings.automated_live_confirmation == "I_CONFIRM_AUTOMATED_LIVE_EXECUTION"
                and settings.automated_live_release_date
                == datetime.now(CHINA_TZ).date().isoformat()
            ),
        }
        return {
            "ready": all(checks.values()),
            "checks": checks,
            "account": "configured" if settings.qmt_account_id else "missing",
            "secrets_exposed": False,
            "execution_universe": self.execution_universe,
            "automated_strategy_execution_enabled": settings.enable_automated_strategy_execution,
        }

    async def _load_execution_universe(self) -> None:
        if not self.settings.enable_dynamic_execution_universe:
            self.execution_universe = None
            return
        async with SessionFactory() as session:
            symbols, snapshot = await ExecutionUniverseService.load_active_symbols(
                session,
                self.settings.execution_universe_release_id,
                execution_mode=self.settings.execution_mode,
            )
        self.settings.allowed_symbols = sorted(set(self.settings.allowed_symbols) | set(symbols))
        self.execution_universe = snapshot

    async def _validate_automated_strategy_release(
        self, payload: dict[str, object]
    ) -> StrategyExecutionReleaseRecord:
        if not self.settings.enable_automated_strategy_execution:
            raise PermissionError("automated strategy execution is disabled")
        release_id = str(payload.get("strategy_release_id") or "")
        signal_id = str(payload.get("signal_id") or "")
        if not release_id or not signal_id:
            raise PermissionError("automated order requires strategy release and signal identity")
        async with SessionFactory() as session:
            row = await session.scalar(
                select(StrategyExecutionReleaseRecord).where(
                    StrategyExecutionReleaseRecord.release_id == release_id
                )
            )
        if row is None or row.status != "active":
            raise PermissionError("automated strategy release is not active")
        today = datetime.now(CHINA_TZ).strftime("%Y%m%d")
        if row.trade_date != today or row.execution_mode != self.settings.execution_mode.value:
            raise PermissionError("automated strategy release is not valid for this session")
        if row.expires_at is None or row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
            raise PermissionError("automated strategy release has expired")
        if row.strategy_id != str(payload.get("strategy_id") or ""):
            raise PermissionError("automated strategy identity does not match release")
        if row.strategy_version != str(payload.get("strategy_version") or ""):
            raise PermissionError("automated strategy version does not match release")
        if row.parameters_hash != str(payload.get("parameters_hash") or ""):
            raise PermissionError("automated strategy parameters do not match release")
        if row.universe_release_id != self.settings.execution_universe_release_id:
            raise PermissionError("automated strategy universe does not match trading engine")
        return row

    async def _check_strategy_release_daily_notional(
        self, release: StrategyExecutionReleaseRecord, proposed: Decimal
    ) -> None:
        day_start = (
            datetime.combine(datetime.now(CHINA_TZ).date(), datetime.min.time(), CHINA_TZ)
            .astimezone(UTC)
            .replace(tzinfo=None)
        )
        async with SessionFactory() as session:
            rows = (
                await session.scalars(
                    select(OmsOrderRecord).where(
                        OmsOrderRecord.strategy_id == release.strategy_id,
                        OmsOrderRecord.execution_mode == release.execution_mode,
                        OmsOrderRecord.created_at >= day_start,
                    )
                )
            ).all()
        used = sum(
            (
                Decimal(str(json.loads(row.payload_json or "{}").get("limit_price") or 0))
                * row.quantity
                for row in rows
            ),
            Decimal("0"),
        )
        if used + proposed > release.max_daily_notional:
            raise PermissionError("automated strategy daily release limit exceeded")

    async def _load_risk_state(self) -> None:
        async with SessionFactory() as session:
            row = await session.scalar(
                select(TradingRiskStateRecord).where(
                    TradingRiskStateRecord.account_key == self._account_key,
                    TradingRiskStateRecord.execution_mode == self.settings.execution_mode.value,
                )
            )
        if row is None:
            return
        self.risk = RiskStateMachine(
            state=RiskOperatingState(row.state),
            reason=row.reason,
            version=row.version,
            changed_at=row.changed_at.replace(tzinfo=UTC),
            reconciled_at=(row.reconciled_at.replace(tzinfo=UTC) if row.reconciled_at else None),
        )

    async def _persist_risk_state(self, actor: str) -> None:
        async with SessionFactory() as session:
            row = await session.scalar(
                select(TradingRiskStateRecord).where(
                    TradingRiskStateRecord.account_key == self._account_key,
                    TradingRiskStateRecord.execution_mode == self.settings.execution_mode.value,
                )
            )
            if row is None:
                row = TradingRiskStateRecord(
                    account_key=self._account_key,
                    execution_mode=self.settings.execution_mode.value,
                )
                session.add(row)
            row.state = self.risk.state.value
            row.reason = self.risk.reason
            row.version = self.risk.version
            row.changed_by = actor
            row.changed_at = self.risk.changed_at.replace(tzinfo=None)
            row.reconciled_at = (
                self.risk.reconciled_at.replace(tzinfo=None) if self.risk.reconciled_at else None
            )
            session.add(
                AuditEventRecord(
                    trace_id=f"risk-state-{self.risk.version}",
                    event_type="trading_risk_state_changed",
                    actor=actor,
                    object_id=self._account_key,
                    payload_json=json.dumps(self.risk.snapshot(), ensure_ascii=False),
                )
            )
            await session.commit()

    def _apply_risk_state(self) -> None:
        gateway = self.runtime.gateway
        if gateway is not None:
            self.risk.apply_to(gateway.safety.switches)

    async def _finish_reconciliation(
        self,
        resume_state: RiskOperatingState,
        *,
        completed_reason: str,
        actor: str,
    ) -> bool:
        report = self.runtime.last_reconciliation
        consistent = isinstance(report, dict) and report.get("consistent") is True
        if not consistent:
            local_count = (
                report.get("unmatched_local_active_count", "unknown") if report else "unknown"
            )
            broker_count = (
                report.get("unmatched_broker_active_count", "unknown") if report else "unknown"
            )
            state_conflict_count = (
                report.get("order_state_conflict_count", "unknown") if report else "unknown"
            )
            gateway_health = (
                report.get("gateway_health_consistent", "unknown") if report else "unknown"
            )
            reason = (
                f"reconciliation mismatch: {local_count} local active and "
                f"{broker_count} broker active unmatched; "
                f"{state_conflict_count} order state conflicts; "
                f"gateway health consistent={gateway_health}"
            )
            self.risk.transition(RiskOperatingState.RECOVERY, reason)
            self.risk.transition(
                RiskOperatingState.HALTED,
                f"{reason}; fail-closed HALTED",
            )
            self._apply_risk_state()
            await self._persist_risk_state(actor)
            return False

        self.risk.mark_reconciled()
        target = (
            resume_state
            if resume_state
            in {
                RiskOperatingState.REDUCE_ONLY,
                RiskOperatingState.CANCEL_ONLY,
                RiskOperatingState.HALTED,
            }
            else RiskOperatingState.NORMAL
        )
        reason = completed_reason
        if target is not RiskOperatingState.NORMAL:
            reason = f"{completed_reason}; restored {target.value}"
        self.risk.transition(target, reason, reconciled=True)
        self._apply_risk_state()
        await self._persist_risk_state(actor)
        return True

    async def _restore_restricted_state_after_failure(
        self,
        resume_state: RiskOperatingState,
        *,
        reason: str,
        actor: str,
    ) -> None:
        if self.risk.state is RiskOperatingState.RECOVERY:
            self.risk.transition(RiskOperatingState.RECOVERY, reason)
            if resume_state in {
                RiskOperatingState.REDUCE_ONLY,
                RiskOperatingState.CANCEL_ONLY,
                RiskOperatingState.HALTED,
            }:
                self.risk.transition(
                    resume_state,
                    f"{reason}; restored {resume_state.value}",
                )
        self._apply_risk_state()
        await self._persist_risk_state(actor)

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            try:
                await self._watchdog_once()
            except Exception as exc:
                self.runtime.last_error = f"watchdog: {type(exc).__name__}"

    async def _watchdog_once(self) -> None:
        gateway = self.runtime.gateway
        if gateway is None:
            return
        disconnected = not gateway.connected or not gateway.subscribed
        recovery_required = bool(getattr(gateway, "recovery_required", False))
        if disconnected or recovery_required:
            if self.risk.state not in {
                RiskOperatingState.HALTED,
                RiskOperatingState.RECOVERY,
            }:
                self._resume_state = self.risk.state
                recovery_reason = str(
                    getattr(gateway, "recovery_reason", None)
                    or ("gateway disconnected" if disconnected else "gateway recovery required")
                )
                reason = f"{recovery_reason}; reconciliation required"
                self.risk.transition(RiskOperatingState.RECOVERY, reason)
                self._apply_risk_state()
                await self._persist_risk_state("watchdog")
            if disconnected:
                return
            if self.risk.state is RiskOperatingState.RECOVERY:
                await self.runtime.reconcile()
                await self._finish_reconciliation(
                    self._resume_state,
                    completed_reason="gateway reconnected and reconciled",
                    actor="watchdog",
                )

    @property
    def _account_key(self) -> str:
        value = self.settings.qmt_account_id
        return f"***{value[-4:]}" if value else "unconfigured"
