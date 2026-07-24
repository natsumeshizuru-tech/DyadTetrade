from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dyadtetrade.db.parquet_store import ParquetStore
from dyadtetrade.domain import Bar, Interval
from dyadtetrade.modules.market_data.stitching import (
    MarketBarStitchingService,
    expected_session_bars,
    last_complete_bar_start,
)
from dyadtetrade.modules.realtime.aggregator import (
    TickDailyBarAggregator,
    TickIntradayBarAggregator,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def history_bar(at: datetime, close: str = "10") -> dict[str, object]:
    return {
        "datetime": at,
        "symbol": "600519.SH",
        "open": 10,
        "high": 11,
        "low": 9,
        "close": float(close),
        "volume": 100,
        "amount": 1000,
        "adjust": "none",
    }


def live_bar(at: datetime, close: str = "10.5") -> Bar:
    return Bar(
        "600519.SH",
        at,
        Interval.DAY_1,
        Decimal("10"),
        Decimal("11"),
        Decimal("9"),
        Decimal(close),
        120,
        adjustment="none",
        source="xtdata_tick_aggregate",
        is_final=False,
        received_at=at.astimezone(UTC),
        updated_at=at.astimezone(UTC),
        quality_status="provisional",
    )


def test_finalized_history_wins_same_bar_key() -> None:
    at = datetime(2026, 7, 13, 9, 30, tzinfo=CHINA_TZ)
    result = MarketBarStitchingService().stitch(
        symbol="600519.SH",
        period="1d",
        adjust_mode="none",
        history=[history_bar(at, "10")],
        realtime=live_bar(at, "10.5"),
        now=datetime(2026, 7, 13, 14, 0, tzinfo=CHINA_TZ),
        trading_days=["20260713"],
    )
    assert len(result.bars) == 1
    assert result.bars[0]["close"] == 10.0
    assert result.bars[0]["is_final"] is True
    assert result.continuity_verified is True


def test_provisional_bar_is_appended_once_and_adjustment_mismatch_is_rejected() -> None:
    history_at = datetime(2026, 7, 10, 9, 30, tzinfo=CHINA_TZ)
    live_at = datetime(2026, 7, 13, 9, 30, tzinfo=CHINA_TZ)
    service = MarketBarStitchingService()
    result = service.stitch(
        symbol="600519.SH",
        period="1d",
        adjust_mode="none",
        history=[history_bar(history_at)],
        realtime=live_bar(live_at),
        now=datetime(2026, 7, 13, 14, 0, tzinfo=CHINA_TZ),
        trading_days=["20260710", "20260713"],
    )
    assert len(result.bars) == 2
    assert result.bars[-1]["quality_status"] == "provisional"
    with pytest.raises(ValueError, match="cannot stitch"):
        service.stitch(
            symbol="600519.SH",
            period="1d",
            adjust_mode="front",
            history=[],
            realtime=live_bar(live_at),
        )


def test_lunch_break_is_not_reported_as_gap() -> None:
    trade_date = date(2026, 7, 13)
    now = datetime(2026, 7, 13, 12, 30, tzinfo=CHINA_TZ)
    history = [
        history_bar(item)
        for item in expected_session_bars(trade_date, "1m")
        if item.time() <= datetime(2026, 7, 13, 11, 30).time()
    ]
    result = MarketBarStitchingService().stitch(
        symbol="600519.SH",
        period="1m",
        adjust_mode="none",
        history=history,
        realtime=None,
        now=now,
        trading_days=["20260713"],
    )
    assert result.has_gap is False
    assert last_complete_bar_start(now, "1m") == datetime(2026, 7, 13, 11, 30, tzinfo=CHINA_TZ)
    assert last_complete_bar_start(now, "1d") is None


def test_intraday_boundaries_match_xtquant_completed_bar_labels() -> None:
    trade_date = date(2026, 7, 13)
    one_minute = expected_session_bars(trade_date, "1m")
    five_minute = expected_session_bars(trade_date, "5m")

    assert len(one_minute) == 241
    assert one_minute[0].time() == datetime(2026, 7, 13, 9, 30).time()
    assert len(five_minute) == 48
    assert five_minute[0].time() == datetime(2026, 7, 13, 9, 35).time()
    assert five_minute[-1].time() == datetime(2026, 7, 13, 15, 0).time()
    assert last_complete_bar_start(datetime(2026, 7, 13, 9, 34, 59, tzinfo=CHINA_TZ), "5m") is None
    assert last_complete_bar_start(datetime(2026, 7, 13, 9, 35, tzinfo=CHINA_TZ), "5m") == datetime(
        2026, 7, 13, 9, 35, tzinfo=CHINA_TZ
    )


def test_missing_trading_minute_is_reported() -> None:
    trade_date = date(2026, 7, 13)
    slots = expected_session_bars(trade_date, "5m")[:4]
    missing = slots.pop(2)
    result = MarketBarStitchingService().stitch(
        symbol="600519.SH",
        period="5m",
        adjust_mode="none",
        history=[history_bar(item) for item in slots],
        realtime=None,
        now=slots[-1],
        trading_days=["20260713"],
    )
    assert missing.isoformat() in result.gaps


def test_backfill_or_calendar_error_keeps_continuity_unverified() -> None:
    at = datetime(2026, 7, 13, 9, 30, tzinfo=CHINA_TZ)
    result = MarketBarStitchingService().stitch(
        symbol="600519.SH",
        period="1m",
        adjust_mode="none",
        history=[history_bar(at)],
        realtime=None,
        now=at,
        trading_days=["20260713"],
        backfill_attempted=True,
        backfill_error="MiniQmtUnavailableError: disconnected",
    )

    assert result.has_gap is False
    assert result.continuity_verified is False
    assert result.backfill_error == "MiniQmtUnavailableError: disconnected"


def test_duplicate_tick_does_not_duplicate_daily_or_intraday_volume() -> None:
    at = datetime(2026, 7, 13, 10, 1, tzinfo=CHINA_TZ)
    payload = {
        "time": int(at.timestamp() * 1000),
        "lastPrice": 10,
        "open": 9.9,
        "high": 10,
        "low": 9.9,
        "volume": 1000,
        "amount": 10000,
    }
    daily = TickDailyBarAggregator()
    first = daily.update("600519.SH", payload, at.astimezone(UTC))
    assert first is not None
    assert daily.update("600519.SH", payload, at.astimezone(UTC)) is None
    intraday = TickIntradayBarAggregator()
    tick = first[0]
    intraday.update("600519.SH", tick, payload, at.astimezone(UTC))
    intraday.update("600519.SH", tick, payload, at.astimezone(UTC))
    assert intraday.current("600519.SH", "1m") is not None
    assert intraday.current("600519.SH", "1m").volume == 0  # type: ignore[union-attr]


def test_cross_day_roll_does_not_finalize_incomplete_previous_daily_bar() -> None:
    aggregator = TickDailyBarAggregator()
    for day in (13, 14):
        at = datetime(2026, 7, day, 9, 31, tzinfo=CHINA_TZ)
        aggregator.update(
            "600519.SH",
            {
                "time": int(at.timestamp() * 1000),
                "lastPrice": 10,
                "volume": 100,
                "amount": 1000,
            },
            at.astimezone(UTC),
        )
    assert aggregator.drain_finalized() == []


def test_close_tick_finalizes_daily_bar_without_waiting_for_next_day() -> None:
    aggregator = TickDailyBarAggregator()
    before_close = datetime(2026, 7, 13, 14, 59, 58, tzinfo=CHINA_TZ)
    at = datetime(2026, 7, 13, 15, 0, tzinfo=CHINA_TZ)
    aggregator.update(
        "600519.SH",
        {
            "time": int(before_close.timestamp() * 1000),
            "lastPrice": 9.99,
            "volume": 90,
            "amount": 900,
        },
        before_close.astimezone(UTC),
    )

    result = aggregator.update(
        "600519.SH",
        {
            "time": int(at.timestamp() * 1000),
            "lastPrice": 10,
            "volume": 100,
            "amount": 1000,
        },
        at.astimezone(UTC),
    )

    assert result is not None and result[1].is_final is True
    finalized = aggregator.drain_finalized()
    assert len(finalized) == 1
    assert finalized[0].timestamp == datetime(2026, 7, 13, 9, 30, tzinfo=CHINA_TZ)


def test_after_close_first_snapshot_does_not_overwrite_backfilled_history() -> None:
    aggregator = TickDailyBarAggregator()
    at = datetime(2026, 7, 13, 15, 0, tzinfo=CHINA_TZ)

    result = aggregator.update(
        "600519.SH",
        {
            "time": int(at.timestamp() * 1000),
            "lastPrice": 10,
            "volume": 100,
            "amount": 1000,
        },
        at.astimezone(UTC),
    )

    assert result is not None and result[1].is_final is True
    assert aggregator.drain_finalized() == []


def test_intraday_labels_match_xtquant_and_completed_bar_is_drained() -> None:
    daily = TickDailyBarAggregator()
    intraday = TickIntradayBarAggregator()
    first_at = datetime(2026, 7, 13, 9, 31, tzinfo=CHINA_TZ)
    second_at = datetime(2026, 7, 13, 9, 35, 1, tzinfo=CHINA_TZ)
    first_payload = {
        "time": int(first_at.timestamp() * 1000),
        "lastPrice": 10,
        "volume": 100,
        "amount": 1000,
    }
    second_payload = {
        "time": int(second_at.timestamp() * 1000),
        "lastPrice": 10.1,
        "volume": 120,
        "amount": 1200,
    }
    first = daily.update("600519.SH", first_payload, first_at.astimezone(UTC))
    second = daily.update("600519.SH", second_payload, second_at.astimezone(UTC))
    assert first is not None and second is not None

    intraday.update("600519.SH", first[0], first_payload, first_at.astimezone(UTC))
    assert intraday.current("600519.SH", "1m").timestamp == datetime(  # type: ignore[union-attr]
        2026, 7, 13, 9, 32, tzinfo=CHINA_TZ
    )
    assert intraday.current("600519.SH", "5m") is not None
    assert intraday.current("600519.SH", "5m").timestamp == datetime(  # type: ignore[union-attr]
        2026, 7, 13, 9, 35, tzinfo=CHINA_TZ
    )
    intraday.update("600519.SH", second[0], second_payload, second_at.astimezone(UTC))
    assert intraday.drain_finalized() == []

    third_at = datetime(2026, 7, 13, 9, 40, 1, tzinfo=CHINA_TZ)
    third_payload = {
        "time": int(third_at.timestamp() * 1000),
        "lastPrice": 10.2,
        "volume": 140,
        "amount": 1400,
    }
    third = daily.update("600519.SH", third_payload, third_at.astimezone(UTC))
    assert third is not None
    intraday.update("600519.SH", third[0], third_payload, third_at.astimezone(UTC))
    completed = intraday.drain_finalized()
    five_minute = [bar for bar in completed if bar.interval == Interval.MINUTE_5]
    assert len(five_minute) == 1
    assert five_minute[0].timestamp == datetime(2026, 7, 13, 9, 40, tzinfo=CHINA_TZ)
    assert five_minute[0].is_final is True

    afternoon = datetime(2026, 7, 13, 13, 0, 10, tzinfo=CHINA_TZ)
    afternoon_payload = {
        "time": int(afternoon.timestamp() * 1000),
        "lastPrice": 10.2,
        "volume": 130,
        "amount": 1300,
    }
    afternoon_tick = daily.update("600519.SH", afternoon_payload, afternoon.astimezone(UTC))
    assert afternoon_tick is not None
    intraday.update("600519.SH", afternoon_tick[0], afternoon_payload, afternoon.astimezone(UTC))
    assert intraday.current("600519.SH", "1m").timestamp == datetime(  # type: ignore[union-attr]
        2026, 7, 13, 13, 1, tzinfo=CHINA_TZ
    )


def test_finalized_intraday_bar_is_persisted_in_its_own_period(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    service = MarketBarStitchingService(store)
    at = datetime(2026, 7, 13, 9, 35, tzinfo=CHINA_TZ)
    bar = Bar(
        "600519.SH",
        at,
        Interval.MINUTE_5,
        Decimal("10"),
        Decimal("10.1"),
        Decimal("9.9"),
        Decimal("10.05"),
        100,
        Decimal("1000"),
        adjustment="none",
        source="xtdata_tick_aggregate",
        is_final=True,
        received_at=at.astimezone(UTC),
        updated_at=at.astimezone(UTC),
    )

    assert service.persist_finalized([bar]) == 1
    stored = store.read("600519.SH", "5m", "none", "trusted")
    assert stored["period"].tolist() == ["5m"]
    assert stored.iloc[0]["datetime"].isoformat() == "2026-07-13T09:35:00+08:00"
    assert not store.path_for("600519.SH", "1d", "none", "trusted").exists()


def test_intraday_cumulative_baseline_resets_by_day_and_does_not_regress() -> None:
    daily = TickDailyBarAggregator()
    intraday = TickIntradayBarAggregator()

    def apply(at: datetime, volume: int, amount: int) -> None:
        payload = {
            "time": int(at.timestamp() * 1000),
            "lastPrice": 10,
            "volume": volume,
            "amount": amount,
        }
        update = daily.update("600519.SH", payload, at.astimezone(UTC))
        assert update is not None
        intraday.update("600519.SH", update[0], payload, at.astimezone(UTC))

    apply(datetime(2026, 7, 13, 15, 0, tzinfo=CHINA_TZ), 1000, 10000)
    apply(datetime(2026, 7, 14, 9, 31, tzinfo=CHINA_TZ), 1500, 15000)
    assert intraday.current("600519.SH", "1m").volume == 0  # type: ignore[union-attr]
    apply(datetime(2026, 7, 14, 9, 31, 1, tzinfo=CHINA_TZ), 1400, 14000)
    apply(datetime(2026, 7, 14, 9, 31, 2, tzinfo=CHINA_TZ), 1550, 15500)
    current = intraday.current("600519.SH", "1m")
    assert current is not None
    assert current.volume == 50
    assert current.amount == Decimal("500")


def test_official_calendar_and_monotonic_time_reject_false_realtime_bars() -> None:
    daily = TickDailyBarAggregator()
    intraday = TickIntradayBarAggregator()
    daily.set_trading_days(["20260710", "20260713"])
    intraday.set_trading_days(["20260710", "20260713"])
    holiday = datetime(2026, 7, 14, 9, 31, tzinfo=CHINA_TZ)
    payload = {
        "time": int(holiday.timestamp() * 1000),
        "lastPrice": 10,
        "volume": 100,
        "amount": 1000,
    }

    assert daily.update("600519.SH", payload, holiday.astimezone(UTC)) is None
    tick = live_bar(holiday).timestamp
    assert tick == holiday  # keep the fixture explicit and timezone-aware

    valid = datetime(2026, 7, 13, 10, 0, tzinfo=CHINA_TZ)
    newer_payload = {**payload, "time": int(valid.timestamp() * 1000), "volume": 200}
    first = daily.update("600519.SH", newer_payload, valid.astimezone(UTC))
    assert first is not None
    future = valid + timedelta(seconds=10)
    future_payload = {**newer_payload, "time": int(future.timestamp() * 1000)}
    assert daily.update("600519.SH", future_payload, valid.astimezone(UTC)) is None
    stale = valid - timedelta(minutes=1)
    stale_payload = {**newer_payload, "time": int(stale.timestamp() * 1000), "volume": 300}
    assert daily.update("600519.SH", stale_payload, valid.astimezone(UTC)) is None
    assert daily.bars()["600519.SH"].volume == 200
    assert daily.rejection_counts()["future_timestamp"] == 1


def test_lunch_close_tick_with_one_second_offset_is_kept() -> None:
    at = datetime(2026, 7, 13, 11, 30, 1, tzinfo=CHINA_TZ)
    result = TickDailyBarAggregator().update(
        "600519.SH",
        {
            "time": int(at.timestamp() * 1000),
            "lastPrice": 10,
            "volume": 100,
            "amount": 1000,
        },
        at.astimezone(UTC),
    )
    assert result is not None
