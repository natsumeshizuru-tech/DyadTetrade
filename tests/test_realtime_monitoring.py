from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from dyadtetrade.api.routes import paper_trading
from dyadtetrade.core.config import ExecutionMode, Settings
from dyadtetrade.domain import Bar, Interval, Tick
from dyadtetrade.modules.monitoring import account_monitor_snapshot
from dyadtetrade.modules.paper_trading import PaperStrategyRunner, PaperTradingService
from dyadtetrade.modules.realtime import RealtimeComputationService, TickDailyBarAggregator
from dyadtetrade.modules.realtime.service import is_main_board_stock

BACKFILL_PERIODS = ("1m", "5m", "15m", "30m", "1h", "1d")


def tick_payload(
    timestamp: datetime, price: float, *, volume: int = 1000, amount: float = 10000
) -> dict[str, Any]:
    return {
        "time": int(timestamp.timestamp() * 1000),
        "lastPrice": price,
        "open": 10,
        "high": max(price, 10),
        "low": min(price, 10),
        "lastClose": 9.9,
        "pvolume": volume,
        "amount": amount,
        "bidPrice": [price - 0.01],
        "askPrice": [price + 0.01],
        "bidVol": [100],
        "askVol": [100],
    }


def test_tick_daily_bar_aggregator_is_cumulative_and_idempotent() -> None:
    aggregator = TickDailyBarAggregator()
    now = datetime(2026, 7, 13, 1, 30, tzinfo=UTC)
    first = aggregator.update("600001.SH", tick_payload(now, 10.1), now)
    assert first is not None
    assert aggregator.update("600001.SH", tick_payload(now, 10.1), now) is None
    second = aggregator.update(
        "600001.SH",
        tick_payload(now + timedelta(seconds=1), 9.8, volume=1200, amount=12000),
        now + timedelta(seconds=1),
    )
    assert second is not None
    _, bar = second
    assert bar.open == Decimal("10")
    assert bar.high == Decimal("10.1")
    assert bar.low == Decimal("9.8")
    assert bar.close == Decimal("9.8")
    assert bar.volume == 1200
    assert bar.adjustment == "none"


