from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from dyadtetrade.core.config import Settings
from dyadtetrade.core.risk_policy import (
    ExecutionRiskPolicy,
    load_risk_policy,
    save_risk_policy,
)


def test_risk_policy_can_tighten_persist_and_restore(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path)
    ceiling = ExecutionRiskPolicy.from_settings(settings)
    tightened = ceiling.model_copy(
        update={
            "max_order_notional": Decimal("5000"),
            "max_daily_notional": Decimal("25000"),
            "max_strategy_daily_notional": Decimal("10000"),
        }
    )
    tightened.ensure_within(ceiling)
    path = tmp_path / "execution_risk_policy.json"
    save_risk_policy(path, tightened, actor="test-operator")

    restored = load_risk_policy(path)
    assert restored == tightened
    assert "test-operator" in path.read_text(encoding="utf-8")


def test_risk_policy_cannot_exceed_startup_ceiling() -> None:
    ceiling = ExecutionRiskPolicy.from_settings(Settings())
    relaxed = ceiling.model_copy(
        update={"max_order_notional": ceiling.max_order_notional + Decimal("1")}
    )
    with pytest.raises(ValueError, match="startup safety ceiling"):
        relaxed.ensure_within(ceiling)
