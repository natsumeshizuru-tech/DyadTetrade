"""CPython 3.11-only quote-data bridge; this process exposes no trading API."""

from __future__ import annotations

import contextlib
import gzip
import json
import os
import sys
import traceback
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def load_xtdata() -> Any:
    site_packages = os.environ.get("DYADTETRADE_XTDATA_SITE_PACKAGES", "")
    if not site_packages:
        raise RuntimeError("DYADTETRADE_XTDATA_SITE_PACKAGES is required")
    # Keep the sidecar venv's modern NumPy/Pandas ahead of the vendor's old
    # pure-Python bundle, while still making the CPython 3.11 XtQuant modules visible.
    sys.path.append(site_packages)
    with contextlib.redirect_stdout(sys.stderr):
        from xtquant import xtdata

    xtdata.enable_hello = False
    return xtdata


def frame_json(frame: Any) -> str:
    return str(frame.to_json(orient="split", date_format="epoch", date_unit="ms"))


def parallel_symbol_map(symbols: Iterable[str], function: Callable[[str], Any]) -> dict[str, Any]:
    values = list(symbols)
    configured = int(os.environ.get("DYADTETRADE_XTDATA_WORKERS", "16"))
    workers = max(1, min(configured, len(values)))
    if workers == 1:
        return {symbol: function(symbol) for symbol in values}
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="xtdata-metadata") as executor:
        results = executor.map(function, values)
        return dict(zip(values, results, strict=True))


