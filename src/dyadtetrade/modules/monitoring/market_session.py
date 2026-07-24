from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import StrEnum
from zoneinfo import ZoneInfo

CHINA_TZ = ZoneInfo("Asia/Shanghai")


class MarketOperationPhase(StrEnum):
    CLOSED = "CLOSED"
    PREOPEN = "PREOPEN"
    TRADING_AM = "TRADING_AM"
    LUNCH = "LUNCH"
    TRADING_PM = "TRADING_PM"
    CLOSING = "CLOSING"
    POSTCLOSE = "POSTCLOSE"


@dataclass(frozen=True, slots=True)
class MarketOperationState:
    phase: MarketOperationPhase
    observed_at: datetime
    new_orders_expected: bool
    operator_action: str
    calendar_source: str = "weekday_fallback"

    def snapshot(self) -> dict[str, object]:
        return {
            "phase": self.phase.value,
            "observed_at": self.observed_at.isoformat(),
            "new_orders_expected": self.new_orders_expected,
            "operator_action": self.operator_action,
            "calendar_source": self.calendar_source,
        }


def market_operation_state(
    at: datetime | None = None,
    *,
    is_trading_day: bool | None = None,
    calendar_source: str = "weekday_fallback",
) -> MarketOperationState:
    current = (at or datetime.now(CHINA_TZ)).astimezone(CHINA_TZ)
    if current.weekday() >= 5 or is_trading_day is False:
        return MarketOperationState(
            MarketOperationPhase.CLOSED,
            current,
            False,
            "非交易日：保持 HALTED/RECOVERY，准备下一交易日",
            calendar_source,
        )
    clock = current.time().replace(tzinfo=None)
    if time(8, 30) <= clock < time(9, 30):
        return MarketOperationState(
            MarketOperationPhase.PREOPEN,
            current,
            False,
            "执行开盘前检查、连接和对账",
            calendar_source,
        )
    if time(9, 30) <= clock < time(11, 30):
        return MarketOperationState(
            MarketOperationPhase.TRADING_AM,
            current,
            True,
            "监控行情、策略、订单和风险",
            calendar_source,
        )
    if time(11, 30) <= clock < time(13):
        return MarketOperationState(
            MarketOperationPhase.LUNCH,
            current,
            False,
            "午间对账，不主动新开仓",
            calendar_source,
        )
    if time(13) <= clock < time(15):
        return MarketOperationState(
            MarketOperationPhase.TRADING_PM,
            current,
            True,
            "监控行情、策略、订单和风险",
            calendar_source,
        )
    if time(15) <= clock < time(15, 15):
        return MarketOperationState(
            MarketOperationPhase.CLOSING,
            current,
            False,
            "停止新委托并完成收盘对账",
            calendar_source,
        )
    if time(15, 15) <= clock < time(18):
        return MarketOperationState(
            MarketOperationPhase.POSTCLOSE,
            current,
            False,
            "结算、报告、数据更新和审计",
            calendar_source,
        )
    return MarketOperationState(
        MarketOperationPhase.CLOSED,
        current,
        False,
        "保持安全停机，等待开盘前窗口",
        calendar_source,
    )
