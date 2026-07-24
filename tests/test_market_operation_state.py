from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from dyadtetrade.modules.monitoring import MarketOperationPhase, market_operation_state

CHINA_TZ = ZoneInfo("Asia/Shanghai")


@pytest.mark.parametrize(
    ("hour", "minute", "expected", "orders"),
    [
        (8, 29, MarketOperationPhase.CLOSED, False),
        (8, 30, MarketOperationPhase.PREOPEN, False),
        (9, 30, MarketOperationPhase.TRADING_AM, True),
        (11, 30, MarketOperationPhase.LUNCH, False),
        (13, 0, MarketOperationPhase.TRADING_PM, True),
        (15, 0, MarketOperationPhase.CLOSING, False),
        (15, 15, MarketOperationPhase.POSTCLOSE, False),
        (18, 0, MarketOperationPhase.CLOSED, False),
    ],
)
def test_market_operation_boundaries(
    hour: int, minute: int, expected: MarketOperationPhase, orders: bool
) -> None:
    state = market_operation_state(datetime(2026, 7, 13, hour, minute, tzinfo=CHINA_TZ))
    assert state.phase is expected
    assert state.new_orders_expected is orders


def test_weekend_is_always_closed() -> None:
    state = market_operation_state(datetime(2026, 7, 11, 10, 0, tzinfo=CHINA_TZ))
    assert state.phase is MarketOperationPhase.CLOSED


def test_weekday_exchange_holiday_is_closed_by_calendar() -> None:
    state = market_operation_state(
        datetime(2026, 10, 1, 10, 0, tzinfo=CHINA_TZ),
        is_trading_day=False,
        calendar_source="xtquant",
    )
    assert state.phase is MarketOperationPhase.CLOSED
    assert not state.new_orders_expected
    assert state.calendar_source == "xtquant"
