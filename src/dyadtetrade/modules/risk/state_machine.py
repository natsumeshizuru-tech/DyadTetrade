from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from dyadtetrade.domain import Direction, RiskOperatingState
from dyadtetrade.modules.risk.engine import TradingSwitches

LEGAL_TRANSITIONS: dict[RiskOperatingState, set[RiskOperatingState]] = {
    RiskOperatingState.NORMAL: {
        RiskOperatingState.REDUCE_ONLY,
        RiskOperatingState.CANCEL_ONLY,
        RiskOperatingState.HALTED,
        RiskOperatingState.RECOVERY,
    },
    RiskOperatingState.REDUCE_ONLY: {
        RiskOperatingState.NORMAL,
        RiskOperatingState.CANCEL_ONLY,
        RiskOperatingState.HALTED,
        RiskOperatingState.RECOVERY,
    },
    RiskOperatingState.CANCEL_ONLY: {
        RiskOperatingState.NORMAL,
        RiskOperatingState.HALTED,
        RiskOperatingState.RECOVERY,
    },
    RiskOperatingState.HALTED: {RiskOperatingState.RECOVERY},
    RiskOperatingState.RECOVERY: {
        RiskOperatingState.NORMAL,
        RiskOperatingState.REDUCE_ONLY,
        RiskOperatingState.CANCEL_ONLY,
        RiskOperatingState.HALTED,
    },
}


@dataclass(slots=True)
class RiskStateMachine:
    state: RiskOperatingState = RiskOperatingState.RECOVERY
    reason: str = "startup"
    version: int = 1
    changed_at: datetime = datetime.min.replace(tzinfo=UTC)
    reconciled_at: datetime | None = None

    def transition(
        self,
        target: RiskOperatingState,
        reason: str,
        *,
        reconciled: bool = False,
        at: datetime | None = None,
    ) -> None:
        if target is self.state:
            if target is RiskOperatingState.RECOVERY:
                if not reason.strip():
                    raise ValueError("risk transition reason is required")
                self.reason = reason.strip()
                self.version += 1
                self.changed_at = at or datetime.now(UTC)
                self.reconciled_at = None
            return
        if target not in LEGAL_TRANSITIONS[self.state]:
            raise ValueError(f"illegal risk state transition: {self.state} -> {target}")
        if self.state is RiskOperatingState.RECOVERY and target is RiskOperatingState.NORMAL:
            if not reconciled and self.reconciled_at is None:
                raise PermissionError("RECOVERY -> NORMAL requires successful reconciliation")
        if not reason.strip():
            raise ValueError("risk transition reason is required")
        current = at or datetime.now(UTC)
        self.state = target
        self.reason = reason.strip()
        self.version += 1
        self.changed_at = current
        if target is RiskOperatingState.RECOVERY:
            self.reconciled_at = None
        if reconciled and target is not RiskOperatingState.RECOVERY:
            self.reconciled_at = current

    def mark_reconciled(self, at: datetime | None = None) -> None:
        self.reconciled_at = at or datetime.now(UTC)

    def apply_to(self, switches: TradingSwitches) -> None:
        switches.global_stop = self.state in {
            RiskOperatingState.HALTED,
            RiskOperatingState.RECOVERY,
        }
        switches.cancel_only = self.state is RiskOperatingState.CANCEL_ONLY
        switches.no_new_positions = self.state is RiskOperatingState.REDUCE_ONLY

    def allows_order(self, direction: Direction, reduces_position: bool) -> bool:
        if self.state is RiskOperatingState.NORMAL:
            return True
        if self.state is RiskOperatingState.REDUCE_ONLY:
            return direction is Direction.SELL and reduces_position
        return False

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "version": self.version,
            "changed_at": self.changed_at.isoformat(),
            "reconciled_at": self.reconciled_at.isoformat() if self.reconciled_at else None,
        }
