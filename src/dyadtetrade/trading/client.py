from __future__ import annotations

import os
from typing import Any

import httpx

from dyadtetrade.core.config import Settings, get_settings


class TradingEngineClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def headers(self) -> dict[str, str]:
        # Read the short-lived IPC token at request time.  The API app can be
        # imported by tooling before the launcher has injected the token, so a
        # value captured in the cached Settings object is not a reliable IPC
        # credential for the lifetime of the process.
        token = os.environ.get(
            "DYADTETRADE_TRADING_ENGINE_TOKEN", self.settings.trading_engine_token
        )
        return {"X-Trading-Engine-Token": token} if token else {}

    def status(self) -> dict[str, object]:
        try:
            # Loopback IPC should answer immediately. A short timeout keeps
            # readiness and monitoring fail-safe without blocking the API.
            with httpx.Client(timeout=0.5, trust_env=False) as client:
                response = client.get(
                    f"{self.settings.trading_engine_url}/status", headers=self.headers
                )
                response.raise_for_status()
                return dict(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            error = type(exc).__name__
            if isinstance(exc, httpx.HTTPStatusError):
                error = f"{error}:{exc.response.status_code}"
            return {
                "execution_mode": self.settings.execution_mode.value,
                "connected": False,
                "subscribed": False,
                "account_healthy": False,
                "positions": [],
                "orders": [],
                "trades": [],
                "risk_state": {
                    "state": "RECOVERY",
                    "reason": "trading engine unavailable",
                },
                "engine_online": False,
                "last_error": error,
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
        try:
            async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
                response = await client.request(
                    method,
                    f"{self.settings.trading_engine_url}{path}",
                    json=json,
                    params=params,
                    headers=self.headers,
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"trading engine unavailable: {type(exc).__name__}") from exc
        if response.is_error:
            detail: object
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise RuntimeError(f"trading engine {response.status_code}: {detail}")
        return dict(response.json())


trading_engine_client = TradingEngineClient()
