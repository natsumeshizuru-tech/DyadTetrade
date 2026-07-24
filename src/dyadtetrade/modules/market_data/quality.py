from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd

Severity = Literal["warning", "error"]


@dataclass(frozen=True)
class QualityFinding:
    symbol: str
    check_code: str
    severity: Severity
    message: str
    trade_date: str | None = None
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class QualityReportPaths:
    json_path: Path
    csv_path: Path


def inspect_bar_quality(
    frame: pd.DataFrame,
    *,
    symbol: str,
    expected_days: list[str] | None = None,
    asset_type: str | None = None,
) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    if frame.empty:
        return [QualityFinding(symbol, "empty_data", "error", "行情数据为空")]

    required = {"datetime", "open", "high", "low", "close", "volume", "amount"}
    missing_columns = sorted(required - set(frame.columns))
    if missing_columns:
        return [
            QualityFinding(
                symbol,
                "missing_fields",
                "error",
                f"缺少字段：{', '.join(missing_columns)}",
                details={"fields": missing_columns},
            )
        ]

    dates = pd.to_datetime(frame["datetime"], errors="coerce")
    invalid_dates = int(dates.isna().sum())
    if invalid_dates:
        findings.append(
            QualityFinding(
                symbol,
                "invalid_datetime",
                "error",
                f"{invalid_dates} 行时间无法解析",
                details={"rows": invalid_dates},
            )
        )
    duplicates = int(frame.duplicated(subset=["datetime"], keep=False).sum())
    if duplicates:
        findings.append(
            QualityFinding(
                symbol,
                "duplicate_rows",
                "warning",
                f"发现 {duplicates} 行重复交易时间，流水线将保留最后一行",
                details={"rows": duplicates},
            )
        )

    numeric = frame[["open", "high", "low", "close", "volume", "amount"]].apply(
        pd.to_numeric, errors="coerce"
    )
    null_rows = numeric[["open", "high", "low", "close"]].isna().any(axis=1)
    if null_rows.any():
        findings.append(
            QualityFinding(
                symbol,
                "null_ohlc",
                "error",
                f"{int(null_rows.sum())} 行 OHLC 缺失",
                details={"rows": int(null_rows.sum())},
            )
        )
    high_bad = numeric["high"] < numeric[["open", "low", "close"]].max(axis=1)
    low_bad = numeric["low"] > numeric[["open", "high", "close"]].min(axis=1)
    non_positive = (numeric[["open", "high", "low", "close"]] <= 0).any(axis=1)
    ohlc_bad = high_bad | low_bad | non_positive
    if ohlc_bad.any():
        findings.append(
            QualityFinding(
                symbol,
                "invalid_ohlc",
                "error",
                f"{int(ohlc_bad.sum())} 行 OHLC 关系异常或价格非正",
                details={"rows": int(ohlc_bad.sum())},
            )
        )
    negative = (numeric[["volume", "amount"]] < 0).any(axis=1)
    if negative.any():
        is_index = asset_type == "index"
        findings.append(
            QualityFinding(
                symbol,
                "index_volume_amount_unavailable" if is_index else "negative_volume_amount",
                "warning" if is_index else "error",
                (
                    f"{int(negative.sum())} 行指数成交量或成交额使用供应商缺失哨兵值，"
                    "价格字段仍可用"
                    if is_index
                    else f"{int(negative.sum())} 行成交量或成交额为负"
                ),
                details={"rows": int(negative.sum())},
            )
        )

    valid_dates = dates.dropna()
    if expected_days and not valid_dates.empty:
        actual = set(valid_dates.dt.strftime("%Y%m%d"))
        first = min(actual)
        relevant = [day for day in expected_days if first <= day <= expected_days[-1]]
        missing_days = sorted(set(relevant) - actual)
        if missing_days:
            findings.append(
                QualityFinding(
                    symbol,
                    "missing_trading_days",
                    "warning",
                    f"缺少 {len(missing_days)} 个交易日（可能包含停牌或上市前日期）",
                    details={"count": len(missing_days), "sample": missing_days[:20]},
                )
            )
        latest = max(actual)
        if relevant and latest < relevant[-1]:
            findings.append(
                QualityFinding(
                    symbol,
                    "latest_day_not_covered",
                    "warning",
                    f"最新数据 {latest}，目标交易日 {relevant[-1]}",
                    trade_date=latest,
                    details={"actual": latest, "expected": relevant[-1]},
                )
            )
    return findings


