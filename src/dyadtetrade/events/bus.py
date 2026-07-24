from __future__ import annotations

import asyncio
import inspect
import logging
import queue
import threading
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from dyadtetrade.domain.enums import EventType

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Event:
    event_type: EventType
    payload: Any = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    source: str = "dyadtetrade"
    sequence: int = 0


@dataclass(frozen=True, slots=True)
class EventDispatchError:
    event: Event
    handler_name: str
    error_type: str
    message: str


EventHandler = Callable[[Event], None | Awaitable[None]]
ErrorHandler = Callable[[EventDispatchError], None]


class EventQueueFull(RuntimeError):
    """Raised when a callback producer exceeds the configured event backlog."""


class EventBus:
    """Thread-safe event engine with bounded replay, backpressure and fault isolation."""

    def __init__(
        self,
        *,
        error_handler: ErrorHandler | None = None,
        history_limit: int = 10_000,
        dead_letter_limit: int = 1_000,
        queue_limit: int = 10_000,
    ) -> None:
        if history_limit < 0 or dead_letter_limit < 0 or queue_limit < 0:
            raise ValueError("event bus limits must be non-negative")
        self._handlers: defaultdict[EventType, list[EventHandler]] = defaultdict(list)
        self._general_handlers: list[EventHandler] = []
        self._lock = threading.RLock()
        self._history: deque[Event] = deque(maxlen=history_limit)
        self._dead_letters: deque[EventDispatchError] = deque(maxlen=dead_letter_limit)
        self._sequence = 0
        self._published_count = 0
        self._enqueued_count = 0
        self._dropped_count = 0
        self._error_handler = error_handler
        self._timer_stop = threading.Event()
        self._timer_thread: threading.Thread | None = None
        self._event_queue: queue.Queue[Event] = queue.Queue(maxsize=queue_limit)
        self._worker_stop = threading.Event()
        self._worker_thread: threading.Thread | None = None

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)
            if not handlers:
                self._handlers.pop(event_type, None)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Register a handler for every event type, matching vn.py's general handlers."""
        with self._lock:
            if handler not in self._general_handlers:
                self._general_handlers.append(handler)

    def unsubscribe_all(self, handler: EventHandler) -> None:
        with self._lock:
            if handler in self._general_handlers:
                self._general_handlers.remove(handler)

    def publish(self, event: Event, *, record: bool = True) -> list[EventDispatchError]:
        sequenced = self._sequence_event(event, record=record)
        errors: list[EventDispatchError] = []
        for handler in self._snapshot_handlers(sequenced.event_type):
            try:
                result = handler(sequenced)
                if inspect.isawaitable(result):
                    self._run_awaitable(result)
            except Exception as exc:  # handler isolation is intentional
                errors.append(self._handle_error(sequenced, handler, exc))
        return errors

    async def publish_async(self, event: Event, *, record: bool = True) -> list[EventDispatchError]:
        sequenced = self._sequence_event(event, record=record)
        errors: list[EventDispatchError] = []
        for handler in self._snapshot_handlers(sequenced.event_type):
            try:
                result = handler(sequenced)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:  # handler isolation is intentional
                errors.append(self._handle_error(sequenced, handler, exc))
        return errors

    def enqueue(self, event: Event) -> None:
        """Queue an event so producer/callback threads never execute consumers."""
        try:
            self._event_queue.put_nowait(event)
        except queue.Full as exc:
            with self._lock:
                self._dropped_count += 1
            raise EventQueueFull(
                f"event queue is full; rejected {event.event_type.value} from {event.source}"
            ) from exc
        with self._lock:
            self._enqueued_count += 1

    def start_worker(self) -> None:
        with self._lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._worker_stop.clear()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="dyadtetrade-event-worker",
                daemon=True,
            )
            self._worker_thread.start()

    def stop_worker(self, timeout: float = 2.0) -> None:
        self._worker_stop.set()
        thread = self._worker_thread
        if thread is not None:
            thread.join(timeout)
        self._worker_thread = None

    def wait_until_idle(self) -> None:
        self._event_queue.join()

    def close(self) -> None:
        """Stop timer and worker threads during gateway/application shutdown."""
        self.stop_timer()
        self.stop_worker()

    def replay(self, events: Iterable[Event] | None = None) -> list[EventDispatchError]:
        source = tuple(events) if events is not None else self.history
        errors: list[EventDispatchError] = []
        for event in source:
            errors.extend(self.publish(event, record=False))
        return errors

    @property
    def history(self) -> tuple[Event, ...]:
        with self._lock:
            return tuple(self._history)

    @property
    def dead_letters(self) -> tuple[EventDispatchError, ...]:
        with self._lock:
            return tuple(self._dead_letters)

    def snapshot(self) -> dict[str, int | bool]:
        with self._lock:
            return {
                "last_sequence": self._sequence,
                "published_count": self._published_count,
                "enqueued_count": self._enqueued_count,
                "dropped_count": self._dropped_count,
                "pending_count": self._event_queue.qsize(),
                "history_count": len(self._history),
                "dead_letter_count": len(self._dead_letters),
                "typed_handler_count": sum(len(items) for items in self._handlers.values()),
                "general_handler_count": len(self._general_handlers),
                "worker_running": bool(
                    self._worker_thread is not None and self._worker_thread.is_alive()
                ),
                "timer_running": bool(
                    self._timer_thread is not None and self._timer_thread.is_alive()
                ),
            }

    def start_timer(self, interval_seconds: float = 1.0) -> None:
        if interval_seconds <= 0:
            raise ValueError("timer interval must be positive")
        with self._lock:
            if self._timer_thread is not None and self._timer_thread.is_alive():
                return
            self._timer_stop.clear()
            self._timer_thread = threading.Thread(
                target=self._timer_loop,
                args=(interval_seconds,),
                name="dyadtetrade-event-timer",
                daemon=True,
            )
            self._timer_thread.start()

    def stop_timer(self, timeout: float = 2.0) -> None:
        self._timer_stop.set()
        thread = self._timer_thread
        if thread is not None:
            thread.join(timeout)
        self._timer_thread = None

    def _timer_loop(self, interval_seconds: float) -> None:
        while not self._timer_stop.wait(interval_seconds):
            self.publish(Event(EventType.TIMER, source="event_bus"))

    def _worker_loop(self) -> None:
        while not self._worker_stop.is_set() or not self._event_queue.empty():
            try:
                event = self._event_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            try:
                self.publish(event)
            finally:
                self._event_queue.task_done()

    def _sequence_event(self, event: Event, *, record: bool) -> Event:
        with self._lock:
            self._sequence += 1
            self._published_count += 1
            sequenced = Event(
                event_type=event.event_type,
                payload=event.payload,
                occurred_at=event.occurred_at,
                trace_id=event.trace_id,
                source=event.source,
                sequence=self._sequence,
            )
            if record:
                self._history.append(sequenced)
            return sequenced

    def _snapshot_handlers(self, event_type: EventType) -> tuple[EventHandler, ...]:
        with self._lock:
            return (*self._handlers.get(event_type, ()), *self._general_handlers)

    def _handle_error(
        self, event: Event, handler: EventHandler, exc: Exception
    ) -> EventDispatchError:
        error = EventDispatchError(
            event=event,
            handler_name=getattr(handler, "__qualname__", repr(handler)),
            error_type=type(exc).__name__,
            message=str(exc),
        )
        logger.exception(
            "event_handler_failed",
            extra={"trace_id": event.trace_id, "event_type": event.event_type.value},
        )
        with self._lock:
            self._dead_letters.append(error)
        if self._error_handler is not None:
            try:
                self._error_handler(error)
            except Exception:  # the diagnostic hook must never stop event delivery
                logger.exception("event_error_handler_failed")
        return error

    @staticmethod
    def _run_awaitable(awaitable: Awaitable[None]) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(EventBus._await_result(awaitable))
            return
        raise RuntimeError("publish() cannot await inside a running loop; use publish_async()")

    @staticmethod
    async def _await_result(awaitable: Awaitable[None]) -> None:
        await awaitable
