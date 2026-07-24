from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.core.config import ExecutionMode
from dyadtetrade.db.models import (
    AuditEventRecord,
    DataFile,
    ExecutionUniverseMemberRecord,
    ExecutionUniverseReleaseRecord,
    Instrument,
    StrategyExecutionReleaseRecord,
    StrategyFileRecord,
    TradingDay,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")
SUPPORTED_ASSETS = frozenset({"stock", "etf"})
SUPPORTED_EXCHANGES = frozenset({"SH", "SZ", "BJ"})


@dataclass(frozen=True, slots=True)
class ExecutionUniverseFilters:
    asset_types: tuple[str, ...] = ("stock", "etf")
    exchanges: tuple[str, ...] = ("SH", "SZ", "BJ")
    exclude_st: bool = True
    exclude_delisting: bool = True
    minimum_listed_days: int = 20
    require_daily_data: bool = True
    enforce_a_share_symbol_family: bool = True


class ExecutionUniverseService:
    @staticmethod
    async def build(
        session: AsyncSession,
        *,
        execution_mode: ExecutionMode,
        filters: ExecutionUniverseFilters,
        observed_at: datetime | None = None,
    ) -> dict[str, object]:
        now = (observed_at or datetime.now(UTC)).astimezone(CHINA_TZ)
        trade_date = now.strftime("%Y%m%d")
        required_data_date = await _required_data_date(session, now)
        asset_types = set(filters.asset_types) & SUPPORTED_ASSETS
        exchanges = set(filters.exchanges) & SUPPORTED_EXCHANGES
        if not asset_types or not exchanges:
            raise ValueError("execution universe requires supported assets and exchanges")
        if filters.minimum_listed_days < 0:
            raise ValueError("minimum_listed_days cannot be negative")

        latest_rows = (
            await session.execute(
                select(DataFile.symbol, func.max(DataFile.end_date))
                .where(
                    DataFile.period == "1d",
                    DataFile.layer.in_(("trusted", "bt_ready")),
                    DataFile.rows > 0,
                )
                .group_by(DataFile.symbol)
            )
        ).all()
        latest_by_symbol = {str(symbol): str(latest or "") for symbol, latest in latest_rows}
        instruments = (
            await session.scalars(
                select(Instrument)
                .where(
                    Instrument.is_active.is_(True),
                    Instrument.asset_type.in_(asset_types),
                    Instrument.exchange.in_(exchanges),
                )
                .order_by(Instrument.symbol)
            )
        ).all()
        cutoff = (now.date() - timedelta(days=filters.minimum_listed_days)).strftime("%Y%m%d")
        accepted: list[tuple[Instrument, str]] = []
        rejected: dict[str, int] = {}
        for instrument in instruments:
            latest = latest_by_symbol.get(instrument.symbol, "")
            reason = _instrument_rejection(
                instrument,
                latest_date=latest,
                required_data_date=required_data_date,
                listed_cutoff=cutoff,
                filters=filters,
            )
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
            else:
                accepted.append((instrument, latest))
        if not accepted:
            raise RuntimeError("execution universe build produced no eligible symbols")

        release_id = f"universe-{trade_date}-{uuid4().hex[:12]}"
        filter_payload = asdict(filters)
        snapshot_hash = _universe_hash(
            [instrument.symbol for instrument, _ in accepted],
            trade_date=trade_date,
            required_data_date=required_data_date,
            filters=filter_payload,
        )
        session.add(
            ExecutionUniverseReleaseRecord(
                release_id=release_id,
                trade_date=trade_date,
                execution_mode=execution_mode.value,
                scope="a_share_and_etf",
                status="draft",
                symbol_count=len(accepted),
                snapshot_hash=snapshot_hash,
                required_data_date=required_data_date,
                filters_json=json.dumps(filter_payload, ensure_ascii=False, sort_keys=True),
                rejection_counts_json=json.dumps(rejected, ensure_ascii=False, sort_keys=True),
            )
        )
        session.add_all(
            [
                ExecutionUniverseMemberRecord(
                    release_id=release_id,
                    symbol=instrument.symbol,
                    exchange=instrument.exchange,
                    asset_type=instrument.asset_type,
                    name=instrument.name,
                    data_latest_date=latest,
                    quality_status="passed",
                )
                for instrument, latest in accepted
            ]
        )
        session.add(
            _audit(
                release_id,
                "execution_universe_built",
                "operator",
                {
                    "symbol_count": len(accepted),
                    "snapshot_hash": snapshot_hash,
                },
            )
        )
        await session.commit()
        return await ExecutionUniverseService.get(session, release_id)

    @staticmethod
    async def review(session: AsyncSession, release_id: str, *, reviewer: str) -> dict[str, object]:
        row, symbols = await _universe_row_and_symbols(session, release_id)
        if row.status != "draft":
            raise ValueError("only a draft execution universe can be reviewed")
        expected = _universe_hash(
            symbols,
            trade_date=row.trade_date,
            required_data_date=row.required_data_date,
            filters=dict(json.loads(row.filters_json)),
        )
        if len(symbols) != row.symbol_count or expected != row.snapshot_hash:
            raise RuntimeError("execution universe snapshot integrity check failed")
        row.status = "reviewed"
        row.reviewed_by = reviewer
        row.reviewed_at = datetime.now(UTC).replace(tzinfo=None)
        await session.commit()
        return await ExecutionUniverseService.get(session, release_id)

    @staticmethod
    async def activate(
        session: AsyncSession,
        release_id: str,
        *,
        approver: str,
        confirmation: str,
        observed_at: datetime | None = None,
    ) -> dict[str, object]:
        row, symbols = await _universe_row_and_symbols(session, release_id)
        now = (observed_at or datetime.now(UTC)).astimezone(CHINA_TZ)
        if row.status != "reviewed":
            raise ValueError("execution universe must be reviewed before activation")
        if row.trade_date != now.strftime("%Y%m%d"):
            raise ValueError("execution universe is not for the current trade date")
        required = (
            "I_CONFIRM_FULL_MARKET_LIVE_UNIVERSE"
            if row.execution_mode == ExecutionMode.QMT_LIVE.value
            else "I_CONFIRM_QMT_SIM_UNIVERSE"
        )
        if confirmation != required:
            raise PermissionError("execution universe activation confirmation failed")
        if len(symbols) != row.symbol_count:
            raise RuntimeError("execution universe member count changed after review")
        await session.execute(
            update(ExecutionUniverseReleaseRecord)
            .where(
                ExecutionUniverseReleaseRecord.execution_mode == row.execution_mode,
                ExecutionUniverseReleaseRecord.trade_date == row.trade_date,
                ExecutionUniverseReleaseRecord.status == "active",
                ExecutionUniverseReleaseRecord.release_id != release_id,
            )
            .values(status="revoked")
        )
        expires = datetime.combine(now.date() + timedelta(days=1), time.min, CHINA_TZ)
        row.status = "active"
        row.approved_by = approver
        row.activated_at = now.astimezone(UTC).replace(tzinfo=None)
        row.expires_at = expires.astimezone(UTC).replace(tzinfo=None)
        session.add(
            _audit(
                release_id,
                "execution_universe_activated",
                approver,
                {
                    "execution_mode": row.execution_mode,
                    "symbol_count": row.symbol_count,
                },
            )
        )
        await session.commit()
        return await ExecutionUniverseService.get(session, release_id)

    @staticmethod
    async def revoke(session: AsyncSession, release_id: str, *, actor: str) -> dict[str, object]:
        row = await _universe_row(session, release_id)
        row.status = "revoked"
        session.add(_audit(release_id, "execution_universe_revoked", actor, {}))
        await session.commit()
        return _universe_snapshot(row)

    @staticmethod
    async def get(session: AsyncSession, release_id: str) -> dict[str, object]:
        return _universe_snapshot(await _universe_row(session, release_id))

    @staticmethod
    async def latest(session: AsyncSession) -> dict[str, object] | None:
        row = await session.scalar(
            select(ExecutionUniverseReleaseRecord).order_by(
                ExecutionUniverseReleaseRecord.created_at.desc()
            )
        )
        return _universe_snapshot(row) if row else None

    @staticmethod
    async def latest_active(
        session: AsyncSession,
        *,
        execution_mode: ExecutionMode,
        observed_at: datetime | None = None,
    ) -> dict[str, object] | None:
        now = (observed_at or datetime.now(UTC)).astimezone(CHINA_TZ)
        row = await session.scalar(
            select(ExecutionUniverseReleaseRecord)
            .where(
                ExecutionUniverseReleaseRecord.execution_mode == execution_mode.value,
                ExecutionUniverseReleaseRecord.trade_date == now.strftime("%Y%m%d"),
                ExecutionUniverseReleaseRecord.status == "active",
            )
            .order_by(ExecutionUniverseReleaseRecord.activated_at.desc())
        )
        if row is None or row.expires_at is None:
            return None
        if row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
            return None
        return _universe_snapshot(row)

    @staticmethod
    async def load_active_symbols(
        session: AsyncSession,
        release_id: str,
        *,
        execution_mode: ExecutionMode,
        observed_at: datetime | None = None,
    ) -> tuple[frozenset[str], dict[str, object]]:
        row, symbols = await _universe_row_and_symbols(session, release_id)
        now = (observed_at or datetime.now(UTC)).astimezone(CHINA_TZ)
        if row.status != "active" or row.execution_mode != execution_mode.value:
            raise RuntimeError("execution universe is not active for this trading mode")
        if row.trade_date != now.strftime("%Y%m%d"):
            raise RuntimeError("execution universe is not valid for today")
        if row.expires_at is None or row.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC):
            raise RuntimeError("execution universe has expired")
        expected = _universe_hash(
            symbols,
            trade_date=row.trade_date,
            required_data_date=row.required_data_date,
            filters=dict(json.loads(row.filters_json)),
        )
        if expected != row.snapshot_hash or len(symbols) != row.symbol_count:
            raise RuntimeError("execution universe integrity validation failed")
        return frozenset(symbols), _universe_snapshot(row)


