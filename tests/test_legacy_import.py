from pathlib import Path

import pandas as pd

from dyadtetrade.db.parquet_store import ParquetStore
from dyadtetrade.modules.market_data.importer import LegacyMarketDataImporter


def test_legacy_import_does_not_modify_source(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    source.mkdir()
    path = source / "600519_SH.csv"
    pd.DataFrame(
        {
            "日期": ["20260709", "20260710"],
            "开盘": [10, 10.2],
            "最高": [10.5, 10.8],
            "最低": [9.8, 10.1],
            "收盘": [10.2, 10.6],
            "成交量": [1000, 1200],
            "成交额": [10200, 12720],
        }
    ).to_csv(path, index=False, encoding="utf-8")
    before = path.read_bytes()
    importer = LegacyMarketDataImporter(ParquetStore(tmp_path / "target"))
    result = importer.import_path(source)
    assert result.imported_symbols == 1
    assert result.errors == {}
    assert path.read_bytes() == before
    assert (tmp_path / "target" / "trusted").exists()
