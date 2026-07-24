from __future__ import annotations

import importlib.util
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pandas as pd

from dyadtetrade.core.config import get_settings
from dyadtetrade.core.symbols import infer_asset_type, is_a_share_symbol, normalize_symbol
from dyadtetrade.core.trading_calendar import sanitize_trading_days


class XtQuantError(RuntimeError):
    """Base error for XtQuant integration failures."""


class MiniQmtUnavailableError(XtQuantError):
    """Raised when the local miniQMT data service cannot be reached."""


@dataclass(frozen=True)
class XtQuantStatus:
    installed: bool
    path: str | None
    connected: bool
    sector_count: int
    live_trading_enabled: bool
    error: str | None = None


def configure_xtquant_path() -> None:
    path: Path | None = get_settings().xtquant_path
    if path and path.exists() and str(path) not in sys.path:
        sys.path.append(str(path))


def _load_xtdata() -> ModuleType:
    configure_xtquant_path()
    try:
        from xtquant import xtdata
    except Exception as exc:  # pragma: no cover - depends on local vendor package
        raise XtQuantError(f"无法导入 XtQuant：{exc}") from exc

    try:
        xtdata.enable_hello = False
    except Exception:
        pass
    return cast(ModuleType, xtdata)


class XtQuantGateway:
    _xtdata_lock = threading.RLock()

    def __init__(self, xtdata: ModuleType | Any | None = None) -> None:
        self.xtdata = xtdata or _load_xtdata()

    def status(self) -> XtQuantStatus:
        spec = importlib.util.find_spec("xtquant")
        try:
            sectors = self.list_sectors()
            return XtQuantStatus(
                installed=spec is not None,
                path=spec.origin if spec else None,
                connected=True,
                sector_count=len(sectors),
                live_trading_enabled=get_settings().enable_live_trading,
            )
        except MiniQmtUnavailableError as exc:
            return XtQuantStatus(
                installed=spec is not None,
                path=spec.origin if spec else None,
                connected=False,
                sector_count=0,
                live_trading_enabled=get_settings().enable_live_trading,
                error=str(exc),
            )

    def list_sectors(self) -> list[str]:
        try:
            with self._xtdata_lock:
                return list(self.xtdata.get_sector_list())
        except Exception as exc:
            raise MiniQmtUnavailableError(
                "无法连接 miniQMT 行情服务，请确认客户端已启动并登录"
            ) from exc

    def list_symbols(self, sector: str) -> list[str]:
        try:
            with self._xtdata_lock:
                values = self.xtdata.get_stock_list_in_sector(sector)
            return sorted({normalize_symbol(item) for item in values})
        except Exception as exc:
            raise MiniQmtUnavailableError(f"读取板块失败：{sector}") from exc

    def list_a_share_universe(self, *, include_bj: bool = True) -> list[str]:
        sectors = self.list_sectors()
        preferred = ["沪深京A股", "沪深A股"] if include_bj else ["沪深A股"]
        fallback = ["上证A股", "深证A股", "京市A股"] if include_bj else ["上证A股", "深证A股"]

        symbols: list[str] = []
        for sector in preferred:
            if sector in sectors:
                symbols.extend(self.list_symbols(sector))
                break
        if not symbols:
            for sector in fallback:
                if sector in sectors:
                    symbols.extend(self.list_symbols(sector))
        return sorted({item for item in symbols if is_a_share_symbol(item, include_bj=include_bj)})

    def instrument_detail(self, symbol: str) -> dict[str, Any]:
        normalized = normalize_symbol(symbol)
        try:
            with self._xtdata_lock:
                value = self.xtdata.get_instrument_detail(normalized, False)
            return dict(value or {})
        except Exception as exc:
            raise MiniQmtUnavailableError(f"读取标的信息失败：{normalized}") from exc

    def full_tick(self, symbols: list[str]) -> dict[str, Any]:
        normalized = [normalize_symbol(symbol) for symbol in symbols]
        try:
            with self._xtdata_lock:
                value = self.xtdata.get_full_tick(normalized)
            return dict(value or {})
        except Exception as exc:
            raise MiniQmtUnavailableError("读取实时行情失败") from exc

    def download_history(
        self,
        symbol: str,
        *,
        period: str,
        start_time: str,
        end_time: str,
        incrementally: bool = True,
    ) -> None:
        normalized = normalize_symbol(symbol)
        try:
            with self._xtdata_lock:
                self.xtdata.download_history_data(
                    stock_code=normalized,
                    period=period,
                    start_time=start_time,
                    end_time=end_time,
                    incrementally=incrementally,
                )
        except Exception as exc:
            raise MiniQmtUnavailableError(f"历史行情下载失败：{normalized}") from exc

    def local_bars(
        self,
        symbols: list[str],
        *,
        fields: list[str],
        period: str,
        start_time: str,
        end_time: str,
        dividend_type: str,
        fill_data: bool = False,
    ) -> dict[str, pd.DataFrame]:
        normalized = [normalize_symbol(symbol) for symbol in symbols]
        try:
            with self._xtdata_lock:
                raw = self.xtdata.get_market_data_ex(
                    field_list=fields,
                    stock_list=normalized,
                    period=period,
                    start_time=start_time,
                    end_time=end_time,
                    count=-1,
                    dividend_type=dividend_type,
                    fill_data=fill_data,
                )
        except Exception as exc:
            raise MiniQmtUnavailableError("读取本地历史行情失败") from exc
        return {str(symbol): frame for symbol, frame in dict(raw or {}).items()}

    def trading_days(self, start_time: str, end_time: str, market: str = "SH") -> list[str]:
        for method_name in ("get_trading_calendar", "get_trading_dates"):
            try:
                with self._xtdata_lock:
                    values = getattr(self.xtdata, method_name)(market, start_time, end_time)
                return sanitize_trading_days(
                    values,
                    start_date=start_time,
                    end_date=end_time,
                )
            except Exception:
                continue

        for index_symbol in ("000001.SH", "399001.SZ"):
            try:
                frames = self.local_bars(
                    [index_symbol],
                    fields=["close"],
                    period="1d",
                    start_time=start_time,
                    end_time=end_time,
                    dividend_type="none",
                )
                frame = frames.get(index_symbol)
                if frame is not None and not frame.empty:
                    days = sanitize_trading_days(
                        frame.index,
                        start_date=start_time,
                        end_date=end_time,
                    )
                    if days:
                        return days
            except Exception:
                continue
        raise MiniQmtUnavailableError("官方接口和指数日线兜底均未取得交易日历")

    def refresh_sector_data(self) -> None:
        try:
            method = getattr(self.xtdata, "download_sector_data", None)
            if method is not None:
                with self._xtdata_lock:
                    method()
        except Exception as exc:
            raise MiniQmtUnavailableError("更新板块数据失败") from exc

    def market_universes(self) -> dict[str, list[str]]:
        """Return merged A-share, ETF and index universes from available sectors."""
        sectors = set(self.list_sectors())
        groups = {
            "stock": ("沪深京A股", "沪深A股", "上证A股", "深证A股", "京市A股"),
            "etf": ("沪深ETF", "上证ETF", "深证ETF"),
            "index": ("沪深指数", "上证指数", "深证指数"),
        }
        result: dict[str, list[str]] = {}
        for asset_type, candidates in groups.items():
            values: set[str] = set()
            for sector in candidates:
                if sector in sectors:
                    values.update(self.list_symbols(sector))
                    if sector in {"沪深京A股", "沪深A股", "沪深ETF", "沪深指数"}:
                        break
            if asset_type == "stock":
                values = {symbol for symbol in values if is_a_share_symbol(symbol)}
            else:
                values = {symbol for symbol in values if infer_asset_type(symbol) == asset_type}
            result[asset_type] = sorted(values)
        return result


def get_xtquant_status() -> XtQuantStatus:
    try:
        return XtQuantGateway().status()
    except XtQuantError as exc:
        return XtQuantStatus(
            installed=False,
            path=None,
            connected=False,
            sector_count=0,
            live_trading_enabled=get_settings().enable_live_trading,
            error=str(exc),
        )