def test_full_session_tick_replay_keeps_one_daily_state_per_symbol() -> None:
    aggregator = TickDailyBarAggregator()
    trading_minutes = [
        *(
            datetime(2026, 7, 13, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(minutes=i)
            for i in range(120)
        ),
        *(
            datetime(2026, 7, 13, 13, 0, tzinfo=ZoneInfo("Asia/Shanghai")) + timedelta(minutes=i)
            for i in range(120)
        ),
    ]
    for index, at in enumerate(trading_minutes):
        price = 10 + ((index % 31) - 15) / 100
        result = aggregator.update(
            "600001.SH",
            tick_payload(at.astimezone(UTC), price, volume=1000 + index, amount=10000 + index),
            at.astimezone(UTC),
        )
        assert result is not None
    bars = aggregator.bars()
    assert list(bars) == ["600001.SH"]
    assert bars["600001.SH"].volume == 1239
    assert bars["600001.SH"].timestamp == trading_minutes[0]
    assert bars["600001.SH"].is_final is False


class FakeXtData:
    def __init__(self) -> None:
        self.step = 0
        self.fail_full_tick = False
        self.fail_trading_days = False
        self.events: list[str] = []

    def list_a_share_universe(self, *, include_bj: bool = True) -> list[str]:
        del include_bj
        return ["600001.SH", "000001.SZ", "300001.SZ", "900001.SH"]

    def instrument_detail(self, symbol: str) -> dict[str, object]:
        return {
            "InstrumentName": symbol,
            "FloatVolume": 100 if symbol == "600001.SH" else 200,
            "PreClose": 10,
            "IsTrading": True,
        }

    def full_tick(self, symbols: list[str]) -> dict[str, Any]:
        self.events.append("full_tick")
        if self.fail_full_tick:
            raise RuntimeError("simulated five-minute disconnect")
        self.step += 1
        now = datetime(2026, 7, 13, 1, 30, tzinfo=UTC) + timedelta(seconds=self.step)
        return {
            symbol: tick_payload(now, 10 + self.step * (0.1 if symbol.endswith("SH") else 0.05))
            for symbol in symbols
        }

    def trading_days(self, start_time: str, end_time: str) -> list[str]:
        del start_time, end_time
        self.events.append("trading_days")
        if self.fail_trading_days:
            raise RuntimeError("simulated trading calendar outage")
        return ["20260713"]


class FakeContinuity:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_period: str | None = None
        self.persisted: list[Bar] = []

    def backfill(
        self,
        gateway: object,
        *,
        symbol: str,
        period: str,
        start_time: str,
        end_time: str,
        adjust_mode: str,
    ) -> int:
        del start_time, end_time, adjust_mode
        events = gateway.events  # type: ignore[attr-defined]
        events.append(f"backfill:{symbol}:{period}")
        self.calls.append((symbol, period))
        if self.fail_period == period:
            raise RuntimeError(f"simulated {period} backfill failure")
        return 1

    def persist_finalized(self, bars: list[Bar]) -> int:
        self.persisted.extend(bars)
        return len(bars)


@pytest.mark.asyncio
async def test_realtime_service_mainboard_candidates_factors_and_handler() -> None:
    fake = FakeXtData()
    settings = Settings(
        enable_realtime_compute=True,
        realtime_symbols=[],
        realtime_small_cap_candidates=0,
        realtime_factor_lookback=3,
    )
    service = RealtimeComputationService(settings, fake)  # type: ignore[arg-type]
    service.continuity = FakeContinuity()  # type: ignore[assignment]
    assert is_main_board_stock("600001.SH")
    assert is_main_board_stock("000001.SZ")
    assert not is_main_board_stock("300001.SZ")
    assert await service.refresh_mainboard_candidates(2) == ["600001.SH", "000001.SZ"]
    batches: list[int] = []

    async def handler(ticks: list[Tick], bars: list[Bar]) -> None:
        assert len(ticks) == len(bars)
        batches.append(len(ticks))

    service.add_handler(handler)
    await service.poll_once()
    await service.poll_once()
    snapshot = service.snapshot()
    assert batches == [2, 2]
    assert snapshot["watchlist_count"] == 2
    assert len(snapshot["factors"]) == 2  # type: ignore[arg-type]
    assert snapshot["orders_sent"] == 0


@pytest.mark.asyncio
async def test_realtime_default_does_not_construct_a_historical_writer() -> None:
    fake = FakeXtData()
    settings = Settings(
        enable_realtime_compute=True,
        realtime_symbols=["600001.SH"],
        realtime_small_cap_candidates=0,
    )
    service = RealtimeComputationService(settings, fake)  # type: ignore[arg-type]

    await service.poll_once()

    snapshot = service.snapshot()
    assert snapshot["continuity_adapter_configured"] is False
    assert snapshot["continuity_recovery_required"] is False
    assert fake.events == ["trading_days", "full_tick"]


@pytest.mark.asyncio
async def test_realtime_disconnect_requires_all_period_backfill_before_ticks_resume() -> None:
    fake = FakeXtData()
    settings = Settings(
        enable_realtime_compute=True,
        realtime_symbols=["600001.SH"],
        realtime_small_cap_candidates=0,
    )
    service = RealtimeComputationService(settings, fake)  # type: ignore[arg-type]
    continuity = FakeContinuity()
    service.continuity = continuity  # type: ignore[assignment]

    await service.poll_once()
    assert [period for _, period in continuity.calls] == list(BACKFILL_PERIODS)
    assert service.snapshot()["continuity_recovery_required"] is False

    fake.fail_full_tick = True
    with pytest.raises(RuntimeError, match="five-minute disconnect"):
        await service.poll_once()
    failed_snapshot = service.snapshot()
    assert failed_snapshot["continuity_recovery_required"] is True
    assert failed_snapshot["market_fresh"] is False

    fake.fail_full_tick = False
    fake.step += 299
    recovery_start = len(fake.events)
    await service.poll_once()
    recovery_events = fake.events[recovery_start:]
    assert recovery_events[-1] == "full_tick"
    assert recovery_events[:-1] == [
        "trading_days",
        *(f"backfill:600001.SH:{period}" for period in BACKFILL_PERIODS),
    ]
    recovered = service.snapshot()
    assert recovered["continuity_recovery_required"] is False
    assert recovered["continuity_backfill_runs"] == 2
    assert recovered["ticks_processed"] == 2


@pytest.mark.asyncio
async def test_backfill_failure_is_fail_closed_and_retried() -> None:
    fake = FakeXtData()
    settings = Settings(
        enable_realtime_compute=True,
        realtime_symbols=["600001.SH"],
        realtime_small_cap_candidates=0,
    )
    service = RealtimeComputationService(settings, fake)  # type: ignore[arg-type]
    continuity = FakeContinuity()
    continuity.fail_period = "5m"
    service.continuity = continuity  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="continuity backfill failed"):
        await service.poll_once()
    assert "full_tick" not in fake.events
    failed = service.snapshot()
    assert failed["continuity_recovery_required"] is True
    assert failed["continuity_failures"]

    continuity.fail_period = None
    await service.poll_once()
    recovered = service.snapshot()
    assert recovered["continuity_recovery_required"] is False
    assert recovered["continuity_failures"] == []
    assert recovered["ticks_processed"] == 1


@pytest.mark.asyncio
async def test_calendar_failure_is_visible_and_blocks_realtime_ticks() -> None:
    fake = FakeXtData()
    fake.fail_trading_days = True
    settings = Settings(
        enable_realtime_compute=True,
        realtime_symbols=["600001.SH"],
        realtime_small_cap_candidates=0,
    )
    service = RealtimeComputationService(settings, fake)  # type: ignore[arg-type]
    service.continuity = FakeContinuity()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="trading calendar"):
        await service.poll_once()

    snapshot = service.snapshot()
    assert snapshot["continuity_recovery_required"] is True
    assert snapshot["continuity_failures"] == [
        {
            "symbol": "*",
            "period": "calendar",
            "error": "RuntimeError: simulated trading calendar outage",
        }
    ]
    assert "full_tick" not in fake.events


