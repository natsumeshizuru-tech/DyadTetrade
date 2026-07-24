from __future__ import annotations

from pathlib import Path
from typing import cast

import duckdb

from dyadtetrade.core.config import get_settings


def connect_duckdb(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    database_path = path or get_settings().duckdb_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(database_path))


def refresh_market_views(
    *,
    connection: duckdb.DuckDBPyConnection | None = None,
    data_root: Path | None = None,
) -> None:
    own_connection = connection is None
    conn = connection or connect_duckdb()
    root = data_root or get_settings().data_root
    try:
        for layer in ("trusted", "bt_ready"):
            files = list((root / layer).glob("bars_*/*/*.parquet"))
            view_name = f"bars_{layer}"
            if files:
                pattern = (root / layer / "bars_*" / "symbol=*" / "*.parquet").as_posix()
                escaped = pattern.replace("'", "''")
                conn.execute(
                    f"CREATE OR REPLACE VIEW {view_name} AS "
                    f"SELECT * FROM read_parquet('{escaped}', union_by_name=true, "
                    "hive_partitioning=true)"
                )
            else:
                conn.execute(
                    f"CREATE OR REPLACE VIEW {view_name} AS SELECT "
                    "CAST(NULL AS TIMESTAMP) AS datetime, CAST(NULL AS VARCHAR) AS symbol, "
                    "CAST(NULL AS DOUBLE) AS open, CAST(NULL AS DOUBLE) AS high, "
                    "CAST(NULL AS DOUBLE) AS low, CAST(NULL AS DOUBLE) AS close, "
                    "CAST(NULL AS DOUBLE) AS volume, CAST(NULL AS DOUBLE) AS amount, "
                    "CAST(NULL AS VARCHAR) AS period, CAST(NULL AS VARCHAR) AS adjust "
                    "WHERE FALSE"
                )
    finally:
        if own_connection:
            conn.close()


def query_bars(
    *,
    symbols: list[str],
    start_date: str,
    end_date: str,
    layer: str = "trusted",
    period: str = "1d",
    adjust: str = "front",
    limit: int = 10000,
) -> list[dict[str, object]]:
    if layer not in {"trusted", "bt_ready"}:
        raise ValueError("DuckDB 查询只支持 trusted 或 bt_ready")
    conn = connect_duckdb()
    try:
        refresh_market_views(connection=conn)
        placeholders = ",".join("?" for _ in symbols)
        query = (
            f"SELECT * FROM bars_{layer} WHERE symbol IN ({placeholders}) "
            "AND period = ? AND adjust = ? "
            "AND CAST(datetime AS DATE) BETWEEN strptime(?, '%Y%m%d') AND strptime(?, '%Y%m%d') "
            "ORDER BY datetime, symbol LIMIT ?"
        )
        frame = conn.execute(
            query, [*symbols, period, adjust, start_date, end_date, limit]
        ).fetchdf()
        return cast(list[dict[str, object]], frame.to_dict(orient="records"))
    finally:
        conn.close()
