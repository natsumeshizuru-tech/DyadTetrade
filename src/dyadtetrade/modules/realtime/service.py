from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dyadtetrade.core.config import Settings, get_settings
from dyadtetrade.domain import Bar, Tick
from dyadtetrade.integrations.xtquant import XtQuantGateway
from dyadtetrade.modules.realtime.aggregator import (
    PERIOD_INTERVALS,
    TickDailyBarAggregator,
    TickIntradayBarAggregator,
)
from dyadtetrade.modules.realtime.continuity import RealtimeContinuityPort

BatchHandler = Callable[[list[Tick], list[Bar]], Awaitable[None] | None]
CHINA_TZ = ZoneInfo("Asia/Shanghai")
CONTINUITY_BACKFILL_PERIODS = ("1m", "5m", "15m", "30m", "1h", "1d")


def is_main_board_stock(symbol: str) -> bool:
    code, _, exchange = symbol.upper().partition(".")
    if exchange == "SH":
        return code.startswith(("600", "601", "603", "605"))
    if exchange == "SZ":
        return code.startswith(("000", "001", "002", "003"))
    return False


class RealtimeComputationService:
    """Bounded polling engine for snapshots, synthetic daily bars and live factors."""

    def __init__(
        self,
        settings: Settings | None = None,
        gateway: XtQuantGateway | None = None,
        continuity: RealtimeContinuityPort | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.gateway = gateway
        self.aggregator = TickDailyBarAggregator()
        self.intraday_aggregator = TickIntradayBarAggregator()
        # Historical storage belongs to DyadResearch. A caller may inject a
        # read-through continuity adapter during the migration window, but the
        # trading runtime never constructs a historical writer by itself.
        self.continuity = continuity
        self.watchlist = set(self.settings.realtime_symbols) | set(self.settings.allowed_symbols)
        self.mainboard_candidates: list[str] = []
        self.float_shares: dict[str, float] = {}
        self.price_history: dict[str, deque[float]] = {}
        self.factors: dict[str, dict[str, float | str | None]] = {}
        self.last_received_at: datetime | None = None
        self.last_tick_at: datetime | None = None
        self.last_error: str | None = None
        self.ticks_processed = 0
        self.poll_count = 0
        self._task: asyncio.Task[None] | None = None
        self._candidate_task: asyncio.Task[None] | None = None
        self._handlers: list[BatchHandler] = []
        self._lock = asyncio.Lock()
        self._pending_backfill_symbols = set(self.watchlist)
        self._calendar_checked_through: date | None = None
        self._continuity_failures: list[dict[str, str]] = []
        self._continuity_backfill_runs = 0
        self._continuity_last_attempt_at: datetime | None = None
        self._continuity_last_recovered_at: datetime | None = None

    def add_handler(self, handler: BatchHandler) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    async def start(self) -> None:
        if not self.settings.enable_realtime_compute or self._task is not None:
            return
        if self.gateway is None:
            try:
                self.gateway = await asyncio.to_thread(XtQuantGateway)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                return
        self._task = asyncio.create_task(self._poll_loop(), name="realtime-computation")
        if self.settings.realtime_small_cap_candidates:
            self._candidate_task = asyncio.create_task(
                self._refresh_candidates_safely(), name="realtime-candidate-refresh"
            )

    async def stop(self) -> None:
        candidate_task = self._candidate_task
        if candidate_task is not None:
            candidate_task.cancel()
            try:
                await candidate_task
            except asyncio.CancelledError:
                pass
            self._candidate_task = None
        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _refresh_candidates_safely(self) -> None:
        try:
            await self.refresh_mainboard_candidates(self.settings.realtime_small_cap_candidates)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"candidate refresh failed: {type(exc).__name__}: {exc}"

    async def set_watchlist(self, symbols: list[str]) -> None:
        normalized = {item.strip().upper() for item in symbols if item.strip()}
        if len(normalized) > 500:
            raise ValueError("real-time watchlist is limited to 500 symbols")
        async with self._lock:
            added = normalized - self.watchlist
            self.watchlist = normalized
            self._pending_backfill_symbols.intersection_update(normalized)
            self._pending_backfill_symbols.update(added)

    async def refresh_mainboard_candidates(self, limit: int = 50) -> list[str]:
        if limit <= 0 or limit > 500:
            raise ValueError("candidate limit must be between 1 and 500")
        gateway = self.gateway or XtQuantGateway()
        self.gateway = gateway

        def load() -> tuple[list[str], dict[str, float]]:
            symbols = [
                item
                for item in gateway.list_a_share_universe(include_bj=False)
                if is_main_board_stock(item)
            ]
            ranked: list[tuple[float, str, float]] = []
            for symbol in symbols:
                detail = gateway.instrument_detail(symbol)
                name = str(detail.get("InstrumentName", "")).upper()
                float_volume = float(detail.get("FloatVolume", 0) or 0)
                reference = float(detail.get("PreClose", 0) or 0)
                if float_volume <= 0 or reference <= 0 or "ST" in name:
                    continue
                ranked.append((float_volume * reference, symbol, float_volume))
            selected = sorted(ranked)[:limit]
            return [item[1] for item in selected], {item[1]: item[2] for item in selected}

        candidates, shares = await asyncio.to_thread(load)
        async with self._lock:
            added = set(candidates) - self.watchlist
            self.mainboard_candidates = candidates
            self.float_shares.update(shares)
            self.watchlist.update(candidates)
            self._pending_backfill_symbols.update(added)
        return candidates

    async def poll_once(self) -> None:
        try:
            await self._poll_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            async with self._lock:
                self._pending_backfill_symbols.update(self.watchlist)
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise
        else:
            self.last_error = None

    async def _poll_once(self) -> None:
        gateway = self.gateway
        if gateway is None:
            raise RuntimeError("XtData gateway is not initialized")
        today = datetime.now(CHINA_TZ).date()
        async with self._lock:
            symbols = sorted(self.watchlist)
            if self._calendar_checked_through != today:
                self._pending_backfill_symbols.update(symbols)
            pending = sorted(self._pending_backfill_symbols.intersection(symbols))
        if not symbols:
            return
        if pending:
            await self._backfill_tail(pending)
        received_at = datetime.now(UTC)
        payloads: dict[str, Any] = {}
        for offset in range(0, len(symbols), 100):
            batch = await asyncio.to_thread(gateway.full_tick, symbols[offset : offset + 100])
            payloads.update(batch)
        ticks: list[Tick] = []
        bars: list[Bar] = []
        for symbol, raw in payloads.items():
            if not isinstance(raw, dict):
                continue
            update = self.aggregator.update(symbol, raw, received_at)
            if update is None:
                continue
            tick, bar = update
            self.intraday_aggregator.update(symbol, tick, raw, received_at)
            ticks.append(tick)
            bars.append(bar)
            history = self.price_history.setdefault(
                symbol, deque(maxlen=self.settings.realtime_factor_lookback)
            )
            history.append(float(tick.last_price))
        self.poll_count += 1
        self.last_received_at = received_at
        if ticks:
            self.last_tick_at = max(item.timestamp for item in ticks)
            self.ticks_processed += len(ticks)
            self._calculate_factors(bars)
            for handler in tuple(self._handlers):
                result = handler(ticks, bars)
                if inspect.isawaitable(result):
                    await result
        finalized = [
            *self.aggregator.drain_finalized(),
            *self.intraday_aggregator.drain_finalized(),
        ]
        if finalized and self.continuity is not None:
            await asyncio.to_thread(self.continuity.persist_finalized, finalized)

    def snapshot(self) -> dict[str, object]:
        now = datetime.now(UTC)
        quote_age = (
            (now - self.last_tick_at).total_seconds() if self.last_tick_at is not None else None
        )
        bars = self.aggregator.bars()
        return {
            "running": self._task is not None and not self._task.done(),
            "watchlist": sorted(self.watchlist),
            "watchlist_count": len(self.watchlist),
            "mainboard_candidate_count": len(self.mainboard_candidates),
            "mainboard_candidates": self.mainboard_candidates,
            "poll_interval_seconds": self.settings.realtime_poll_interval_seconds,
            "poll_count": self.poll_count,
            "ticks_processed": self.ticks_processed,
            "last_received_at": self.last_received_at.isoformat()
            if self.last_received_at
            else None,
            "last_tick_at": self.last_tick_at.isoformat() if self.last_tick_at else None,
            "quote_age_seconds": quote_age,
            "market_fresh": (
                quote_age is not None
                and 0 <= quote_age <= 10
                and not self._pending_backfill_symbols
                and self.last_error is None
            ),
            "continuity_recovery_required": bool(self._pending_backfill_symbols),
            "continuity_adapter_configured": self.continuity is not None,
            "continuity_pending_symbols": sorted(self._pending_backfill_symbols),
            "continuity_backfill_periods": list(CONTINUITY_BACKFILL_PERIODS),
            "continuity_backfill_runs": self._continuity_backfill_runs,
            "continuity_last_attempt_at": self._continuity_last_attempt_at.isoformat()
            if self._continuity_last_attempt_at
            else None,
            "continuity_last_recovered_at": self._continuity_last_recovered_at.isoformat()
            if self._continuity_last_recovered_at
            else None,
            "continuity_failures": list(self._continuity_failures),
            "calendar_checked_through": self._calendar_checked_through.isoformat()
            if self._calendar_checked_through
            else None,
            "rejected_daily_ticks": self.aggregator.rejection_counts(),
            "rejected_intraday_ticks": self.intraday_aggregator.rejection_counts(),
            "bars": [self._bar_payload(item) for item in bars.values()],
            "intraday_bars": [
                self._bar_payload(bar)
                for symbol in sorted(self.watchlist)
                for period in PERIOD_INTERVALS
                if (bar := self.intraday_aggregator.current(symbol, period)) is not None
            ],
            "factors": list(self.factors.values()),
            "last_error": self.last_error,
            "orders_sent": 0,
        }

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self.poll_once()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(self.settings.realtime_poll_interval_seconds)
                continue
            await asyncio.sleep(self.settings.realtime_poll_interval_seconds)

    def _calculate_factors(self, bars: list[Bar]) -> None:
        raw: list[tuple[str, float, float, float | None]] = []
        for bar in bars:
            history = self.price_history.get(bar.symbol)
            momentum = (
                history[-1] / history[0] - 1
                if history is not None and len(history) >= 2 and history[0] > 0
                else 0.0
            )
            intraday = (
                float(bar.close / bar.pre_close - 1)
                if bar.pre_close is not None and bar.pre_close > 0
                else 0.0
            )
            shares = self.float_shares.get(bar.symbol)
            market_cap = shares * float(bar.close) if shares is not None else None
            raw.append((bar.symbol, momentum, intraday, market_cap))
        caps = sorted(item[3] for item in raw if item[3] is not None)
        momentums = sorted(item[1] for item in raw)
        for symbol, momentum, intraday, market_cap in raw:
            momentum_rank = _percentile_rank(momentums, momentum)
            small_cap_rank = (
                1 - _percentile_rank(caps, market_cap) if market_cap is not None else 0.0
            )
            self.factors[symbol] = {
                "symbol": symbol,
                "tick_momentum": momentum,
                "intraday_return": intraday,
                "float_market_cap": market_cap,
                "momentum_rank": momentum_rank,
                "small_cap_rank": small_cap_rank,
                "composite_score": momentum_rank * 0.7 + small_cap_rank * 0.3,
                "updated_at": self.last_received_at.isoformat() if self.last_received_at else None,
            }

    @staticmethod
    def _bar_payload(bar: Bar) -> dict[str, object]:
        return {
            "symbol": bar.symbol,
            "timestamp": bar.timestamp.isoformat(),
            "period": bar.interval.value,
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
            "adjust_mode": bar.adjustment,
        }

    async def _backfill_tail(self, symbols: list[str]) -> None:
        gateway = self.gateway
        if gateway is None:
            raise RuntimeError("XtData gateway is not initialized")
        now = datetime.now(CHINA_TZ)
        start = (now.date() - timedelta(days=7)).strftime("%Y%m%d")
        end = now.date().strftime("%Y%m%d")
        self._continuity_backfill_runs += 1
        self._continuity_last_attempt_at = datetime.now(UTC)
        try:
            trading_days = await asyncio.to_thread(gateway.trading_days, start, end)
        except Exception as exc:
            self._continuity_failures = [
                {
                    "symbol": "*",
                    "period": "calendar",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            ]
            raise RuntimeError(f"continuity trading calendar failed: {exc}") from exc
        self.aggregator.set_trading_days(trading_days)
        self.intraday_aggregator.set_trading_days(trading_days)
        failures: list[dict[str, str]] = []
        if self.continuity is not None:
            for symbol in symbols:
                for period in CONTINUITY_BACKFILL_PERIODS:
                    try:
                        await asyncio.to_thread(
                            self.continuity.backfill,
                            gateway,
                            symbol=symbol,
                            period=period,
                            start_time=start,
                            end_time=end,
                            adjust_mode="none",
                        )
                    except Exception as exc:
                        failures.append(
                            {
                                "symbol": symbol,
                                "period": period,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
        self._continuity_failures = failures
        if failures:
            summary = ", ".join(
                f"{item['symbol']}/{item['period']}: {item['error']}" for item in failures
            )
            raise RuntimeError(f"continuity backfill failed: {summary}")
        self.aggregator.reset_symbols(symbols)
        self.intraday_aggregator.reset_symbols(symbols)
        async with self._lock:
            self._pending_backfill_symbols.difference_update(symbols)
            self._calendar_checked_through = now.date()
        self._continuity_last_recovered_at = datetime.now(UTC)


def _percentile_rank(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    below = sum(item < value for item in values)
    equal = sum(item == value for item in values)
    return (below + equal * 0.5) / len(values)
