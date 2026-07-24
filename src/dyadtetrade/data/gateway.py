from __future__ import annotations

from typing import Any, Protocol

import pandas as pd


class DataGatewayError(RuntimeError):
    """Base class for read-only upstream market-data failures."""


class DataGatewayUnavailable(DataGatewayError):
    """The local quote service or vendor runtime is unavailable."""


class DataGatewayTimeout(DataGatewayError):
    """A bounded vendor call exceeded its deadline."""


class DataGatewaySchemaError(DataGatewayError):
    """The vendor returned a structure that cannot be normalized safely."""


class MarketDataGateway(Protocol):
    def capabilities(self) -> dict[str, Any]: ...

    def download_dataset(
        self,
        dataset: str,
        *,
        symbols: list[str] | None = None,
        period: str = "",
        start: str = "",
        end: str = "",
        tables: list[str] | None = None,
    ) -> None: ...

    def doctor(self) -> dict[str, Any]: ...

    def download_holiday_data(self) -> None: ...

    def get_trading_calendar(self, market: str, start: str, end: str) -> list[str]: ...

    def refresh_sector_data(self) -> None: ...

    def refresh_history_contracts(self) -> None: ...

    def get_sector_list(self) -> list[str]: ...

    def get_stock_list_in_sector(self, sector: str) -> list[str]: ...

    def get_instrument_detail(self, symbol: str) -> dict[str, Any]: ...

    def get_instrument_details(self, symbols: list[str]) -> dict[str, dict[str, Any]]: ...

    def download_history(
        self, symbols: list[str], *, period: str, start: str, end: str
    ) -> None: ...

    def get_local_data(
        self,
        symbols: list[str],
        *,
        fields: list[str],
        period: str,
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]: ...

    def get_divid_factors(self, symbol: str, start: str, end: str) -> pd.DataFrame: ...

    def get_divid_factors_batch(
        self, symbols: list[str], start: str, end: str
    ) -> dict[str, pd.DataFrame]: ...

    def download_financial_data(
        self, symbols: list[str], tables: list[str], start: str, end: str
    ) -> None: ...

    def get_financial_data(
        self,
        symbols: list[str],
        tables: list[str],
        start: str,
        end: str,
        *,
        report_type: str,
    ) -> dict[str, dict[str, pd.DataFrame]]: ...

    def download_index_weight(self) -> None: ...

    def get_index_weight(self, index_code: str) -> dict[str, float]: ...
