from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dyadtetrade.db.models import Instrument, Sector, SectorMember, SectorMemberSnapshot
from dyadtetrade.db.repositories import upsert_instrument_records
from dyadtetrade.db.session import Base
from dyadtetrade.modules.universe.schemas import PoolFilters, StockPoolCreate
from dyadtetrade.modules.universe.service import (
    InstrumentCatalogService,
    StockPoolService,
    _selected_sector_names,
    sector_members_as_of,
)


class _CatalogGateway:
    def refresh_sector_data(self) -> None:
        return None

    def market_universes(self) -> dict[str, list[str]]:
        return {"stock": ["600519.SH"], "etf": ["510300.SH"]}

    def instrument_detail(self, symbol: str) -> dict[str, object]:
        return {
            "InstrumentName": symbol,
            "OpenDate": "20100101",
            "ExpireDate": 99999999,
            "PriceTick": 0.01 if symbol == "600519.SH" else 0.001,
            "VolumeMultiple": 1,
        }


@pytest.mark.asyncio
async def test_custom_and_dynamic_stock_pools(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await upsert_instrument_records(
            session,
            [
                {"symbol": "600519.SH", "exchange": "SH", "asset_type": "stock"},
                {"symbol": "000001.SZ", "exchange": "SZ", "asset_type": "stock"},
                {"symbol": "510300.SH", "exchange": "SH", "asset_type": "etf"},
            ],
        )
        service = StockPoolService()
        custom = await service.create(
            session, StockPoolCreate(name="核心池", symbols=["600519.SH", "000001.SZ"])
        )
        dynamic = await service.create(
            session,
            StockPoolCreate(
                name="沪市股票",
                pool_type="filter",
                is_dynamic=True,
                filters=PoolFilters(markets=["SH"], asset_types=["stock"]),
            ),
        )
        assert await service.resolve(session, custom.id) == ["000001.SZ", "600519.SH"]
        today = datetime.now().strftime("%Y%m%d")
        assert await service.resolve(session, custom.id, as_of=today) == [
            "000001.SZ",
            "600519.SH",
        ]
        with pytest.raises(ValueError, match="historical dynamic"):
            await service.resolve(session, dynamic.id, as_of=today)
        assert await service.resolve(session, dynamic.id) == ["600519.SH"]
        assert len(await service.list_pools(session)) == 2
        assert await service.delete(session, custom.id)
    await engine.dispose()


@pytest.mark.asyncio
async def test_sector_membership_uses_latest_snapshot_not_current_members(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sector.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await upsert_instrument_records(
            session,
            [
                {"symbol": "600519.SH", "exchange": "SH", "asset_type": "stock"},
                {"symbol": "000001.SZ", "exchange": "SZ", "asset_type": "stock"},
            ],
        )
        sector = Sector(name="test-index", category="index")
        session.add(sector)
        await session.flush()
        session.add(SectorMember(sector_id=sector.id, symbol="000001.SZ"))
        session.add_all(
            [
                SectorMemberSnapshot(
                    sector_id=sector.id, symbol="600519.SH", as_of_date="20250101"
                ),
                SectorMemberSnapshot(
                    sector_id=sector.id, symbol="000001.SZ", as_of_date="20260101"
                ),
            ]
        )
        await session.commit()
        snapshot_date, historical = await sector_members_as_of(session, sector.id, "20251231")
        _, current = await sector_members_as_of(session, sector.id)
        assert snapshot_date == "20250101"
        assert historical == ["600519.SH"]
        assert current == ["000001.SZ"]
    await engine.dispose()


def test_sector_selection_keeps_industries_and_index_components() -> None:
    values = _selected_sector_names(
        [
            "沪深京A股",
            "THY1食品饮料",
            "TGN沪深300样本股",
            "TGN人工智能",
            "中证500等权",
        ]
    )
    assert values == ["TGN沪深300样本股", "THY1食品饮料", "沪深京A股"]


@pytest.mark.asyncio
async def test_catalog_does_not_treat_xtdata_volume_multiple_as_order_lot(
    tmp_path: Path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        service = InstrumentCatalogService(_CatalogGateway())  # type: ignore[arg-type]
        await service.sync(
            session,
            refresh_remote_sectors=False,
            include_details=True,
            include_all_sectors=False,
        )
        stock = await session.get(Instrument, "600519.SH")
        etf = await session.get(Instrument, "510300.SH")
        assert stock is not None and stock.lot_size == 100
        assert etf is not None and etf.lot_size == 100
    await engine.dispose()
