from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dyadtetrade.domain import Direction, RiskOperatingState
from dyadtetrade.modules.risk import RiskStateMachine, TradingSwitches


def test_recovery_requires_reconciliation_before_normal() -> None:
    machine = RiskStateMachine()
    with pytest.raises(PermissionError, match="reconciliation"):
        machine.transition(RiskOperatingState.NORMAL, "not reconciled")
    at = datetime(2026, 7, 13, tzinfo=UTC)
    machine.mark_reconciled(at)
    machine.transition(RiskOperatingState.NORMAL, "reconciled", at=at)
    assert machine.state is RiskOperatingState.NORMAL
    assert machine.version == 2


def test_halted_can_only_enter_recovery() -> None:
    machine = RiskStateMachine(state=RiskOperatingState.HALTED)
    with pytest.raises(ValueError, match="illegal"):
        machine.transition(RiskOperatingState.NORMAL, "unsafe shortcut", reconciled=True)
    machine.transition(RiskOperatingState.RECOVERY, "operator released kill switch")
    assert machine.state is RiskOperatingState.RECOVERY


def test_new_recovery_epoch_clears_stale_reconciliation_evidence() -> None:
    old_reconciliation = datetime(2026, 7, 13, 1, tzinfo=UTC)
    machine = RiskStateMachine(
        state=RiskOperatingState.NORMAL,
        reconciled_at=old_reconciliation,
    )
    machine.transition(
        RiskOperatingState.RECOVERY,
        "gateway disconnected",
        reconciled=True,
    )
    assert machine.reconciled_at is None
    with pytest.raises(PermissionError, match="reconciliation"):
        machine.transition(RiskOperatingState.NORMAL, "unsafe stale recovery")


def test_reentering_recovery_refreshes_reason_and_clears_reconciliation() -> None:
    machine = RiskStateMachine(
        state=RiskOperatingState.RECOVERY,
        reconciled_at=datetime(2026, 7, 13, 1, tzinfo=UTC),
    )
    version = machine.version
    machine.transition(RiskOperatingState.RECOVERY, "new reconciliation attempt")
    assert machine.reason == "new reconciliation attempt"
    assert machine.reconciled_at is None
    assert machine.version == version + 1


@pytest.mark.parametrize(
    ("state", "global_stop", "cancel_only", "no_new_positions"),
    [
        (RiskOperatingState.NORMAL, False, False, False),
        (RiskOperatingState.REDUCE_ONLY, False, False, True),
        (RiskOperatingState.CANCEL_ONLY, False, True, False),
        (RiskOperatingState.HALTED, True, False, False),
        (RiskOperatingState.RECOVERY, True, False, False),
    ],
)
def test_each_risk_state_maps_to_unambiguous_switches(
    state: RiskOperatingState,
    global_stop: bool,
    cancel_only: bool,
    no_new_positions: bool,
) -> None:
    switches = TradingSwitches()
    RiskStateMachine(state=state).apply_to(switches)
    assert switches.global_stop is global_stop
    assert switches.cancel_only is cancel_only
    assert switches.no_new_positions is no_new_positions


def test_reduce_only_accepts_only_position_reducing_sell() -> None:
    machine = RiskStateMachine(state=RiskOperatingState.REDUCE_ONLY)
    assert machine.allows_order(Direction.SELL, reduces_position=True)
    assert not machine.allows_order(Direction.SELL, reduces_position=False)
    assert not machine.allows_order(Direction.BUY, reduces_position=False)
