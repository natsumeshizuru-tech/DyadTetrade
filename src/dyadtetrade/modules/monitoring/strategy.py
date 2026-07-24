from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

HEARTBEAT_STALE_SECONDS = 15.0
TERMINAL_STATUSES = frozenset({"stopped", "completed", "cancelled"})
ERROR_STATUSES = frozenset({"error", "failed", "crashed"})


@dataclass(frozen=True, slots=True)
class StrategyHealth:
    health: str
    heartbeat_age_seconds: float | None


def strategy_health(
    heartbeat: datetime | None,
    *,
    status: str,
    error: str | None = None,
    now: datetime | None = None,
) -> StrategyHealth:
    """Normalize persisted and in-memory runtimes into one health vocabulary."""
    normalized_status = status.strip().lower()
    if error or normalized_status in ERROR_STATUSES:
        return StrategyHealth("error", _heartbeat_age(heartbeat, now=now))
    if normalized_status in TERMINAL_STATUSES:
        return StrategyHealth("stopped", _heartbeat_age(heartbeat, now=now))
    age = _heartbeat_age(heartbeat, now=now)
    if age is None:
        return StrategyHealth("waiting", None)
    return StrategyHealth(
        "healthy" if age <= HEARTBEAT_STALE_SECONDS else "stale",
        age,
    )


def strategy_runtime_seconds(
    started_at: datetime | None,
    *,
    stopped_at: datetime | None = None,
    now: datetime | None = None,
) -> float | None:
    if started_at is None or started_at.year < 2000:
        return None
    observed_at = stopped_at or now or datetime.now(UTC)
    return max((_as_utc(observed_at) - _as_utc(started_at)).total_seconds(), 0.0)


def strategy_recovery_status(*, status: str, attached: bool) -> str:
    normalized_status = status.strip().lower()
    if normalized_status in TERMINAL_STATUSES:
        return "not_required"
    if attached:
        return "attached"
    return "awaiting_recovery"


def _heartbeat_age(value: datetime | None, *, now: datetime | None) -> float | None:
    if value is None:
        return None
    observed_at = now or datetime.now(UTC)
    return max((_as_utc(observed_at) - _as_utc(value)).total_seconds(), 0.0)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


__all__ = [
    "HEARTBEAT_STALE_SECONDS",
    "StrategyHealth",
    "strategy_health",
    "strategy_recovery_status",
    "strategy_runtime_seconds",
]
