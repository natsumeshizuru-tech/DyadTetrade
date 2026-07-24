from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

TICK_PERIOD: Final = "tick"
TICK_LEVEL: Final = "level1"
TICK_CADENCE_SECONDS: Final = 3
TICK_SNAPSHOTS_PER_DAY: Final = 4_800
TICK_RAW_BYTES_PER_ROW: Final = 64
TICK_TRUSTED_BYTES_PER_ROW: Final = 72


@dataclass(frozen=True)
class DatasetSpec:
    period: str
    start_date: str
    adjust: str
    source_period: str | None = None

    @property
    def is_derived(self) -> bool:
        return self.source_period is not None


@dataclass(frozen=True)
class DownloadBatch:
    period: str
    start_date: str
    end_date: str
    adjust: str
    symbols: list[str]


@dataclass(frozen=True)
class CapacityEstimate:
    symbol_count: int
    required_bytes: int
    free_bytes: int
    safety_reserve_bytes: int
    bounded: bool
    reasons: list[str]
    by_dataset: dict[str, int]
    estimated_tick_rows: int = 0
    tick_cadence_seconds: int = TICK_CADENCE_SECONDS
    tick_level: str = TICK_LEVEL


DEFAULT_DATASETS: Final[tuple[DatasetSpec, ...]] = (
    DatasetSpec("1d", "20100101", "none"),
    DatasetSpec("1d", "20100101", "front"),
    DatasetSpec("1m", "20240101", "none"),
    DatasetSpec("1m", "20240101", "front"),
    DatasetSpec("tick", "20240101", "none"),
    DatasetSpec("5m", "20240101", "none", "1m"),
    DatasetSpec("15m", "20240101", "none", "1m"),
    DatasetSpec("30m", "20240101", "none", "1m"),
    DatasetSpec("1h", "20240101", "none", "1m"),
    DatasetSpec("5m", "20240101", "front", "1m"),
    DatasetSpec("15m", "20240101", "front", "1m"),
    DatasetSpec("30m", "20240101", "front", "1m"),
    DatasetSpec("1h", "20240101", "front", "1m"),
    DatasetSpec("1w", "20100101", "none", "1d"),
    DatasetSpec("1q", "20100101", "none", "1d"),
    DatasetSpec("1y", "20100101", "none", "1d"),
    DatasetSpec("1w", "20100101", "front", "1d"),
    DatasetSpec("1q", "20100101", "front", "1d"),
    DatasetSpec("1y", "20100101", "front", "1d"),
)


def create_download_batches(
    symbols: list[str],
    *,
    end_date: str,
    batch_size: int = 100,
    include_tick: bool = False,
) -> list[DownloadBatch]:
    if batch_size < 1 or batch_size > 1000:
        raise ValueError("batch_size 必须在 1 到 1000 之间")
    normalized = sorted(set(symbols))
    native = [item for item in DEFAULT_DATASETS if not item.is_derived]
    if not include_tick:
        native = [item for item in native if item.period != TICK_PERIOD]
    batches: list[DownloadBatch] = []
    for spec in native:
        windows = (
            _annual_windows(spec.start_date, end_date)
            if spec.period == TICK_PERIOD
            else [(spec.start_date, end_date)]
        )
        for window_start, window_end in windows:
            for offset in range(0, len(normalized), batch_size):
                batches.append(
                    DownloadBatch(
                        spec.period,
                        window_start,
                        window_end,
                        spec.adjust,
                        normalized[offset : offset + batch_size],
                    )
                )
    return batches


def estimate_capacity(
    symbol_count: int,
    *,
    data_root: Path,
    as_of: date | None = None,
    include_tick: bool = True,
    reserve_ratio: float = 0.20,
) -> CapacityEstimate:
    """给出规划级保守估算；不把估算当作可用空间的承诺。"""

    current = as_of or date.today()
    daily_days = max((current - date(2010, 1, 1)).days * 245 // 365, 1)
    recent_days = max((current - date(2024, 1, 1)).days * 245 // 365, 1)
    # 压缩后估算字节/行，并计入现有多层数据。Tick 仅按 raw+trusted 两份估算；
    # Bar 按 raw/clean/trusted/bt_ready 四份估算。
    estimates = {
        "1d_none_front": symbol_count * daily_days * 2 * 80 * 4,
        "1m_none_front": symbol_count * recent_days * 240 * 2 * 90 * 4,
        "derived_intraday": symbol_count * recent_days * (48 + 16 + 8 + 4) * 2 * 80 * 4,
        "derived_calendar": symbol_count * daily_days * 3 * 2 * 80 * 4 // 50,
    }
    estimated_tick_rows = 0
    if include_tick:
        estimated_tick_rows = symbol_count * recent_days * TICK_SNAPSHOTS_PER_DAY
        estimates["tick"] = estimated_tick_rows * (
            TICK_RAW_BYTES_PER_ROW + TICK_TRUSTED_BYTES_PER_ROW
        )
    required = sum(estimates.values())
    usage = shutil.disk_usage(data_root.resolve().anchor or data_root)
    reserve = int(usage.total * reserve_ratio)
    usable = max(usage.free - reserve, 0)
    reasons: list[str] = []
    if required > usable:
        reasons.append(
            f"预计需要 {format_bytes(required)}，扣除安全预留后仅可用 {format_bytes(usable)}"
        )
    tick_bytes = estimates.get("tick", 0)
    if tick_bytes > usage.free // 2:
        reasons.append("全市场 Tick 估算超过当前空闲空间的 50%，禁止创建无界执行计划")
    return CapacityEstimate(
        symbol_count=symbol_count,
        required_bytes=required,
        free_bytes=usage.free,
        safety_reserve_bytes=reserve,
        bounded=not reasons,
        reasons=reasons,
        by_dataset=estimates,
        estimated_tick_rows=estimated_tick_rows,
    )


def write_plan(
    path: Path,
    *,
    batches: list[DownloadBatch],
    estimate: CapacityEstimate,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "executable": estimate.bounded,
        "capacity": asdict(estimate),
        "tick_profile": {
            "level": TICK_LEVEL,
            "cadence_seconds": TICK_CADENCE_SECONDS,
            "snapshots_per_full_trading_day": TICK_SNAPSHOTS_PER_DAY,
            "compression": "zstd",
            "physical_layers": ["raw", "trusted"],
            "task_partition": "calendar_year",
        },
        "batches": [asdict(item) for item in batches],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.resolve()


def format_bytes(value: int) -> str:
    number = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if number < 1024 or unit == "PiB":
            return f"{number:.2f} {unit}"
        number /= 1024
    return f"{number:.2f} PiB"


def _annual_windows(start_date: str, end_date: str) -> list[tuple[str, str]]:
    start = date.fromisoformat(f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}")
    end = date.fromisoformat(f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}")
    if start > end:
        raise ValueError("start_date must not exceed end_date")
    windows: list[tuple[str, str]] = []
    for year in range(start.year, end.year + 1):
        left = max(start, date(year, 1, 1))
        right = min(end, date(year, 12, 31))
        windows.append((left.strftime("%Y%m%d"), right.strftime("%Y%m%d")))
    return windows
