from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from dyadtetrade.domain import Bar, Interval, Tick
from dyadtetrade.integrations.xtquant_converters import tick_from_xtdata

CHINA_TZ = ZoneInfo("Asia/Shanghai")
PERIOD_INTERVALS = {
    "1m": (1, Interval.MINUTE_1),
    "5m": (5, Interval.MINUTE_5),
    "15m": (15, Interval.MINUTE_15),
    "30m": (30, Interval.MINUTE_30),
    "1h": (60, Interval.HOUR_1),
}
FUTURE_TICK_TOLERANCE = timedelta(seconds=5)


def _normalize_trading_days(values: Iterable[str | date]) -> frozenset[date]:
    result: set[date] = set()
    for value in values:
        if isinstance(value, datetime):
            result.add(value.date())
        elif isinstance(value, date):
            result.add(value)
        else:
            stripped = value.strip()
            result.add(
                datetime.strptime(stripped, "%Y%m%d").date()
                if len(stripped) == 8 and stripped.isdigit()
                else date.fromisoformat(stripped)
            )
    return frozenset(result)


def _is_trading_day(value: date, known_days: frozenset[date] | None) -> bool:
    if known_days is not None:
        return value in known_days
    return value.weekday() < 5


def _positive_decimal(payload: dict[str, Any], name: str, fallback: Decimal) -> Decimal:
    value = Decimal(str(payload.get(name, 0) or 0))
    return value if value > 0 else fallback


@dataclass(slots=True)
class _DailyState:
    trade_date: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    amount: Decimal
    pre_close: Decimal | None
    bar_start: datetime
    last_timestamp: datetime
    received_at: datetime
    observed_before_close: bool
    finalized_signature: tuple[Decimal, Decimal, Decimal, Decimal, int, Decimal] | None = None


class TickDailyBarAggregator:
    """Build a visible-at-now unadjusted daily bar from cumulative XtData ticks."""

    def __init__(self) -> None:
        self._states: dict[str, _DailyState] = {}
        self._signature_queues: dict[str, deque[tuple[datetime, Decimal, int, Decimal]]] = {}
        self._signature_sets: dict[str, set[tuple[datetime, Decimal, int, Decimal]]] = {}
        self._finalized: deque[Bar] = deque()
        self._trading_days: frozenset[date] | None = None
        self._rejections: dict[str, int] = {}

    def set_trading_days(self, values: Iterable[str | date]) -> None:
        self._trading_days = _normalize_trading_days(values)

    def reset_symbols(self, symbols: Iterable[str]) -> None:
        normalized = set(symbols)
        for symbol in normalized:
            self._states.pop(symbol, None)
            self._signature_queues.pop(symbol, None)
            self._signature_sets.pop(symbol, None)
        if normalized:
            self._finalized = deque(bar for bar in self._finalized if bar.symbol not in normalized)

    def rejection_counts(self) -> dict[str, int]:
        return dict(sorted(self._rejections.items()))

    def _reject(self, reason: str) -> None:
        self._rejections[reason] = self._rejections.get(reason, 0) + 1

    def update(
        self, symbol: str, payload: dict[str, Any], received_at: datetime
    ) -> tuple[Tick, Bar] | None:
        tick = tick_from_xtdata(symbol, payload, received_at=received_at)
        if tick.last_price <= 0:
            self._reject("nonpositive_price")
            return None
        if tick.timestamp > received_at + FUTURE_TICK_TOLERANCE:
            self._reject("future_timestamp")
            return None
        local_tick = tick.timestamp.astimezone(CHINA_TZ)
        if not _is_trading_day(local_tick.date(), self._trading_days):
            self._reject("non_trading_day")
            return None
        if not _is_continuous_auction(local_tick.time()):
            self._reject("outside_continuous_auction")
            return None
        volume = int(payload.get("pvolume", payload.get("volume", 0)) or 0)
        amount = Decimal(str(payload.get("amount", 0) or 0))
        signature = (tick.timestamp, tick.last_price, volume, amount)
        signatures = self._signature_sets.setdefault(symbol, set())
        if signature in signatures:
            return None
        queue = self._signature_queues.setdefault(symbol, deque(maxlen=2048))
        if len(queue) == queue.maxlen:
            signatures.discard(queue[0])
        queue.append(signature)
        signatures.add(signature)

        trade_date = local_tick.date().isoformat()
        state = self._states.get(symbol)
        if state is not None and tick.timestamp < state.last_timestamp:
            self._reject("stale_timestamp")
            return None
        if state is None or state.trade_date != trade_date:
            open_price = _positive_decimal(payload, "open", tick.last_price)
            bar_start = datetime.combine(local_tick.date(), time(9, 30), tzinfo=CHINA_TZ)
            state = _DailyState(
                trade_date=trade_date,
                open=open_price,
                high=_positive_decimal(payload, "high", max(open_price, tick.last_price)),
                low=_positive_decimal(payload, "low", min(open_price, tick.last_price)),
                close=tick.last_price,
                volume=max(volume, 0),
                amount=max(amount, Decimal("0")),
                pre_close=(
                    value if (value := Decimal(str(payload.get("lastClose", 0) or 0))) > 0 else None
                ),
                bar_start=bar_start,
                last_timestamp=tick.timestamp,
                received_at=received_at,
                observed_before_close=local_tick.time() < time(15, 0),
            )
            self._states[symbol] = state
        else:
            state.high = max(
                state.high,
                tick.last_price,
                _positive_decimal(payload, "high", tick.last_price),
            )
            state.low = min(
                state.low,
                tick.last_price,
                _positive_decimal(payload, "low", tick.last_price),
            )
            state.close = tick.last_price
            state.volume = max(state.volume, volume)
            state.amount = max(state.amount, amount)
            state.last_timestamp = tick.timestamp
            state.received_at = received_at
            state.observed_before_close = state.observed_before_close or local_tick.time() < time(
                15, 0
            )

        is_final = local_tick.time() >= time(15, 0)
        bar = self._to_bar(symbol, state, is_final=is_final)
        if is_final and state.observed_before_close:
            finalized_signature = (
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
                bar.amount,
            )
            if finalized_signature != state.finalized_signature:
                self._finalized.append(bar)
                state.finalized_signature = finalized_signature
        return tick, bar

    def bars(self) -> dict[str, Bar]:
        result: dict[str, Bar] = {}
        for symbol, state in self._states.items():
            local_tick = state.last_timestamp.astimezone(CHINA_TZ)
            result[symbol] = self._to_bar(symbol, state, is_final=local_tick.time() >= time(15, 0))
        return result

    def drain_finalized(self) -> list[Bar]:
        result = list(self._finalized)
        self._finalized.clear()
        return result

    @staticmethod
    def _to_bar(symbol: str, state: _DailyState, *, is_final: bool) -> Bar:
        return Bar(
            symbol=symbol,
            timestamp=state.bar_start,
            interval=Interval.DAY_1,
            open=state.open,
            high=state.high,
            low=state.low,
            close=state.close,
            volume=state.volume,
            amount=state.amount,
            pre_close=state.pre_close,
            adjustment="none",
            source="xtdata_tick_aggregate",
            is_final=is_final,
            received_at=state.received_at,
            updated_at=state.received_at,
            quality_status="valid" if is_final else "provisional",
        )


