from __future__ import annotations

import gzip
import importlib
import inspect
import io
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd

from dyadtetrade.data.config import QmtConfig
from dyadtetrade.data.gateway import (
    DataGatewayError,
    DataGatewaySchemaError,
    DataGatewayTimeout,
    DataGatewayUnavailable,
)


class XtDataGateway:
    """Version-isolated, read-only adapter around the vendor quote API."""

    def __init__(self, config: QmtConfig, module: ModuleType | Any | None = None) -> None:
        self.config = config
        self.xtdata = module if module is not None else self._load_module()

    def _load_module(self) -> ModuleType:
        if self.config.site_packages:
            value = str(self.config.site_packages.resolve())
            if value not in sys.path:
                sys.path.insert(0, value)
        try:
            return importlib.import_module("xtquant.xtdata")
        except Exception as exc:
            raise DataGatewayUnavailable(f"cannot import read-only XtData runtime: {exc}") from exc

    def _call(self, method: str, *args: object, **kwargs: object) -> Any:
        function = getattr(self.xtdata, method, None)
        if function is None or not callable(function):
            raise DataGatewayUnavailable(f"XtData method is unavailable: {method}")
        attempts = self.config.retries + 1
        last_error: BaseException | None = None
        for _ in range(attempts):
            executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="xtdata-readonly")
            future = executor.submit(function, *args, **kwargs)
            try:
                return future.result(timeout=self.config.call_timeout_seconds)
            except TimeoutError as exc:
                future.cancel()
                last_error = DataGatewayTimeout(
                    f"XtData {method} exceeded {self.config.call_timeout_seconds}s"
                )
                last_error.__cause__ = exc
            except Exception as exc:
                last_error = exc
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        assert last_error is not None
        if isinstance(last_error, DataGatewayError):
            raise last_error
        raise DataGatewayUnavailable(f"XtData {method} failed: {last_error}") from last_error

    def doctor(self) -> dict[str, Any]:
        sectors = self.get_sector_list()
        data_dir = getattr(self.xtdata, "data_dir", None)
        return {
            "runtime": "in_process",
            "module": str(getattr(self.xtdata, "__file__", "")),
            "sector_count": len(sectors),
            "data_dir": None if data_dir is None else str(data_dir),
            "safe_read_only": True,
        }

    def capabilities(self) -> dict[str, Any]:
        names = [
            "download_history_data",
            "download_history_data2",
            "download_financial_data",
            "download_financial_data2",
            "download_sector_data",
            "download_history_contracts",
            "download_holiday_data",
            "download_cb_data",
            "download_etf_info",
            "download_index_weight",
        ]
        try:
            periods = list(self._call("get_period_list"))
        except DataGatewayError:
            periods = []
        sectors = self.get_sector_list()
        return {
            "runtime": sys.version,
            "module": str(getattr(self.xtdata, "__file__", "")),
            "data_dir": str(getattr(self.xtdata, "data_dir", "")),
            "connected": True,
            "sector_count": len(sectors),
            "available_periods": periods,
            "functions": {name: callable(getattr(self.xtdata, name, None)) for name in names},
        }

    def download_dataset(
        self,
        dataset: str,
        *,
        symbols: list[str] | None = None,
        period: str = "",
        start: str = "",
        end: str = "",
        tables: list[str] | None = None,
    ) -> None:
        values = symbols or []
        if dataset == "history":
            self.download_history(values, period=period, start=start, end=end)
            return
        if dataset == "financial":
            self.download_financial_data(values, tables or [], start, end)
            return
        methods = {
            "sector": "download_sector_data",
            "history_contracts": "download_history_contracts",
            "holiday": "download_holiday_data",
            "convertible_bond": "download_cb_data",
            "etf": "download_etf_info",
            "index_weight": "download_index_weight",
        }
        method = methods.get(dataset)
        if method is None:
            raise ValueError(f"unsupported xtdata dataset: {dataset}")
        if not callable(getattr(self.xtdata, method, None)):
            raise DataGatewayUnavailable(f"current xtdata does not provide {method}")
        self._call(method)

    def download_holiday_data(self) -> None:
        method = getattr(self.xtdata, "download_holiday_data", None)
        if callable(method):
            self._call("download_holiday_data")
        else:
            self._call("get_holidays")

    def get_trading_calendar(self, market: str, start: str, end: str) -> list[str]:
        raw = self._call("get_trading_calendar", market, start, end)
        if not isinstance(raw, (list, tuple)):
            raise DataGatewaySchemaError("XtData trading calendar must be a sequence")
        return sorted({str(value)[:8] for value in raw if str(value)[:8].isdigit()})

    def refresh_sector_data(self) -> None:
        self._call("download_sector_data")

    def refresh_history_contracts(self) -> None:
        self._call("download_history_contracts")

    def get_sector_list(self) -> list[str]:
        raw = self._call("get_sector_list")
        if not isinstance(raw, (list, tuple)):
            raise DataGatewaySchemaError("XtData sector list must be a sequence")
        return sorted({str(value) for value in raw})

    def get_stock_list_in_sector(self, sector: str) -> list[str]:
        raw = self._call("get_stock_list_in_sector", sector)
        if not isinstance(raw, (list, tuple)):
            raise DataGatewaySchemaError(f"XtData sector payload is invalid: {sector}")
        return sorted({str(value).upper() for value in raw})

    def get_instrument_detail(self, symbol: str) -> dict[str, Any]:
        raw = self._call("get_instrument_detail", symbol, False)
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise DataGatewaySchemaError(f"XtData instrument payload is invalid: {symbol}")
        return {str(key): value for key, value in raw.items()}

    def get_instrument_details(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        self._call("get_client")
        workers = max(1, min(16, len(symbols)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="xtdata-metadata") as pool:
            details = pool.map(self.get_instrument_detail, symbols)
            return dict(zip(symbols, details, strict=True))

    def download_history(self, symbols: list[str], *, period: str, start: str, end: str) -> None:
        if len(symbols) == 1 and callable(getattr(self.xtdata, "download_history_data", None)):
            self._call("download_history_data", symbols[0], period, start, end)
            return
        batch = getattr(self.xtdata, "download_history_data2", None)
        if callable(batch):
            signature = inspect.signature(batch)
            if "callback" in signature.parameters:
                self._call("download_history_data2", symbols, period, start, end, None)
            else:
                self._call("download_history_data2", symbols, period, start, end)
            return
        for symbol in symbols:
            self._call("download_history_data", symbol, period, start, end)

    def get_local_data(
        self,
        symbols: list[str],
        *,
        fields: list[str],
        period: str,
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]:
        method = (
            "get_local_data"
            if callable(getattr(self.xtdata, "get_local_data", None))
            else "get_market_data_ex"
        )
        raw = self._call(
            method,
            field_list=fields,
            stock_list=symbols,
            period=period,
            start_time=start,
            end_time=end,
            count=-1,
            dividend_type="none",
            fill_data=False,
        )
        if not isinstance(raw, dict):
            raise DataGatewaySchemaError("XtData local history payload must be a mapping")
        result: dict[str, pd.DataFrame] = {}
        for symbol, frame in raw.items():
            if not isinstance(frame, pd.DataFrame):
                raise DataGatewaySchemaError(f"XtData history is not a DataFrame: {symbol}")
            result[str(symbol).upper()] = frame
        return result

    def get_adjusted_data(
        self,
        symbols: list[str],
        *,
        fields: list[str],
        period: str,
        start: str,
        end: str,
        dividend_type: str,
    ) -> dict[str, pd.DataFrame]:
        raw = self._call(
            "get_market_data_ex",
            field_list=fields,
            stock_list=symbols,
            period=period,
            start_time=start,
            end_time=end,
            count=-1,
            dividend_type=dividend_type,
            fill_data=False,
        )
        if not isinstance(raw, dict):
            raise DataGatewaySchemaError("XtData adjusted history payload must be a mapping")
        result: dict[str, pd.DataFrame] = {}
        for symbol, frame in raw.items():
            if not isinstance(frame, pd.DataFrame):
                raise DataGatewaySchemaError(
                    f"XtData adjusted history is not a DataFrame: {symbol}"
                )
            result[str(symbol).upper()] = frame
        return result

    def get_divid_factors(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        raw = self._call("get_divid_factors", symbol, start, end)
        if not isinstance(raw, pd.DataFrame):
            raise DataGatewaySchemaError(f"XtData dividend payload is invalid: {symbol}")
        return raw

    def get_divid_factors_batch(
        self, symbols: list[str], start: str, end: str
    ) -> dict[str, pd.DataFrame]:
        self._call("get_client")
        workers = max(1, min(16, len(symbols)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="xtdata-actions") as pool:
            frames = pool.map(lambda symbol: self.get_divid_factors(symbol, start, end), symbols)
            return dict(zip(symbols, frames, strict=True))

    def download_financial_data(
        self, symbols: list[str], tables: list[str], start: str, end: str
    ) -> None:
        method = (
            "download_financial_data2"
            if callable(getattr(self.xtdata, "download_financial_data2", None))
            else "download_financial_data"
        )
        self._call(method, symbols, tables, start, end)

    def get_financial_data(
        self,
        symbols: list[str],
        tables: list[str],
        start: str,
        end: str,
        *,
        report_type: str,
    ) -> dict[str, dict[str, pd.DataFrame]]:
        if report_type not in {"report_time", "announce_time"}:
            raise ValueError(f"unsupported financial report type: {report_type}")
        raw = self._call("get_financial_data", symbols, tables, start, end, report_type)
        if not isinstance(raw, dict):
            raise DataGatewaySchemaError("XtData financial payload must be a mapping")
        result: dict[str, dict[str, pd.DataFrame]] = {}
        for symbol, raw_tables in raw.items():
            if not isinstance(raw_tables, dict):
                raise DataGatewaySchemaError(f"XtData financial tables are invalid: {symbol}")
            frames: dict[str, pd.DataFrame] = {}
            for table, frame in raw_tables.items():
                if not isinstance(frame, pd.DataFrame):
                    raise DataGatewaySchemaError(
                        f"XtData financial table is not a DataFrame: {symbol}/{table}"
                    )
                frames[str(table)] = frame
            result[str(symbol).upper()] = frames
        return result

    def download_index_weight(self) -> None:
        self._call("download_index_weight")

    def get_index_weight(self, index_code: str) -> dict[str, float]:
        raw = self._call("get_index_weight", index_code)
        if not isinstance(raw, dict):
            raise DataGatewaySchemaError(f"XtData index-weight payload is invalid: {index_code}")
        try:
            return {str(symbol).upper(): float(weight) for symbol, weight in raw.items()}
        except (TypeError, ValueError) as exc:
            raise DataGatewaySchemaError(
                f"XtData index-weight values are invalid: {index_code}"
            ) from exc


class SubprocessXtDataGateway:
    """Use a CPython 3.11 helper when the vendor extensions cannot load in 3.12."""

    def __init__(self, config: QmtConfig, *, project_root: Path | None = None) -> None:
        if config.sidecar_python is None or config.site_packages is None:
            raise DataGatewayUnavailable("sidecar_python and site_packages are required")
        self.config = config
        self.python = config.sidecar_python.resolve()
        self.site_packages = config.site_packages.resolve()
        self.project_root = project_root or Path.cwd()
        self.script = self.project_root / "scripts" / "xtdata_readonly_sidecar.py"
        for path in (self.python, self.site_packages, self.script):
            if not path.exists():
                raise DataGatewayUnavailable(f"XtData sidecar path does not exist: {path}")

    def _request(self, operation: str, **parameters: object) -> Any:
        request = json.dumps(
            {"operation": operation, "parameters": parameters}, ensure_ascii=False
        ).encode("utf-8")
        environment = dict(os.environ)
        environment["DYADTETRADE_XTDATA_SITE_PACKAGES"] = str(self.site_packages)
        try:
            completed = subprocess.run(
                [str(self.python), str(self.script)],
                input=request,
                capture_output=True,
                cwd=self.project_root,
                env=environment,
                timeout=self.config.call_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DataGatewayTimeout(
                f"XtData sidecar {operation} exceeded {self.config.call_timeout_seconds}s"
            ) from exc
        if completed.returncode != 0:
            message = completed.stderr.decode("utf-8", errors="replace")[-4000:]
            raise DataGatewayUnavailable(
                f"XtData sidecar {operation} failed ({completed.returncode}): {message}"
            )
        try:
            payload = json.loads(gzip.decompress(completed.stdout).decode("utf-8"))
        except Exception as exc:
            raise DataGatewaySchemaError(
                f"XtData sidecar returned invalid data for {operation}"
            ) from exc
        if not payload.get("ok"):
            raise DataGatewayUnavailable(str(payload.get("error", "unknown sidecar failure")))
        return payload.get("result")

    def doctor(self) -> dict[str, Any]:
        result = self._request("doctor")
        if not isinstance(result, dict):
            raise DataGatewaySchemaError("sidecar doctor result must be a mapping")
        return {str(key): value for key, value in result.items()}

    def capabilities(self) -> dict[str, Any]:
        result = self._request("capabilities")
        if not isinstance(result, dict):
            raise DataGatewaySchemaError("sidecar capability result must be a mapping")
        return {str(key): value for key, value in result.items()}

    def download_dataset(
        self,
        dataset: str,
        *,
        symbols: list[str] | None = None,
        period: str = "",
        start: str = "",
        end: str = "",
        tables: list[str] | None = None,
    ) -> None:
        self._request(
            "download_dataset",
            dataset=dataset,
            symbols=symbols or [],
            period=period,
            start=start,
            end=end,
            tables=tables or [],
        )

    def download_holiday_data(self) -> None:
        self._request("download_holiday_data")

    def get_trading_calendar(self, market: str, start: str, end: str) -> list[str]:
        result = self._request("get_trading_calendar", market=market, start=start, end=end)
        if not isinstance(result, list):
            raise DataGatewaySchemaError("sidecar calendar result must be a list")
        return [str(value) for value in result]

    def refresh_sector_data(self) -> None:
        self._request("refresh_sector_data")

    def refresh_history_contracts(self) -> None:
        self._request("refresh_history_contracts")

    def get_sector_list(self) -> list[str]:
        result = self._request("get_sector_list")
        if not isinstance(result, list):
            raise DataGatewaySchemaError("sidecar sector result must be a list")
        return [str(value) for value in result]

    def get_stock_list_in_sector(self, sector: str) -> list[str]:
        result = self._request("get_stock_list_in_sector", sector=sector)
        if not isinstance(result, list):
            raise DataGatewaySchemaError("sidecar stock-list result must be a list")
        return [str(value) for value in result]

    def get_instrument_detail(self, symbol: str) -> dict[str, Any]:
        result = self._request("get_instrument_detail", symbol=symbol)
        if not isinstance(result, dict):
            raise DataGatewaySchemaError("sidecar instrument result must be a mapping")
        return {str(key): value for key, value in result.items()}

    def get_instrument_details(self, symbols: list[str]) -> dict[str, dict[str, Any]]:
        result = self._request("get_instrument_details", symbols=symbols)
        if not isinstance(result, dict):
            raise DataGatewaySchemaError("sidecar instrument batch must be a mapping")
        return {
            str(symbol): {str(key): value for key, value in detail.items()}
            for symbol, detail in result.items()
            if isinstance(detail, dict)
        }

    def download_history(self, symbols: list[str], *, period: str, start: str, end: str) -> None:
        self._request("download_history", symbols=symbols, period=period, start=start, end=end)

    def get_local_data(
        self,
        symbols: list[str],
        *,
        fields: list[str],
        period: str,
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]:
        result = self._request(
            "get_local_data",
            symbols=symbols,
            fields=fields,
            period=period,
            start=start,
            end=end,
        )
        if not isinstance(result, dict):
            raise DataGatewaySchemaError("sidecar history result must be a mapping")
        frames: dict[str, pd.DataFrame] = {}
        for symbol, encoded in result.items():
            if not isinstance(encoded, str):
                raise DataGatewaySchemaError(f"sidecar frame is invalid: {symbol}")
            frames[str(symbol)] = pd.read_json(io.StringIO(encoded), orient="split")
        return frames

    def get_adjusted_data(
        self,
        symbols: list[str],
        *,
        fields: list[str],
        period: str,
        start: str,
        end: str,
        dividend_type: str,
    ) -> dict[str, pd.DataFrame]:
        result = self._request(
            "get_market_data",
            symbols=symbols,
            fields=fields,
            period=period,
            start=start,
            end=end,
            dividend_type=dividend_type,
        )
        if not isinstance(result, dict):
            raise DataGatewaySchemaError("sidecar adjusted history result must be a mapping")
        frames: dict[str, pd.DataFrame] = {}
        for symbol, encoded in result.items():
            if not isinstance(encoded, str):
                raise DataGatewaySchemaError(f"sidecar adjusted frame is invalid: {symbol}")
            frames[str(symbol).upper()] = pd.read_json(io.StringIO(encoded), orient="split")
        return frames

    def get_divid_factors(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        result = self._request("get_divid_factors", symbol=symbol, start=start, end=end)
        if not isinstance(result, str):
            raise DataGatewaySchemaError("sidecar dividend result must be encoded DataFrame")
        return pd.read_json(io.StringIO(result), orient="split")

    def get_divid_factors_batch(
        self, symbols: list[str], start: str, end: str
    ) -> dict[str, pd.DataFrame]:
        result = self._request("get_divid_factors_batch", symbols=symbols, start=start, end=end)
        if not isinstance(result, dict):
            raise DataGatewaySchemaError("sidecar dividend batch must be a mapping")
        return {
            str(symbol): pd.read_json(io.StringIO(encoded), orient="split")
            for symbol, encoded in result.items()
            if isinstance(encoded, str)
        }

    def download_financial_data(
        self, symbols: list[str], tables: list[str], start: str, end: str
    ) -> None:
        self._request(
            "download_financial_data",
            symbols=symbols,
            tables=tables,
            start=start,
            end=end,
        )

    def get_financial_data(
        self,
        symbols: list[str],
        tables: list[str],
        start: str,
        end: str,
        *,
        report_type: str,
    ) -> dict[str, dict[str, pd.DataFrame]]:
        result = self._request(
            "get_financial_data",
            symbols=symbols,
            tables=tables,
            start=start,
            end=end,
            report_type=report_type,
        )
        if not isinstance(result, dict):
            raise DataGatewaySchemaError("sidecar financial result must be a mapping")
        frames: dict[str, dict[str, pd.DataFrame]] = {}
        for symbol, raw_tables in result.items():
            if not isinstance(raw_tables, dict):
                raise DataGatewaySchemaError(f"sidecar financial tables are invalid: {symbol}")
            frames[str(symbol)] = {}
            for table, encoded in raw_tables.items():
                if not isinstance(encoded, str):
                    raise DataGatewaySchemaError(
                        f"sidecar financial frame is invalid: {symbol}/{table}"
                    )
                frames[str(symbol)][str(table)] = pd.read_json(io.StringIO(encoded), orient="split")
        return frames

    def download_index_weight(self) -> None:
        self._request("download_index_weight")

    def get_index_weight(self, index_code: str) -> dict[str, float]:
        result = self._request("get_index_weight", index_code=index_code)
        if not isinstance(result, dict):
            raise DataGatewaySchemaError("sidecar index-weight result must be a mapping")
        try:
            return {str(symbol): float(weight) for symbol, weight in result.items()}
        except (TypeError, ValueError) as exc:
            raise DataGatewaySchemaError("sidecar index-weight values are invalid") from exc


def build_xtdata_gateway(config: QmtConfig) -> XtDataGateway | SubprocessXtDataGateway:
    if os.environ.get("DYADTETRADE_XTDATA_IN_PROCESS") == "1":
        return XtDataGateway(config)
    if config.sidecar_python is not None:
        return SubprocessXtDataGateway(config)
    return XtDataGateway(config)
