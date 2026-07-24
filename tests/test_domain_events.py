from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dyadtetrade.domain import Direction, EventType, Order, OrderRequest, OrderStatus
from dyadtetrade.domain.orders import LEGAL_ORDER_TRANSITIONS, can_transition
from dyadtetrade.events import Event, EventBus, EventQueueFull


def test_order_status_machine_and_fill() -> None:
    order = Order("O1", OrderRequest("600519.SH", Direction.BUY, 200))
    order.transition(OrderStatus.SUBMITTING)
    order.transition(OrderStatus.SUBMITTED)
    order.apply_fill(100, Decimal("100"), datetime.now(UTC))
    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.remaining_quantity == 100
    order.apply_fill(100, Decimal("102"), datetime.now(UTC))
    assert order.status is OrderStatus.FILLED
    assert order.average_fill_price == Decimal("101")
    with pytest.raises(ValueError, match="illegal"):
        order.transition(OrderStatus.CANCELLED)


@given(st.sampled_from(list(OrderStatus)), st.sampled_from(list(OrderStatus)))
def test_order_transitions_match_transition_table(
    current: OrderStatus, target: OrderStatus
) -> None:
    assert can_transition(current, target) is (target in LEGAL_ORDER_TRANSITIONS[current])


def test_event_bus_isolates_errors_and_replays() -> None:
    values: list[int] = []
    errors = []
    bus = EventBus(error_handler=errors.append)

    def good(event: Event) -> None:
        values.append(int(event.payload))

    def bad(_: Event) -> None:
        raise RuntimeError("isolated")

    bus.subscribe(EventType.BAR, bad)
    bus.subscribe(EventType.BAR, good)
    dispatch_errors = bus.publish(Event(EventType.BAR, 7, trace_id="trace-1"))
    assert values == [7]
    assert len(dispatch_errors) == len(errors) == 1
    assert bus.history[0].trace_id == "trace-1"
    bus.unsubscribe(EventType.BAR, bad)
    bus.replay()
    assert values == [7, 7]
    assert bus.snapshot()["dead_letter_count"] == 1


def test_event_bus_general_handlers_bounded_history_and_backpressure() -> None:
    seen: list[EventType] = []
    bus = EventBus(history_limit=2, queue_limit=1)

    def observe(event: Event) -> None:
        seen.append(event.event_type)

    bus.subscribe_all(observe)
    bus.publish(Event(EventType.BAR))
    bus.publish(Event(EventType.TICK))
    bus.publish(Event(EventType.ORDER))
    assert seen == [EventType.BAR, EventType.TICK, EventType.ORDER]
    assert [event.event_type for event in bus.history] == [EventType.TICK, EventType.ORDER]

    bus.enqueue(Event(EventType.TRADE))
    with pytest.raises(EventQueueFull, match="queue is full"):
        bus.enqueue(Event(EventType.POSITION))
    snapshot = bus.snapshot()
    assert snapshot["enqueued_count"] == 1
    assert snapshot["dropped_count"] == 1
    assert snapshot["general_handler_count"] == 1
    bus.start_worker()
    bus.wait_until_idle()
    bus.close()
    assert bus.snapshot()["worker_running"] is False
    bus.unsubscribe_all(observe)


@pytest.mark.asyncio
async def test_event_bus_supports_async_handlers_and_timer() -> None:
    received = asyncio.Event()
    bus = EventBus()

    async def handler(_: Event) -> None:
        received.set()

    bus.subscribe(EventType.TIMER, handler)
    bus.start_timer(0.01)
    try:
        await asyncio.wait_for(received.wait(), timeout=1)
    finally:
        bus.stop_timer()
    assert any(event.event_type is EventType.TIMER for event in bus.history)


@given(st.integers(min_value=1, max_value=10000), st.integers(min_value=1, max_value=10000))
def test_fill_never_exceeds_remaining(quantity: int, attempted: int) -> None:
    order = Order("O", OrderRequest("510300.SH", Direction.BUY, quantity))
    order.transition(OrderStatus.SUBMITTING)
    order.transition(OrderStatus.SUBMITTED)
    if attempted <= quantity:
        order.apply_fill(attempted, Decimal("4"), datetime.now(UTC))
        assert order.filled_quantity <= quantity
    else:
        with pytest.raises(ValueError):
            order.apply_fill(attempted, Decimal("4"), datetime.now(UTC))
