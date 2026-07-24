from datetime import UTC, datetime
from decimal import Decimal

import pytest

from dyadtetrade.domain import Direction, OrderStatus, TradingStatus
from dyadtetrade.integrations.xtquant_converters import (
    account_from_xttrader,
    order_from_xttrader,
    position_from_xttrader,
    tick_from_xtdata,
    trade_from_xttrader,
)


def test_xtdata_tick_is_converted_at_adapter_boundary() -> None:
    tick = tick_from_xtdata(
        "510300.SH",
        {
            "lastPrice": 4.2,
            "volume": 100,
            "amount": 420,
            "bidPrice": [4.19],
            "askPrice": [4.21],
        },
        received_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert tick.last_price == Decimal("4.2")
    assert tick.trading_status is TradingStatus.TRADING
    assert tick.source == "xtdata"


def test_xtdata_tick_accepts_native_local_time_and_epoch_formats() -> None:
    local_tick = tick_from_xtdata(
        "510300.SH",
        {"time": "20260710 15:29:49", "lastPrice": 4.2, "volume": 100},
    )
    assert local_tick.timestamp == datetime(2026, 7, 10, 7, 29, 49, tzinfo=UTC)

    seconds_tick = tick_from_xtdata(
        "510300.SH",
        {"time": "1783668589", "lastPrice": 4.2, "volume": 100},
    )
    milliseconds_tick = tick_from_xtdata(
        "510300.SH",
        {"time": 1_783_668_589_000, "lastPrice": 4.2, "volume": 100},
    )
    assert seconds_tick.timestamp == milliseconds_tick.timestamp


def test_xttrader_payloads_convert_without_exposing_account_id() -> None:
    order = order_from_xttrader(
        {
            "stock_code": "600519.SH",
            "order_id": 123,
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 50,
            "traded_price": 1200,
            "order_status": 55,
            "order_time": 1_767_225_590,
        }
    )
    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.qmt_order_id == 123
    assert order.request.account_id == "masked"
    assert order.submitted_at == datetime.fromtimestamp(1_767_225_590, tz=UTC)
    trade = trade_from_xttrader(
        {
            "stock_code": "600519.SH",
            "order_id": 123,
            "traded_id": 456,
            "order_type": 23,
            "traded_volume": 50,
            "traded_price": 1200,
            "traded_time": 1_767_225_600,
            "commission": 5.123456789,
        },
        source="qmt_trade_callback",
        execution_mode="qmt_sim",
        account_id="masked",
    )
    assert trade.direction is Direction.BUY
    assert trade.qmt_order_id == 123
    assert trade.order_sysid is None
    assert trade.broker_trade_id == "456"
    assert trade.broker_reported_fee == Decimal("5.12345679")
    assert trade.total_fees == Decimal("5.12345679")
    assert trade.commission == Decimal("0")
    assert trade.fee_source == "xttrade.commission"
    assert trade.fee_breakdown_complete is False
    account = account_from_xttrader(
        {
            "account_id": "never-expose",
            "cash": 1000,
            "frozen_cash": 100,
            "market_value": 5000,
            "total_asset": 6100,
        }
    )
    assert account.account_id == "masked"
    assert account.available_cash == Decimal("1000")
    assert account.cash == Decimal("1100")
    assert account.frozen_cash == Decimal("100")
    assert account.positions_value == Decimal("5000")
    assert account.total_assets == Decimal("6100")
    account.assert_identity(expected_total=Decimal("6100"))


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (48, OrderStatus.SUBMITTING),
        (49, OrderStatus.SUBMITTING),
        (50, OrderStatus.SUBMITTED),
        (51, OrderStatus.CANCELLING),
        (52, OrderStatus.CANCELLING),
        (53, OrderStatus.CANCELLED),
        (54, OrderStatus.CANCELLED),
        (55, OrderStatus.PARTIALLY_FILLED),
        (56, OrderStatus.FILLED),
        (57, OrderStatus.REJECTED),
        (255, OrderStatus.UNKNOWN),
    ],
)
def test_xttrader_official_order_status_mapping(status_code: int, expected: OrderStatus) -> None:
    order = order_from_xttrader(
        {
            "stock_code": "510300.SH",
            "order_id": 123,
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 0,
            "traded_price": 0,
            "order_status": status_code,
        }
    )
    assert order.status is expected


