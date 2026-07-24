from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.core.symbols import infer_asset_type
from dyadtetrade.db.models import (
    Instrument,
    Sector,
    SectorMember,
    SectorMemberSnapshot,
    StockPool,
    StockPoolMember,
    StockPoolMemberSnapshot,
)
from dyadtetrade.db.repositories import (
    assign_industry,
    clear_xtquant_industries,
    deactivate_stale_xtquant_instruments,
    replace_sector_members,
    upsert_instrument_records,
)
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.modules.universe.schemas import PoolFilters, StockPoolCreate


def _detail_value(detail: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in detail.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


class InstrumentCatalogService:
    def __init__(self, gateway: XtQuantGateway) -> None:
        self.gateway = gateway

    async def sync(
        self,
        session: AsyncSession,
        *,
        refresh_remote_sectors: bool,
        include_details: bool,
        include_all_sectors: bool,
    ) -> dict[str, int]:
        if refresh_remote_sectors:
            await asyncio.to_thread(self.gateway.refresh_sector_data)
        universes = await asyncio.to_thread(self.gateway.market_universes)
        asset_by_symbol = {
            symbol: asset_type for asset_type, symbols in universes.items() for symbol in symbols
        }
        records: list[dict[str, Any]] = []
        for symbol, asset_type in sorted(asset_by_symbol.items()):
            detail: dict[str, Any] = {}
            if include_details:
                try:
                    detail = await asyncio.to_thread(self.gateway.instrument_detail, symbol)
                except Exception:
                    detail = {}
            price_tick = _as_decimal(_detail_value(detail, "PriceTick", "price_tick"))
            records.append(
                {
                    "symbol": symbol,
                    "name": _detail_value(detail, "InstrumentName", "name", "证券名称"),
                    "exchange": symbol.split(".")[1],
                    "asset_type": asset_type,
                    "list_date": _compact_date(_detail_value(detail, "OpenDate", "list_date")),
                    "delist_date": _compact_date(
                        _detail_value(detail, "ExpireDate", "delist_date")
                    ),
                    # XtData VolumeMultiple is a contract multiplier (normally 1 for
                    # stocks/ETFs), not the exchange order quantity unit.
                    "lot_size": 100 if asset_type in {"stock", "etf"} else 1,
                    "price_tick": price_tick,
                    "price_decimals": _price_decimals(price_tick),
                    "quantity_decimals": 0,
                    "currency": "CNY",
                    "money_decimals": 2,
                    "is_active": True,
                    "source": "xtquant",
                }
            )
        await upsert_instrument_records(session, records)
        await deactivate_stale_xtquant_instruments(session, sorted(asset_by_symbol))

        sector_count = 0
        if include_all_sectors:
            await clear_xtquant_industries(session)
            all_sector_names = await asyncio.to_thread(self.gateway.list_sectors)
            sector_names = _selected_sector_names(all_sector_names)
            for sector_name in sector_names:
                try:
                    symbols = await asyncio.to_thread(self.gateway.list_symbols, sector_name)
                except Exception:
                    continue
                symbols = [symbol for symbol in symbols if symbol in asset_by_symbol]
                await replace_sector_members(
                    session,
                    sector_name=sector_name,
                    category=_sector_category(sector_name),
                    symbols=symbols,
                )
                if sector_name.startswith("THY1") and symbols:
                    await assign_industry(
                        session, symbols=symbols, industry=sector_name.removeprefix("THY1")
                    )
                sector_count += 1
        return {
            "instrument_count": len(records),
            "stock_count": len(universes.get("stock", [])),
            "etf_count": len(universes.get("etf", [])),
            "index_count": len(universes.get("index", [])),
            "sector_count": sector_count,
        }


class StockPoolService:
    async def create(self, session: AsyncSession, request: StockPoolCreate) -> StockPool:
        existing = await session.scalar(select(StockPool).where(StockPool.name == request.name))
        if existing is not None:
            raise ValueError(f"股票池名称已存在：{request.name}")
        if request.symbols:
            await upsert_instrument_records(
                session,
                [
                    {
                        "symbol": symbol,
                        "exchange": symbol.split(".")[1],
                        "asset_type": infer_asset_type(symbol),
                        "is_active": True,
                        "source": "user",
                    }
                    for symbol in request.symbols
                ],
            )
        pool = StockPool(
            name=request.name,
            pool_type=request.pool_type,
            description=request.description,
            filters_json=request.filters.model_dump_json(),
            is_dynamic=request.is_dynamic,
        )
        session.add(pool)
        await session.flush()
        session.add_all(
            StockPoolMember(pool_id=pool.id, symbol=symbol) for symbol in request.symbols
        )
        effective_from = datetime.now().strftime("%Y%m%d")
        session.add_all(
            StockPoolMemberSnapshot(
                pool_id=pool.id,
                symbol=symbol,
                effective_from=effective_from,
            )
            for symbol in request.symbols
        )
        await session.commit()
        await session.refresh(pool)
        return pool

    async def list_pools(self, session: AsyncSession) -> list[tuple[StockPool, int]]:
        rows = await session.execute(
            select(StockPool, func.count(StockPoolMember.id))
            .outerjoin(StockPoolMember, StockPoolMember.pool_id == StockPool.id)
            .group_by(StockPool.id)
            .order_by(StockPool.name)
        )
        return [(pool, int(count)) for pool, count in rows.all()]

    async def delete(self, session: AsyncSession, pool_id: int) -> bool:
        pool = await session.get(StockPool, pool_id)
        if pool is None:
            return False
        await session.execute(delete(StockPoolMember).where(StockPoolMember.pool_id == pool_id))
        await session.delete(pool)
        await session.commit()
        return True

    async def resolve(
        self, session: AsyncSession, pool_id: int, *, as_of: str | None = None
    ) -> list[str]:
        pool = await session.get(StockPool, pool_id)
        if pool is None:
            raise ValueError(f"股票池不存在：{pool_id}")
        if as_of is not None:
            if len(as_of) != 8 or not as_of.isdigit():
                raise ValueError("as_of must use YYYYMMDD")
            if pool.is_dynamic or pool.pool_type != "custom":
                raise ValueError(
                    "historical dynamic pool resolution requires a persisted membership snapshot"
                )
            explicit = set(
                (
                    await session.scalars(
                        select(StockPoolMemberSnapshot.symbol).where(
                            StockPoolMemberSnapshot.pool_id == pool_id,
                            StockPoolMemberSnapshot.effective_from <= as_of,
                            or_(
                                StockPoolMemberSnapshot.effective_to.is_(None),
                                StockPoolMemberSnapshot.effective_to > as_of,
                            ),
                        )
                    )
                ).all()
            )
            if not explicit:
                cutoff = datetime.strptime(as_of, "%Y%m%d") + timedelta(days=1)
                explicit = set(
                    (
                        await session.scalars(
                            select(StockPoolMember.symbol).where(
                                StockPoolMember.pool_id == pool_id,
                                StockPoolMember.added_at <= cutoff,
                            )
                        )
                    ).all()
                )
            return sorted(explicit)
        explicit = set(
            (
                await session.scalars(
                    select(StockPoolMember.symbol).where(StockPoolMember.pool_id == pool_id)
                )
            ).all()
        )
        filters = PoolFilters.model_validate(json.loads(pool.filters_json or "{}"))
        if pool.is_dynamic or pool.pool_type != "custom":
            explicit.update(await self.filter_symbols(session, filters))
        return sorted(explicit)

    async def filter_symbols(self, session: AsyncSession, filters: PoolFilters) -> list[str]:
        statement = select(Instrument.symbol).distinct()
        if filters.sectors:
            statement = (
                statement.join(SectorMember, SectorMember.symbol == Instrument.symbol)
                .join(Sector, Sector.id == SectorMember.sector_id)
                .where(Sector.name.in_(filters.sectors))
            )
        if filters.markets:
            statement = statement.where(Instrument.exchange.in_(filters.markets))
        if filters.asset_types:
            statement = statement.where(Instrument.asset_type.in_(filters.asset_types))
        if filters.industries:
            statement = statement.where(Instrument.industry.in_(filters.industries))
        if filters.active_only:
            statement = statement.where(Instrument.is_active.is_(True))
        if filters.symbol_prefixes:
            statement = statement.where(
                or_(*(Instrument.symbol.startswith(prefix) for prefix in filters.symbol_prefixes))
            )
        return sorted((await session.scalars(statement)).all())


async def sector_members_as_of(
    session: AsyncSession, sector_id: int, as_of: str | None = None
) -> tuple[str | None, list[str]]:
    sector = await session.get(Sector, sector_id)
    if sector is None:
        raise ValueError(f"板块不存在：{sector_id}")
    if as_of is None:
        symbols = (
            await session.scalars(
                select(SectorMember.symbol).where(SectorMember.sector_id == sector_id)
            )
        ).all()
        return None, sorted(symbols)
    if len(as_of) != 8 or not as_of.isdigit():
        raise ValueError("as_of must use YYYYMMDD")
    snapshot_date = await session.scalar(
        select(func.max(SectorMemberSnapshot.as_of_date)).where(
            SectorMemberSnapshot.sector_id == sector_id,
            SectorMemberSnapshot.as_of_date <= as_of,
        )
    )
    if snapshot_date is None:
        return None, []
    symbols = (
        await session.scalars(
            select(SectorMemberSnapshot.symbol).where(
                SectorMemberSnapshot.sector_id == sector_id,
                SectorMemberSnapshot.as_of_date == snapshot_date,
            )
        )
    ).all()
    return str(snapshot_date), sorted(symbols)


def pool_to_dict(pool: StockPool, member_count: int) -> dict[str, Any]:
    return {
        "id": pool.id,
        "name": pool.name,
        "pool_type": pool.pool_type,
        "description": pool.description,
        "is_dynamic": pool.is_dynamic,
        "member_count": member_count,
        "filters": json.loads(pool.filters_json or "{}"),
    }


def _sector_category(name: str) -> str:
    if name.startswith(("THY1", "GICS1", "SW1", "CSRC1")) or "行业" in name:
        return "industry"
    if "指数" in name or (name.startswith("TGN") and ("样本股" in name or "成份股" in name)):
        return "index"
    if "概念" in name:
        return "concept"
    return "sector"


def _selected_sector_names(names: list[str]) -> list[str]:
    """Keep production-useful sectors and avoid thousands of derived/equal-weight groups."""
    basic_names = {
        "上证A股",
        "深证A股",
        "京市A股",
        "沪深A股",
        "沪深京A股",
        "创业板",
        "科创板",
        "科创板CDR",
        "上证ETF",
        "深证ETF",
        "沪市ETF",
        "深市ETF",
        "沪深ETF",
        "上证指数",
        "深证指数",
        "沪市指数",
        "深市指数",
        "沪深指数",
    }
    detailed_prefixes = ("THY1", "GICS1", "SW1", "CSRC1")
    index_components = (
        "TGN沪深300样本股",
        "TGN中证500成份股",
        "TGN中证1000成份股",
        "TGN上证50样本股",
    )
    selected = []
    for name in names:
        if name in basic_names or name.startswith(detailed_prefixes) or name in index_components:
            selected.append(name)
    return sorted(set(selected))


def _compact_date(value: Any) -> str | None:
    if value is None:
        return None
    digits = "".join(character for character in str(value) if character.isdigit())
    return digits[:8] if len(digits) >= 8 else None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _price_decimals(value: Decimal | None) -> int:
    if value is None:
        return 2
    exponent = value.normalize().as_tuple().exponent
    return max(-exponent, 0) if isinstance(exponent, int) else 2