def test_tick_momentum_small_cap_strategy_uses_mainboard_only() -> None:
    service = PaperTradingService()
    with pytest.raises(ValueError, match="main-board"):
        PaperStrategyRunner(
            service,
            "tick_momentum_small_cap",
            "bad-universe",
            {
                "symbols": ["300001.SZ", "600001.SH"],
                "float_shares": {"300001.SZ": 100, "600001.SH": 100},
                "small_cap_n": 2,
                "top_n": 1,
            },
        )

    runner = PaperStrategyRunner(
        service,
        "tick_momentum_small_cap",
        "tick-mainboard",
        {
            "symbols": ["600001.SH", "000001.SZ"],
            "float_shares": {"600001.SH": 100, "000001.SZ": 200},
            "momentum_lookback_ticks": 3,
            "small_cap_n": 2,
            "top_n": 1,
            "rebalance_every_ticks": 2,
        },
    )
    runner.start()
    start = datetime(2026, 7, 13, 1, 30, tzinfo=UTC)
    for step, prices in enumerate(((10, 10), (10.1, 10), (10.3, 9.9))):
        at = start + timedelta(seconds=step)
        bars = [
            Bar(
                symbol,
                at,
                Interval.DAY_1,
                Decimal("10"),
                Decimal("11"),
                Decimal("9"),
                Decimal(str(price)),
                100000,
            )
            for symbol, price in zip(("600001.SH", "000001.SZ"), prices, strict=True)
        ]
        ticks = [
            Tick(symbol, at, Decimal(str(price)), volume=100000)
            for symbol, price in zip(("600001.SH", "000001.SZ"), prices, strict=True)
        ]
        service.on_bars(at, bars)
        runner.after_market_ticks(ticks, bars)
    assert runner.strategy.state["selected"] == ["600001.SH"]
    assert runner.status()["order_count"] >= 1  # type: ignore[operator]
    assert service.risk.switches.no_new_positions is False
    assert all(order.status.value != "RISK_REJECTED" for order in service.oms.orders.values())


@pytest.mark.asyncio
async def test_recovered_strategy_heartbeat_is_refreshed_before_risk_monitoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = PaperTradingService()
    strategy_id = "recovered-paper-strategy"
    runner = PaperStrategyRunner(
        service,
        "buy_and_hold",
        strategy_id,
        {"symbol": "600001.SH", "quantity": 100},
    )
    runner.start()
    old = datetime(2026, 7, 13, 1, 0, tzinfo=UTC)
    current = old + timedelta(hours=1)
    service.heartbeat_strategy(strategy_id, old)
    monkeypatch.setattr(paper_trading, "paper_service", service)
    monkeypatch.setattr(paper_trading, "paper_runners", {strategy_id: runner})
    monkeypatch.setattr(paper_trading, "_last_realtime_persist", datetime.now(UTC))
    bar = Bar(
        "600001.SH",
        current,
        Interval.DAY_1,
        Decimal("10"),
        Decimal("10.2"),
        Decimal("9.9"),
        Decimal("10.1"),
        100_000,
    )
    tick = Tick("600001.SH", current, Decimal("10.1"), volume=100_000)

    await paper_trading.handle_realtime_batch([tick], [bar])

    assert strategy_id not in (service.risk.switches.stopped_strategies or set())
    assert service.risk.switches.no_new_positions is False
    assert runner.status()["order_count"] == 1


def test_account_monitor_calculates_capital_orders_and_exposure() -> None:
    snapshot = account_monitor_snapshot(
        {
            "execution_mode": "qmt_sim",
            "connected": True,
            "subscribed": True,
            "account_healthy": True,
            "assets": {"total_assets": "1000", "available_cash": "400"},
            "positions": [
                {
                    "symbol": "600001.SH",
                    "quantity": 50,
                    "average_cost": "10",
                    "market_value": "600",
                }
            ],
            "orders": [{"status": "SUBMITTED"}, {"status": "FILLED"}],
            "trades": [{"price": "12", "quantity": 50}],
        }
    )
    capital = snapshot["capital"]
    assert isinstance(capital, dict)
    assert capital["unrealized_pnl"] == "100"
    assert capital["gross_exposure"] == pytest.approx(0.6)
    statistics = snapshot["order_statistics"]
    assert isinstance(statistics, dict) and statistics["active"] == 1


def test_qmt_live_release_gate_is_daily_and_account_whitelisted(tmp_path: Path) -> None:
    base = {
        "execution_mode": ExecutionMode.QMT_LIVE,
        "enable_live_trading": True,
        "enable_qmt_live_trading": True,
        "qmt_account_id": "live-account",
        "qmt_live_account_whitelist": ["live-account"],
        "qmt_userdata_path": tmp_path,
        "allowed_symbols": ["600001.SH"],
        "qmt_live_confirmation": "I_CONFIRM_QMT_LIVE",
        "qmt_live_release_date": date.today().isoformat(),
    }
    Settings(**base).validate_execution_safety()
    with pytest.raises(RuntimeError, match="release gate"):
        Settings(**{**base, "qmt_live_release_date": "2000-01-01"}).validate_execution_safety()