def test_xttrader_binary_float_tails_are_normalized_at_adapter_boundary() -> None:
    position = position_from_xttrader(
        {
            "stock_code": "123242.SZ",
            "volume": 1930,
            "can_use_volume": 1930,
            "open_price": 133.69540414507773,
            "market_value": 251843.77000000002,
        }
    )
    account = account_from_xttrader({"cash": 18038.490000000005, "frozen_cash": 0.00000000001})

    assert position.average_cost == Decimal("133.69540415")
    assert position.last_price == Decimal("130.48900000")
    assert position.market_value == Decimal("251843.77000000")
    assert account.cash == Decimal("18038.49")
    assert account.frozen_cash == Decimal("0.00")
    assert account.available_cash == Decimal("18038.49")


def test_xttrader_uses_order_sysid_when_numeric_order_id_is_zero() -> None:
    payload = {
        "stock_code": "510300.SH",
        "order_id": 0,
        "order_sysid": "SYS-FALLBACK",
        "traded_id": "TRADE-FALLBACK",
        "order_type": 23,
        "order_volume": 100,
        "traded_volume": 100,
        "traded_price": 4.2,
        "order_status": 51,
        "traded_time": 1_767_225_600,
    }
    order = order_from_xttrader(payload)
    trade = trade_from_xttrader(
        payload,
        source="qmt_trade_query",
        execution_mode="qmt_sim",
        account_id="masked",
    )

    assert order.order_id == "SYS-FALLBACK"
    assert order.qmt_order_id is None
    assert order.order_sysid == "SYS-FALLBACK"
    assert trade.broker_order_id == "SYS-FALLBACK"
    assert trade.qmt_order_id is None
    assert trade.order_sysid == "SYS-FALLBACK"


def test_numeric_order_sysid_is_not_mislabeled_as_qmt_order_id() -> None:
    order = order_from_xttrader(
        {
            "stock_code": "510300.SH",
            "order_id": 0,
            "order_sysid": "18657",
            "order_type": 23,
            "order_volume": 100,
            "traded_volume": 100,
            "traded_price": 4.2,
            "order_status": 56,
        }
    )

    assert order.order_id == "18657"
    assert order.broker_order_id == "18657"
    assert order.qmt_order_id is None
    assert order.order_sysid == "18657"


def test_xttrader_missing_commission_is_not_invented_as_broker_zero() -> None:
    trade = trade_from_xttrader(
        {
            "stock_code": "510300.SH",
            "order_id": 123,
            "traded_id": "NO-FEE-FIELD",
            "order_type": 23,
            "traded_volume": 100,
            "traded_price": 4.2,
            "traded_time": 1_767_225_600,
        },
        source="qmt_trade_query",
        execution_mode="qmt_sim",
        account_id="masked",
    )

    assert trade.broker_reported_fee is None
    assert trade.total_fees == Decimal("0")
    assert trade.fee_source == "unavailable"


def test_qmt_live_trade_can_retain_simulated_broker_funds_provenance() -> None:
    trade = trade_from_xttrader(
        {
            "stock_code": "510300.SH",
            "order_id": 123,
            "traded_id": "SIMULATED-LIVE-FLOW",
            "order_type": 23,
            "traded_volume": 100,
            "traded_price": 4.2,
            "traded_time": 1_767_225_600,
        },
        source="qmt_trade_query",
        execution_mode="qmt_live",
        account_id="SIMULATED_ACCOUNT",
        is_simulated=True,
    )

    assert trade.execution_mode == "qmt_live"
    assert trade.is_simulated is True
    assert trade.fee_breakdown_complete is False


def test_xttrader_rejects_negative_broker_fee() -> None:
    with pytest.raises(ValueError, match="commission must be non-negative"):
        trade_from_xttrader(
            {
                "stock_code": "510300.SH",
                "order_id": 123,
                "traded_id": "BAD-FEE",
                "order_type": 23,
                "traded_volume": 100,
                "traded_price": 4.2,
                "traded_time": 1_767_225_600,
                "commission": -1,
            },
            source="qmt_trade_query",
            execution_mode="qmt_sim",
            account_id="masked",
        )