def inspect_tick_quality(
    frame: pd.DataFrame,
    *,
    symbol: str,
    expected_days: list[str] | None = None,
) -> list[QualityFinding]:
    if frame.empty:
        return [QualityFinding(symbol, "empty_tick_data", "error", "Tick 数据为空")]
    columns = {str(column).lower(): str(column) for column in frame.columns}
    time_name = next(
        (columns[name] for name in ("datetime", "time", "timetag") if name in columns),
        None,
    )
    price_name = next(
        (columns[name] for name in ("last_price", "lastprice") if name in columns), None
    )
    findings: list[QualityFinding] = []
    if time_name is None or price_name is None:
        fields = (("datetime/time", time_name), ("last_price", price_name))
        missing = [name for name, value in fields if value is None]
        return [
            QualityFinding(
                symbol,
                "missing_tick_fields",
                "error",
                f"Tick 数据缺少字段：{', '.join(missing)}",
                details={"fields": missing},
            )
        ]
    raw_times = frame[time_name]
    numeric_times = pd.to_numeric(raw_times, errors="coerce")
    if numeric_times.notna().all():
        if numeric_times.abs().max() > 10**11:
            parsed_times = pd.to_datetime(numeric_times, unit="ms", utc=True, errors="coerce")
        else:
            parsed_times = pd.to_datetime(numeric_times, unit="s", utc=True, errors="coerce")
        times = parsed_times.dt.tz_convert("Asia/Shanghai")
    else:
        times = pd.to_datetime(raw_times, errors="coerce")
    invalid_time = int(times.isna().sum())
    if invalid_time:
        findings.append(
            QualityFinding(
                symbol,
                "invalid_tick_datetime",
                "error",
                f"{invalid_time} 行 Tick 时间无法解析",
                details={"rows": invalid_time},
            )
        )
    duplicates = int(times.duplicated(keep=False).sum())
    if duplicates:
        findings.append(
            QualityFinding(
                symbol,
                "duplicate_ticks",
                "warning",
                f"发现 {duplicates} 行重复 Tick 时间",
                details={"rows": duplicates},
            )
        )
    valid_times = times.dropna().sort_values()
    if not valid_times.empty:
        day_text = valid_times.dt.strftime("%Y%m%d")
        weekday_bad = valid_times.dt.weekday >= 5
        calendar_bad = (
            ~day_text.isin(set(expected_days))
            if expected_days is not None
            else pd.Series(False, index=valid_times.index)
        )
        non_trading = int((weekday_bad | calendar_bad).sum())
        if non_trading:
            findings.append(
                QualityFinding(
                    symbol,
                    "non_trading_day_ticks",
                    "error",
                    f"发现 {non_trading} 行周末或非交易日 Tick",
                    details={"rows": non_trading},
                )
            )
        seconds = valid_times.dt.hour * 3600 + valid_times.dt.minute * 60 + valid_times.dt.second
        in_session = seconds.between(9 * 3600 + 30 * 60, 11 * 3600 + 30 * 60 + 1) | (
            seconds.between(13 * 3600, 15 * 3600 + 1)
        )
        session_times = valid_times.loc[in_session]
        time_deltas: Any = session_times.groupby(session_times.dt.strftime("%Y%m%d")).diff()
        deltas = time_deltas.dt.total_seconds()
        irregular = deltas.notna() & ((deltas < 2) | (deltas > 4))
        if irregular.any():
            findings.append(
                QualityFinding(
                    symbol,
                    "tick_cadence_outliers",
                    "warning",
                    f"发现 {int(irregular.sum())} 个非 3 秒 Level1 间隔",
                    details={
                        "rows": int(irregular.sum()),
                        "expected_seconds": 3,
                    },
                )
            )
    prices = pd.to_numeric(frame[price_name], errors="coerce")
    invalid_price = int((prices.isna() | (prices <= 0)).sum())
    if invalid_price:
        findings.append(
            QualityFinding(
                symbol,
                "invalid_tick_price",
                "error",
                f"{invalid_price} 行 Tick 最新价无效",
                details={"rows": invalid_price},
            )
        )
    return findings


def write_quality_report(
    findings: list[QualityFinding],
    *,
    report_root: Path,
    report_name: str,
) -> QualityReportPaths:
    report_root.mkdir(parents=True, exist_ok=True)
    records = []
    for finding in findings:
        record = asdict(finding)
        record["details"] = json.dumps(record["details"] or {}, ensure_ascii=False)
        records.append(record)
    columns = ["symbol", "check_code", "severity", "message", "trade_date", "details"]
    table = pd.DataFrame(records, columns=columns)
    json_path = report_root / f"{report_name}.json"
    csv_path = report_root / f"{report_name}.csv"
    json_path.write_text(
        json.dumps([asdict(item) for item in findings], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    table.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return QualityReportPaths(json_path.resolve(), csv_path.resolve())
