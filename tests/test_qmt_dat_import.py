from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dyadtetrade.data.qmt_dat_import import (
    QMT_DAT_HEADER,
    QmtDatFormatError,
    QmtMinuteDatReader,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _seconds(value: str) -> int:
    return int(datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=CHINA_TZ).timestamp())


def _write_dat(path: Path, records: list[tuple[int, ...]]) -> None:
    path.parent.mkdir(parents=True)
    with path.open("wb") as handle:
        handle.write(struct.pack("<Q", QMT_DAT_HEADER))
        for record in records:
            handle.write(struct.pack("<16I", *record))


def _record(value: str, close: int, *, trailer: int = 0x7FFD) -> tuple[int, ...]:
    return (
        _seconds(value),
        close,
        close + 10,
        close - 10,
        close,
        0,
        100,
        7,
        close * 100,
        0,
        13,
        0x3F800000,
        0,
        close - 5,
        0,
        trailer,
    )


def test_qmt_dat_reader_extracts_requested_minutes(tmp_path: Path) -> None:
    path = tmp_path / "SH" / "60" / "600000.DAT"
    _write_dat(
        path,
        [
            _record("20250715145900", 13330),
            _record("20250715150000", 13340),
            _record("20250716093000", 13400),
        ],
    )

    records = QmtMinuteDatReader(tmp_path).read("600000.SH", start=20250715, end=20250715)

    assert [item.trade_date for item in records] == [20250715, 20250715]
    assert [item.close for item in records] == [13.33, 13.34]
    assert records[0].volume == 100
    assert records[0].amount == 1_333_000
    assert records[0].source_hash


def test_qmt_dat_reader_rejects_unknown_format(tmp_path: Path) -> None:
    path = tmp_path / "SZ" / "60" / "000001.DAT"
    _write_dat(path, [_record("20250715150000", 10000, trailer=123)])

    with pytest.raises(QmtDatFormatError, match="trailer"):
        QmtMinuteDatReader(tmp_path).read("000001.SZ", start=20250715, end=20250715)