class StrategyExecutionReleaseService:
    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        execution_mode: ExecutionMode,
        strategy_id: str,
        strategy_name: str,
        strategy_version: str,
        universe_release_id: str,
        parameters: dict[str, object],
        max_order_notional: Decimal,
        max_daily_notional: Decimal,
    ) -> dict[str, object]:
        if max_order_notional <= 0 or max_daily_notional <= 0:
            raise ValueError("strategy execution limits must be positive")
        if max_order_notional > max_daily_notional:
            raise ValueError("strategy order limit cannot exceed daily limit")
        universe = await _universe_row(session, universe_release_id)
        if universe.status != "active" or universe.execution_mode != execution_mode.value:
            raise ValueError("strategy release requires an active matching universe")
        now = datetime.now(UTC)
        if (
            universe.trade_date != now.astimezone(CHINA_TZ).strftime("%Y%m%d")
            or universe.expires_at is None
            or universe.expires_at.replace(tzinfo=UTC) <= now
        ):
            raise ValueError("strategy release requires today's unexpired execution universe")
        if execution_mode is ExecutionMode.QMT_LIVE:
            if strategy_name == "tick_momentum_small_cap":
                raise PermissionError("tick_momentum_small_cap remains paper-only")
            strategy_file = await session.scalar(
                select(StrategyFileRecord).where(StrategyFileRecord.name == strategy_name)
            )
            if (
                strategy_file is None
                or strategy_file.smoke_status != "passed"
                or strategy_file.published_version is None
                or str(strategy_file.published_version) != strategy_version
            ):
                raise PermissionError("live strategy must be published and smoke-tested")
        parameter_hash = hashlib.sha256(
            json.dumps(parameters, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()
        release_id = f"strategy-release-{uuid4().hex}"
        session.add(
            StrategyExecutionReleaseRecord(
                release_id=release_id,
                trade_date=universe.trade_date,
                execution_mode=execution_mode.value,
                strategy_id=strategy_id,
                strategy_name=strategy_name,
                strategy_version=strategy_version,
                universe_release_id=universe_release_id,
                parameters_hash=parameter_hash,
                status="draft",
                max_order_notional=max_order_notional,
                max_daily_notional=max_daily_notional,
            )
        )
        await session.commit()
        return await StrategyExecutionReleaseService.get(session, release_id)

    @staticmethod
    async def activate(
        session: AsyncSession,
        release_id: str,
        *,
        approver: str,
        confirmation: str,
        observed_at: datetime | None = None,
    ) -> dict[str, object]:
        row = await _strategy_release_row(session, release_id)
        now = (observed_at or datetime.now(UTC)).astimezone(CHINA_TZ)
        required = (
            "I_CONFIRM_AUTOMATED_LIVE_STRATEGY"
            if row.execution_mode == ExecutionMode.QMT_LIVE.value
            else "I_CONFIRM_AUTOMATED_QMT_SIM_STRATEGY"
        )
        if confirmation != required:
            raise PermissionError("automated strategy activation confirmation failed")
        if row.status != "draft" or row.trade_date != now.strftime("%Y%m%d"):
            raise ValueError("strategy release is not an activatable draft for today")
        universe = await _universe_row(session, row.universe_release_id)
        if (
            universe.status != "active"
            or universe.execution_mode != row.execution_mode
            or universe.trade_date != row.trade_date
            or universe.expires_at is None
            or universe.expires_at.replace(tzinfo=UTC) <= datetime.now(UTC)
        ):
            raise ValueError("strategy release execution universe is no longer active")
        await session.execute(
            update(StrategyExecutionReleaseRecord)
            .where(
                StrategyExecutionReleaseRecord.strategy_id == row.strategy_id,
                StrategyExecutionReleaseRecord.execution_mode == row.execution_mode,
                StrategyExecutionReleaseRecord.trade_date == row.trade_date,
                StrategyExecutionReleaseRecord.status == "active",
                StrategyExecutionReleaseRecord.release_id != release_id,
            )
            .values(status="revoked")
        )
        expires = datetime.combine(now.date() + timedelta(days=1), time.min, CHINA_TZ)
        row.status = "active"
        row.approved_by = approver
        row.activated_at = now.astimezone(UTC).replace(tzinfo=None)
        row.expires_at = expires.astimezone(UTC).replace(tzinfo=None)
        session.add(
            _audit(
                release_id,
                "automated_strategy_release_activated",
                approver,
                {
                    "strategy_id": row.strategy_id,
                    "universe_release_id": row.universe_release_id,
                },
            )
        )
        await session.commit()
        return await StrategyExecutionReleaseService.get(session, release_id)

    @staticmethod
    async def get(session: AsyncSession, release_id: str) -> dict[str, object]:
        return _strategy_snapshot(await _strategy_release_row(session, release_id))

    @staticmethod
    async def revoke(session: AsyncSession, release_id: str, *, actor: str) -> dict[str, object]:
        row = await _strategy_release_row(session, release_id)
        row.status = "revoked"
        session.add(_audit(release_id, "automated_strategy_release_revoked", actor, {}))
        await session.commit()
        return _strategy_snapshot(row)

    @staticmethod
    async def list(session: AsyncSession) -> list[dict[str, object]]:
        rows = (
            await session.scalars(
                select(StrategyExecutionReleaseRecord)
                .order_by(StrategyExecutionReleaseRecord.created_at.desc())
                .limit(100)
            )
        ).all()
        return [_strategy_snapshot(row) for row in rows]


async def _required_data_date(session: AsyncSession, now: datetime) -> str:
    current = now.strftime("%Y%m%d")
    statement = select(func.max(TradingDay.trade_date)).where(
        TradingDay.market == "SH",
        TradingDay.trade_date <= current
        if now.time() >= time(15, 10)
        else TradingDay.trade_date < current,
    )
    value = await session.scalar(statement)
    fallback = now.date() if now.time() >= time(15, 10) else now.date() - timedelta(days=1)
    return str(value) if value else fallback.strftime("%Y%m%d")


def _instrument_rejection(
    instrument: Instrument,
    *,
    latest_date: str,
    required_data_date: str,
    listed_cutoff: str,
    filters: ExecutionUniverseFilters,
) -> str | None:
    name = (instrument.name or "").upper()
    if filters.enforce_a_share_symbol_family and not _is_supported_symbol_family(instrument):
        return "unsupported_symbol_family"
    if filters.exclude_st and ("ST" in name or "ＳＴ" in name):
        return "st"
    if filters.exclude_delisting and (
        "退" in name
        or bool(
            instrument.delist_date
            and instrument.delist_date != "99999999"
            and instrument.delist_date <= required_data_date
        )
    ):
        return "delisting"
    if instrument.list_date and instrument.list_date > listed_cutoff:
        return "recent_listing"
    if not instrument.lot_size or instrument.lot_size <= 0:
        return "invalid_lot_size"
    if instrument.price_tick is None or instrument.price_tick <= 0:
        return "invalid_price_tick"
    if filters.require_daily_data and not latest_date:
        return "missing_daily_data"
    if filters.require_daily_data and latest_date < required_data_date:
        return "stale_daily_data"
    return None


def _is_supported_symbol_family(instrument: Instrument) -> bool:
    code = instrument.symbol.split(".", 1)[0]
    if instrument.asset_type == "stock":
        prefixes = {
            "SH": ("600", "601", "603", "605", "688", "689"),
            "SZ": ("000", "001", "002", "003", "300", "301"),
            "BJ": ("43", "83", "87", "88", "92"),
        }
    elif instrument.asset_type == "etf":
        prefixes = {"SH": ("51", "56", "58"), "SZ": ("15", "16"), "BJ": ()}
    else:
        return False
    return code.startswith(prefixes.get(instrument.exchange, ()))


def _universe_hash(
    symbols: list[str],
    *,
    trade_date: str,
    required_data_date: str,
    filters: dict[str, object],
) -> str:
    payload = {
        "symbols": sorted(symbols),
        "trade_date": trade_date,
        "required_data_date": required_data_date,
        "filters": filters,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


async def _universe_row(session: AsyncSession, release_id: str) -> ExecutionUniverseReleaseRecord:
    row = await session.scalar(
        select(ExecutionUniverseReleaseRecord).where(
            ExecutionUniverseReleaseRecord.release_id == release_id
        )
    )
    if row is None:
        raise KeyError(release_id)
    return row


async def _universe_row_and_symbols(
    session: AsyncSession, release_id: str
) -> tuple[ExecutionUniverseReleaseRecord, list[str]]:
    row = await _universe_row(session, release_id)
    symbols = list(
        await session.scalars(
            select(ExecutionUniverseMemberRecord.symbol)
            .where(ExecutionUniverseMemberRecord.release_id == release_id)
            .order_by(ExecutionUniverseMemberRecord.symbol)
        )
    )
    return row, symbols


async def _strategy_release_row(
    session: AsyncSession, release_id: str
) -> StrategyExecutionReleaseRecord:
    row = await session.scalar(
        select(StrategyExecutionReleaseRecord).where(
            StrategyExecutionReleaseRecord.release_id == release_id
        )
    )
    if row is None:
        raise KeyError(release_id)
    return row


def _audit(
    trace_id: str, event_type: str, actor: str, payload: dict[str, object]
) -> AuditEventRecord:
    return AuditEventRecord(
        trace_id=trace_id,
        event_type=event_type,
        actor=actor,
        object_id=trace_id,
        payload_json=json.dumps(payload, ensure_ascii=False, default=str),
    )


def _universe_snapshot(row: ExecutionUniverseReleaseRecord) -> dict[str, object]:
    return {
        "release_id": row.release_id,
        "trade_date": row.trade_date,
        "execution_mode": row.execution_mode,
        "scope": row.scope,
        "status": row.status,
        "symbol_count": row.symbol_count,
        "snapshot_hash": row.snapshot_hash,
        "required_data_date": row.required_data_date,
        "filters": json.loads(row.filters_json),
        "rejection_counts": json.loads(row.rejection_counts_json),
        "reviewed_by": row.reviewed_by,
        "approved_by": row.approved_by,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


def _strategy_snapshot(row: StrategyExecutionReleaseRecord) -> dict[str, object]:
    return {
        "release_id": row.release_id,
        "trade_date": row.trade_date,
        "execution_mode": row.execution_mode,
        "strategy_id": row.strategy_id,
        "strategy_name": row.strategy_name,
        "strategy_version": row.strategy_version,
        "universe_release_id": row.universe_release_id,
        "parameters_hash": row.parameters_hash,
        "status": row.status,
        "max_order_notional": str(row.max_order_notional),
        "max_daily_notional": str(row.max_daily_notional),
        "approved_by": row.approved_by,
        "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


__all__ = [
    "ExecutionUniverseFilters",
    "ExecutionUniverseService",
    "StrategyExecutionReleaseService",
]
