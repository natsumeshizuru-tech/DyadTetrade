from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from dyadtetrade.core.symbols import normalize_symbol
from dyadtetrade.db.parquet_store import ParquetMetadata, ParquetStore
from dyadtetrade.modules.market_data.pipeline import MarketDataPipeline
from dyadtetrade.modules.market_data.quality import QualityFinding

SYMBOL_IN_PATH = re.compile(r"(?P<code>\d{6})(?:[._-]?(?P<market>SH|SZ|BJ))?", re.IGNORECASE)
COLUMN_ALIASES = {
    "日期": "datetime",
    "时间": "datetime",
    "交易日期": "datetime",
    "代码": "symbol",
    "证券代码": "symbol",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
    "前收盘": "pre_close",
    "复权因子": "adjust_factor",
}


@dataclass(frozen=True)
class ImportResult:
    source_files: int
    imported_symbols: int
    files: list[ParquetMetadata]
    findings: list[QualityFinding]
    errors: dict[str, str]


class LegacyMarketDataImporter:
    def __init__(self, store: ParquetStore | None = None) -> None:
        self.store = store or ParquetStore()
        self.pipeline = MarketDataPipeline(self.store)

    def import_path(
        self,
        source: Path,
        *,
        period: str = "1d",
        adjust: str = "front",
    ) -> ImportResult:
        resolved = source.resolve()
        if not resolved.exists():
            raise FileNotFoundError(resolved)
        target = self.store.root.resolve()
        if resolved == target or target in resolved.parents:
            raise ValueError("导入源不能位于 DyadTetrade 数据目录内")
        paths = (
            [resolved]
            if resolved.is_file()
            else sorted([*resolved.rglob("*.parquet"), *resolved.rglob("*.csv")])
        )
        files: list[ParquetMetadata] = []
        findings: list[QualityFinding] = []
        errors: dict[str, str] = {}
        imported_symbols: set[str] = set()
        for path in paths:
            try:
                frame = self._read(path)
                for symbol, symbol_frame in self._split_symbols(frame, path).items():
                    result = self.pipeline.process(
                        symbol_frame,
                        symbol=symbol,
                        period=period,
                        adjust=adjust,
                        imported_from=str(path.resolve()),
                    )
                    files.extend(result.files)
                    findings.extend(result.findings)
                    imported_symbols.add(symbol)
            except Exception as exc:
                errors[str(path)] = str(exc)
        return ImportResult(len(paths), len(imported_symbols), files, findings, errors)

    def _read(self, path: Path) -> pd.DataFrame:
        if path.suffix.lower() == ".parquet":
            frame = pd.read_parquet(path)
        else:
            try:
                frame = pd.read_csv(path, encoding="utf-8")
            except UnicodeDecodeError:
                frame = pd.read_csv(path, encoding="gb18030")
        frame.columns = [
            COLUMN_ALIASES.get(str(column).strip(), str(column).strip()) for column in frame
        ]
        return frame

    def _split_symbols(self, frame: pd.DataFrame, path: Path) -> dict[str, pd.DataFrame]:
        symbol_column = next(
            (column for column in ("symbol", "stock_code", "code") if column in frame), None
        )
        if symbol_column is not None:
            normalized = frame[symbol_column].astype(str).map(normalize_symbol)
            return {
                str(symbol): group.drop(columns=[symbol_column, "_symbol"]).copy()
                for symbol, group in frame.assign(_symbol=normalized).groupby("_symbol")
            }
        match = SYMBOL_IN_PATH.search(path.stem.upper())
        if match is None:
            raise ValueError("文件名和字段中均未找到证券代码")
        value = match.group("code")
        if match.group("market"):
            value = f"{value}.{match.group('market').upper()}"
        return {normalize_symbol(value): frame}
