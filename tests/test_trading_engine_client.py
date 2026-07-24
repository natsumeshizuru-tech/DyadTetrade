from __future__ import annotations

import pytest

from dyadtetrade.core.config import Settings
from dyadtetrade.trading.client import TradingEngineClient


def test_ipc_token_is_resolved_at_request_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings may be constructed by API discovery before the launcher injects
    # its per-run token.  The client must not keep that stale empty credential.
    client = TradingEngineClient(Settings(trading_engine_token="stale"))
    monkeypatch.setenv("DYADTETRADE_TRADING_ENGINE_TOKEN", "current-per-run-token")
    assert client.headers == {"X-Trading-Engine-Token": "current-per-run-token"}


def test_ipc_token_falls_back_to_explicit_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DYADTETRADE_TRADING_ENGINE_TOKEN", raising=False)
    client = TradingEngineClient(Settings(trading_engine_token="explicit"))
    assert client.headers == {"X-Trading-Engine-Token": "explicit"}
