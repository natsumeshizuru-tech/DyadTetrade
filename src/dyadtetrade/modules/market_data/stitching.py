from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from dyadtetrade.db.parquet_store import ParquetStore
from dyadtetrade.domain import Bar
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.modules.market_data.normalizer import normalize_datetime
from dyadtetrade.modules.market_data.pipeline import MarketDataPipeline

CHINA_TZ = ZoneInfo("Asia/Shanghai")
MARKET_FIELDS = ["open", "high", "low", "close", "volume", "amount", "preClose"]
MINUTE_PERIODS = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60}


@dataclass(frozen=True, slots=True)
class StitchResult:
    symbol: str
    period: str
    adjust_mode: str
    bars: list[dict[str, object]]
    has_gap: bool
    gaps: list[str]
    last_history_time: str | None
    last_realtime_time: str | None
    backfill_attempted: bool = False
    backfill_error: str | None = None
    realtime_error: str | None = None
    calendar_error: str | None = None
    continuity_verified: bool = False


def canonical_bar_start(value: object, period: str) -> datetime:
    if not isinstance(value, (str, date, datetime, int, float)):
        raise ValueError(f"unsupported bar timestamp {type(value).__name__}")
    parsed = pd.Timestamp(value).to_pydatetime()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CHINA_TZ)
    else:
        parsed = parsed.astimezone(CHINA_TZ)
    if period == "1d":
        return datetime.combine(parsed.date(), time(9, 30), tzinfo=CHINA_TZ)
    return parsed.replace(second=0, microsecond=0)


def expected_session_bars(trade_date: date, period: str) -> list[datetime]:
    minutes = MINUTE_PERIODS.get(period)
    if minutes is None:
        return [datetime.combine(trade_date, time(9, 30), tzinfo=CHINA_TZ)]
    step = timedelta(minutes=minutes)
    values: list[datetime] = []
    first_offset = timedelta(0) if minutes == 1 else step
    current = datetime.combine(trade_date, time(9, 30), tzinfo=CHINA_TZ) + first_offset
    morning_end = datetime.combine(trade_date, time(11, 30), tzinfo=CHINA_TZ)
    while current <= morning_end:
        values.append(current)
        current += step
    current = datetime.combine(trade_date, time(13, 0), tzinfo=CHINA_TZ) + step
    afternoon_end = datetime.combine(trade_date, time(15, 0), tzinfo=CHINA_TZ)
    while current <= afternoon_end:
        values.append(current)
        current += step
    return values


def last_complete_bar_start(now: datetime, period: str) -> datetime | None:
    local = now.astimezone(CHINA_TZ)
    slots = expected_session_bars(local.date(), period)
    if period == "1d":
        return slots[0] if local.time() >= time(15, 0) else None
    complete = [item for item in slots if item <= local]
    return complete[-1] if complete else None


