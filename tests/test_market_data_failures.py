from __future__ import annotations

from datetime import datetime

import pytest

from dyadtetrade.modules.market_data.failures import (
    DownloadFailureCategory,
    InstrumentFacts,
    classify_download_failure,
)

ACTIVE_ETF = InstrumentFacts(
    symbol="560000.SH",
    name="智能电车ETF浦银",
    exchange="SH",
    asset_type="etf",
    list_date="20210930",
    delist_date=None,
    is_active=True,
)


@pytest.mark.parametrize(
    ("error", "instrument", "detail", "category", "retryable", "quarantine"),
    [
        (
            ValueError("miniQMT 返回空行情"),
            ACTIVE_ETF,
            {"OpenDate": "20260720"},
            DownloadFailureCategory.NOT_LISTED_YET,
            False,
            True,
        ),
        (
            ValueError("行情数据存在非正数 OHLC"),
            None,
            {},
            DownloadFailureCategory.DELISTED,
            False,
            True,
        ),
        (
            ValueError("miniQMT 返回空行情"),
            ACTIVE_ETF,
            {},
            DownloadFailureCategory.NO_MARKET_DATA,
            False,
            True,
        ),
        (
            RuntimeError("temporary connection failure"),
            ACTIVE_ETF,
            {},
            DownloadFailureCategory.TEMPORARILY_UNAVAILABLE,
            True,
            False,
        ),
        (
            ValueError("行情数据存在负成交量或成交额"),
            ACTIVE_ETF,
            {},
            DownloadFailureCategory.QUALITY_REJECTED,
            False,
            True,
        ),
        (
            ValueError("miniQMT 返回空行情"),
            ACTIVE_ETF,
            {"InstrumentName": "认购款"},
            DownloadFailureCategory.UNSUPPORTED_INSTRUMENT,
            False,
            True,
        ),
        (
            RuntimeError("vendor rejected request"),
            ACTIVE_ETF,
            {},
            DownloadFailureCategory.PROVIDER_ERROR,
            True,
            False,
        ),
    ],
)
def test_download_failure_classifier_covers_all_required_categories(
    error: Exception,
    instrument: InstrumentFacts | None,
    detail: dict[str, object],
    category: DownloadFailureCategory,
    retryable: bool,
    quarantine: bool,
) -> None:
    result = classify_download_failure(
        error,
        symbol="560000.SH",
        start_date="20260101",
        end_date="20260713",
        instrument=instrument,
        provider_detail=detail,
        now=datetime(2026, 7, 13, 20, 0),
    )

    assert result.category == category
    assert result.retryable is retryable
    assert result.quarantine is quarantine
    assert bool(result.next_retry_at) is retryable


def test_future_dated_beijing_bond_is_not_treated_as_stock() -> None:
    facts = InstrumentFacts(
        symbol="821006.BJ",
        name="24智都02",
        exchange="BJ",
        asset_type="stock",
        list_date="20240806",
        delist_date="20310801",
        is_active=True,
    )

    result = classify_download_failure(
        ValueError("miniQMT 返回空行情"),
        symbol=facts.symbol,
        start_date="20200101",
        end_date="20260713",
        instrument=facts,
    )

    assert result.category == DownloadFailureCategory.UNSUPPORTED_INSTRUMENT


def test_failed_detail_lookup_does_not_fabricate_a_delisting() -> None:
    result = classify_download_failure(
        ValueError("行情数据存在非正数 OHLC"),
        symbol="600193.SH",
        start_date="20200101",
        end_date="20260713",
        instrument=None,
        provider_detail=None,
    )

    assert result.category == DownloadFailureCategory.QUALITY_REJECTED
    assert result.details["provider_detail_checked"] is False