def dispatch(xtdata: Any, operation: str, parameters: dict[str, Any]) -> Any:
    with contextlib.redirect_stdout(sys.stderr):
        if operation == "capabilities":
            function_names = [
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
                available_periods = list(xtdata.get_period_list())
            except Exception:
                available_periods = []
            sectors = list(xtdata.get_sector_list())
            return {
                "runtime": sys.version,
                "module": str(xtdata.__file__),
                "data_dir": str(xtdata.init_data_dir()),
                "connected": True,
                "sector_count": len(sectors),
                "available_periods": available_periods,
                "functions": {
                    name: callable(getattr(xtdata, name, None)) for name in function_names
                },
            }
        if operation == "doctor":
            sectors = list(xtdata.get_sector_list())
            return {
                "runtime": sys.version,
                "module": str(xtdata.__file__),
                "sector_count": len(sectors),
                "data_dir": str(xtdata.init_data_dir()),
                "safe_read_only": True,
            }
        if operation == "download_holiday_data":
            method = getattr(xtdata, "download_holiday_data", None)
            return method() if callable(method) else xtdata.get_holidays()
        if operation == "get_trading_calendar":
            return list(
                xtdata.get_trading_calendar(
                    parameters["market"], parameters["start"], parameters["end"]
                )
            )
        if operation == "refresh_sector_data":
            return xtdata.download_sector_data()
        if operation == "refresh_history_contracts":
            return xtdata.download_history_contracts()
        if operation == "get_sector_list":
            return list(xtdata.get_sector_list())
        if operation == "get_stock_list_in_sector":
            return list(xtdata.get_stock_list_in_sector(parameters["sector"]))
        if operation == "get_instrument_detail":
            return dict(xtdata.get_instrument_detail(parameters["symbol"], False) or {})
        if operation == "get_instrument_details":
            # Initialize the vendor singleton on the main thread before workers use it.
            xtdata.get_client()
            return parallel_symbol_map(
                parameters["symbols"],
                lambda symbol: dict(xtdata.get_instrument_detail(symbol, False) or {}),
            )
        if operation == "download_history":
            symbols = list(parameters["symbols"])
            if len(symbols) == 1 and callable(getattr(xtdata, "download_history_data", None)):
                return xtdata.download_history_data(
                    symbols[0], parameters["period"], parameters["start"], parameters["end"]
                )
            if callable(getattr(xtdata, "download_history_data2", None)):
                return xtdata.download_history_data2(
                    symbols, parameters["period"], parameters["start"], parameters["end"]
                )
            for symbol in symbols:
                xtdata.download_history_data(
                    symbol, parameters["period"], parameters["start"], parameters["end"]
                )
            return None
        if operation == "get_local_data":
            method = getattr(xtdata, "get_local_data", xtdata.get_market_data_ex)
            frames = method(
                field_list=list(parameters["fields"]),
                stock_list=list(parameters["symbols"]),
                period=parameters["period"],
                start_time=parameters["start"],
                end_time=parameters["end"],
                count=-1,
                dividend_type="none",
                fill_data=False,
            )
            return {str(symbol): frame_json(frame) for symbol, frame in frames.items()}
        if operation == "get_market_data":
            frames = xtdata.get_market_data_ex(
                field_list=list(parameters["fields"]),
                stock_list=list(parameters["symbols"]),
                period=parameters["period"],
                start_time=parameters["start"],
                end_time=parameters["end"],
                count=-1,
                dividend_type=parameters["dividend_type"],
                fill_data=False,
            )
            return {str(symbol): frame_json(frame) for symbol, frame in frames.items()}
        if operation == "get_divid_factors":
            return frame_json(
                xtdata.get_divid_factors(
                    parameters["symbol"], parameters["start"], parameters["end"]
                )
            )
        if operation == "get_divid_factors_batch":
            xtdata.get_client()
            return parallel_symbol_map(
                parameters["symbols"],
                lambda symbol: frame_json(
                    xtdata.get_divid_factors(symbol, parameters["start"], parameters["end"])
                ),
            )
        if operation == "download_financial_data":
            method = getattr(
                xtdata,
                "download_financial_data2",
                xtdata.download_financial_data,
            )
            return method(
                list(parameters["symbols"]),
                list(parameters["tables"]),
                parameters["start"],
                parameters["end"],
            )
        if operation == "get_financial_data":
            nested = xtdata.get_financial_data(
                list(parameters["symbols"]),
                list(parameters["tables"]),
                parameters["start"],
                parameters["end"],
                parameters["report_type"],
            )
            return {
                str(symbol): {str(table): frame_json(frame) for table, frame in tables.items()}
                for symbol, tables in nested.items()
            }
        if operation == "download_index_weight":
            return xtdata.download_index_weight()
        if operation == "get_index_weight":
            return dict(xtdata.get_index_weight(parameters["index_code"]))
        if operation == "download_dataset":
            dataset = str(parameters["dataset"])
            if dataset == "history":
                symbols = list(parameters.get("symbols", []))
                period = str(parameters["period"])
                start = str(parameters.get("start", ""))
                end = str(parameters.get("end", ""))
                batch = getattr(xtdata, "download_history_data2", None)
                if callable(batch):
                    return batch(symbols, period, start, end)
                for symbol in symbols:
                    xtdata.download_history_data(symbol, period, start, end)
                return None
            if dataset == "financial":
                symbols = list(parameters.get("symbols", []))
                tables = list(parameters.get("tables", []))
                start = str(parameters.get("start", ""))
                end = str(parameters.get("end", ""))
                modern = getattr(xtdata, "download_financial_data2", None)
                if callable(modern):
                    return modern(symbols, tables, start, end)
                return xtdata.download_financial_data(symbols, tables)
            simple = {
                "sector": "download_sector_data",
                "history_contracts": "download_history_contracts",
                "holiday": "download_holiday_data",
                "convertible_bond": "download_cb_data",
                "etf": "download_etf_info",
                "index_weight": "download_index_weight",
            }
            method_name = simple.get(dataset)
            if method_name is None:
                raise ValueError(f"unsupported xtdata dataset: {dataset}")
            method = getattr(xtdata, method_name, None)
            if not callable(method):
                raise RuntimeError(f"current xtdata does not provide {method_name}")
            return method()
    raise ValueError(f"unsupported sidecar operation: {operation}")


def main() -> int:
    try:
        request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        xtdata = load_xtdata()
        result = dispatch(xtdata, str(request["operation"]), dict(request["parameters"]))
        payload = {"ok": True, "result": result}
        status = 0
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        status = 1
    sys.stdout.buffer.write(gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
