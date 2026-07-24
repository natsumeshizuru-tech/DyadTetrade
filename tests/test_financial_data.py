from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from dyadtetrade.data.config import StorageConfig
from dyadtetrade.data.financial import normalize_financial_frames
from dyadtetrade.data.models import FinancialRecord
from dyadtetrade.data.sqlite import FinancialRepository


def test_financial_normalization_unions_modes_and_preserves_all_fields() -> None:
    report_frame = pd.DataFrame(
        [
            {
                "m_timetag": "20240331",
                "m_anntime": "20240430",
                "m_quarter": 521325015669,
                "total_assets": 12.5,
                "missing": float("nan"),
            }
        ]
    )
    announce_frame = report_frame.copy()

    records = normalize_financial_frames(
        "600000.SH",
        [{"Balance": report_frame}, {"Balance": announce_frame}],
        tables=["Balance"],
        start=20240101,
        end=20260717,
        source="test",
        ingested_at=123,
    )

    assert len(records) == 1
    assert records[0].report_date == 20240331
    assert records[0].announcement_date == 20240430
    assert json.loads(records[0].raw_json) == {
        "m_anntime": "20240430",
        "m_quarter": None,
        "m_timetag": "20240331",
        "missing": None,
        "total_assets": 12.5,
    }


def test_financial_normalization_keeps_multiple_holders_deterministically() -> None:
    frame = pd.DataFrame(
        [
            {"endDate": "20241231", "declareDate": "20250330", "rank": 2, "name": "B"},
            {"endDate": "20241231", "declareDate": "20250330", "rank": 1, "name": "A"},
        ]
    )

    records = normalize_financial_frames(
        "000001.SZ",
        [{"Top10Holder": frame}],
        tables=["Top10Holder"],
        start=20240101,
        end=20260717,
        source="test",
        ingested_at=123,
    )

    assert [record.row_ordinal for record in records] == [0, 1]
    assert [json.loads(record.raw_json)["name"] for record in records] == ["A", "B"]


def test_financial_normalization_excludes_announcements_after_asof_end() -> None:
    frame = pd.DataFrame([{"m_timetag": "20260717", "m_anntime": "20260718", "value": 1}])

    records = normalize_financial_frames(
        "600000.SH",
        [{"Capital": frame}],
        tables=["Capital"],
        start=20240101,
        end=20260717,
        source="test",
        ingested_at=123,
    )

    assert records == []


def test_financial_repository_shards_and_upserts_idempotently(tmp_path: Path) -> None:
    repository = FinancialRepository(StorageConfig(root=tmp_path))
    original = _record(raw_json='{"value":1}', source_hash="one")

    first = repository.upsert([original])
    repeated = repository.upsert([original])
    revised = repository.upsert([_record(raw_json='{"value":2}', source_hash="two")])

    assert first.inserted_rows == 1
    assert repeated.unchanged_rows == 1
    assert revised.updated_rows == 1
    assert repository.symbols_with_tables(["Balance"], start_year=2024, end_year=2026) == {
        "600000.SH"
    }
    path = tmp_path / "fundamentals_2024.db"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT raw_json FROM financial_records").fetchone() == (
            '{"value":2}',
        )
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def _record(*, raw_json: str, source_hash: str) -> FinancialRecord:
    return FinancialRecord(
        symbol="600000.SH",
        table_name="Balance",
        report_date=20240331,
        announcement_date=20240430,
        row_ordinal=0,
        raw_json=raw_json,
        source="test",
        ingested_at=123,
        source_hash=source_hash,
    )
