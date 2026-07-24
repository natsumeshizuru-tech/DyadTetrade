from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from dyadtetrade.api.routes.market_data import GatewayDep
from dyadtetrade.db.models import Instrument, Sector
from dyadtetrade.db.session import get_session
from dyadtetrade.modules.universe.schemas import (
    CatalogSyncRequest,
    CatalogSyncResult,
    PoolFilters,
    StockPoolCreate,
    StockPoolResult,
)
from dyadtetrade.modules.universe.service import (
    InstrumentCatalogService,
    StockPoolService,
    pool_to_dict,
    sector_members_as_of,
)

router = APIRouter(prefix="/universe", tags=["universe"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/catalog/sync", response_model=CatalogSyncResult)
async def sync_catalog(
    request: CatalogSyncRequest,
    gateway: GatewayDep,
    session: SessionDep,
) -> CatalogSyncResult:
    result = await InstrumentCatalogService(gateway).sync(
        session,
        refresh_remote_sectors=request.refresh_remote_sectors,
        include_details=request.include_details,
        include_all_sectors=request.include_all_sectors,
    )
    return CatalogSyncResult.model_validate(result)


@router.get("/instruments")
async def instruments(
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=64)] = None,
    market: Literal["SH", "SZ", "BJ"] | None = None,
    asset_type: Literal["stock", "etf", "index", "other"] | None = None,
    active_only: bool = True,
    limit: Annotated[int, Query(ge=1, le=10000)] = 500,
) -> list[dict[str, Any]]:
    statement = select(Instrument).order_by(Instrument.symbol).limit(limit)
    if market:
        statement = statement.where(Instrument.exchange == market)
    if asset_type:
        statement = statement.where(Instrument.asset_type == asset_type)
    if active_only:
        statement = statement.where(Instrument.is_active.is_(True))
    if q:
        pattern = f"%{q.strip()}%"
        statement = statement.where(
            or_(Instrument.symbol.ilike(pattern), Instrument.name.ilike(pattern))
        )
    rows = (await session.scalars(statement)).all()
    return [
        {
            "symbol": item.symbol,
            "name": item.name,
            "exchange": item.exchange,
            "asset_type": item.asset_type,
            "industry": item.industry,
            "price_tick": str(item.price_tick or "0.01"),
            "price_decimals": item.price_decimals,
            "quantity_decimals": item.quantity_decimals,
            "lot_size": item.lot_size or 100,
            "currency": item.currency,
            "money_decimals": item.money_decimals,
            "list_date": item.list_date,
            "delist_date": item.delist_date,
            "is_active": item.is_active,
            "updated_at": item.updated_at,
        }
        for item in rows
    ]


@router.get("/sectors")
async def stored_sectors(session: SessionDep) -> list[dict[str, Any]]:
    values = (await session.scalars(select(Sector).order_by(Sector.category, Sector.name))).all()
    return [
        {
            "id": item.id,
            "name": item.name,
            "category": item.category,
            "updated_at": item.updated_at,
        }
        for item in values
    ]


@router.get("/sectors/{sector_id}/members")
async def sector_members(
    sector_id: int,
    session: SessionDep,
    as_of: Annotated[str | None, Query(pattern=r"^\d{8}$")] = None,
) -> dict[str, Any]:
    try:
        snapshot_date, symbols = await sector_members_as_of(session, sector_id, as_of)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "sector_id": sector_id,
        "requested_as_of": as_of,
        "snapshot_date": snapshot_date,
        "count": len(symbols),
        "symbols": symbols,
    }


@router.post("/pools", response_model=StockPoolResult, status_code=201)
async def create_pool(request: StockPoolCreate, session: SessionDep) -> StockPoolResult:
    service = StockPoolService()
    try:
        pool = await service.create(session, request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    members = await service.resolve(session, pool.id)
    return StockPoolResult.model_validate(pool_to_dict(pool, len(members)))


@router.get("/pools", response_model=list[StockPoolResult])
async def pools(session: SessionDep) -> list[StockPoolResult]:
    values = await StockPoolService().list_pools(session)
    return [StockPoolResult.model_validate(pool_to_dict(pool, count)) for pool, count in values]


@router.get("/pools/filter")
async def filter_pool(
    session: SessionDep,
    markets: Annotated[list[Literal["SH", "SZ", "BJ"]] | None, Query()] = None,
    asset_types: Annotated[list[Literal["stock", "etf", "index", "other"]] | None, Query()] = None,
    sectors: Annotated[list[str] | None, Query()] = None,
    industries: Annotated[list[str] | None, Query()] = None,
) -> dict[str, Any]:
    filters = PoolFilters(
        markets=markets or [],
        asset_types=asset_types or [],
        sectors=sectors or [],
        industries=industries or [],
    )
    symbols = await StockPoolService().filter_symbols(session, filters)
    return {"count": len(symbols), "symbols": symbols}


@router.get("/pools/{pool_id}/members")
async def pool_members(
    pool_id: int,
    session: SessionDep,
    as_of: Annotated[str | None, Query(pattern=r"^\d{8}$")] = None,
) -> dict[str, Any]:
    try:
        symbols = await StockPoolService().resolve(session, pool_id, as_of=as_of)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"pool_id": pool_id, "as_of": as_of, "count": len(symbols), "symbols": symbols}


@router.delete("/pools/{pool_id}", status_code=204)
async def delete_pool(pool_id: int, session: SessionDep) -> None:
    if not await StockPoolService().delete(session, pool_id):
        raise HTTPException(status_code=404, detail="股票池不存在")
