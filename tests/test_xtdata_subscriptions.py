from __future__ import annotations

import asyncio

import pytest

from dyadtetrade.modules.realtime import XtDataSubscriptionService


class FakeXtDataSubscription:
    def __init__(self) -> None:
        self.next_seq = 1
        self.callbacks: dict[int, object] = {}
        self.unsubscribed: list[int] = []

    def subscribe_quote(
        self,
        stock_code: str,
        period: str = "tick",
        start_time: str = "",
        end_time: str = "",
        count: int = 0,
        callback: object | None = None,
    ) -> int:
        del period, start_time, end_time, count
        seq = self._store(callback)
        assert callable(callback)
        callback({stock_code: [{"lastPrice": 10.0, "time": 1}]})
        return seq

    def subscribe_whole_quote(self, code_list: list[str], callback: object | None = None) -> int:
        assert code_list == ["SH", "SZ"]
        seq = self._store(callback)
        assert callable(callback)
        callback(
            {
                "600000.SH": {"lastPrice": 10.1, "time": 2},
                "000001.SZ": {"lastPrice": 11.2, "time": 2},
            }
        )
        return seq

    def unsubscribe_quote(self, seq: int) -> None:
        self.unsubscribed.append(seq)

    def _store(self, callback: object | None) -> int:
        seq = self.next_seq
        self.next_seq += 1
        self.callbacks[seq] = callback
        return seq


@pytest.mark.asyncio
async def test_single_and_whole_market_subscriptions_use_bounded_consumer() -> None:
    fake = FakeXtDataSubscription()
    service = XtDataSubscriptionService(fake, max_queue=2)
    await service.subscribe_single(["600000.SH"], "1m")
    await service.subscribe_whole(["SZ", "SH"])
    await asyncio.sleep(0.05)
    snapshot = service.snapshot()
    assert snapshot["subscription_count"] == 2
    assert snapshot["latest_symbol_count"] == 2
    assert snapshot["fresh"] is True
    assert service.latest(["600000.SH"])["600000.SH"]["lastPrice"] == 10.1  # type: ignore[index]
    await service.stop()
    assert fake.unsubscribed == [1, 2]


@pytest.mark.asyncio
async def test_subscription_limits_and_queue_drop_are_fail_safe() -> None:
    service = XtDataSubscriptionService(FakeXtDataSubscription(), max_queue=2)
    with pytest.raises(ValueError, match="1 to 50"):
        await service.subscribe_single([f"{index:06d}.SH" for index in range(51)])
    with pytest.raises(ValueError, match="SH and SZ"):
        await service.subscribe_whole(["BJ"])
    await service._ensure_worker()
    for index in range(5):
        service._callback({"600000.SH": {"lastPrice": index}})
    await asyncio.sleep(0.05)
    assert service.dropped_batches >= 3
    await service.stop()
