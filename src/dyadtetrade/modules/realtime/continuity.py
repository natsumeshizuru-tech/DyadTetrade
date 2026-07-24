from __future__ import annotations

from typing import Protocol

from dyadtetrade.domain import Bar


class RealtimeContinuityPort(Protocol):
    """Optional read-through adapter; implementations must not grant history write ownership."""

    def backfill(
        self,
        gateway: object,
        *,
        symbol: str,
        period: str,
        start_time: str,
        end_time: str,
        adjust_mode: str,
    ) -> int: ...

    def persist_finalized(self, bars: list[Bar]) -> int: ...