def _is_continuous_auction(value: time) -> bool:
    return time(9, 30) <= value < time(11, 31) or time(13, 0) <= value < time(15, 1)


@dataclass(slots=True)
class _IntradayState:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    amount: Decimal
    received_at: datetime
    eligible_for_finalization: bool


@dataclass(slots=True)
class _CumulativeState:
    trade_date: date
    volume: int
    amount: Decimal
    last_timestamp: datetime


class TickIntradayBarAggregator:
    """Aggregate provisional minute bars from cumulative full-tick counters."""

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], _IntradayState] = {}
        self._last_cumulative: dict[str, _CumulativeState] = {}
        self._latest: dict[tuple[str, str], Bar] = {}
        self._finalized: deque[Bar] = deque()
        self._trading_days: frozenset[date] | None = None
        self._rejections: dict[str, int] = {}

    def set_trading_days(self, values: Iterable[str | date]) -> None:
        self._trading_days = _normalize_trading_days(values)

    def reset_symbols(self, symbols: Iterable[str]) -> None:
        normalized = set(symbols)
        for symbol in normalized:
            self._last_cumulative.pop(symbol, None)
        self._states = {
            key: state for key, state in self._states.items() if key[0] not in normalized
        }
        self._latest = {key: bar for key, bar in self._latest.items() if key[0] not in normalized}
        if normalized:
            self._finalized = deque(bar for bar in self._finalized if bar.symbol not in normalized)

    def rejection_counts(self) -> dict[str, int]:
        return dict(sorted(self._rejections.items()))

    def _reject(self, reason: str) -> None:
        self._rejections[reason] = self._rejections.get(reason, 0) + 1

    def update(
        self,
        symbol: str,
        tick: Tick,
        payload: dict[str, Any],
        received_at: datetime,
    ) -> list[Bar]:
        local = tick.timestamp.astimezone(CHINA_TZ)
        if tick.timestamp > received_at + FUTURE_TICK_TOLERANCE:
            self._reject("future_timestamp")
            return []
        if not _is_trading_day(local.date(), self._trading_days):
            self._reject("non_trading_day")
            return []
        if not _is_continuous_auction(local.time()):
            self._reject("outside_continuous_auction")
            return []
        cumulative_volume = max(int(payload.get("pvolume", payload.get("volume", 0)) or 0), 0)
        cumulative_amount = max(Decimal(str(payload.get("amount", 0) or 0)), Decimal("0"))
        previous = self._last_cumulative.get(symbol)
        if previous is not None and tick.timestamp < previous.last_timestamp:
            self._reject("stale_timestamp")
            return []
        if previous is None or previous.trade_date != local.date():
            volume_delta = 0
            amount_delta = Decimal("0")
            self._last_cumulative[symbol] = _CumulativeState(
                local.date(), cumulative_volume, cumulative_amount, tick.timestamp
            )
        else:
            volume_delta = max(cumulative_volume - previous.volume, 0)
            amount_delta = max(cumulative_amount - previous.amount, Decimal("0"))
            if cumulative_volume < previous.volume or cumulative_amount < previous.amount:
                self._reject("cumulative_counter_regression")
            previous.volume = max(previous.volume, cumulative_volume)
            previous.amount = max(previous.amount, cumulative_amount)
            previous.last_timestamp = tick.timestamp
        result: list[Bar] = []
        for period, (minutes, interval) in PERIOD_INTERVALS.items():
            start = _intraday_bar_start(local, minutes)
            key = (symbol, period)
            state = self._states.get(key)
            if state is None or state.start != start:
                contiguous_transition = False
                if state is not None and state.start.date() == start.date() and state.start < start:
                    contiguous_transition = True
                    if state.eligible_for_finalization:
                        self._finalized.append(self._to_bar(symbol, interval, state, is_final=True))
                state = _IntradayState(
                    start=start,
                    open=tick.last_price,
                    high=tick.last_price,
                    low=tick.last_price,
                    close=tick.last_price,
                    volume=volume_delta,
                    amount=amount_delta,
                    received_at=received_at,
                    eligible_for_finalization=contiguous_transition,
                )
                self._states[key] = state
            else:
                state.high = max(state.high, tick.last_price)
                state.low = min(state.low, tick.last_price)
                state.close = tick.last_price
                state.volume += volume_delta
                state.amount += amount_delta
                state.received_at = received_at
            bar = self._to_bar(symbol, interval, state, is_final=False)
            self._latest[key] = bar
            result.append(bar)
        return result

    def current(self, symbol: str, period: str) -> Bar | None:
        return self._latest.get((symbol, period))

    def drain_finalized(self) -> list[Bar]:
        result = list(self._finalized)
        self._finalized.clear()
        return result

    @staticmethod
    def _to_bar(
        symbol: str,
        interval: Interval,
        state: _IntradayState,
        *,
        is_final: bool,
    ) -> Bar:
        return Bar(
            symbol=symbol,
            timestamp=state.start,
            interval=interval,
            open=state.open,
            high=state.high,
            low=state.low,
            close=state.close,
            volume=state.volume,
            amount=state.amount,
            adjustment="none",
            source="xtdata_tick_aggregate",
            is_final=is_final,
            received_at=state.received_at,
            updated_at=state.received_at,
            quality_status="valid" if is_final else "provisional",
        )


def _intraday_bar_start(local: datetime, minutes: int) -> datetime:
    session_start = time(9, 30) if local.time() <= time(11, 30) else time(13, 0)
    start = datetime.combine(local.date(), session_start, tzinfo=CHINA_TZ)
    if minutes == 1:
        if time(11, 30) <= local.time() < time(11, 31):
            return datetime.combine(local.date(), time(11, 30), tzinfo=CHINA_TZ)
        if time(15, 0) <= local.time() < time(15, 1):
            return datetime.combine(local.date(), time(15, 0), tzinfo=CHINA_TZ)
        return local.replace(second=0, microsecond=0) + timedelta(minutes=1)
    if time(11, 30) <= local.time() < time(11, 31):
        return datetime.combine(local.date(), time(11, 30), tzinfo=CHINA_TZ)
    if time(15, 0) <= local.time() < time(15, 1):
        return datetime.combine(local.date(), time(15, 0), tzinfo=CHINA_TZ)
    elapsed = int((local - start).total_seconds() // 60)
    return start + timedelta(minutes=((elapsed // minutes) + 1) * minutes)
