from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dyadtetrade.modules.monitoring.strategy import (
    strategy_health,
    strategy_recovery_status,
    strategy_runtime_seconds,
)
from dyadtetrade.modules.paper_trading import PaperTradingService


def test_strategy_health_distinguishes_healthy_stale_stopped_and_error() -> None:
    now = datetime(2026, 7, 13, 9, 30, tzinfo=UTC)
    assert strategy_health(now - timedelta(seconds=2), status="running", now=now).health == (
        "healthy"
    )
    assert strategy_health(now - timedelta(seconds=16), status="running", now=now).health == (
        "stale"
    )
    assert strategy_health(now, status="stopped", now=now).health == "stopped"
    assert strategy_health(now, status="running", error="worker crashed", now=now).health == (
        "error"
    )


def test_strategy_runtime_and_recovery_status_are_deterministic() -> None:
    started = datetime(2026, 7, 13, 1, 30)
    stopped = datetime(2026, 7, 13, 1, 32)
    assert strategy_runtime_seconds(started, stopped_at=stopped) == 120
    assert strategy_runtime_seconds(datetime.min) is None
    assert strategy_recovery_status(status="running", attached=True) == "attached"
    assert strategy_recovery_status(status="running", attached=False) == "awaiting_recovery"
    assert strategy_recovery_status(status="stopped", attached=False) == "not_required"


def test_paper_runtime_clock_is_valid_before_first_market_event() -> None:
    service = PaperTradingService()
    assert service.broker.now.year >= 2000
