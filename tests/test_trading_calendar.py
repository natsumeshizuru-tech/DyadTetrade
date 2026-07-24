from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from dyadtetrade.core.trading_calendar import sanitize_trading_days
from dyadtetrade.db.models import TradingDay
from dyadtetrade.db.repositories import upsert_trading_days
from dyadtetrade.db.session import Base
from dyadtetrade.integrations.xtquant import XtQuantGateway

CHINA_TZ = ZoneInfo("Asia/Shanghai")


class EpochCalendarXtData:
    def get_trading_calendar(self, market: str, start: str, end: str) -> list[object]:
        assert market == "SH"
        monday = int(datetime(2026, 7, 13, tzinfo=CHINA_TZ).timestamp() * 1000)
        return [monday, "20260711", "20260713", "bad"]


class ClosedDayXtData:
    def get_trading_calendar(self, market: str, start: str, end: str) -> list[object]:
        return []


def test_xtquant_epoch_calendar_keeps_china_local_date_and_filters_weekend() -> None:
    gateway = XtQuantGateway(EpochCalendarXtData())
    assert gateway.trading_days("20260711", "20260713") == ["20260713"]


def test_official_empty_calendar_is_a_valid_closed_day() -> None:
    assert XtQuantGateway(ClosedDayXtData()).trading_days("20261001", "20261001") == []


def test_calendar_sanitizer_rejects_out_of_range_and_weekend() -> None:
    assert sanitize_trading_days(
        ["20260710", "20260711", "20260713", "20260714"],
        start_date="20260711",
        end_date="20260713",
    ) == ["20260713"]


@pytest.mark.asyncio
async def test_calendar_range_sync_removes_stale_weekend_rows(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'calendar.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(TradingDay(market="SH", trade_date="20260711", source="old"))
        await session.commit()
        await upsert_trading_days(
            session,
            "SH",
            ["20260710", "20260713"],
            start_date="20260710",
            end_date="20260713",
        )
        rows = list(
            (
                await session.scalars(
                    select(TradingDay.trade_date).where(TradingDay.market == "SH")
                )
            ).all()
        )
    await engine.dispose()
    assert sorted(rows) == ["20260710", "20260713"]
