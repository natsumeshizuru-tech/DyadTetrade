from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from dyadtetrade.api.routes import execution
from dyadtetrade.api.routes.execution import (
    AutomatedQmtOrder,
    ManualQmtOrder,
    RiskStateUpdate,
    SwitchUpdate,
)
from dyadtetrade.domain import Direction, RiskOperatingState


class TradingClientStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, Any] | None]] = []
        self.current_state = RiskOperatingState.NORMAL.value
        self.fail = False

    def status(self) -> dict[str, object]:
        return {
            "execution_mode": "qmt_sim",
            "connected": True,
            "subscribed": True,
            "engine_online": True,
            "risk_state": {"state": self.current_state, "reason": "test"},
        }

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 15,
    ) -> dict[str, object]:
        del timeout
        if self.fail:
            raise RuntimeError("engine offline")
        self.calls.append((method, path, json, params))
        if path == "/risk-state" and json is not None:
            self.current_state = str(json["state"])
        if path.endswith("/cancel"):
            return {"qmt_order_id": 9001, "cancel_result": 0}
        if path == "/orders":
            return {"qmt_seq": 7, "status": "SUBMITTING", "execution_mode": "qmt_sim"}
        return self.status()


def test_manual_order_rejects_client_id_longer_than_qmt_order_remark() -> None:
    with pytest.raises(ValidationError, match="at most 48 characters"):
        ManualQmtOrder(
            symbol="600519.SH",
            direction=Direction.BUY,
            quantity=100,
            limit_price=Decimal("10"),
            client_order_id="x" * 49,
        )


@pytest.mark.asyncio
async def test_execution_routes_proxy_only_to_isolated_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TradingClientStub()
    monkeypatch.setattr(execution, "trading_engine_client", client)

    assert execution.execution_status()["engine_online"] is True
    assert (await execution.connect_execution())["connected"] is True
    request = ManualQmtOrder(
        symbol="600519.SH",
        direction=Direction.BUY,
        quantity=100,
        limit_price=Decimal("10"),
        strategy_id="api-test",
        client_order_id="api-client",
        idempotency_key="api-key",
        confirm_simulated_order=True,
    )
    result = await execution.submit_qmt_order(request)
    assert result["qmt_seq"] == 7
    order_call = next(call for call in client.calls if call[1] == "/orders")
    assert order_call[2] is not None
    assert order_call[2]["limit_price"] == "10"
    assert order_call[2]["confirm_simulated_order"] is True

    cancelled = await execution.cancel_qmt_order(9001, confirm_simulated_order=True)
    assert cancelled["cancel_result"] == 0
    cancel_call = next(call for call in client.calls if call[1].endswith("/cancel"))
    assert cancel_call[3] == {"confirmed": True}
    assert (await execution.reconcile_execution())["connected"] is True


@pytest.mark.asyncio
async def test_risk_state_and_compatibility_switch_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TradingClientStub()
    monkeypatch.setattr(execution, "trading_engine_client", client)

    halted = await execution.update_risk_state(
        RiskStateUpdate(state=RiskOperatingState.HALTED, reason="operator emergency stop")
    )
    assert halted["risk_state"]["state"] == "HALTED"
    recovery = await execution.update_switches(SwitchUpdate(kill_switch=False))
    assert recovery["risk_state"]["state"] == "RECOVERY"
    reduced = await execution.update_switches(SwitchUpdate(no_new_positions=True))
    assert reduced["risk_state"]["state"] == "REDUCE_ONLY"


@pytest.mark.asyncio
async def test_proxy_fails_closed_when_trading_engine_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TradingClientStub()
    client.fail = True
    monkeypatch.setattr(execution, "trading_engine_client", client)
    request = ManualQmtOrder(
        symbol="600519.SH",
        direction=Direction.BUY,
        quantity=100,
        limit_price=Decimal("10"),
        confirm_simulated_order=True,
    )
    with pytest.raises(HTTPException) as exc_info:
        await execution.submit_qmt_order(request)
    assert exc_info.value.status_code == 409
    with pytest.raises(HTTPException) as connect_error:
        await execution.connect_execution()
    assert connect_error.value.status_code == 503


@pytest.mark.asyncio
async def test_automated_order_uses_release_and_signal_as_stable_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = TradingClientStub()
    monkeypatch.setattr(execution, "trading_engine_client", client)
    request = AutomatedQmtOrder(
        symbol="600519.SH",
        direction=Direction.BUY,
        quantity=100,
        limit_price=Decimal("1500.00"),
        strategy_id="released-strategy",
        strategy_version="3",
        strategy_release_id="strategy-release-1",
        signal_id="20260713-close-600519",
        parameters={"lookback": 20},
    )
    await execution.submit_automated_qmt_order(request)
    await execution.submit_automated_qmt_order(request)
    calls = [call for call in client.calls if call[1] == "/orders"]
    assert len(calls) == 2
    assert calls[0][2] is not None and calls[1][2] is not None
    assert calls[0][2]["idempotency_key"] == calls[1][2]["idempotency_key"]
    assert calls[0][2]["parameters_hash"] == calls[1][2]["parameters_hash"]
    assert calls[0][2]["automated"] is True