class MarketBarStitchingService:
    """Owns history/backfill/provisional merging; clients never concatenate bars."""

    def __init__(self, store: ParquetStore | None = None) -> None:
        self.store = store or ParquetStore()
        self.pipeline = MarketDataPipeline(self.store)

    def stitch(
        self,
        *,
        symbol: str,
        period: str,
        adjust_mode: str,
        history: list[dict[str, object]],
        realtime: Bar | None,
        now: datetime | None = None,
        trading_days: list[str] | None = None,
        backfill_attempted: bool = False,
        backfill_error: str | None = None,
        realtime_error: str | None = None,
        calendar_error: str | None = None,
    ) -> StitchResult:
        current = now or datetime.now(UTC)
        if realtime is not None and realtime.adjustment != adjust_mode:
            raise ValueError(
                f"cannot stitch {adjust_mode} history with {realtime.adjustment} realtime data"
            )
        keyed: dict[tuple[str, str, datetime, str], dict[str, object]] = {}
        last_history: datetime | None = None
        for record in history:
            record_adjust = str(record.get("adjust", adjust_mode))
            if record_adjust != adjust_mode:
                raise ValueError(
                    f"history adjust mode {record_adjust} does not match {adjust_mode}"
                )
            start = canonical_bar_start(record["datetime"], period)
            payload = self._history_payload(record, symbol, period, adjust_mode, start)
            keyed[(symbol, period, start, adjust_mode)] = payload
            last_history = max(last_history, start) if last_history is not None else start
        last_realtime: datetime | None = None
        if realtime is not None:
            start = canonical_bar_start(realtime.timestamp, period)
            key = (symbol, period, start, adjust_mode)
            if key not in keyed or not bool(keyed[key]["is_final"]):
                keyed[key] = self._realtime_payload(realtime, period, start)
            last_realtime = start
        ordered = [keyed[key] for key in sorted(keyed, key=lambda item: item[2])]
        gaps = self._gaps(ordered, period, current, trading_days)
        return StitchResult(
            symbol=symbol,
            period=period,
            adjust_mode=adjust_mode,
            bars=ordered,
            has_gap=bool(gaps),
            gaps=gaps,
            last_history_time=last_history.isoformat() if last_history else None,
            last_realtime_time=last_realtime.isoformat() if last_realtime else None,
            backfill_attempted=backfill_attempted,
            backfill_error=backfill_error,
            realtime_error=realtime_error,
            calendar_error=calendar_error,
            continuity_verified=(
                backfill_error is None
                and realtime_error is None
                and calendar_error is None
                and trading_days is not None
            ),
        )

    def backfill(
        self,
        gateway: XtQuantGateway,
        *,
        symbol: str,
        period: str,
        start_time: str,
        end_time: str,
        adjust_mode: str = "none",
        now: datetime | None = None,
    ) -> int:
        if adjust_mode != "none":
            raise ValueError("realtime continuity backfill only accepts unadjusted data")
        gateway.download_history(
            symbol,
            period=period,
            start_time=start_time,
            end_time=end_time,
            incrementally=True,
        )
        frames = gateway.local_bars(
            [symbol],
            fields=MARKET_FIELDS,
            period=period,
            start_time=start_time,
            end_time=end_time,
            dividend_type="none",
            fill_data=False,
        )
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            return 0
        values = (
            frame[next(name for name in ("datetime", "time", "date") if name in frame)]
            if any(name in frame for name in ("datetime", "time", "date"))
            else frame.index
        )
        timestamps = normalize_datetime(values)
        current = now or datetime.now(UTC)
        complete = last_complete_bar_start(current, period)
        if period == "1d":
            cutoff = (
                complete.date()
                if complete is not None
                else current.astimezone(CHINA_TZ).date() - timedelta(days=1)
            )
            mask = pd.Series(timestamps.date <= cutoff, index=frame.index)
        elif complete is not None:
            mask = pd.Series(
                timestamps <= pd.Timestamp(complete.replace(tzinfo=None)),
                index=frame.index,
            )
        else:
            mask = pd.Series(False, index=frame.index)
        frame = frame.loc[mask]
        if frame.empty:
            return 0
        result = self.pipeline.process(frame, symbol=symbol, period=period, adjust="none")
        return result.trusted_rows

    def persist_finalized(self, bars: list[Bar]) -> int:
        grouped: dict[tuple[str, str], list[dict[str, object]]] = {}
        for bar in bars:
            if not bar.is_final or bar.adjustment != "none":
                continue
            period = bar.interval.value
            if period not in {*MINUTE_PERIODS, "1d"}:
                continue
            grouped.setdefault((bar.symbol, period), []).append(
                {
                    "datetime": canonical_bar_start(bar.timestamp, period),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "amount": bar.amount,
                    "pre_close": bar.pre_close,
                }
            )
        count = 0
        for (symbol, period), records in grouped.items():
            result = self.pipeline.process(
                pd.DataFrame(records),
                symbol=symbol,
                period=period,
                adjust="none",
            )
            count += result.trusted_rows
        return count

    @staticmethod
    def _history_payload(
        record: dict[str, object],
        symbol: str,
        period: str,
        adjust_mode: str,
        start: datetime,
    ) -> dict[str, object]:
        return {
            **record,
            "datetime": start.isoformat(),
            "bar_start_time": start.isoformat(),
            "symbol": symbol,
            "period": period,
            "adjust_mode": adjust_mode,
            "source": str(record.get("source", "parquet_trusted")),
            "is_final": True,
            "received_at": record.get("received_at"),
            "updated_at": record.get("updated_at"),
            "quality_status": str(record.get("quality_status", "valid")),
        }

    @staticmethod
    def _realtime_payload(bar: Bar, period: str, start: datetime) -> dict[str, object]:
        return {
            "datetime": start.isoformat(),
            "bar_start_time": start.isoformat(),
            "symbol": bar.symbol,
            "period": period,
            "adjust_mode": bar.adjustment,
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume": bar.volume,
            "amount": str(bar.amount),
            "pre_close": str(bar.pre_close) if bar.pre_close is not None else None,
            "source": bar.source,
            "is_final": bar.is_final,
            "received_at": bar.received_at.isoformat() if bar.received_at else None,
            "updated_at": bar.updated_at.isoformat() if bar.updated_at else None,
            "quality_status": bar.quality_status,
        }

    @staticmethod
    def _gaps(
        bars: list[dict[str, object]],
        period: str,
        now: datetime,
        trading_days: list[str] | None,
    ) -> list[str]:
        if not bars:
            return []
        available = {canonical_bar_start(item["bar_start_time"], period) for item in bars}
        first = min(available)
        last_complete = last_complete_bar_start(now, period)
        if last_complete is None or last_complete < first:
            return []
        if trading_days is None:
            days: list[date] = []
            value = first.date()
            while value <= last_complete.date():
                if value.weekday() < 5:
                    days.append(value)
                value += timedelta(days=1)
        else:
            days = [
                parsed
                for value in trading_days
                if (parsed := datetime.strptime(value, "%Y%m%d").date()).weekday() < 5
            ]
        expected: list[datetime] = []
        for day in days:
            expected.extend(
                item
                for item in expected_session_bars(day, period)
                if first <= item <= last_complete
            )
        return [item.isoformat() for item in expected if item not in available]
