from __future__ import annotations

import asyncio
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any, Protocol


class XtDataSubscriptionApi(Protocol):
    def subscribe_quote(
        self,
        stock_code: str,
        period: str = "tick",
        start_time: str = "",
        end_time: str = "",
        count: int = 0,
        callback: object | None = None,
    ) -> int: ...

    def subscribe_whole_quote(
        self, code_list: list[str], callback: object | None = None
    ) -> int: ...

    def unsubscribe_quote(self, seq: int) -> object: ...


class XtDataSubscriptionService:
    """Bounded XtData callback bridge; callback threads only enqueue raw snapshots."""

    def __init__(
        self, xtdata_api: XtDataSubscriptionApi | None = None, max_queue: int = 256
    ) -> None:
        self._xtdata_api = xtdata_api
        self._queue: deque[tuple[datetime, dict[str, Any]]] = deque(maxlen=max_queue)
        self._queue_lock = threading.Lock()
        self._wake: asyncio.Event | None = None
        self._worker: asyncio.Task[None] | None = None
        self._subscriptions: dict[int, dict[str, object]] = {}
        self._latest: dict[str, dict[str, Any]] = {}
        self.received_batches = 0
        self.received_symbols = 0
        self.dropped_batches = 0
        self.last_received_at: datetime | None = None
        self.last_error: str | None = None

    async def subscribe_single(self, symbols: list[str], period: str = "tick") -> dict[str, object]:
        normalized = sorted({item.strip().upper() for item in symbols if item.strip()})
        if not normalized or len(normalized) > 50:
            raise ValueError("single-symbol subscriptions require 1 to 50 symbols")
        if period not in {"tick", "1m", "5m", "15m", "30m", "60m", "1d"}:
            raise ValueError("unsupported subscription period")
        await self._ensure_worker()
        api = self._api
        for symbol in normalized:
            seq = await asyncio.to_thread(
                api.subscribe_quote,
                symbol,
                period,
                "",
                "",
                0,
                self._callback,
            )
            if seq <= 0:
                raise RuntimeError(f"XtData subscription failed for {symbol}")
            self._subscriptions[seq] = {
                "mode": "single",
                "symbol": symbol,
                "period": period,
            }
        return self.snapshot()

    async def subscribe_whole(self, markets: list[str]) -> dict[str, object]:
        normalized = sorted({item.strip().upper() for item in markets if item.strip()})
        if not normalized or any(item not in {"SH", "SZ"} for item in normalized):
            raise ValueError("whole-market subscription only accepts SH and SZ")
        await self._ensure_worker()
        seq = await asyncio.to_thread(
            self._api.subscribe_whole_quote,
            normalized,
            self._callback,
        )
        if seq <= 0:
            raise RuntimeError("XtData whole-market subscription failed")
        self._subscriptions[seq] = {"mode": "whole", "markets": normalized, "period": "tick"}
        return self.snapshot()

    async def stop(self) -> None:
        api = self._xtdata_api
        if api is not None:
            for seq in tuple(self._subscriptions):
                try:
                    await asyncio.to_thread(api.unsubscribe_quote, seq)
                except Exception as exc:
                    self.last_error = f"unsubscribe: {type(exc).__name__}"
        self._subscriptions.clear()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None
        self._wake = None

    def snapshot(self) -> dict[str, object]:
        now = datetime.now(UTC)
        age = (
            (now - self.last_received_at).total_seconds()
            if self.last_received_at is not None
            else None
        )
        with self._queue_lock:
            queue_depth = len(self._queue)
        return {
            "active": bool(self._subscriptions),
            "subscriptions": list(self._subscriptions.values()),
            "subscription_count": len(self._subscriptions),
            "latest_symbol_count": len(self._latest),
            "received_batches": self.received_batches,
            "received_symbols": self.received_symbols,
            "dropped_batches": self.dropped_batches,
            "queue_depth": queue_depth,
            "last_received_at": self.last_received_at.isoformat()
            if self.last_received_at
            else None,
            "quote_age_seconds": age,
            "fresh": age is not None and age <= 10,
            "last_error": self.last_error,
        }

    def latest(self, symbols: list[str] | None = None, limit: int = 200) -> dict[str, object]:
        if limit <= 0 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        selected = sorted({item.upper() for item in symbols or []})
        keys = selected if selected else sorted(self._latest)[:limit]
        return {key: self._latest[key] for key in keys[:limit] if key in self._latest}

    def _callback(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        received_at = datetime.now(UTC)
        with self._queue_lock:
            if len(self._queue) == self._queue.maxlen:
                self._queue.popleft()
                self.dropped_batches += 1
            self._queue.append((received_at, payload))
        wake = self._wake
        if wake is not None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                loop = self._worker.get_loop() if self._worker is not None else None
                if loop is not None:
                    loop.call_soon_threadsafe(wake.set)
            else:
                wake.set()

    async def _ensure_worker(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._wake = asyncio.Event()
        self._worker = asyncio.create_task(self._consume(), name="xtdata-subscription-consumer")

    async def _consume(self) -> None:
        assert self._wake is not None
        while True:
            await self._wake.wait()
            self._wake.clear()
            while True:
                with self._queue_lock:
                    if not self._queue:
                        break
                    received_at, batch = self._queue.popleft()
                self._apply_batch(received_at, batch)

    def _apply_batch(self, received_at: datetime, batch: dict[str, Any]) -> None:
        updated = 0
        for symbol, raw in batch.items():
            value = raw[-1] if isinstance(raw, list) and raw else raw
            if not isinstance(value, dict):
                continue
            self._latest[str(symbol).upper()] = {**value, "received_at": received_at.isoformat()}
            updated += 1
        self.received_batches += 1
        self.received_symbols += updated
        self.last_received_at = received_at
        self.last_error = None

    @property
    def _api(self) -> XtDataSubscriptionApi:
        if self._xtdata_api is None:
            from xtquant import xtdata

            self._xtdata_api = xtdata
        return self._xtdata_api


xtdata_subscription_service = XtDataSubscriptionService()
