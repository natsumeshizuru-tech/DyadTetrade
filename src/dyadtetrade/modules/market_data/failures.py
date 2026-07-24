from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from dyadtetrade.integrations.xtquant import MiniQmtUnavailableError


class DownloadFailureCategory(StrEnum):
    NOT_LISTED_YET = "not_listed_yet"
    DELISTED = "delisted"
    NO_MARKET_DATA = "no_market_data"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    QUALITY_REJECTED = "quality_rejected"
    UNSUPPORTED_INSTRUMENT = "unsupported_instrument"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True)
class InstrumentFacts:
    symbol: str
    name: str | None
    exchange: str
    asset_type: str
    list_date: str | None
    delist_date: str | None
    is_active: bool


@dataclass(frozen=True)
class DownloadFailureAssessment:
    category: DownloadFailureCategory
    retryable: bool
    quarantine: bool
    message: str
    details: dict[str, Any]
    next_retry_at: datetime | None = None


_UNSUPPORTED_NAME_PARTS = ("认购款", "申购款", "赎回款")
_BOND_LIKE_NAME = re.compile(r"^(?:\d{2}|V\d{2})")
_TRANSIENT_TEXT = (
    "temporary",
    "temporarily",
    "timeout",
    "timed out",
    "connection",
    "连接",
    "超时",
    "断开",
    "不可用",
)
_QUALITY_TEXT = (
    "负成交量",
    "负成交额",
    "负成交",
    "非正数 OHLC",
    "high 小于",
    "low 大于",
    "质量检查后无可用",
    "invalid_ohlc",
    "negative_volume_amount",
)


def classify_download_failure(
    error: Exception,
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    instrument: InstrumentFacts | None,
    provider_detail: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> DownloadFailureAssessment:
    provider_detail_checked = provider_detail is not None
    detail = provider_detail or {}
    current = now or datetime.now()
    message = str(error) or type(error).__name__
    name = _text(_detail_value(detail, "InstrumentName", "name")) or (
        instrument.name if instrument else None
    )
    list_date = _compact_date(_detail_value(detail, "OpenDate", "list_date")) or (
        instrument.list_date if instrument else None
    )
    delist_date = _compact_date(_detail_value(detail, "ExpireDate", "delist_date")) or (
        instrument.delist_date if instrument else None
    )
    asset_type = instrument.asset_type if instrument else None
    evidence = {
        "error_type": type(error).__name__,
        "name": name,
        "asset_type": asset_type,
        "list_date": list_date,
        "delist_date": delist_date,
        "catalog_active": instrument.is_active if instrument else None,
        "provider_detail_checked": provider_detail_checked,
        "provider_detail_available": bool(detail),
    }

    if list_date and list_date > end_date:
        return _permanent(
            DownloadFailureCategory.NOT_LISTED_YET,
            message,
            evidence | {"reason": "list_date_after_requested_range"},
        )
    if delist_date and delist_date < start_date:
        return _permanent(
            DownloadFailureCategory.DELISTED,
            message,
            evidence | {"reason": "delist_date_before_requested_range"},
        )
    if instrument is None and provider_detail_checked and not detail and _is_quality_error(message):
        return _permanent(
            DownloadFailureCategory.DELISTED,
            message,
            evidence | {"reason": "absent_from_catalog_and_provider"},
        )
    if _is_unsupported(symbol, name, asset_type, delist_date, end_date):
        return _permanent(
            DownloadFailureCategory.UNSUPPORTED_INSTRUMENT,
            message,
            evidence | {"reason": "non_tradable_or_wrong_instrument_family"},
        )
    if _is_quality_error(message):
        return _permanent(
            DownloadFailureCategory.QUALITY_REJECTED,
            message,
            evidence | {"reason": "canonical_quality_gate_rejected_rows"},
        )
    if "返回空行情" in message or "empty" in message.lower():
        return _permanent(
            DownloadFailureCategory.NO_MARKET_DATA,
            message,
            evidence | {"reason": "provider_returned_no_rows"},
        )
    if isinstance(error, MiniQmtUnavailableError) or any(
        text in message.lower() for text in _TRANSIENT_TEXT
    ):
        return DownloadFailureAssessment(
            category=DownloadFailureCategory.TEMPORARILY_UNAVAILABLE,
            retryable=True,
            quarantine=False,
            message=message,
            details=evidence | {"reason": "transient_provider_connectivity"},
            next_retry_at=current + timedelta(minutes=5),
        )
    return DownloadFailureAssessment(
        category=DownloadFailureCategory.PROVIDER_ERROR,
        retryable=True,
        quarantine=False,
        message=message,
        details=evidence | {"reason": "unclassified_provider_exception"},
        next_retry_at=current + timedelta(minutes=30),
    )


def _permanent(
    category: DownloadFailureCategory,
    message: str,
    details: dict[str, Any],
) -> DownloadFailureAssessment:
    return DownloadFailureAssessment(
        category=category,
        retryable=False,
        quarantine=True,
        message=message,
        details=details,
    )


def _is_quality_error(message: str) -> bool:
    lowered = message.lower()
    return any(text.lower() in lowered for text in _QUALITY_TEXT)


def _is_unsupported(
    symbol: str,
    name: str | None,
    asset_type: str | None,
    delist_date: str | None,
    end_date: str,
) -> bool:
    if asset_type == "other":
        return True
    if name and any(part in name for part in _UNSUPPORTED_NAME_PARTS):
        return True
    code, _, exchange = symbol.partition(".")
    return bool(
        exchange == "BJ"
        and code.startswith(("8", "9"))
        and name
        and _BOND_LIKE_NAME.match(name)
        and delist_date
        and delist_date > end_date
    )


def _detail_value(detail: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in detail.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _compact_date(value: Any) -> str | None:
    text = _text(value)
    if text is None:
        return None
    digits = "".join(character for character in text if character.isdigit())[:8]
    if len(digits) != 8 or digits in {"00000000", "19700101", "99999999"}:
        return None
    return digits


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
